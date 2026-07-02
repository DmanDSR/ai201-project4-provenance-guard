"""
Unit tests for Signal 2 (stylometric heuristics) and verification that the
score combiner + label thresholds match planning.md.

Signal 2 is tested independently of the Flask pipeline (pure computation).
The final block verifies the *scoring logic* against the exact weights and
threshold bands written in planning.md Sections 1 and 2 — this is the
"does the implementation silently diverge from the spec?" check.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from signals.stylometric_signal import score
from combiner import combine_scores, BASE_WEIGHTS


# ---------------------------------------------------------------------------
# Reference samples with known feature profiles
# ---------------------------------------------------------------------------
AI_LIKE = (
    "It is worth noting that artificial intelligence continues to reshape modern "
    "industry. In conclusion, the transformative impact of these systems cannot be "
    "overstated. It is important to consider the broader implications carefully. "
    "Furthermore, organizations must adapt to this rapidly evolving landscape today."
)

HUMAN_LIKE = (
    "Rain. Again. My grandmother — she of the iron skillet and the sharper tongue — "
    "would have laughed at me standing here, soaked through, waiting for a bus that "
    "clearly wasn't coming. Was it worth it? Honestly, who knows. I bought the ticket; "
    "I take the ride. Sometimes the whole point is just to get gloriously, "
    "irretrievably lost for an afternoon and call it living."
)


# ---------------------------------------------------------------------------
# Return-shape contract
# ---------------------------------------------------------------------------
def test_return_shape():
    result = score(AI_LIKE)
    assert set(result.keys()) == {"score", "features"}
    assert 0.0 <= result["score"] <= 1.0
    f = result["features"]
    for key in ("ttr", "mean_sentence_length", "sentence_length_std",
                "ai_phrase_density", "punctuation_entropy",
                "subscores", "token_count", "sentence_count"):
        assert key in f, f"missing feature key: {key}"


# ---------------------------------------------------------------------------
# Directional correctness (the core requirement: higher = more AI-like)
# ---------------------------------------------------------------------------
def test_ai_like_scores_above_half():
    assert score(AI_LIKE)["score"] > 0.5


def test_human_like_scores_below_half():
    assert score(HUMAN_LIKE)["score"] < 0.5


def test_ai_scores_higher_than_human():
    assert score(AI_LIKE)["score"] > score(HUMAN_LIKE)["score"]


# ---------------------------------------------------------------------------
# Individual feature behaviour
# ---------------------------------------------------------------------------
def test_ai_phrases_detected():
    # AI_LIKE contains "it is worth noting", "in conclusion", etc.
    assert score(AI_LIKE)["features"]["ai_phrase_density"] > 0.0
    # HUMAN_LIKE contains none of the catalogued phrases.
    assert score(HUMAN_LIKE)["features"]["ai_phrase_density"] == 0.0


def test_human_has_higher_sentence_variance():
    ai_std = score(AI_LIKE)["features"]["sentence_length_std"]
    human_std = score(HUMAN_LIKE)["features"]["sentence_length_std"]
    assert human_std > ai_std


def test_all_subscores_in_range():
    subs = score(AI_LIKE)["features"]["subscores"]
    assert all(0.0 <= v <= 1.0 for v in subs.values())


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
def test_empty_text_does_not_crash():
    result = score("")
    assert 0.0 <= result["score"] <= 1.0
    assert result["features"]["token_count"] == 0


def test_single_word_does_not_crash():
    result = score("Hello")
    assert 0.0 <= result["score"] <= 1.0


def test_score_is_deterministic():
    assert score(AI_LIKE)["score"] == score(AI_LIKE)["score"]


def test_ttr_subscore_nonzero_on_realistic_prose():
    # Calibration fix (#3): the TTR tent was re-centred (peak 0.5 → 0.75) because
    # ordinary short prose (TTR ≈ 0.85–0.90) previously collapsed the sub-score to
    # 0.0 — dead weight that only ever biased scores toward "human". It should now
    # read near-neutral (well above 0) for normal text.
    ttr_sub = score(HUMAN_LIKE)["features"]["subscores"]["ttr"]
    assert ttr_sub > 0.2, f"TTR sub-score collapsed to {ttr_sub}; re-centring regressed"


# ===========================================================================
# SCORING-LOGIC VERIFICATION vs planning.md
# ===========================================================================
# planning.md Section 1: weights groq_llm=0.50, stylometric=0.35, watermark=0.15
# planning.md Section 2: >=0.85 high AI, <=0.20 high human, else uncertain
# ---------------------------------------------------------------------------

def test_base_weights_match_spec():
    assert BASE_WEIGHTS["groq_llm"] == 0.50
    assert BASE_WEIGHTS["stylometric"] == 0.35
    assert BASE_WEIGHTS["watermark"] == 0.15


def test_two_signal_combine_matches_renormalized_formula():
    # planning.md: with watermark absent, weights renormalize over 0.85.
    groq, stylo = 0.9, 0.4
    expected = (0.50 * groq + 0.35 * stylo) / 0.85
    got = combine_scores({"groq_llm": groq, "stylometric": stylo})
    assert got == pytest.approx(expected)


def test_all_three_signals_combine_matches_formula():
    groq, stylo, wm = 0.9, 0.8, 1.0
    expected = (0.50 * groq + 0.35 * stylo + 0.15 * wm) / 1.0
    got = combine_scores({"groq_llm": groq, "stylometric": stylo, "watermark": wm})
    assert got == pytest.approx(expected)


def test_combine_output_always_in_range():
    assert combine_scores({"groq_llm": 1.0, "stylometric": 1.0}) <= 1.0
    assert combine_scores({"groq_llm": 0.0, "stylometric": 0.0}) >= 0.0


@pytest.fixture
def get_label(tmp_path, monkeypatch):
    """Import the real label selector from app.py with an isolated DB."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import app as app_module
    return app_module._get_label


def test_label_bands_match_spec(get_label):
    # planning.md Section 2 boundaries — verify the exact thresholds, including
    # the inclusive edges (>= 0.85 and <= 0.20).
    assert get_label(0.85)[0] == "high_confidence_ai"
    assert get_label(0.90)[0] == "high_confidence_ai"
    assert get_label(0.8499)[0] == "uncertain"
    assert get_label(0.60)[0] == "uncertain"
    assert get_label(0.2001)[0] == "uncertain"
    assert get_label(0.20)[0] == "high_confidence_human"
    assert get_label(0.05)[0] == "high_confidence_human"


def test_label_text_is_verbatim_from_spec(get_label):
    assert "high confidence" in get_label(0.90)[1]
    assert "human-authored" in get_label(0.10)[1]
    assert "has not been flagged" in get_label(0.60)[1]
