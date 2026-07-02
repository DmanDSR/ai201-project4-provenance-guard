"""
Unit tests for Signal 3 (KGW watermark z-score) and verification that its
additive-only contribution matches planning.md.

Signal 3 is tested independently of the Flask pipeline (pure computation, no
API key). Because the green/red partition is a deterministic hash
(`_is_green(prev, curr)`), a *synthetically watermarked* text can be
constructed here that is guaranteed to fire — every token is chosen to land in
its own green list. Ordinary prose sits at the ~50 % chance rate and must stay
silent. This is the "prove it fires / prove it stays silent" check that the
Signal 1 and Signal 2 tests already have.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math

import pytest

from signals.watermark_signal import detect, _is_green, _Z_THRESHOLD, _GREEN_FRACTION
from combiner import combine_scores, BASE_WEIGHTS


# ---------------------------------------------------------------------------
# Ordinary prose — the same samples used by the Signal 1 / Signal 2 live tests.
# These carry no reconstructable watermark, so green membership is ~50 % and the
# detector must NOT fire.
# ---------------------------------------------------------------------------
HUMAN_PROSE = (
    "The sun dipped below the horizon, painting the sky in hues of amber and rose. "
    "I sat on the porch, coffee in hand, watching the neighborhood slowly go quiet."
)

AI_PROSE = (
    "It is worth noting that artificial intelligence has the potential to revolutionize "
    "numerous industries. In conclusion, the transformative impact of AI on society "
    "cannot be overstated. It is important to consider the ethical implications."
)


# ---------------------------------------------------------------------------
# Synthetic-watermark construction
#
# Greedily pick each next word from a pool so that _is_green(prev, word) is True.
# With a ~90-word pool the chance no candidate is green is 0.5**90 ≈ 0, so every
# appended token is green by construction → green_count == n_tested.
#   z = (n_tested*0.5) / sqrt(n_tested*0.25) = sqrt(n_tested)
# ---------------------------------------------------------------------------
_POOL = [
    "the", "of", "and", "to", "a", "in", "that", "is", "was", "he", "for", "it",
    "with", "as", "his", "on", "be", "at", "by", "had", "not", "are", "but", "from",
    "or", "have", "an", "they", "which", "one", "you", "were", "her", "all", "she",
    "there", "would", "their", "we", "him", "been", "has", "when", "who", "will",
    "more", "no", "if", "out", "so", "said", "what", "up", "its", "about", "into",
    "than", "them", "can", "only", "other", "new", "some", "could", "time", "these",
    "two", "may", "then", "do", "first", "any", "my", "now", "such", "like", "our",
    "over", "man", "me", "even", "most", "made", "after", "also", "did", "many",
]


def _build_watermarked_text(n_words: int, seed_word: str = "the") -> str:
    """Construct text where every token after the first is in its green list."""
    words = [seed_word]
    for _ in range(n_words - 1):
        prev = words[-1]
        for cand in _POOL:
            if _is_green(prev, cand):
                words.append(cand)
                break
        else:  # pragma: no cover — statistically unreachable with this pool
            words.append(_POOL[0])
    return " ".join(words)


# ---------------------------------------------------------------------------
# Return-shape contract
# ---------------------------------------------------------------------------
def test_return_shape():
    result = detect(AI_PROSE)
    assert set(result.keys()) == {"z_score", "fires"}
    assert isinstance(result["z_score"], float)
    assert isinstance(result["fires"], bool)


# ---------------------------------------------------------------------------
# Core behaviour — fires on watermarked, silent on ordinary prose
# ---------------------------------------------------------------------------
def test_synthetic_watermark_fires():
    result = detect(_build_watermarked_text(60))
    assert result["fires"] is True
    # All-green construction ⇒ z ≈ sqrt(n_tested) = sqrt(59) ≈ 7.68
    assert result["z_score"] == pytest.approx(math.sqrt(59), abs=1e-3)


def test_human_prose_does_not_fire():
    result = detect(HUMAN_PROSE)
    assert result["fires"] is False


def test_ai_prose_does_not_fire():
    # Even LLM-styled prose has no *reconstructable* watermark here — this is the
    # documented limitation: absence of a fire is not evidence of human origin.
    result = detect(AI_PROSE)
    assert result["fires"] is False


# ---------------------------------------------------------------------------
# Threshold logic — fire boundary is z >= 4.0 (planning.md Section 1)
# ---------------------------------------------------------------------------
def test_fires_exactly_at_threshold():
    # 17 words → 16 tested, all green → z = 8 / sqrt(4) = 4.0 exactly → fires (>=).
    result = detect(_build_watermarked_text(17))
    assert result["z_score"] == pytest.approx(_Z_THRESHOLD)
    assert result["fires"] is True


def test_does_not_fire_just_below_threshold():
    # 16 words → 15 tested, all green → z = 7.5 / sqrt(3.75) ≈ 3.873 < 4.0.
    result = detect(_build_watermarked_text(16))
    assert result["z_score"] < _Z_THRESHOLD
    assert result["fires"] is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
def test_empty_text_does_not_crash():
    assert detect("") == {"z_score": 0.0, "fires": False}


def test_single_word_does_not_fire():
    # n < 2: no token pairs to test.
    assert detect("Hello") == {"z_score": 0.0, "fires": False}


def test_detect_is_deterministic():
    text = _build_watermarked_text(40)
    assert detect(text) == detect(text)


def test_green_fraction_is_one_half():
    # The z-score math assumes p = 0.5 per token; guard the constant so a change
    # to the partition can't silently break every threshold above.
    assert _GREEN_FRACTION == 0.5


# ===========================================================================
# ADDITIVE-ONLY WIRING vs planning.md
# ===========================================================================
# planning.md Section 1: watermark weight 0.15, applied ONLY when it fires; a
# firing watermark is passed to the combiner as {"watermark": 1.0}.
# ---------------------------------------------------------------------------

def test_watermark_weight_matches_spec():
    assert BASE_WEIGHTS["watermark"] == 0.15


def test_firing_watermark_raises_combined_score():
    # Same two base signals, with vs. without a firing watermark. Firing must
    # push the combined confidence up (additive-only: it never lowers it).
    without = combine_scores({"groq_llm": 0.6, "stylometric": 0.6})
    with_wm = combine_scores({"groq_llm": 0.6, "stylometric": 0.6, "watermark": 1.0})
    assert with_wm > without
    # Exact renormalized value from the spec formula.
    expected = (0.50 * 0.6 + 0.35 * 0.6 + 0.15 * 1.0) / 1.0
    assert with_wm == pytest.approx(expected)
