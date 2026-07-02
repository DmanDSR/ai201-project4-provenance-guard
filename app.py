"""
Provenance Guard — Flask application entry point.

Wires together the rate limiter, detection pipeline, score combiner,
audit log, and response builder for the POST /submit endpoint.
Signal 2 (stylometric) is stubbed at 0.5 for this milestone (M3).
"""

import uuid
import json
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

from signals.groq_signal import classify as groq_classify, label_to_ai_probability
from signals.stylometric_signal import score as stylometric_score
from signals.watermark_signal import detect as watermark_detect
from combiner import combine_scores
from database import init_db, log_classification
from labels import get_label
from appeals import appeals_bp

load_dotenv()

# ---------------------------------------------------------------------------
# Short-text guard threshold
#
# Below this many whitespace tokens, the stylometric signal is disabled and a
# low_token_warning is set (Edge Case 1). Originally 50 per planning.md, but
# calibration testing (see context_2.md) showed that most real paragraphs run
# 30–60 tokens, so a 50-token cutoff disabled stylometric on the majority of
# realistic inputs — leaving the weaker Groq signal to decide alone, which it
# did poorly. Lowered to 30: still excludes haiku-length fragments (~17 tokens)
# where TTR/variance are genuinely unreliable, but keeps stylometric active on
# ordinary paragraphs so it can counterbalance a confidently-wrong Groq verdict.
# ---------------------------------------------------------------------------
SHORT_TEXT_TOKEN_THRESHOLD = 30

app = Flask(__name__)

# Appeals workflow (M5) — POST /appeals/{content_id}. Separate flow from the
# detection pipeline; touches only the storage layer.
app.register_blueprint(appeals_bp)

# ---------------------------------------------------------------------------
# Rate limiter — 10/minute and 100/day per IP on /submit
#
# Rationale (see README "Rate limiting"): a genuine writer submits their own
# drafts a handful of times while iterating, so 10 requests in any 60-second
# window is comfortably above real human cadence yet stops a script from
# flooding the (paid, latency-bound) Groq signal. The 100/day ceiling caps
# sustained abuse that stays under the per-minute bar — a human iterating all
# day rarely nears 100 submissions, but a slow-drip bot would.
# ---------------------------------------------------------------------------
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],          # no global default; limits are per-route
    storage_uri="memory://",
)

# A rate-limited request should get the same JSON shape as every other error
# on this API, not Flask-Limiter's default HTML page.
@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        "error": "Rate limit exceeded. Please slow down and retry shortly.",
        "limit": e.description,   # e.g. "10 per 1 minute"
    }), 429


# ---------------------------------------------------------------------------
# Database bootstrap
# ---------------------------------------------------------------------------
with app.app_context():
    init_db()


# ---------------------------------------------------------------------------
# POST /submit
# ---------------------------------------------------------------------------
@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    """
    Accept a piece of text content for AI-vs-human classification.

    Request body (JSON):
        content    str  — the text to classify
        creator_id str  — identifier for the submitting creator

    Response (JSON):
        content_id       str   — unique ID for this submission
        label            str   — high_confidence_ai | high_confidence_human | uncertain
        confidence       float — combined score in [0.0, 1.0]
        transparency_text str  — verbatim label text for display to end users
        signal_status    dict  — per-signal outcome (ok | failed | inactive)
        metadata         dict  — low_token_warning, etc.
    """
    body = request.get_json(silent=True)

    # --- Input validation ---------------------------------------------------
    if not body:
        return jsonify({"error": "Request body must be JSON."}), 422

    # Accept "text" as an alias for "content" so both field names work
    content = (body.get("content") or body.get("text") or "").strip()
    creator_id = body.get("creator_id", "").strip()

    if not content:
        return jsonify({"error": "'content' (or 'text') field is required and must not be empty."}), 422
    if not creator_id:
        return jsonify({"error": "'creator_id' field is required and must not be empty."}), 422

    # --- Run detection pipeline --------------------------------------------
    content_id = str(uuid.uuid4())
    submitted_at = datetime.now(timezone.utc).isoformat()

    signal_scores = {}
    signal_status = {}
    metadata = {}

    # Token count for short-text guard
    token_count = len(content.split())
    low_token = token_count < SHORT_TEXT_TOKEN_THRESHOLD
    if low_token:
        metadata["low_token_warning"] = True

    # Signal 1 — Groq LLM
    groq_result = groq_classify(content)
    groq_reasoning = None
    groq_attribution = None
    if groq_result["status"] == "ok":
        # Normalise to AI-probability direction before the combiner:
        # Groq's "confidence" means confidence in its own label, not in AI.
        # label_to_ai_probability() inverts the score when label="human_written".
        ai_prob = label_to_ai_probability(groq_result["label"], groq_result["confidence"])
        signal_scores["groq_llm"] = ai_prob
        signal_status["groq_llm"] = "ok"
        groq_reasoning = groq_result.get("reasoning")
        groq_attribution = groq_result.get("label")   # "ai_generated" | "human_written"
    else:
        signal_status["groq_llm"] = "failed"

    # Signal 2 — Stylometric heuristics (stub returns 0.5 in M3)
    # Weight is zeroed if token count < 50
    if not low_token:
        stylo_result = stylometric_score(content)
        signal_scores["stylometric"] = stylo_result["score"]
        signal_status["stylometric"] = "ok"
    else:
        signal_status["stylometric"] = "inactive"

    # Signal 3 — Watermark z-score (additive-only)
    watermark_result = watermark_detect(content)
    signal_status["watermark"] = "ok"
    if watermark_result["fires"]:
        signal_scores["watermark"] = 1.0   # firing = strong AI indicator
        metadata["watermark_z_score"] = watermark_result["z_score"]
    # else: signal does not contribute — not added to signal_scores

    # --- Combine scores ----------------------------------------------------
    confidence = combine_scores(signal_scores)

    # --- Assign label & transparency text ----------------------------------
    label, transparency_text = _get_label(confidence)

    # --- Write audit log ---------------------------------------------------
    # Written before the response is returned (per requirements spec). A failed
    # write is swallowed inside log_classification so the caller still responds.
    log_classification(
        content_id=content_id,
        creator_id=creator_id,
        submitted_at=submitted_at,
        label=label,
        confidence=round(confidence, 4),
        llm_score=signal_scores.get("groq_llm"),          # Signal 1 score; None if it failed
        stylometric_score=signal_scores.get("stylometric"),  # Signal 2 score; None if inactive
        signals_json=json.dumps(signal_scores),
        signal_status_json=json.dumps(signal_status),
        status="classified",
    )

    # --- Return response ---------------------------------------------------
    return jsonify({
        "content_id": content_id,
        "attribution": groq_attribution,          # raw Signal 1 verdict
        "label": label,                           # final combined label
        "confidence": round(confidence, 4),
        "transparency_text": transparency_text,
        "signal_status": signal_status,
        "groq_reasoning": groq_reasoning,
        "metadata": metadata,
    }), 200


# ---------------------------------------------------------------------------
# GET /log
# ---------------------------------------------------------------------------
@app.route("/log", methods=["GET"])
def get_log():
    """
    Return the most recent audit-log entries, newest first, as JSON.

    Query params:
        limit  int  — max entries to return (default 20)

    Note: intentionally unauthenticated — this exists for documentation and
    grading visibility. A real deployment would gate it behind auth.
    """
    from database import fetch_log
    limit = request.args.get("limit", 20, type=int)
    return jsonify({"entries": fetch_log(limit=limit)}), 200


# ---------------------------------------------------------------------------
# Label selector
# ---------------------------------------------------------------------------
def _get_label(confidence: float) -> tuple[str, str]:
    """
    Map a confidence score to (label, verbatim transparency text).

    Thin adapter over ``labels.get_label`` (the single source of truth for the
    thresholds and verbatim copy in planning.md Section 3). Kept as a tuple-
    returning shim so the existing /submit wiring is unchanged.
    """
    result = get_label(confidence)
    return result["label"], result["transparency_text"]


if __name__ == "__main__":
    app.run(debug=True)
