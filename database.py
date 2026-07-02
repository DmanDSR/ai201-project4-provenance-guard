"""
Audit log — SQLite backend.

Schema (table: classifications):
    content_id         TEXT  PRIMARY KEY
    creator_id         TEXT  identifier for the submitting creator
    submitted_at       TEXT  ISO 8601 timestamp
    label              TEXT  high_confidence_ai | high_confidence_human | uncertain
    confidence         REAL  combined score across all active signals
    llm_score          REAL  Signal 1 (Groq LLM) AI-probability, NULL if the signal failed
    stylometric_score  REAL  Signal 2 (stylometric) AI-probability, NULL if inactive (short text)
    signals_json       TEXT  JSON object of signal name → score
    signal_status_json TEXT  JSON object of signal name → ok | failed | inactive
    status             TEXT  classified | under_review
    appeal_json        TEXT  NULL until an appeal is filed (JSON)

Every classification is written before the HTTP response is returned.
A failed write is logged internally; it does not prevent the response.
"""

import json
import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH: str = "provenance_guard.db"


def _db_path() -> str:
    """
    Resolve the database path at call time (not import time).

    Reading DB_PATH lazily lets tests point each case at an isolated temp DB via
    monkeypatch.setenv — capturing it once at import silently ignored that and
    routed every test write into the real provenance_guard.db.
    """
    return os.environ.get("DB_PATH", DEFAULT_DB_PATH)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they do not exist, and migrate older schemas in place."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS classifications (
                content_id         TEXT PRIMARY KEY,
                creator_id         TEXT,
                submitted_at       TEXT NOT NULL,
                label              TEXT NOT NULL,
                confidence         REAL NOT NULL,
                llm_score          REAL,
                stylometric_score  REAL,
                signals_json       TEXT NOT NULL,
                signal_status_json TEXT NOT NULL,
                status             TEXT NOT NULL DEFAULT 'classified',
                appeal_json        TEXT
            )
        """)
        # Lightweight migration: add columns introduced after the first schema
        # so an existing provenance_guard.db keeps working without a rebuild.
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(classifications)")}
        for column, ddl in (("creator_id", "TEXT"), ("llm_score", "REAL"),
                            ("stylometric_score", "REAL")):
            if column not in existing:
                conn.execute(f"ALTER TABLE classifications ADD COLUMN {column} {ddl}")
        conn.commit()
    logger.info("Database initialised at %s", _db_path())


def log_classification(
    content_id: str,
    creator_id: str,
    submitted_at: str,
    label: str,
    confidence: float,
    signals_json: str,
    signal_status_json: str,
    llm_score: float | None = None,
    stylometric_score: float | None = None,
    status: str = "classified",
) -> None:
    """
    Write a classification record to the audit log.

    Both per-signal scores are stored explicitly alongside the combined
    ``confidence``: ``llm_score`` (Signal 1, Groq) and ``stylometric_score``
    (Signal 2). Either is ``None`` when that signal did not contribute — Groq
    failed, or stylometric was inactive on short text. The watermark signal
    (additive-only, fires rarely) remains in ``signals_json``.
    """
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO classifications
                    (content_id, creator_id, submitted_at, label, confidence,
                     llm_score, stylometric_score, signals_json,
                     signal_status_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (content_id, creator_id, submitted_at, label, confidence,
                 llm_score, stylometric_score, signals_json,
                 signal_status_json, status),
            )
            conn.commit()
    except Exception as exc:
        # Log internally but do NOT raise — the caller still returns a response
        logger.error("Audit log write failed for %s: %s", content_id, exc)


def get_record(content_id: str) -> dict | None:
    """Return the full classification record, or None if not found."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM classifications WHERE content_id = ?",
                (content_id,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)
    except Exception as exc:
        logger.error("DB read failed for content_id %s: %s", content_id, exc)
        return None


def update_appeal(content_id: str, appeal: dict) -> bool:
    """
    Append an appeal to the record and flip status to under_review.

    Returns True on success, False if the record was not found or write failed.
    """
    try:
        with _connect() as conn:
            result = conn.execute(
                """
                UPDATE classifications
                SET appeal_json = ?, status = 'under_review'
                WHERE content_id = ?
                """,
                (json.dumps(appeal), content_id),
            )
            conn.commit()
            return result.rowcount == 1
    except Exception as exc:
        logger.error("Appeal update failed for %s: %s", content_id, exc)
        return False


def fetch_log(limit: int = 20) -> list[dict]:
    """Return the most recent `limit` audit-log entries, newest first."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM classifications
                ORDER BY submitted_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        entries = []
        for row in rows:
            row = dict(row)
            appeal = json.loads(row["appeal_json"]) if row.get("appeal_json") else None
            # Shape each entry to the audit-log contract:
            #   content_id, creator_id, timestamp, attribution, confidence,
            #   llm_score, stylometric_score, status
            #   (+ full signal diagnostics for review)
            entry = {
                "content_id": row["content_id"],
                "creator_id": row.get("creator_id"),
                "timestamp": row["submitted_at"],
                "attribution": row["label"],
                "confidence": row["confidence"],
                "llm_score": row.get("llm_score"),
                "stylometric_score": row.get("stylometric_score"),
                "status": row["status"],
                "signals": json.loads(row.get("signals_json") or "{}"),
                "signal_status": json.loads(row.get("signal_status_json") or "{}"),
                "appeal": appeal,
                # Flat convenience field: the creator's explanation, promoted to
                # the top level so a reviewer (or GET /log check) can read it
                # without unpacking the nested appeal object. NULL until appealed.
                "appeal_reasoning": appeal.get("reason") if appeal else None,
            }
            entries.append(entry)
        return entries
    except Exception as exc:
        logger.error("Audit log fetch failed: %s", exc)
        return []
