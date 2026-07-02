"""
Signal 3 — Watermark Z-Score (KGW Framework)

Passive detection of statistically unlikely concentrations of "green list"
tokens — the trace left by KGW-style watermarking embedded by some LLMs.

Algorithm (simplified KGW):
  1. For each token, use a seeded hash of the *previous* token to split the
     vocabulary into a "green list" (~50 % of vocab) and a "red list".
  2. Count the number of tokens that fall into their green list.
  3. Compute a one-tailed z-score under the null hypothesis that tokens are
     assigned green/red at random (p = 0.5 per token).

Threshold: z-score ≥ 4.0 is treated as evidence of LLM generation.

Additive-only: if the signal does not fire (z < 4.0) it contributes 0 to
the score combiner — absence of a watermark is NOT evidence of human origin.

Return contract (from planning.md Section 1):
    {
        "z_score": float,
        "fires":   bool        — True when z_score >= 4.0
    }
"""

import hashlib
import logging
import math

logger = logging.getLogger(__name__)

# Shared seed used to partition the vocabulary (pseudo-random, not secret)
_SEED: int = 42
# Fraction of vocabulary assigned to the green list
_GREEN_FRACTION: float = 0.5
# Threshold above which the signal fires
_Z_THRESHOLD: float = 4.0


def detect(text: str) -> dict:
    """
    Run the KGW watermark detector on the submitted text.

    Args:
        text: The text to test.

    Returns:
        {"z_score": float, "fires": bool}
    """
    tokens = text.split()
    n = len(tokens)

    if n < 2:
        # Too short to compute a meaningful z-score
        return {"z_score": 0.0, "fires": False}

    green_count = 0
    for i in range(1, n):
        prev_token = tokens[i - 1]
        curr_token = tokens[i]
        if _is_green(prev_token, curr_token):
            green_count += 1

    # Tokens tested = n - 1 (we start at index 1)
    n_tested = n - 1
    expected = n_tested * _GREEN_FRACTION
    std_dev = math.sqrt(n_tested * _GREEN_FRACTION * (1 - _GREEN_FRACTION))

    if std_dev == 0:
        return {"z_score": 0.0, "fires": False}

    z_score = (green_count - expected) / std_dev
    fires = z_score >= _Z_THRESHOLD

    logger.debug(
        "watermark_signal: n=%d green=%d z=%.3f fires=%s",
        n_tested, green_count, z_score, fires,
    )

    return {"z_score": round(z_score, 4), "fires": fires}


def _is_green(prev_token: str, curr_token: str) -> bool:
    """
    Determine whether curr_token falls in the green list given prev_token.

    Uses a deterministic hash of (seed, prev_token, curr_token) to simulate
    the pseudo-random partition a watermarking LLM would have used.
    """
    key = f"{_SEED}:{prev_token}:{curr_token}".encode()
    digest = hashlib.sha256(key).digest()
    # Use the first byte to decide green (< 128) vs red (>= 128)
    return digest[0] < int(256 * _GREEN_FRACTION)
