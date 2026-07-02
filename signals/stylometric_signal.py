"""
Signal 2 — Stylometric Heuristics  (M4 implementation)

Statistical surface properties of the text that differ systematically between
AI and human writing. AI text tends to be more uniform; human writing is more
variable and idiosyncratic.

Five features are computed in pure Python (no external calls or dependencies):

    Feature                       What it captures
    ---------------------------   -----------------------------------------------
    Type-Token Ratio (TTR)        Lexical diversity: unique / total tokens.
                                  AI clusters at mid-range uniformity.
    Mean sentence length          AI tends toward consistent, moderate length.
    Sentence-length std dev       Human writing has higher variance; low = AI.
    AI phrase density             Frequency of phrases over-represented in LLM
                                  output ("it is worth noting", "in conclusion").
    Punctuation entropy           Variety of punctuation used; AI uses fewer types.

Each raw feature is mapped to a per-feature AI sub-score in [0.0, 1.0]
(higher = more AI-like), then combined via a fixed internal weight map into a
single stylometric score.

DIRECTION CONVENTION (see context_2.md Issue 6): every signal score must run the
same way — higher = more AI-like. Groq's raw confidence was backwards and had to
be inverted; stylometric is authored in the correct direction from the start.

Return contract:
    {
        "score":   float 0.0-1.0  (higher = more AI-like),
        "features": {
            # raw measured values
            "ttr": float, "mean_sentence_length": float,
            "sentence_length_std": float, "ai_phrase_density": float,
            "punctuation_entropy": float,
            # per-feature normalised AI sub-scores (for transparency/debugging)
            "subscores": { ... },
            "token_count": int, "sentence_count": int,
        }
    }
"""

import re
import math
import logging
import statistics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal feature weights — sum to 1.0
#
# The two strongest, most directional signals (sentence-length variance and
# AI-phrase density) carry the most weight. TTR and mean sentence length are
# the weakest (both modelled as "AI clusters in a mid-range band"), so they
# carry the least. These are design decisions, calibrated by hand and
# documented here so they can be re-tuned against real samples.
# ---------------------------------------------------------------------------
FEATURE_WEIGHTS: dict[str, float] = {
    "sentence_length_std": 0.30,
    "ai_phrase_density":   0.30,
    "punctuation_entropy": 0.15,
    "mean_sentence_length": 0.15,
    "ttr":                 0.10,
}

# Phrases statistically over-represented in LLM output. Matched as
# case-insensitive substrings against the lowercased text.
AI_PHRASES: tuple[str, ...] = (
    "it is worth noting",
    "it is important to note",
    "it is important to consider",
    "it is important to",
    "in conclusion",
    "in summary",
    "overall,",
    "furthermore",
    "moreover",
    "additionally,",
    "cannot be overstated",
    "rapidly evolving",
    "when it comes to",
    "in today's",
    "a testament to",
    "plays a crucial role",
    "plays a vital role",
    "the transformative",
    "delve into",
    "navigating the",
)

# Punctuation marks considered when computing punctuation entropy.
_PUNCTUATION: str = ".,;:!?\"'()-—…"

# Regex for word tokens (letters, digits, and internal apostrophes).
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
# Sentence splitter: break on runs of ., !, ? (and keep it simple).
_SENTENCE_RE = re.compile(r"[.!?]+")


def _clamp01(x: float) -> float:
    """Clamp a value into [0.0, 1.0]."""
    return max(0.0, min(1.0, x))


def _tent(value: float, peak: float, half_width: float) -> float:
    """
    Triangular ('tent') response peaking at `peak`, falling linearly to 0 at
    `peak ± half_width`. Used for features where the AI-like region is a
    *middle band* rather than an extreme (TTR, mean sentence length).
    """
    return _clamp01(1.0 - abs(value - peak) / half_width)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------
def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _type_token_ratio(tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    return len(set(tokens)) / len(tokens)


def _sentence_lengths(sentences: list[str]) -> list[int]:
    return [len(_tokenize(s)) for s in sentences if _tokenize(s)]


def _ai_phrase_density(text: str, token_count: int) -> float:
    """AI-phrase hits per 100 words (length-normalised)."""
    lowered = text.lower()
    hits = sum(lowered.count(phrase) for phrase in AI_PHRASES)
    if token_count == 0:
        return 0.0
    return hits / (token_count / 100.0)


def _punctuation_entropy(text: str) -> tuple[float, int]:
    """
    Shannon entropy (bits) over the distribution of punctuation marks used.
    Returns (entropy, total_punctuation_count).
    """
    counts: dict[str, int] = {}
    for ch in text:
        if ch in _PUNCTUATION:
            counts[ch] = counts.get(ch, 0) + 1

    total = sum(counts.values())
    if total == 0:
        return 0.0, 0

    entropy = 0.0
    for c in counts.values():
        p = c / total
        entropy -= p * math.log2(p)
    return entropy, total


# ---------------------------------------------------------------------------
# Per-feature normalisation → AI sub-score in [0, 1] (higher = more AI-like)
# ---------------------------------------------------------------------------
def _score_ttr(ttr: float) -> float:
    # AI text clusters at a moderately-high, uniform lexical diversity; very
    # repetitive (low TTR) and very idiosyncratic (high TTR) both read as more
    # human. Calibration testing (see context_2.md) showed the original peak of
    # 0.50 sat far below the TTR of *all* realistic short text (~0.85–0.90), so
    # this sub-score collapsed to 0.0 on every input — dead weight that only
    # ever biased the aggregate toward "human." Re-centred to peak 0.75 with a
    # 0.24 half-width so ordinary prose (TTR ≈ 0.87) reads ≈ neutral 0.5 instead
    # of a hard 0, and the feature stops systematically dragging scores down.
    return _tent(ttr, peak=0.75, half_width=0.24)


def _score_mean_sentence_length(mean_len: float) -> float:
    # AI favours consistent, moderate sentence length (~18 words). Very short
    # or very long mean lengths are more human. Peak at 18 words.
    return _tent(mean_len, peak=18.0, half_width=14.0)


def _score_sentence_length_std(std: float) -> float:
    # Low variance is the classic AI tell; human writing mixes long and short
    # sentences. Monotonic: std == 0 → 1.0, std >= 10 → 0.0.
    return _clamp01(1.0 - std / 10.0)


def _score_ai_phrase_density(density_per_100w: float) -> float:
    # Direct signal. ~1.5 AI phrases per 100 words saturates to 1.0.
    return _clamp01(density_per_100w / 1.5)


def _score_punctuation_entropy(entropy: float, punct_count: int) -> float:
    # Fewer punctuation *types* (lower entropy) is more AI-like. Normalise
    # against ~3 bits (≈ 8 distinct marks) of maximum expected variety.
    # Guard: too little punctuation to judge → neutral 0.5.
    if punct_count < 3:
        return 0.5
    normalised = _clamp01(entropy / 3.0)
    return _clamp01(1.0 - normalised)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------
def score(text: str) -> dict:
    """
    Compute stylometric features and return a combined AI-likelihood score.

    Args:
        text: The text to analyse.

    Returns:
        {"score": float, "features": dict}  (see module docstring for shape)

    Note: this function is *pure computation* — it does not apply the
    short-text guard. The pipeline (app.py) is responsible for zeroing the
    stylometric weight when token_count < 50 (Edge Case 1). This keeps the
    signal independently testable on any input.
    """
    tokens = _tokenize(text)
    sentences = _split_sentences(text)
    sent_lengths = _sentence_lengths(sentences)

    token_count = len(tokens)

    # --- raw features ------------------------------------------------------
    ttr = _type_token_ratio(tokens)
    mean_len = statistics.fmean(sent_lengths) if sent_lengths else 0.0
    # pstdev needs >= 1 value; a single sentence has 0 variance by definition.
    std_len = statistics.pstdev(sent_lengths) if len(sent_lengths) >= 2 else 0.0
    phrase_density = _ai_phrase_density(text, token_count)
    punct_entropy, punct_count = _punctuation_entropy(text)

    # --- per-feature AI sub-scores ----------------------------------------
    subscores = {
        "ttr": _score_ttr(ttr),
        "mean_sentence_length": _score_mean_sentence_length(mean_len),
        "sentence_length_std": _score_sentence_length_std(std_len),
        "ai_phrase_density": _score_ai_phrase_density(phrase_density),
        "punctuation_entropy": _score_punctuation_entropy(punct_entropy, punct_count),
    }

    # --- weighted combine --------------------------------------------------
    combined = sum(FEATURE_WEIGHTS[name] * sub for name, sub in subscores.items())
    combined = _clamp01(combined)

    logger.debug(
        "stylometric: score=%.3f ttr=%.3f mean=%.2f std=%.2f phrases/100w=%.2f Hpunct=%.2f",
        combined, ttr, mean_len, std_len, phrase_density, punct_entropy,
    )

    return {
        "score": round(combined, 4),
        "features": {
            "ttr": round(ttr, 4),
            "mean_sentence_length": round(mean_len, 4),
            "sentence_length_std": round(std_len, 4),
            "ai_phrase_density": round(phrase_density, 4),
            "punctuation_entropy": round(punct_entropy, 4),
            "subscores": {k: round(v, 4) for k, v in subscores.items()},
            "token_count": token_count,
            "sentence_count": len(sent_lengths),
        },
    }
