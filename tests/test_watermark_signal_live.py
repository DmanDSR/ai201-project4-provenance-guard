"""
Standalone live test for Signal 3 (KGW watermark z-score).

Run directly — no pytest, no server, no API key needed (pure Python):
    .venv\\Scripts\\python.exe tests/test_watermark_signal_live.py

Runs the detector on the SAME three samples as the Signal 1 / Signal 2 live
tests (so the three signals can be read side by side) plus one *synthetically
watermarked* sample constructed so every token lands in its own green list.

What it demonstrates:
  - Ordinary prose (human, AI-styled, ambiguous) carries no reconstructable
    watermark → green fraction ≈ 0.5 → z ≈ 0 → SILENT. This is the documented
    limitation: absence of a fire is NOT evidence of human origin.
  - The synthetic sample → green fraction ≈ 1.0 → high z → FIRES, proving the
    detector is alive and discriminating.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals.watermark_signal import detect, _is_green, _GREEN_FRACTION

# ---------------------------------------------------------------------------
# Identical samples to tests/test_stylometric_signal_live.py
# ---------------------------------------------------------------------------
SAMPLES = [
    {
        "id": "human_1",
        "note": "human prose",
        "text": (
            "The sun dipped below the horizon, painting the sky in hues of amber "
            "and rose. I sat on the porch, coffee in hand, watching the neighborhood "
            "slowly go quiet."
        ),
    },
    {
        "id": "ai_1",
        "note": "AI-styled prose",
        "text": (
            "It is worth noting that artificial intelligence has the potential to "
            "revolutionize numerous industries. In conclusion, the transformative "
            "impact of AI on society cannot be overstated. It is important to "
            "consider the ethical implications as we move forward in this rapidly "
            "evolving landscape."
        ),
    },
    {
        "id": "ambiguous_1",
        "note": "ambiguous prose",
        "text": (
            "My grandmother used to say the best soup starts with a good argument. "
            "She wasn't wrong. There are several key steps to making an excellent "
            "broth: first, you must source quality ingredients; second, patience "
            "is essential; and third, one should not underestimate the value of "
            "seasoning."
        ),
    },
]

# Word pool used to synthesise a watermarked sample (mirrors the unit test).
_POOL = [
    "the", "of", "and", "to", "a", "in", "that", "is", "was", "he", "for", "it",
    "with", "as", "his", "on", "be", "at", "by", "had", "not", "are", "but", "from",
    "or", "have", "an", "they", "which", "one", "you", "were", "her", "all", "she",
    "there", "would", "their", "we", "him", "been", "has", "when", "who", "will",
    "more", "no", "if", "out", "so", "said", "what", "up", "its", "about", "into",
    "than", "them", "can", "only", "other", "new", "some", "could", "time", "these",
]


def _build_watermarked_text(n_words: int, seed_word: str = "the") -> str:
    words = [seed_word]
    for _ in range(n_words - 1):
        prev = words[-1]
        for cand in _POOL:
            if _is_green(prev, cand):
                words.append(cand)
                break
        else:
            words.append(_POOL[0])
    return " ".join(words)


def _green_fraction(text: str) -> tuple[int, int]:
    """Return (green_count, n_tested) for display alongside the z-score."""
    tokens = text.split()
    green = sum(1 for i in range(1, len(tokens)) if _is_green(tokens[i - 1], tokens[i]))
    return green, max(0, len(tokens) - 1)


def run():
    print("\n" + "=" * 70)
    print("  Watermark Signal — Live Independent Test (KGW z-score)")
    print("=" * 70)

    rows = []
    samples = list(SAMPLES) + [
        {"id": "synthetic_wm", "note": "synthetic watermark", "text": _build_watermarked_text(60)}
    ]

    for s in samples:
        result = detect(s["text"])
        green, n_tested = _green_fraction(s["text"])
        frac = (green / n_tested) if n_tested else 0.0

        print(f"\n[{s['id']}] {s['note']}")
        print(f"  tokens_tested       = {n_tested}")
        print(f"  green_fraction      = {green}/{n_tested} = {frac:.3f}  (chance = {_GREEN_FRACTION})")
        print(f"  z_score             = {result['z_score']:.4f}")
        print(f"  >> fires: {'YES' if result['fires'] else 'no'}")
        rows.append((s["id"], n_tested, frac, result["z_score"], result["fires"]))

    # --- summary table -----------------------------------------------------
    print("\n" + "-" * 70)
    print(f"  {'sample':<16}{'tested':<9}{'green frac':<13}{'z_score':<12}{'fires?'}")
    print("  " + "-" * 66)
    for sid, n_tested, frac, z, fires in rows:
        print(f"  {sid:<16}{n_tested:<9}{frac:<13.3f}{z:<12.4f}{'YES' if fires else 'no'}")
    print("=" * 70)
    print("  Expected: ordinary prose SILENT (frac ~0.5), synthetic FIRES (frac ~1.0)")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    run()
