"""
Route smoke tests — verifies the Flask skeleton without making real API calls.

Groq signal is patched to return a fixed result so tests run offline.
"""

import json
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create a test Flask client with an isolated in-memory DB."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))

    import app as app_module
    app_module.app.config["TESTING"] = True
    # Disable the rate limiter for functional tests. Its storage is a
    # module-level in-memory counter that is NOT reset between tests, so once the
    # suite makes more than 10 /submit calls the limiter starts returning 429 —
    # which previously went unnoticed because every test shared one polluted DB.
    app_module.limiter.enabled = False
    # Re-init DB against the temp path
    app_module.init_db()

    with app_module.app.test_client() as c:
        yield c


def _mock_groq_ok(confidence=0.88):
    return {
        "status": "ok",
        "label": "ai_generated",
        "confidence": confidence,
        "reasoning": "Uniform sentence structure and cliché phrases.",
    }


def _mock_groq_failed():
    return {"status": "failed", "error": "timeout"}


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_missing_content_returns_422(client):
    resp = client.post("/submit", json={"creator_id": "user_1"})
    assert resp.status_code == 422
    assert "content" in resp.get_data(as_text=True)


def test_missing_creator_id_returns_422(client):
    resp = client.post("/submit", json={"content": "Some text here."})
    assert resp.status_code == 422
    assert "creator_id" in resp.get_data(as_text=True)


def test_empty_body_returns_422(client):
    resp = client.post("/submit", data="not json", content_type="text/plain")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_submit_returns_required_fields(client):
    with patch("app.groq_classify", return_value=_mock_groq_ok(0.91)):
        resp = client.post("/submit", json={
            "content": "It is worth noting that in conclusion this work demonstrates important qualities.",
            "creator_id": "user_42",
        })
    assert resp.status_code == 200
    body = resp.get_json()
    for field in ("content_id", "label", "confidence", "transparency_text", "signal_status"):
        assert field in body, f"Missing field: {field}"


def test_high_confidence_ai_label(client):
    # With stylometric stub at 0.5, combined score = 0.95*0.588 + 0.5*0.412 = 0.765
    # That's uncertain in M3. To hit high_confidence_ai we need Groq alone
    # (i.e., short text so stylometric is inactive) with a very high score.
    # Short text (< 50 tokens) disables stylometric → Groq weight = 1.0
    with patch("app.groq_classify", return_value=_mock_groq_ok(0.95)):
        resp = client.post("/submit", json={
            "content": "Short text.",   # < 50 tokens → stylometric inactive
            "creator_id": "user_1",
        })
    body = resp.get_json()
    assert body["label"] == "high_confidence_ai"
    assert "high confidence" in body["transparency_text"]


def test_uncertain_label(client):
    with patch("app.groq_classify", return_value=_mock_groq_ok(0.55)):
        resp = client.post("/submit", json={
            "content": " ".join(["word"] * 60),
            "creator_id": "user_1",
        })
    body = resp.get_json()
    assert body["label"] == "uncertain"
    assert "has not been flagged" in body["transparency_text"]


def test_human_label(client):
    # Short text disables stylometric → Groq weight = 1.0 → score = 0.05
    with patch("app.groq_classify", return_value=_mock_groq_ok(0.05)):
        resp = client.post("/submit", json={
            "content": "Short text.",   # < 50 tokens → stylometric inactive
            "creator_id": "user_1",
        })
    body = resp.get_json()
    assert body["label"] == "high_confidence_human"
    assert "human-authored" in body["transparency_text"]


# ---------------------------------------------------------------------------
# Groq failure fallback
# ---------------------------------------------------------------------------

def test_groq_failure_still_returns_response(client):
    with patch("app.groq_classify", return_value=_mock_groq_failed()):
        resp = client.post("/submit", json={
            "content": " ".join(["word"] * 60),
            "creator_id": "user_1",
        })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["signal_status"]["groq_llm"] == "failed"
    assert "confidence" in body


# ---------------------------------------------------------------------------
# Short-text guard
# ---------------------------------------------------------------------------

def test_short_text_sets_low_token_warning(client):
    with patch("app.groq_classify", return_value=_mock_groq_ok(0.7)):
        resp = client.post("/submit", json={
            "content": "Short poem here.",   # < 30 tokens
            "creator_id": "user_1",
        })
    body = resp.get_json()
    assert body["metadata"].get("low_token_warning") is True
    assert body["signal_status"]["stylometric"] == "inactive"


def test_guard_threshold_is_calibrated_to_30():
    # Calibration fix (#1): lowered from 50 → 30 so ordinary 30–49 token
    # paragraphs keep the stylometric signal active. If this constant drifts,
    # realistic paragraphs silently fall back to Groq-only scoring again.
    import app as app_module
    assert app_module.SHORT_TEXT_TOKEN_THRESHOLD == 30


def test_mid_length_paragraph_keeps_stylometric_active(client):
    # A 40-word paragraph sits in the old dead zone (30 ≤ tokens < 50): it must
    # now run the stylometric signal rather than be excluded.
    text = " ".join(["insight"] * 40)
    with patch("app.groq_classify", return_value=_mock_groq_ok(0.7)):
        resp = client.post("/submit", json={"content": text, "creator_id": "user_1"})
    body = resp.get_json()
    assert body["signal_status"]["stylometric"] == "ok"
    assert body["metadata"].get("low_token_warning") is not True


# ---------------------------------------------------------------------------
# Groq confidence cap (calibration fix #2)
# ---------------------------------------------------------------------------

def test_groq_confidence_is_capped():
    # An over-confident Groq verdict (0.95) must be pulled to the cap (0.85) in
    # both directions before it reaches the combiner, so one weak LLM signal
    # cannot single-handedly push the score past a high-confidence threshold.
    from signals.groq_signal import label_to_ai_probability, GROQ_CONFIDENCE_CAP
    assert GROQ_CONFIDENCE_CAP == 0.85
    assert label_to_ai_probability("ai_generated", 0.95) == pytest.approx(0.85)
    assert label_to_ai_probability("human_written", 0.95) == pytest.approx(0.15)
    # Below the cap, confidence passes through unchanged.
    assert label_to_ai_probability("ai_generated", 0.55) == pytest.approx(0.55)
    assert label_to_ai_probability("human_written", 0.60) == pytest.approx(0.40)


# ---------------------------------------------------------------------------
# Audit log endpoint
# ---------------------------------------------------------------------------

def test_log_endpoint_returns_entries(client):
    with patch("app.groq_classify", return_value=_mock_groq_ok(0.9)):
        client.post("/submit", json={"content": " ".join(["word"] * 60), "creator_id": "u1"})
    resp = client.get("/log")
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, dict)
    assert isinstance(body["entries"], list)
    assert len(body["entries"]) >= 1


def test_log_entry_is_structured(client):
    with patch("app.groq_classify", return_value=_mock_groq_ok(0.9)):
        client.post("/submit", json={"content": " ".join(["word"] * 60), "creator_id": "u1"})
    entry = client.get("/log").get_json()["entries"][0]

    # Every entry carries the audit-log contract fields
    for field in ("content_id", "creator_id", "timestamp", "attribution",
                  "confidence", "llm_score", "stylometric_score", "status"):
        assert field in entry, f"Missing audit field: {field}"

    assert entry["creator_id"] == "u1"
    assert entry["status"] == "classified"
    # Signal 1 score is recorded in AI-probability direction (Groq label=ai_generated).
    # The raw Groq confidence (0.9) is capped at GROQ_CONFIDENCE_CAP (0.85) before it
    # enters the combiner, so the logged score reflects the capped value the system
    # actually used — see signals/groq_signal.label_to_ai_probability.
    assert entry["llm_score"] == pytest.approx(0.85)


def test_log_captures_both_signal_scores(client):
    # The audit log records each signal's individual score alongside the combined
    # confidence. This 60-token submission keeps the stylometric signal active, so
    # both llm_score and stylometric_score must be populated (not None), and the
    # combined confidence must sit between them.
    with patch("app.groq_classify", return_value=_mock_groq_ok(0.9)):
        client.post("/submit", json={
            "content": " ".join(["word"] * 60), "creator_id": "u_both",
        })
    entry = client.get("/log").get_json()["entries"][0]

    assert entry["llm_score"] is not None
    assert entry["stylometric_score"] is not None
    assert 0.0 <= entry["stylometric_score"] <= 1.0
    # The combined score is a weighted average of the two, so it lies within
    # their range (inclusive).
    lo, hi = sorted((entry["llm_score"], entry["stylometric_score"]))
    assert lo <= entry["confidence"] <= hi


def test_log_stylometric_score_none_when_inactive(client):
    # On short text (< 30 tokens) the stylometric signal is inactive, so its
    # logged score is NULL — mirroring how llm_score is NULL when Groq fails.
    with patch("app.groq_classify", return_value=_mock_groq_ok(0.7)):
        client.post("/submit", json={"content": "Just a short note.", "creator_id": "u_short"})
    entry = client.get("/log").get_json()["entries"][0]

    assert entry["stylometric_score"] is None
    assert entry["llm_score"] is not None
