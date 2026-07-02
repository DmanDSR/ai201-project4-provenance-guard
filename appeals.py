"""
Appeals workflow (M5).

A separate flow from the detection pipeline (see the architecture diagram in
planning.md): it touches only the storage layer, never the signals. No
automated re-classification happens — an appeal flags the record for a human
moderator.

Two request shapes are supported, sharing one core:

    POST /appeal                 body: {content_id, creator_reasoning}
        The simple, content_id-in-body form. creator_id is optional here — the
        only thing required to file is the reasoning.

    POST /appeals/<content_id>   body: {creator_id, reason}
        The original path-param form (planning.md Section 4). creator_id and
        reason are both required.

Both do the same thing (planning.md Section 4, "What the system does"):
    1. Look up content_id           → 404 if it does not exist.
    2. Reject a duplicate appeal    → 409 if one is already pending.
    3. Append the appeal to the audit log (timestamped) and
    4. flip the record status from `classified` to `under_review`.
    5. Return 200 with a confirmation payload.

Authentication is intentionally not required beyond knowing the content_id,
which is handed back to the creator in the original /submit response.
"""

from datetime import datetime, timezone

from flask import Blueprint, request, jsonify

from database import get_record, update_appeal

appeals_bp = Blueprint("appeals", __name__)

CONFIRMATION_MESSAGE = "Your appeal has been logged and will be reviewed by a moderator."


def _record_appeal(content_id: str, creator_id: str | None, reasoning: str):
    """
    Core appeal handling shared by both routes.

    Looks up the record, rejects a duplicate, writes the appeal, and flips the
    status to under_review. The creator's explanation is stored under the
    ``reason`` key so a single audit-log shape serves both request forms.

    Returns:
        (payload: dict, http_status: int) — ready to hand to jsonify().
    """
    # --- Step 1: look up the content record --------------------------------
    record = get_record(content_id)
    if record is None:
        return {
            "error": "No classification record found for the given content_id.",
            "content_id": content_id,
        }, 404

    # --- Step 2: reject a duplicate appeal ---------------------------------
    # An appeal is already pending if the record carries an appeal_json payload
    # (status will also be under_review). We do not overwrite it.
    if record.get("appeal_json"):
        return {
            "error": "An appeal for this content is already under review.",
            "content_id": content_id,
            "status": "under_review",
        }, 409

    # --- Steps 3 & 4: append appeal to the audit log, flip status ----------
    appeal = {
        "appealed_at": datetime.now(timezone.utc).isoformat(),
        "creator_id": creator_id,
        "reason": reasoning,
    }
    if not update_appeal(content_id, appeal):
        # The record existed a moment ago but the write did not land. Surface a
        # server-side failure rather than a misleading success.
        return {
            "error": "Failed to record the appeal. Please retry.",
            "content_id": content_id,
        }, 500

    # --- Step 5: confirmation payload (planning.md Section 4) ---------------
    return {
        "status": "appeal_received",
        "content_id": content_id,
        "message": CONFIRMATION_MESSAGE,
    }, 200


@appeals_bp.route("/appeal", methods=["POST"])
def submit_appeal_simple():
    """
    File an appeal with the content_id in the request body.

    Request body (JSON):
        content_id        str  — the ID returned by the original /submit call.
        creator_reasoning str  — why the creator believes the classification is wrong.
        creator_id        str  — optional identifier for the appealing creator.

    Responses:
        200  appeal_received      — logged and status set to under_review.
        404  unknown content_id   — no such classification record.
        409  appeal already open  — a pending appeal exists for this record.
        422  invalid request body — missing/empty content_id or creator_reasoning.
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be JSON."}), 422

    content_id = (body.get("content_id") or "").strip()
    creator_reasoning = (body.get("creator_reasoning") or "").strip()
    # creator_id is optional on this route — the reasoning is what's required.
    creator_id = (body.get("creator_id") or "").strip() or None

    if not content_id:
        return jsonify({"error": "'content_id' field is required and must not be empty."}), 422
    if not creator_reasoning:
        return jsonify({"error": "'creator_reasoning' field is required and must not be empty."}), 422

    payload, status = _record_appeal(content_id, creator_id, creator_reasoning)
    return jsonify(payload), status


@appeals_bp.route("/appeals/<content_id>", methods=["POST"])
def submit_appeal(content_id: str):
    """
    File an appeal with the content_id as a path parameter.

    Path param:
        content_id  str  — the ID returned by the original /submit call.

    Request body (JSON):
        creator_id  str  — identifier for the appealing creator.
        reason      str  — why the creator believes the classification is wrong.

    Responses:
        200  appeal_received      — logged and status set to under_review.
        404  unknown content_id   — no such classification record.
        409  appeal already open  — a pending appeal exists for this record.
        422  invalid request body — missing/empty creator_id or reason.
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be JSON."}), 422

    creator_id = (body.get("creator_id") or "").strip()
    reason = (body.get("reason") or "").strip()

    if not creator_id:
        return jsonify({"error": "'creator_id' field is required and must not be empty."}), 422
    if not reason:
        return jsonify({"error": "'reason' field is required and must not be empty."}), 422

    payload, status = _record_appeal(content_id, creator_id, reason)
    return jsonify(payload), status
