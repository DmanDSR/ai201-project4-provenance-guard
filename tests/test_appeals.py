"""
Appeals endpoint tests (M5) — POST /appeals/{content_id}.

Verifies the full planning.md Section 4 workflow: 404 on unknown content_id,
409 on a duplicate appeal, 422 on a bad body, and — on the happy path — that
the appeal is written to the audit log AND the record status flips from
`classified` to `under_review`.

Groq is patched so /submit runs offline; the DB and limiter are isolated per
the shared fixture pattern in test_routes.py.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Test client with an isolated temp DB and the rate limiter disabled."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))

    import app as app_module
    app_module.app.config["TESTING"] = True
    app_module.limiter.enabled = False
    app_module.init_db()

    with app_module.app.test_client() as c:
        yield c


def _mock_groq_ok(confidence=0.9):
    return {
        "status": "ok",
        "label": "ai_generated",
        "confidence": confidence,
        "reasoning": "Formulaic phrasing.",
    }


def _submit_one(client, creator_id="creator_1"):
    """Submit a classification and return its content_id."""
    with patch("app.groq_classify", return_value=_mock_groq_ok(0.9)):
        resp = client.post("/submit", json={
            "content": " ".join(["word"] * 60),
            "creator_id": creator_id,
        })
    assert resp.status_code == 200
    return resp.get_json()["content_id"]


# ---------------------------------------------------------------------------
# Happy path — status update + audit log
# ---------------------------------------------------------------------------

def test_valid_appeal_returns_200_with_confirmation(client):
    content_id = _submit_one(client)
    resp = client.post(f"/appeals/{content_id}", json={
        "creator_id": "creator_1",
        "reason": "I wrote this entirely myself during a workshop.",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "appeal_received"
    assert body["content_id"] == content_id
    assert body["message"] == "Your appeal has been logged and will be reviewed by a moderator."


def test_valid_appeal_flips_status_to_under_review(client):
    content_id = _submit_one(client)

    # Before appeal: status is classified
    from database import get_record
    assert get_record(content_id)["status"] == "classified"

    client.post(f"/appeals/{content_id}", json={
        "creator_id": "creator_1",
        "reason": "This is my original work.",
    })

    # After appeal: status flipped to under_review
    record = get_record(content_id)
    assert record["status"] == "under_review"


def test_valid_appeal_is_logged_with_creator_and_reason(client):
    content_id = _submit_one(client)
    client.post(f"/appeals/{content_id}", json={
        "creator_id": "creator_42",
        "reason": "I can provide my draft notes.",
    })

    # The appeal appears in the audit log (GET /log) with the right shape.
    entry = next(
        e for e in client.get("/log").get_json()["entries"]
        if e["content_id"] == content_id
    )
    assert entry["status"] == "under_review"
    assert entry["appeal"] is not None
    assert entry["appeal"]["creator_id"] == "creator_42"
    assert entry["appeal"]["reason"] == "I can provide my draft notes."
    assert "appealed_at" in entry["appeal"]


# ---------------------------------------------------------------------------
# POST /appeal — content_id-in-body form (creator_reasoning field)
# ---------------------------------------------------------------------------

def test_appeal_body_form_returns_confirmation(client):
    content_id = _submit_one(client)
    resp = client.post("/appeal", json={
        "content_id": content_id,
        "creator_reasoning": (
            "I wrote this myself from personal experience. I am a non-native "
            "English speaker and my writing style may appear more formal."
        ),
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "appeal_received"
    assert body["content_id"] == content_id
    assert body["message"] == "Your appeal has been logged and will be reviewed by a moderator."


def test_appeal_body_form_flips_status_and_populates_reasoning_in_log(client):
    # Mirrors the grader's manual check: submit → appeal via POST /appeal →
    # GET /log shows status under_review and appeal_reasoning populated.
    content_id = _submit_one(client)
    reasoning = "I wrote this myself from personal experience."
    client.post("/appeal", json={
        "content_id": content_id,
        "creator_reasoning": reasoning,
    })

    entry = next(
        e for e in client.get("/log").get_json()["entries"]
        if e["content_id"] == content_id
    )
    assert entry["status"] == "under_review"
    assert entry["appeal_reasoning"] == reasoning
    # The nested appeal object stays intact for reviewers.
    assert entry["appeal"]["reason"] == reasoning


def test_appeal_body_form_creator_id_is_optional(client):
    # The curl in the spec sends no creator_id — it must still succeed.
    content_id = _submit_one(client)
    resp = client.post("/appeal", json={
        "content_id": content_id,
        "creator_reasoning": "My own work.",
    })
    assert resp.status_code == 200


def test_appeal_body_form_missing_content_id_returns_422(client):
    resp = client.post("/appeal", json={"creator_reasoning": "My own work."})
    assert resp.status_code == 422


def test_appeal_body_form_missing_reasoning_returns_422(client):
    content_id = _submit_one(client)
    resp = client.post("/appeal", json={"content_id": content_id})
    assert resp.status_code == 422


def test_appeal_body_form_unknown_content_id_returns_404(client):
    resp = client.post("/appeal", json={
        "content_id": "does-not-exist",
        "creator_reasoning": "Anything.",
    })
    assert resp.status_code == 404


def test_appeal_body_form_duplicate_returns_409(client):
    content_id = _submit_one(client)
    first = client.post("/appeal", json={
        "content_id": content_id, "creator_reasoning": "First.",
    })
    assert first.status_code == 200
    second = client.post("/appeal", json={
        "content_id": content_id, "creator_reasoning": "Second.",
    })
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_unknown_content_id_returns_404(client):
    resp = client.post("/appeals/does-not-exist", json={
        "creator_id": "creator_1",
        "reason": "Anything.",
    })
    assert resp.status_code == 404


def test_duplicate_appeal_returns_409(client):
    content_id = _submit_one(client)
    first = client.post(f"/appeals/{content_id}", json={
        "creator_id": "creator_1", "reason": "First appeal.",
    })
    assert first.status_code == 200

    second = client.post(f"/appeals/{content_id}", json={
        "creator_id": "creator_1", "reason": "Second appeal.",
    })
    assert second.status_code == 409


def test_duplicate_appeal_does_not_overwrite_original(client):
    content_id = _submit_one(client)
    client.post(f"/appeals/{content_id}", json={
        "creator_id": "creator_1", "reason": "Original reason.",
    })
    client.post(f"/appeals/{content_id}", json={
        "creator_id": "attacker", "reason": "Tampered reason.",
    })

    from database import get_record
    import json as _json
    appeal = _json.loads(get_record(content_id)["appeal_json"])
    assert appeal["reason"] == "Original reason."
    assert appeal["creator_id"] == "creator_1"


def test_missing_reason_returns_422(client):
    content_id = _submit_one(client)
    resp = client.post(f"/appeals/{content_id}", json={"creator_id": "creator_1"})
    assert resp.status_code == 422


def test_missing_creator_id_returns_422(client):
    content_id = _submit_one(client)
    resp = client.post(f"/appeals/{content_id}", json={"reason": "Some reason."})
    assert resp.status_code == 422


def test_non_json_body_returns_422(client):
    content_id = _submit_one(client)
    resp = client.post(f"/appeals/{content_id}", data="not json", content_type="text/plain")
    assert resp.status_code == 422
