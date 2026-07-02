"""
Standalone live test for Signal 2 (stylometric heuristics).

Run directly — no pytest, no server, no API key needed (pure Python):
    .venv\\Scripts\\python.exe tests/test_stylometric_signal_live.py

Uses the SAME three samples as the Signal 1 live test
(tests/test_groq_signal_live.py) so the two signals can be compared
head-to-head. Prints each feature value, the combined stylometric score, and
a side-by-side agreement table against Signal 1's recorded live results.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals.stylometric_signal import score

# ---------------------------------------------------------------------------
# Identical samples to tests/test_groq_signal_live.py
# ---------------------------------------------------------------------------
SAMPLES = [
    {
        "id": "human_1",
        "expected": "human_written",
        "text": (
            "The sun dipped below the horizon, painting the sky in hues of amber "
            "and rose. I sat on the porch, coffee in hand, watching the neighborhood "
            "slowly go quiet."
        ),
    },
    {
        "id": "ai_1",
        "expected": "ai_generated",
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
        "expected": "uncertain",
        "text": (
            "My grandmother used to say the best soup starts with a good argument. "
            "She wasn't wrong. There are several key steps to making an excellent "
            "broth: first, you must source quality ingredients; second, patience "
            "is essential; and third, one should not underestimate the value of "
            "seasoning."
        ),
    },
]

# Signal 1's recorded live results (from context_2.md "Signal 1 — Live Test").
# Stored as the AI-probability the combiner actually consumes:
#   Groq label=human_written, confidence=0.95  →  ai_prob = 1 - 0.95 = 0.05
SIGNAL_1_AI_PROB = {
    "human_1":     0.05,   # labelled human_written @ 0.95  → 0.05 AI
    "ai_1":        0.05,   # labelled human_written @ 0.95 (MISS)  → 0.05 AI
    "ambiguous_1": 0.05,   # labelled human_written @ 0.95  → 0.05 AI
}


def _direction(ai_prob: float) -> str:
    if ai_prob >= 0.5:
        return "AI-leaning"
    return "human-leaning"


def run():
    print("\n" + "=" * 70)
    print("  Stylometric Signal — Live Independent Test (same inputs as Signal 1)")
    print("=" * 70)

    rows = []
    for s in SAMPLES:
        result = score(s["text"])
        f = result["features"]
        sty = result["score"]
        g = SIGNAL_1_AI_PROB[s["id"]]

        print(f"\n[{s['id']}] expected: {s['expected']}")
        print(f"  tokens={f['token_count']}  sentences={f['sentence_count']}")
        print(f"  ttr                 = {f['ttr']:.3f}   -> sub {f['subscores']['ttr']:.3f}")
        print(f"  mean_sentence_len   = {f['mean_sentence_length']:.2f}  -> sub {f['subscores']['mean_sentence_length']:.3f}")
        print(f"  sentence_len_std    = {f['sentence_length_std']:.2f}  -> sub {f['subscores']['sentence_length_std']:.3f}")
        print(f"  ai_phrase_density   = {f['ai_phrase_density']:.2f}/100w -> sub {f['subscores']['ai_phrase_density']:.3f}")
        print(f"  punctuation_entropy = {f['punctuation_entropy']:.3f}  -> sub {f['subscores']['punctuation_entropy']:.3f}")
        print(f"  >> stylometric score = {sty:.3f}  ({_direction(sty)})")
        print(f"  >> signal 1 (Groq)   = {g:.3f}  ({_direction(g)})")

        agree = _direction(sty) == _direction(g)
        print(f"  >> agreement: {'AGREE' if agree else 'DISAGREE'}")
        rows.append((s["id"], s["expected"], g, sty, agree))

    # --- summary table -----------------------------------------------------
    print("\n" + "-" * 70)
    print(f"  {'sample':<14}{'expected':<15}{'sig1 (AI p)':<13}{'sig2 (AI p)':<13}{'agree?'}")
    print("  " + "-" * 66)
    for sid, exp, g, sty, agree in rows:
        print(f"  {sid:<14}{exp:<15}{g:<13.3f}{sty:<13.3f}{'yes' if agree else 'NO'}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    run()
