"""
Score combiner — merges per-signal scores into a single confidence value.

Signal weights (from planning.md Section 1):
    groq_llm     0.50
    stylometric  0.35
    watermark    0.15

If a signal is absent from the scores dict (failed, inactive, or did not
fire), its weight is dropped and the remaining weights are renormalized so
they always sum to 1.0.

The result is always in [0.0, 1.0].
"""

BASE_WEIGHTS: dict[str, float] = {
    "groq_llm": 0.50,
    "stylometric": 0.35,
    "watermark": 0.15,
}


def combine_scores(scores: dict[str, float]) -> float:
    """
    Compute a weighted-average confidence score from active signal scores.

    Args:
        scores: Mapping of signal name → score (0.0–1.0).
                Only signals present in this dict contribute to the result.
                Signals that failed, were inactive, or did not fire must be
                excluded by the caller before passing here.

    Returns:
        A float in [0.0, 1.0].  Returns 0.5 (maximum uncertainty) if no
        active signals are present so the pipeline never crashes.
    """
    active = {name: score for name, score in scores.items() if name in BASE_WEIGHTS}

    if not active:
        # No signals available — return neutral uncertainty score
        return 0.5

    total_weight = sum(BASE_WEIGHTS[name] for name in active)
    weighted_sum = sum(BASE_WEIGHTS[name] * score for name, score in active.items())

    confidence = weighted_sum / total_weight
    # Guard against floating-point drift outside [0, 1]
    return max(0.0, min(1.0, confidence))
