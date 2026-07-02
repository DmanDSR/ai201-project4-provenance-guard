# Provenance Guard — Living Development Context

> **Last updated:** Signal 3 (watermark) brought to test parity — dedicated unit-test file + standalone live test added (no production logic changed); full suite now 45/45. Prior: M4 follow-up (scoring recalibration, two test-infra bug fixes, both per-signal scores recorded in the audit log).
> This document is a running record of what has been built, what broke along the way, and what to watch out for as the project moves forward. Update it at the end of each milestone.

---

## Project Summary

Provenance Guard is a Flask backend API that classifies text-based creative content as AI-generated or human-written. It returns a confidence score, a plain-language transparency label, and gives creators an appeals path for contested results. The system is designed to be plugged into any creative-sharing platform.

---

## What Has Been Done

### Milestone 2 — Architecture & Planning

**Completed:**
- `planning.md` created at project root covering all five required spec sections:
  - Detection signals (3 signals, output formats, weights)
  - Uncertainty representation (score bands, threshold logic, asymmetric design rationale)
  - Transparency label design (verbatim text for all 3 variants)
  - Appeals workflow (request/response contract, audit log entry format)
  - Anticipated edge cases (4 specific scenarios)
- `## Architecture` section in `planning.md` with ASCII diagram matching the Mermaid flowchart from the original brief
- `## AI Tool Plan` section with M3/M4/M5 build-and-verify specs
- `.kiro/specs/provenance-guard-planning/requirements.md` created with 7 formal requirements using EARS patterns

**Key design decisions locked in:**
- Three signals: Groq LLM (weight 0.50), stylometric heuristics (weight 0.35), KGW watermark z-score (weight 0.15, additive-only)
- Confidence thresholds: ≥ 0.85 = high-confidence AI, ≤ 0.20 = high-confidence human, in between = uncertain
- False positive protection: threshold deliberately set high (0.85) to accept more false negatives in exchange for fewer false positives against human creators
- Watermark signal is additive-only — a negative result contributes zero, not a penalty

---

### Milestone 3 — Flask Skeleton + Signal 1 (Groq LLM)

**Completed:**

| File | What it does |
|---|---|
| `app.py` | Flask application, `POST /submit` route, rate limiter (10/min per IP), label selector, `GET /log` stub |
| `signals/groq_signal.py` | Signal 1: Groq LLM classifier — structured JSON prompt, response parser, 2 retries with 5s timeout, graceful failure mode |
| `signals/stylometric_signal.py` | Signal 2 stub: returns `{"score": 0.5, "features": {}}` — placeholder so the pipeline runs end-to-end in M3 |
| `signals/watermark_signal.py` | Signal 3: KGW z-score watermark detector — additive-only, fires when z ≥ 4.0 |
| `combiner.py` | Weighted-average score combiner with automatic renormalization when signals are absent/failed |
| `database.py` | SQLite audit log — schema creation, write-on-classify, appeal status update, fetch-log with limit |
| `tests/test_routes.py` | 10 route-level tests (all passing) covering validation, 3 label variants, Groq failure fallback, short-text guard, log endpoint |

**`POST /submit` API contract (as implemented):**

Request body:
```json
{ "content": "string", "creator_id": "string" }
```

> **Note:** The endpoint also accepts `"text"` as a field name alias for `"content"`. Both work. The task brief used `"text"`, so the endpoint was updated to accept either rather than break the example curl command.

Response:
```json
{
  "content_id": "uuid",
  "attribution": "ai_generated | human_written",
  "label": "high_confidence_ai | high_confidence_human | uncertain",
  "confidence": 0.0,
  "transparency_text": "...",
  "groq_reasoning": "one or two sentence explanation from the model",
  "signal_status": { "groq_llm": "ok | failed", "stylometric": "ok | inactive", "watermark": "ok" },
  "metadata": { "low_token_warning": true }
}
```

**Test results:** 10/10 passing

---

### Signal 1 — Live Independent Test

A standalone test script was created at `tests/test_groq_signal_live.py`. It calls `classify()` directly (no Flask server needed) on three sample texts and validates the return shape and confidence range.

**How to run it:**
```
.venv\Scripts\python.exe tests/test_groq_signal_live.py
```

**Sample results from the live run:**

| Sample | Expected | Groq label | Confidence |
|---|---|---|---|
| Human prose (sunset scene) | human_written | human_written ✅ | 0.950 |
| AI-patterned text ("it is worth noting...") | ai_generated | human_written ❌ | 0.950 |
| Ambiguous (grandma soup anecdote) | uncertain | human_written | 0.950 |

The AI-patterned sample being labelled `human_written` is expected — LLM-based detection is an unsolved problem, and this is exactly why the multi-signal approach exists. Signal 2 (stylometric) is designed to catch structural uniformity that the LLM might miss.

---

### Milestone 4 — Signal 2 (Stylometric Heuristics) + Full Confidence Scoring

**Completed:**

| File | What it does |
|---|---|
| `signals/stylometric_signal.py` | Signal 2 real implementation — replaces the M3 stub. Computes all 5 features, normalizes each to a `[0,1]` AI sub-score, combines via a fixed internal weight map. Pure Python, no external calls. |
| `tests/test_stylometric_signal.py` | 16 pytest tests: return-shape, directional correctness (AI > 0.5, human < 0.5), per-feature behaviour, edge cases, **plus scoring-logic verification against planning.md weights and threshold bands**. |
| `tests/test_stylometric_signal_live.py` | Standalone comparison test — runs Signal 2 on the same 3 samples as the Signal 1 live test and prints a side-by-side agreement table. No API key needed (pure Python). |

**Five features + normalization (all output higher = more AI-like, matching the Issue 6 direction convention):**

| Feature | Internal weight | Normalization rationale |
|---|---|---|
| Sentence-length std dev | 0.30 | Monotonic — low variance → AI. Human writing mixes long/short sentences. |
| AI phrase density | 0.30 | Hits per 100 words (length-normalized); saturates at ~1.5/100w. Direct signal. |
| Punctuation entropy | 0.15 | Lower entropy (fewer punctuation types) → AI. Neutral 0.5 if < 3 marks. |
| Mean sentence length | 0.15 | Tent function peaking at 18 words (AI favours consistent, moderate length). |
| Type-Token Ratio (TTR) | 0.10 | Tent peaking at 0.5 ("mid-range uniformity"). Weakest feature by design. |

**Return contract:** `{"score": float, "features": {...raw values + subscores + token/sentence counts}}` — same key (`score`) the M3 combiner wiring already read, so activating it required no change to `app.py`.

**Scoring-logic verification (the "does it silently diverge from spec?" check):**
- Combiner weights confirmed `0.50 / 0.35 / 0.15` (matches planning.md §1).
- Two-signal renormalization confirmed `(0.50·g + 0.35·s) / 0.85` (matches §1).
- Label bands confirmed against `_get_label`, **including inclusive edges**: `0.85 → high_confidence_ai`, `0.8499 → uncertain`, `0.20 → high_confidence_human`, `0.2001 → uncertain` (matches §2).
- **No divergence found** — the implementation matches the specified ranges.

**Test results:** 27/27 passing (16 stylometric + 11 route). The route tests now exercise the real stylometric signal instead of the 0.5 stub and still pass.

---

### Signal 2 — Live Independent Test

Run directly (no server, no API key — pure Python):
```
.venv\Scripts\python.exe tests/test_stylometric_signal_live.py
```

**Comparison on the same 3 samples as Signal 1** (scores shown in AI-probability direction — higher = more AI):

| Sample | Expected | Signal 1 (Groq) | Signal 2 (stylometric) | Agree? |
|---|---|---|---|---|
| human_1 (sunset scene) | human_written | 0.050 | 0.499 | ✅ |
| ai_1 ("it is worth noting…") | ai_generated | 0.050 (**miss**) | **0.758** | ❌ |
| ambiguous_1 (grandma soup) | uncertain | 0.050 | 0.163 | ✅ |

**Where they diverge is the whole point:** Groq *missed* the formulaic AI sample (`ai_1`), labelling it human — the exact failure recorded in the Signal 1 live test. Signal 2 caught it (0.758), driven almost entirely by AI-phrase density (16.3 phrases/100 words → sub-score 1.0). This is the multi-signal thesis working as designed: the LLM misses lexical/structural tells that the cheap stylometric pass catches. On the two samples where Groq was right (human_1, ambiguous_1), the signals agree.

**Two calibration caveats surfaced by the live run (flag in README):**
1. **`human_1` scored 0.499 — right on the fence.** It's a 29-token text, so in the real pipeline the < 50-token short-text guard makes Signal 2 *inactive* anyway. Treat this as a below-threshold edge, not a confident human read.
2. **TTR sub-scored 0.0 on all three samples.** Short passages have naturally high TTR (0.83–0.96), so the tent centered at 0.5 rarely fires for short text. TTR is the weakest, lowest-weighted feature by design — but if the normalization is later calibrated against long-form samples, this is the feature to revisit.

---

### Milestone 4 (follow-up) — Scoring Calibration & Audit Log Enhancement

**Trigger:** A deliberate calibration test of the scoring function on 4 hand-chosen inputs — one clearly AI, one clearly human, and two borderline (formal human, lightly-edited AI). Each signal was printed separately so a miscalibration could be isolated to a specific signal rather than the combined number.

**What the test found:** the combined scorer confidently *mislabelled the clearly-AI input as `high_confidence_human`* (0.05). Printing both signals isolated two compounding causes:

1. **Groq labelled all 4 inputs `human_written` @0.95** — including the blatantly-AI one. Its unchecked 0.95 (→ AI-probability 0.05) dominated because it carries the largest weight, and the *full* weight whenever it is the only active signal.
2. **The `< 50`-token short-text guard disabled the stylometric signal on 3 of the 4 inputs** (they were 39–43 tokens) — including the clearly-AI one, which stylometric alone scored correctly (0.649). The one signal that caught the AI text was switched off, leaving Groq to decide alone.

**Three fixes applied (all documented inline + in `planning.md`):**

| # | Fix | File | Rationale |
|---|---|---|---|
| 1 | Short-text guard `50 → 30` tokens (`SHORT_TEXT_TOKEN_THRESHOLD`) | `app.py` | Most real paragraphs run 30–60 tokens; 50 disabled stylometric on the majority of realistic inputs. 30 still excludes haiku-length fragments (~17 tokens) but keeps stylometric active on ordinary paragraphs so it can counterbalance a wrong Groq verdict. |
| 2 | Cap Groq confidence at `0.85` (`GROQ_CONFIDENCE_CAP`) | `signals/groq_signal.py` | `llama-3.1-8b-instant` is an over-confident weak detector. The cap lets it *reach* but not *exceed* the high-confidence boundary alone, so a second signal is always needed to move a verdict decisively. |
| 3 | Re-centre TTR tent `peak 0.50 → 0.75` (half-width 0.24) | `signals/stylometric_signal.py` | The old peak sat far below the TTR of all realistic short text (~0.85–0.90), collapsing the sub-score to 0.0 on every input — dead weight that only ever biased toward "human." Now reads ≈ neutral 0.5 for ordinary prose. |

**Before → after (combined confidence, full pipeline with live Groq):**

| Input | Before | After |
|---|---|---|
| Clearly AI | **0.05 → high_confidence_human** ❌ | 0.374 → uncertain ✅ |
| Clearly human | 0.134 → high_confidence_human | 0.213 → uncertain |
| Formal human | 0.05 → high_confidence_human | 0.243 → uncertain |
| Lightly-edited AI | 0.05 → high_confidence_human | 0.224 → uncertain |

Scores are now correctly **ordered** by AI-likelihood (AI > formal-human > edited-AI > human) and the clearly-AI text is no longer confidently mislabelled. Because Groq blanket-calls everything "human," the honest ceiling for these inputs is "uncertain" — the old `high_confidence_human` on the AI sample was false confidence in the wrong direction.

**Trade-off (flagged, not blocking):** the cap symmetrically weakens Groq's *correct* confident-human reads too, so the clearly-human sample slipped from `high_confidence_human` to `uncertain` (0.213, just over the 0.20 line). Harmless to creators (uncertain is not an AI flag). If a confident-human badge is wanted back, raising `GROQ_CONFIDENCE_CAP` to ~0.88 restores it while still fixing the AI case — a one-line knob.

**Audit log enhancement:** both per-signal scores are now stored as first-class columns alongside the combined `confidence`, mirroring the existing `llm_score`:

- New column `stylometric_score REAL` in the `classifications` table (with in-place migration for the existing `provenance_guard.db` — verified against a copy of the real DB; legacy rows read `None`).
- `log_classification()` gained a `stylometric_score` param; `app.py` passes `signal_scores.get("stylometric")`.
- `fetch_log()` / `GET /log` now return `stylometric_score` in every entry.
- Both fields are nullable, consistently: `llm_score` is `None` when Groq fails, `stylometric_score` is `None` when the signal is inactive (< 30 tokens). The watermark score remains in `signals_json`.

**Test results:** 33/33 passing (was 27). New regression tests lock in the 30-token guard, the 0.85 Groq cap, the non-zero TTR sub-score, and that the log captures both signal scores (with combined confidence between them).

---

### Signal 3 — Test Coverage to Parity + Live Independent Test

**Trigger:** Signal 3 (KGW watermark) had shipped as working production code in M3 and was wired into the pipeline, but it was the only signal that never received a dedicated unit-test file or a standalone live test — the pattern established for Signals 1 and 2. This step closes that gap. **No production logic changed** (`watermark_signal.py`, `app.py`, `combiner.py` untouched); this is test + verification work only.

**Completed:**

| File | What it does |
|---|---|
| `tests/test_watermark_signal.py` | 12 pytest tests: return-shape, fires-on-watermark / silent-on-prose, the `z ≥ 4.0` threshold boundary (exactly-at vs. just-below), edge cases (empty, single word, determinism), and additive-only combiner wiring vs. planning.md (weight 0.15, firing raises the combined score). |
| `tests/test_watermark_signal_live.py` | Standalone comparison test — runs `detect()` on the same 3 samples as the Signal 1/2 live tests plus a synthetically-watermarked sample, printing green-fraction, z-score, and fire status. No API key needed (pure Python). |

**Key testing insight — how to make a hash-based detector deterministically fire:** the green/red partition is a deterministic hash, `_is_green(prev, curr) = sha256("42:{prev}:{curr}")[0] < 128`. So a *guaranteed-watermarked* text can be constructed by greedily picking each next word from a pool such that it lands in its own green list. Every token is then green by construction, so `green_count == n_tested` and the z-score reduces to a clean closed form:

```
z = (n_tested·0.5) / sqrt(n_tested·0.25) = sqrt(n_tested)
```

This makes the threshold tests exact rather than probabilistic: 17 words → 16 tested → z = 8/√4 = **4.0 exactly** (fires, inclusive `>=`); 16 words → 15 tested → z ≈ **3.873** (does not fire).

**Live run results:**

| Sample | tokens tested | green fraction | z-score | fires? |
|---|---|---|---|---|
| human_1 (sunset prose) | 28 | 0.500 | 0.0000 | no |
| ai_1 (AI-styled prose) | 42 | 0.452 | −0.6172 | no |
| ambiguous_1 (grandma soup) | 45 | 0.511 | 0.1491 | no |
| **synthetic_wm** (constructed) | 59 | **1.000** | **7.6811** | **YES** |

The three real samples sit right at the ~0.5 chance rate — confirming the documented limitation (Watch-Out #4): ordinary prose, whether human- or AI-styled, carries no *reconstructable* watermark, so the detector correctly stays silent. Absence of a fire is not evidence of human origin. The synthetic sample proves the detector is alive and fires decisively when a watermark trace is genuinely present.

**How to run it:**
```
.venv\Scripts\python.exe tests/test_watermark_signal_live.py
```

**Test results:** full suite **45/45 passing** (was 33; +12 watermark unit tests). All three signals now have parity: dedicated unit coverage + a standalone live verification test.

---

## Issues Encountered

### Issue 1 — pytest not in venv

**What happened:** `pytest` was not installed in the `.venv`. Running `.venv\Scripts\python.exe -m pytest` failed with "No module named pytest."

**Fix:** `pip install pytest` into the venv. Not in `requirements.txt` yet.

**Action needed:** Add `pytest` to `requirements.txt` or a separate `requirements-dev.txt` before M4.

---

### Issue 2 — Mock patch target mismatch

**What happened:** Tests initially patched `signals.groq_signal.classify`, but `app.py` imports it as `from signals.groq_signal import classify as groq_classify`. That binding is resolved at import time, so patching the original module location had no effect — the real Groq API was being called despite the mock.

**Fix:** Changed all test patches to target `app.groq_classify` (the name as bound in the module where it's used).

**Rule to remember:** When you mock a function imported with `from module import func`, patch it at the import destination (`app.func`), not the source (`module.func`).

---

### Issue 3 — Combiner math invalidated label tests

**What happened:** Two tests (`test_high_confidence_ai_label` and `test_human_label`) were asserting labels based on the Groq signal confidence alone (e.g., Groq = 0.95 → expected `high_confidence_ai`). But the combiner mixes in the stylometric stub (fixed at 0.5), so even a Groq score of 0.95 produces a combined score of ~0.765, which lands in the `uncertain` band.

**Math:**
- Groq (0.95) weight renormalized to 0.588 (0.50 / 0.85), stylometric (0.50) weight 0.412 (0.35 / 0.85)
- Combined = 0.95 × 0.588 + 0.50 × 0.412 = 0.765 → uncertain

**Fix:** Changed the label-band tests to use short text (< 50 tokens), which disables the stylometric signal and lets Groq drive the score 1:1.

**Action needed:** Once the real stylometric signal is implemented in M4, update these tests to use long text with known AI/human feature profiles. The short-text workaround is only valid for M3.

---

### Issue 4 — Groq client instantiation timing

**What happened:** The Groq client uses lazy initialization (`_client = None`; `_get_client()` on first call). However, `app.py` calls `init_db()` inside `with app.app_context()` at module load time. If `GROQ_API_KEY` is not set when the module is imported, the first actual Groq call will raise `EnvironmentError` rather than a clean signal failure.

**Current state:** This is handled — the `classify()` function wraps the client call in a try/except and returns `{"status": "failed"}` if any exception propagates. But the test fixture sets `GROQ_API_KEY=test-key` via `monkeypatch.setenv`, which only applies for the duration of the test, and the module is imported once at session start.

**Potential problem:** If a real deployment runs without `GROQ_API_KEY` set, the first call to `/submit` will catch the `EnvironmentError` inside the retry loop and mark Signal 1 as failed rather than crashing — which is the intended behavior. But the error message in the audit log will say "unknown error" unless the `EnvironmentError` is specifically caught. **Worth patching before production.**

---

### Issue 5 — Groq model `llama3-8b-8192` decommissioned

**What happened:** The live signal test failed immediately with HTTP 400: `"The model llama3-8b-8192 has been decommissioned and is no longer supported."` Groq retired this model after the project was originally scoped.

**Fix:** Updated `groq_signal.py` to use `llama-3.1-8b-instant`, which is the current production replacement — same parameter class, same speed tier, free-tier available.

**Rule to remember:** Groq model names change. If you see a 400 with `model_decommissioned` in the error body, check [console.groq.com/docs/deprecations](https://console.groq.com/docs/deprecations) for the current recommended replacement. The model name is a single string in `groq_signal.py` — easy to update.

---

### Issue 6 — Groq confidence direction bug (critical)

**What happened:** The first live endpoint call returned `attribution: "human_written"` but `label: "high_confidence_ai"`. The `attribution` and `label` were contradicting each other.

**Root cause:** Groq's `confidence` field means "how confident I am in **my own label**" — not "how likely is this to be AI-generated." So when Groq returns `{label: "human_written", confidence: 0.95}`, the raw `0.95` was being fed directly into the score combiner. The combiner sees 0.95 and maps it above the 0.85 threshold → `high_confidence_ai`. The number was being interpreted in the wrong direction.

**Fix:** Added `label_to_ai_probability(label, confidence)` to `groq_signal.py`. The rule:
- `label="ai_generated"`, confidence=0.95 → AI probability = **0.95** (already correct direction)
- `label="human_written"`, confidence=0.95 → AI probability = **0.05** (invert: 1.0 − 0.95)

`app.py` now calls this function before passing the score to the combiner.

**Why this matters for M4:** The stylometric signal also needs to return a score in the same direction — higher = more AI-like. Double-check the stylometric feature normalization is consistent with this convention when implementing M4.

---

### Issue 7 — `curl` in PowerShell is an alias for `Invoke-WebRequest`, not real curl

**What happened:** Running the task's curl command in PowerShell failed with:
```
Cannot bind parameter 'Headers'. Cannot convert the "Content-Type: application/json" value of type "System.String" to type "System.Collections.IDictionary".
```

PowerShell has a built-in alias `curl` that maps to `Invoke-WebRequest`, which has completely different syntax from real curl. The `-H` flag doesn't work; headers must be passed as a hashtable.

**Fix (two options):**

Option 1 — Use `curl.exe` explicitly (real curl, if installed):
```powershell
curl.exe -s -X POST http://localhost:5000/submit `
  -H "Content-Type: application/json" `
  -d '{\"text\": \"...\", \"creator_id\": \"test-user-1\"}'
```

Option 2 — Use `Invoke-RestMethod` (native PowerShell, recommended for this project):
```powershell
$body = '{"text": "...", "creator_id": "test-user-1"}'
Invoke-RestMethod -Method POST -Uri http://localhost:5000/submit `
  -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 5
```

**Rule to remember:** On Windows with PowerShell, always use `Invoke-RestMethod` or explicitly call `curl.exe`. Never just `curl` — it will silently use the wrong tool.

---

### Issue 8 — Audit log was not test-isolated; tests wrote to the real DB

**What happened:** `database.py` read the DB path once at import time (`DB_PATH = os.environ.get("DB_PATH", ...)`). The test fixtures set `DB_PATH` via `monkeypatch.setenv` *after* the module was already imported, so the override was silently ignored — every test wrote into the real `provenance_guard.db`. The log tests only appeared to pass because that shared DB always held leftover rows from prior runs; they never actually verified their own write.

**Surfaced by:** the audit-log/calibration work. Once the path was resolved correctly per-test, the log tests started reading empty databases and failing — exposing the latent bug.

**Fix:** resolve the path lazily via `_db_path()` on every connect, so `monkeypatch.setenv` takes effect and each test gets an isolated temp DB. Side benefit: the test suite no longer pollutes the production `provenance_guard.db`.

**Rule to remember:** read environment-driven config at *call time*, not module-import time, or test monkeypatching (and any runtime env change) will be silently ignored.

---

### Issue 9 — Rate limiter state leaks across tests (10/min shared counter)

**What happened:** With DB isolation fixed (Issue 8), two `/log` tests still failed with *empty* logs. Root cause: Flask-Limiter uses a module-level in-memory counter (`storage_uri="memory://"`) that is **not reset between tests**. All tests hit `/submit` from the same client IP, so once the suite crossed 10 submits, later submits returned HTTP 429 → no classification → nothing logged. The old shared DB had masked this too.

**Fix:** disable the limiter in the `client` test fixture (`app_module.limiter.enabled = False`). There is no dedicated rate-limit test, so this is safe; functional tests should not be subject to the production rate cap.

**Rule to remember:** global/singleton middleware state (rate limiters, caches, connection pools) persists across tests in the same process. Reset or disable it in the fixture.

---

## What to Watch Out For Going Into M4

### 1. The stylometric stub is masking confidence extremes

Any confidence score produced in M3 for long text (> 50 tokens) will land in the uncertain band because the 0.5 stub drags every score toward the center. This is correct for M3 but means you cannot test the full label range until M4 replaces the stub.

**When you replace the stub:** update the label-band tests to use realistic long-text samples (clearly AI-written, clearly human-written) instead of the short-text workaround.

### 2. Stylometric feature normalization needs calibration

The stylometric signal (M4) needs to normalize each of its five features (TTR, mean sentence length, std dev, AI phrase density, punctuation entropy) onto a `[0, 1]` scale before combining them. The normalization bounds (e.g., "what TTR counts as maximally AI-like?") are design decisions that will need testing against real AI and human samples. If the normalization is off, the stylometric score could systematically push the combined score in the wrong direction.

**Recommendation:** Run `stylometric.score()` on at least one known AI paragraph and one known human paragraph before wiring it into the endpoint, and verify the scores are directionally correct (AI > 0.5, human < 0.5).

**Critical direction convention:** All signal scores must follow the same direction — higher = more AI-like. Groq's raw confidence was backwards (Issue 6) and had to be inverted. Make sure stylometric follows the same convention before wiring in.

### 3. Short-text guard sets stylometric to inactive — but long-text threshold is arbitrary

The current short-text guard zeroes out stylometric when token count < 50. That number was chosen from the spec but hasn't been tested. A haiku has ~17 tokens; a short paragraph might have 60–80. If a 45-token text that's clearly AI-generated gets stylometric disabled, the system relies solely on Groq — which may still get it right, but with lower total evidence weight.

**Not a blocking issue, but worth noting in the README when documenting confidence scoring.**

### 4. Watermark signal uses a simplified token-partition scheme

The KGW watermark detector in `watermark_signal.py` reconstructs green-list membership using a SHA-256 hash of `(seed, prev_token, curr_token)`. This is a reasonable approximation of the KGW framework, but real watermarked text uses a more complex key schedule. The practical implication: this detector will reliably fire on *synthetically watermarked* text (if you test it that way), but it is unlikely to detect real watermarks from production LLMs, which use proprietary seed schedules.

**This is fine for the project** — the signal is documented as additive-only and a known-limitation case (edge case 4 in `planning.md`) already covers it. Just don't rely on a negative result from this signal as meaningful evidence.

### 5. Rate limiter uses in-memory storage

`app.py` configures Flask-Limiter with `storage_uri="memory://"`. This means rate limit counters reset every time the server restarts and are not shared across multiple worker processes. For a single-process development server this is fine. For any multi-worker deployment (gunicorn with multiple workers, etc.) it would need to switch to Redis or another shared store.

**Not a problem now; flag it in the README as a known limitation.**

### 6. `GET /log` is wired but not rate-limited

The `GET /log` endpoint is implemented and functional, but it has no rate limit. A caller could hammer it to enumerate the full audit log. For the project scope this is fine, but worth noting if this ever becomes a real deployment.

### 7. SQLite write-before-response ordering

`database.py` writes the audit log entry before `app.py` returns the HTTP response. This is intentional (per requirements spec: "The System SHALL write the Audit_Log entry before returning the classification response"). However, if the write fails, the code currently catches the exception, logs it internally, and continues. The classification response is still returned — which is the right behavior — but there is no retry or alerting. If the DB file becomes unwritable (disk full, permissions issue), failures will be silently swallowed.

---

## What Remains

| Milestone | Remaining work |
|---|---|
| ~~**M4**~~ | ~~Replace stylometric stub with real implementation~~ ✅ done — 5 features, normalization, tests |
| ~~**M4**~~ | ~~Verify combined scores are directionally correct on real text samples~~ ✅ done — live comparison confirms Signal 2 catches the AI sample Groq missed |
| ~~**M4 (follow-up)**~~ | ~~Recalibrate scoring against deliberate test inputs~~ ✅ done — TTR tent re-centred (0.50 → 0.75), short-text guard 50 → 30, Groq confidence capped at 0.85. See "M4 (follow-up)" section above |
| ~~**M4 (follow-up)**~~ | ~~Capture both per-signal scores in the audit log~~ ✅ done — `stylometric_score` column added alongside `llm_score`; both surfaced in `GET /log` |
| **M4 (follow-up)** | Optional: recalibrate mean-sentence-length normalization against long-form samples (still a tent centred at 18 words; fine for now). The primary discriminating power at short lengths remains the AI-phrase keyword list — a fragile mechanism worth strengthening if longer samples are added |
| **Any** | Consider giving the watermark signal its own audit column too (currently only in `signals_json`) if it becomes more than additive-only |
| ~~**M5**~~ | ~~`POST /appeals/{content_id}` endpoint (validation, 404/409, audit log update, status flip)~~ ✅ done — `appeals.py` blueprint, 404/409/422 handling, status flip `classified → under_review`, appeal logged with timestamp |
| ~~**M5**~~ | ~~Label selector as a pure function~~ ✅ done — extracted to `labels.py` (`get_label(confidence) -> {label, transparency_text}`); `app._get_label` is now a thin shim over it (single source of truth for §3 text) |
| **M5** | Full `GET /log` (already stubbed — confirm pagination works correctly) |
| ~~**M5**~~ | ~~Integration test: submit → check verbatim label text → appeal → verify status in DB~~ ✅ done — `tests/test_appeals.py` (submit → appeal → status flip + audit-log verification) and `tests/test_labels.py` (three variants verbatim vs. §3) |
| **Any** | `README.md` needs all required grader evidence: verbatim label variants, rate limit config + rationale, audit log sample (≥ 3 entries), appeal handling description |
| **Any** | Add `pytest` to `requirements.txt` or a `requirements-dev.txt` |

---

## File Map (current state)

```
project root/
├── app.py                        ✅ + SHORT_TEXT_TOKEN_THRESHOLD=30, logs both signal scores (M3 + M4 follow-up);
│                                     registers appeals blueprint, _get_label now delegates to labels.py (M5)
├── labels.py                     ✅ M5 — pure label selector get_label(confidence) -> {label, transparency_text}, verbatim §3 text
├── appeals.py                    ✅ M5 — POST /appeals/{content_id} blueprint (404/409/422, status flip, appeal logged)
├── combiner.py                   ✅ implemented (M3)
├── database.py                   ✅ lazy DB path + stylometric_score column w/ migration (M3 + M4 follow-up)
├── planning.md                   ✅ complete, calibration notes added (M2 + M4 follow-up)
├── context_2.md                  ✅ this file
├── Context.md                    📄 original project brief
├── README.md                     ⚠️  nearly empty — needs grader evidence
├── requirements.txt              ⚠️  missing pytest
├── signals/
│   ├── __init__.py               ✅
│   ├── groq_signal.py            ✅ Signal 1, live-tested, llama-3.1-8b-instant,
│   │                                  confidence direction fix + GROQ_CONFIDENCE_CAP=0.85 (M3 + M4 follow-up)
│   ├── stylometric_signal.py     ✅ Signal 2, real 5-feature impl, TTR tent re-centred to 0.75 (M4 + follow-up)
│   └── watermark_signal.py       ✅ Signal 3, implemented (M3)
└── tests/
    ├── test_routes.py                  ✅ passing — real stylometric signal, limiter disabled in fixture,
    │                                       cap/guard/both-signal-log regression tests (M3 + M4 follow-up)
    ├── test_groq_signal_live.py        ✅ standalone Signal 1 live test, run directly with python (M3+)
    ├── test_stylometric_signal.py      ✅ Signal 2 units + scoring-vs-spec + TTR-nonzero regression (M4 + follow-up)
    ├── test_stylometric_signal_live.py ✅ standalone Signal 2 live test + Signal 1 comparison (M4)
    ├── test_watermark_signal.py        ✅ Signal 3 units — fires/silent, z>=4.0 boundary, additive-only wiring (Signal 3 parity)
    ├── test_watermark_signal_live.py   ✅ standalone Signal 3 live test — synthetic-watermark fire vs. silent prose (Signal 3 parity)
    ├── test_labels.py                  ✅ M5 — three variants verbatim vs. §3, inclusive threshold edges, return shape
    └── test_appeals.py                 ✅ M5 — 200/404/409/422, status flip to under_review, appeal logged (creator/reason/timestamp)
```

Full suite: **59/59 passing** (was 45; +5 labels, +9 appeals).

Legend: ✅ done &nbsp;|&nbsp; 🔲 stub/placeholder &nbsp;|&nbsp; ⚠️ action needed &nbsp;|&nbsp; 📄 reference only

---

## Saved Test Data

Keep these IDs handy for testing appeals in M5.

| content_id | submitted text (truncated) | label returned |
|---|---|---|
| `ccb7e641-748a-415a-927d-54c373723abf` | "The sun dipped below the horizon..." | high_confidence_human |

To test the appeals endpoint in M5:
```powershell
$body = '{"creator_id": "test-user-1", "reason": "I wrote this myself on my porch."}'
Invoke-RestMethod -Method POST -Uri http://localhost:5000/appeals/ccb7e641-748a-415a-927d-54c373723abf `
  -ContentType "application/json" -Body $body | ConvertTo-Json
```
