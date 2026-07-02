"""
Standalone live test for Signal 1 (Groq LLM classification).

Run directly — no pytest needed, no server needed:
    python tests/test_groq_signal_live.py

Requires GROQ_API_KEY to be set in .env or the environment.
Tests the classify() function independently before it's wired into the endpoint.
"""

import sys
import os
import json

# Make sure project root is on path regardless of where the script is run from
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from signals.groq_signal import classify

# ---------------------------------------------------------------------------
# Test samples
# ---------------------------------------------------------------------------
SAMPLES = [
    {
        "id": "human_1",
        "label": "expected: human_written",
        "text": (
            "The sun dipped below the horizon, painting the sky in hues of amber "
            "and rose. I sat on the porch, coffee in hand, watching the neighborhood "
            "slowly go quiet."
        ),
    },
    {
        "id": "ai_1",
        "label": "expected: ai_generated",
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
        "label": "expected: uncertain (mixed signals)",
        "text": (
            "My grandmother used to say the best soup starts with a good argument. "
            "She wasn't wrong. There are several key steps to making an excellent "
            "broth: first, you must source quality ingredients; second, patience "
            "is essential; and third, one should not underestimate the value of "
            "seasoning."
        ),
    },
]

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run():
    passed = 0
    failed = 0

    print("\n" + "=" * 60)
    print("  Groq Signal — Live Independent Test")
    print("=" * 60)

    for sample in SAMPLES:
        print(f"\n[{sample['id']}] {sample['label']}")
        print(f"  Text snippet: {sample['text'][:80]}...")

        result = classify(sample["text"])

        if result["status"] == "ok":
            print(f"  ✅ Status:     ok")
            print(f"  Label:        {result['label']}")
            print(f"  Confidence:   {result['confidence']:.3f}")
            print(f"  Reasoning:    {result['reasoning']}")
            passed += 1
        else:
            print(f"  ❌ Status:     failed")
            print(f"  Error:        {result.get('error', 'unknown')}")
            failed += 1

    print("\n" + "-" * 60)
    print(f"  Results: {passed} ok, {failed} failed out of {len(SAMPLES)} samples")

    # Structural validation — check the dict shape regardless of content
    print("\n  Validating return structure...")
    test_result = classify("Hello world.")
    required_keys_on_ok = {"status", "label", "confidence", "reasoning"}
    required_keys_on_fail = {"status", "error"}

    if test_result["status"] == "ok":
        missing = required_keys_on_ok - test_result.keys()
        if missing:
            print(f"  ❌ Missing keys on ok result: {missing}")
        else:
            print(f"  ✅ Return shape is correct for ok result")
    else:
        missing = required_keys_on_fail - test_result.keys()
        if missing:
            print(f"  ❌ Missing keys on failed result: {missing}")
        else:
            print(f"  ✅ Return shape is correct for failed result")

    # Confidence range check
    if test_result["status"] == "ok":
        c = test_result["confidence"]
        if 0.0 <= c <= 1.0:
            print(f"  ✅ Confidence {c:.3f} is in valid range [0.0, 1.0]")
        else:
            print(f"  ❌ Confidence {c} is OUT OF RANGE")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    run()
