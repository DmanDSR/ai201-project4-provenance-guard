# Provenance Guard

A backend API that classifies text-based creative content as **AI-generated** or
**human-written**, returns a calibrated **confidence score**, surfaces a
plain-language **transparency label** for readers, and gives creators a
structured **appeals** path when they believe a verdict is wrong. It is designed
to be plugged into any creative-sharing platform (writing communities, poetry
apps, story forums) so the platform gets attribution transparency without
building detection infrastructure itself.

Perfect AI detection is an unsolved problem, and this system does not pretend
otherwise. Its job is to **acknowledge uncertainty honestly** and to **protect
creators from wrong accusations** — not to hand down binary verdicts it cannot
justify.

---

## Table of contents

- [Setup & run](#setup--run)
- [API reference](#api-reference)
  - [POST /submit](#post-submit--content-submission-endpoint)
  - [POST /appeals/{content_id}](#post-appealscontent_id--appeals-workflow)
  - [GET /log](#get-log--audit-log)
- [Detection signals (why these signals)](#detection-signals)
- [Confidence scoring & uncertainty (why this approach)](#confidence-scoring--uncertainty)
- [Transparency label (verbatim variants)](#transparency-label)
- [Appeals workflow](#appeals-workflow)
- [Rate limiting](#rate-limiting)
- [Audit log](#audit-log)
- [Testing](#testing)
- [Known limitations & what I'd change for a real deployment](#known-limitations--what-id-change-for-a-real-deployment)

---

## Setup & run

```bash
# 1. Install dependencies (a virtualenv is recommended; includes pytest for the test suite)
pip install -r requirements.txt

# 2. Provide a Groq API key for Signal 1 (the LLM classifier)
cp .env.example .env
#   then edit .env:  GROQ_API_KEY=your_key_here

# 3. Run the API (defaults to http://localhost:5000)
python app.py
```

The audit log is a SQLite file (`provenance_guard.db`) created automatically on
first run. **Signal 1 fails gracefully without a key** — the pipeline falls back
to the remaining signals rather than crashing — but for a representative
classification you want the Groq key set.

> **Windows / PowerShell note:** `curl` in PowerShell is an alias for
> `Invoke-WebRequest` and does **not** accept `-H`/`-d`. Use `curl.exe`
> explicitly, or `Invoke-RestMethod`. Examples below use POSIX `curl` syntax
> (Git Bash / macOS / Linux).

---

## API reference

| Method & path | Purpose |
|---|---|
| `POST /submit` | Classify a piece of text; returns attribution, confidence, and the transparency label. |
| `POST /appeals/{content_id}` | File an appeal against a classification (path-param form). |
| `POST /appeal` | File an appeal with `content_id` in the JSON body (convenience form). |
| `GET /log?limit=N` | Read the most recent audit-log entries, newest first, as JSON. |

### `POST /submit` — Content Submission Endpoint

Accepts a text-based submission and returns the full attribution result in one
structured response: the label, the confidence score, and the **verbatim
transparency text** that would be shown to a reader.

**Request body (JSON):**

```json
{ "content": "The text to classify …", "creator_id": "creator-123" }
```

`"text"` is accepted as an alias for `"content"` (the task brief's example curl
used `text`), so either field name works.

**Response (`200`):**

```json
{
  "content_id": "a77d7220-e0aa-4410-9fbc-05c5820f7e9a",
  "attribution": "human_written",
  "label": "uncertain",
  "confidence": 0.41,
  "transparency_text": "Our system could not confidently determine the origin of this content. This work has not been flagged as AI-generated.",
  "signal_status": { "groq_llm": "ok", "stylometric": "ok", "watermark": "ok" },
  "groq_reasoning": "The prose has natural rhythm and idiosyncratic phrasing.",
  "metadata": {}
}
```

- `attribution` is the **raw Signal 1 (Groq) verdict** (`ai_generated` /
  `human_written`); `label` is the **final combined** band. They can differ —
  that difference is the multi-signal design working, not a bug.
- `confidence` is the combined AI-probability in `[0.0, 1.0]` (higher = more
  AI-like).
- `signal_status` reports each signal's outcome: `ok` / `failed` / `inactive`.
- `metadata.low_token_warning: true` appears for very short inputs (see
  [Edge cases](#edge-cases)).

**Validation:** missing/empty `content` or `creator_id`, or a non-JSON body,
returns `422` with a JSON `error`. Exceeding the rate limit returns `429`
([details](#rate-limiting)).

**Example:**

```bash
curl -s -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"text": "The sun dipped below the horizon, spilling amber across the water.", "creator_id": "creator-1"}'
```

---

## Detection signals

> *This section explains the reasoning — why each signal exists and why the
> combination is more informative than any one alone — not just the mechanics.*

The pipeline uses **three independent signals**. Two are required
(`groq_llm`, `stylometric`); the third (`watermark`) is a stretch **ensemble**
addition that contributes only when it fires. The design principle is
**independence**: each signal measures a genuinely different property of the
text, so their errors are uncorrelated. When two independent signals agree, that
agreement is real evidence; when they disagree, the honest output is *uncertain*
— which is exactly what the scoring is built to express.

### Signal 1 — Groq LLM classification (weight 0.50)

**What it measures:** semantic and stylistic coherence, assessed *holistically*.
The model reads the whole passage and judges whether the word choice, structure,
and "voice" read as machine- or human-authored. It returns structured JSON
(`label`, `confidence`, `reasoning`) via a JSON-only prompt to
`llama-3.1-8b-instant`, with 2 retries and a 5 s per-attempt timeout.

**Why include it:** it is the only signal that understands *meaning*. Stylometry
can't tell that an argument is generic or that an anecdote rings hollow; a
language model can. It is the highest-information single signal, which is why it
carries the largest weight.

**Why it is capped, and why it is not trusted alone.** Live testing exposed a
real weakness: `llama-3.1-8b-instant` is an **over-confident, weak detector** —
it returned `0.95` confidence on nearly every sample, *including text it labelled
wrong* (it repeatedly called blatantly-formulaic AI prose "human"). Two fixes
followed, both deliberate:

1. **Direction fix.** Groq's `confidence` means "how sure I am of *my own
   label*," not "how likely this is AI." A raw `{label: human_written,
   confidence: 0.95}` was being read straight into the combiner as `0.95` →
   *high-confidence AI*, the opposite of the truth. `label_to_ai_probability()`
   now inverts the score for a human label (`1 − 0.95 = 0.05`).
2. **Confidence cap at `0.85` (`GROQ_CONFIDENCE_CAP`).** Because Groq carries the
   largest weight — and the *full* weight when it is the only active signal — an
   unchecked `0.95` let one confidently-wrong LLM verdict dominate. Capping at
   `0.85` lets Groq *reach* the high-confidence boundary but never push *past* it
   alone, so a second signal is always required to move a verdict to an extreme.

### Signal 2 — Stylometric heuristics (weight 0.35)

**What it measures:** statistical surface properties that differ systematically
between AI and human writing. AI text tends to be uniform; human writing is
variable and idiosyncratic. Computed in **pure Python, no external calls** — it's
free, deterministic, and can't time out. Five features, each normalised to an
AI sub-score in `[0,1]` then weighted internally:

| Feature | Internal weight | What it captures / normalisation |
|---|---|---|
| **Sentence-length std dev** | 0.30 | Low variance is the classic AI tell; humans mix long and short sentences. Monotonic: `std=0 → 1.0`, `std≥10 → 0.0`. |
| **AI-phrase density** | 0.30 | Hits per 100 words for phrases over-represented in LLM output ("it is worth noting", "in conclusion", "delve into"). Saturates at ~1.5/100w. |
| **Punctuation entropy** | 0.15 | Fewer *types* of punctuation → more AI-like. Neutral 0.5 when there are < 3 marks to judge. |
| **Mean sentence length** | 0.15 | AI favours consistent, moderate length. Tent function peaking at 18 words. |
| **Type-Token Ratio (TTR)** | 0.10 | Lexical diversity; AI clusters at mid-range uniformity. Weakest feature by design (tent peaking at 0.75). |

**Why pair it with the LLM:** it is **semantically blind** but **structurally
sharp** — the exact inverse of Signal 1. The live tests make the case
concretely: on a formulaic AI paragraph that Groq *missed* (called human),
stylometry scored **0.758**, driven almost entirely by AI-phrase density. One
signal caught what the other couldn't. That is the whole reason a single signal
is not acceptable.

### Signal 3 — Watermark z-score, KGW framework (weight 0.15, additive-only) — *ensemble stretch*

**What it measures:** passive detection of statistically unlikely concentrations
of "green-list" tokens — the trace some LLMs embed at generation time. It
reconstructs a pseudo-random token partition from a shared seed and runs a
z-score hypothesis test; **z ≥ 4.0** is treated as evidence of LLM generation.

**Additive-only, and why:** a positive result *adds* to the AI score; a negative
result contributes **nothing** — it does not push toward "human." Most AI content
submitted to a platform will not carry a reconstructable watermark, so *absence*
of a watermark is not evidence of human origin. Treating it symmetrically would
manufacture false confidence. It's included because C2PA-compliant tools and some
enterprise LLMs are starting to watermark by default, so it adds genuine signal
at **zero marginal cost** where present.

### Score combiner

The final confidence is a **weighted average over the signals that are active**,
with weights renormalised so they always sum to 1.0:

```
confidence = Σ(wᵢ · scoreᵢ) / Σ(wᵢ)      for active signals i
```

- Watermark doesn't fire (the common case) → weights renormalise over Groq and
  stylometric only: `0.50/0.85 ≈ 0.588` and `0.35/0.85 ≈ 0.412`.
- Groq fails or stylometric is inactive → its weight is dropped and the rest
  renormalise. If *no* signal is available, the combiner returns `0.5` (maximum
  uncertainty) rather than crashing.
- The result is clamped to `[0.0, 1.0]`.

---

## Confidence scoring & uncertainty

> *This section explains the design decision behind the score — what a number is
> supposed to mean to a user, how I tested that it's meaningful, and what I'd
> change before deploying for real.*

### The score is a design decision before it's a technical one

I decided what the bands should *mean to a reader* first, then built the scoring
to hit them:

| Score range | Band | Meaning |
|---|---|---|
| `0.85 – 1.00` | **high-confidence AI** | Multiple independent signals agree strongly. |
| `0.21 – 0.84` | **uncertain** | Evidence is mixed or weak — the system genuinely does not know. |
| `0.00 – 0.20` | **high-confidence human** | Signals point consistently to human authorship. |

A `0.6` is **not** a softened AI accusation. It means there is somewhat more
evidence for AI than against, but not enough to assert anything — so it produces
the *uncertain* label, which is framed to protect the creator. A `0.95` and a
`0.51` therefore produce **meaningfully different** outputs: one names a verdict
and opens an appeal path, the other explicitly declines to flag the work.

### Asymmetric by design: false positives are the expensive error

On a creative platform, **flagging a human's work as AI is far worse than missing
some AI**. A false positive damages a creator's reputation and their relationship
with an audience; a false negative is a missed detection. The whole system leans
into that asymmetry:

- The high-AI threshold is set **high (≥ 0.85)** so it's hard to trigger.
- Groq — the noisiest signal — is **capped at 0.85** so it can never single-
  handedly push a verdict into the AI band.
- The *uncertain* label is worded as a **non-accusation** ("has not been
  flagged"), never a hedge.
- The appeals workflow is the recovery path for when the threshold still
  misfires.

The cost of this stance is accepted: more AI content lands in *uncertain* than a
symmetric system would allow. That is the intended trade.

### How I tested that the scores are meaningful

Testing was **directional and calibration-based**, not accuracy-chasing (perfect
detection isn't achievable, so a raw accuracy number would be misleading):

1. **Per-signal live tests** on the same three fixed samples (clear human,
   formulaic AI, ambiguous). Printing each signal *separately* is what surfaced
   that Groq blanket-labels everything "human @0.95" while stylometry correctly
   flagged the AI sample at 0.758 — proving the signals fail independently.
2. **A deliberate calibration test** on four hand-chosen inputs (clear AI, clear
   human, formal-human, lightly-edited AI). The first version **confidently
   mislabelled the clearly-AI input as `high_confidence_human` (0.05)**. Isolating
   the cause to two compounding bugs drove three fixes:

   | Fix | Before → after |
   |---|---|
   | Short-text guard `50 → 30` tokens (stylometry was disabled on the 39–43-token AI sample, leaving Groq to decide alone) | — |
   | Cap Groq confidence at `0.85` (one over-confident verdict was dominating) | — |
   | Re-centre the TTR tent `0.50 → 0.75` (the old peak sat below the TTR of all realistic short text, so the sub-score collapsed to 0.0 on every input — dead weight biasing toward "human") | — |

   After the fixes, the four inputs became **correctly ordered** by AI-likelihood
   (AI > formal-human > edited-AI > human) and the clearly-AI text was no longer
   mislabelled: `0.05 → high_confidence_human` ❌ became `0.374 → uncertain` ✅.
3. **Combiner invariant checks** (unit tests): the combined score always sits
   between the contributing signal scores, and the label bands are verified at
   their **inclusive edges** (`0.85 → AI`, `0.8499 → uncertain`, `0.20 → human`,
   `0.2001 → uncertain`).

**Honest read of the result:** because Groq calls almost everything "human," the
realistic ceiling for these samples is *uncertain* — and that's the correct,
honest output. A confident label in either direction would be false confidence.

### Two worked examples: the score varies, it isn't a constant

These are two **real end-to-end `/submit` runs** from the Milestone 4 testing —
the same two submissions recorded in the [audit log](#sample-entries) below
(their `content_id`s and per-signal scores are traceable there). One AI-styled,
one human-authored; both signals active (each input clears the 30-token guard):

| | **Higher-confidence case** (AI-styled) | **Lower-confidence case** (human-authored) |
|---|---|---|
| Submission | Formulaic prose — heavy on AI-tell phrases, uniform sentence length | Personal, varied prose with idiosyncratic phrasing |
| Signal 1 — Groq (`llm_score`) | 0.15 | 0.15 |
| Signal 2 — stylometric (`stylometric_score`) | **0.7814** | **0.3827** |
| **Combined `confidence`** | **0.41** | **0.2458** |
| Label | `uncertain` | `uncertain` |
| `content_id` | `a77d7220-…` | `e6a7df4c-…` |

The combined scores differ by **~0.16** — the scorer is clearly producing
**meaningful variation, not a constant**, and it orders the two correctly
(AI-styled > human). The variation is driven almost entirely by the stylometric
signal (0.78 vs 0.38), because Groq returns its blanket "human @capped-0.85"
(→ 0.15 AI-probability) on *both* — a vivid illustration of why the second,
independent signal is what makes the score move at all.

Why this matters as evidence: **before** the M4 recalibration, the scorer *was*
effectively a constant — the four calibration inputs all collapsed to `0.05`
(`high_confidence_human`), including the clearly-AI one. Fixing the short-text
guard, the Groq cap, and the TTR tent is what turned a flat `0.05` into a
spread that separates AI-leaning from human-leaning content.

**On the absence of a `≥ 0.85` example:** no real submission in testing reached
the *high-confidence AI* band, and that is honest rather than a gap. The Groq cap
(0.85) plus its blanket "human" verdict deliberately hold the ceiling at
*uncertain* for these inputs — the system does not manufacture confidence it
hasn't earned. Reaching the high-AI band in production requires a stronger LLM
signal (see [what I'd change](#what-id-change-before-deploying-this-for-real)),
not a looser threshold.

### What I'd change before deploying this for real

- **Replace or fine-tune the LLM signal.** `llama-3.1-8b-instant` is too weak to
  carry 0.50 weight; the cap is a patch, not a fix. A detector-tuned model (or an
  ensemble of LLM prompts) would raise the ceiling above "uncertain."
- **Calibrate thresholds against a labelled corpus.** The 0.85/0.20 bands and all
  stylometric normalisation constants were hand-tuned on a handful of samples. In
  production I'd fit them to a real dataset and report precision/recall at the
  chosen operating point — explicitly optimising for low false-positive rate.
- **Recalibrate stylometry on long-form text.** Features (TTR, mean-length) were
  tuned on short paragraphs; the AI-phrase keyword list is a fragile mechanism
  that a determined user could avoid.
- **Add score calibration (e.g. Platt scaling)** so the number is a real
  probability, not just a monotonic evidence score.
- **Human-review loop feeding back into thresholds** — appeals data is training
  signal that currently goes unused.

---

## Transparency label

The label is displayed to a reader **alongside the content** (not as an
interstitial or warning), and is written for a non-technical audience. All three
variants are stored **verbatim** in [labels.py](labels.py) — the single source of
truth — and selected purely from the confidence score. The tables below give the
**typed identifier** each variant returns (the `label` field in the `/submit`
response), its trigger, and the **exact text displayed** to the reader.

| Variant | Typed `label` value | Trigger |
|---|---|---|
| **A — High-confidence AI** | `high_confidence_ai` | `confidence ≥ 0.85` |
| **B — High-confidence human** | `high_confidence_human` | `confidence ≤ 0.20` |
| **C — Uncertain** | `uncertain` | `0.21 ≤ confidence ≤ 0.84` |

**Exact display text for each variant** (verbatim — copy/paste from `labels.py`):

**Variant A — `high_confidence_ai`:**

> Our system has determined with high confidence that this content was generated by an AI writing tool. It has been labeled accordingly. If you are the creator and believe this is incorrect, you can submit an appeal below.

**Variant B — `high_confidence_human`:**

> Our system has reviewed this content and found no significant indicators of AI generation. This work appears to be human-authored.

**Variant C — `uncertain`:**

> Our system could not confidently determine the origin of this content. This work has not been flagged as AI-generated.

**Design rationale:**

- **Variant A** names the verdict directly but leads with "high confidence" to
  signal the system isn't guessing, and immediately surfaces the appeal path.
- **Variant B** is brief and positive — a clean result needs no over-explaining.
- **Variant C** is deliberately **protective**. "Has not been flagged" is the
  operative phrase: a reader sees a **non-accusation**, not a suspicious hedge.
  This is the front line of false-positive defence — most borderline cases land
  here by design.

---

## Appeals workflow

A creator who believes a classification is wrong can contest it. The appeals flow
is **completely separate** from the detection pipeline — it touches only the
storage layer and triggers **no automated re-classification**; it flags the
record for a human moderator.

**Endpoint:** `POST /appeals/{content_id}` (a body-based `POST /appeal` variant
also exists). No auth beyond knowing the `content_id`, which is returned by the
original `/submit` call.

**Request body:**

```json
{ "creator_id": "user_42", "reason": "I wrote this poem myself during a workshop; I can provide draft notes." }
```

**What the system does:**

1. Looks up `content_id` → **`404`** if it doesn't exist.
2. Rejects a duplicate appeal → **`409`** if one is already pending.
3. Validates the body → **`422`** if `creator_id`/`reason` is missing or empty.
4. Appends the appeal to the audit log **with a timestamp**, alongside the
   original decision and signal scores.
5. Flips the record's status `classified → under_review`.
6. Returns **`200`**:

```json
{
  "status": "appeal_received",
  "content_id": "…",
  "message": "Your appeal has been logged and will be reviewed by a moderator."
}
```

**Example:**

```bash
curl -s -X POST http://localhost:5000/appeals/<content_id> \
  -H "Content-Type: application/json" \
  -d '{"creator_id": "user_42", "reason": "I wrote this myself."}'
```

The appeal (creator reasoning + timestamp) is visible in `GET /log`, and the
appealed entry's `status` reads `under_review` — see the third
[sample audit entry](#sample-entries) below.

---

## Rate limiting

The `POST /submit` endpoint is rate-limited with
[Flask-Limiter](https://flask-limiter.readthedocs.io/), keyed by client IP
(`get_remote_address`). Storage is in-memory (`storage_uri="memory://"`), which
is appropriate for local development and single-process grading; a production
deployment would swap in a shared backend (e.g. Redis) so limits hold across
workers.

**Applied limit:** `10 per minute; 100 per day`

### Why these numbers

The limit is set on the `/submit` route only (there is no global default), so
read-only endpoints like `/log` stay unthrottled.

- **10 per minute** — models a real writer checking their own drafts. A person
  iterating on a piece submits it a handful of times while editing; even an
  eager user rarely fires more than a few requests in any 60-second window. Ten
  gives comfortable headroom for legitimate bursts (re-checking a paragraph a
  few times in quick succession) while a script hammering the endpoint hits the
  wall almost immediately. This matters because every `/submit` triggers a
  paid, latency-bound call to the Groq LLM signal — unbounded traffic is both a
  cost and an availability risk.

- **100 per day** — a second ceiling that catches *slow-drip* abuse which stays
  under the per-minute bar (e.g. a bot pacing itself at one request every ~15
  seconds to look human). A genuine writer working all day is nowhere near 100
  submissions of their own content; a scraper trying to bulk-classify a corpus
  blows past it. The daily cap turns "stay under 10/min forever" into a
  bounded, defensible budget.

Both limits are deliberately generous toward the honest single user and
restrictive toward automation — the goal is to price out abuse, not to
inconvenience the writer the product is built for.

### Response when limited

A throttled request returns HTTP **429** with the same JSON shape as every other
error on this API (a custom `429` error handler replaces Flask-Limiter's default
HTML page):

```json
{
  "error": "Rate limit exceeded. Please slow down and retry shortly.",
  "limit": "10 per 1 minute"
}
```

### Evidence

Run the server (`python app.py`), then in a second terminal send 12 rapid
requests — more than the 10/minute limit:

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "request $i -> %{http_code}\n" -X POST http://localhost:5000/submit \
    -H "Content-Type: application/json" \
    -d '{"text": "This is a test submission for rate limit testing purposes only.", "creator_id": "ratelimit-test"}'
done
```

Captured output — the first 10 succeed, the 11th and 12th are rejected:

```text
request 1 -> 200
request 2 -> 200
request 3 -> 200
request 4 -> 200
request 5 -> 200
request 6 -> 200
request 7 -> 200
request 8 -> 200
request 9 -> 200
request 10 -> 200
request 11 -> 429
request 12 -> 429
```

The body of a rejected (429) request:

```text
{
  "error": "Rate limit exceeded. Please slow down and retry shortly.",
  "limit": "10 per 1 minute"
}
HTTP 429
```

---

## Audit log

Every classification is written to a structured **SQLite** audit log
(`provenance_guard.db`, table `classifications`) *before* the HTTP response is
returned. The log is exposed as **JSON** through the read-only `GET /log`
endpoint (`?limit=N`, newest first) — there is no unformatted console logging;
the record of record is the database, and every entry is machine-readable JSON.

### Fields captured

Each entry captures the full provenance of a decision:

| Requirement | Field in `GET /log` | Notes |
|---|---|---|
| **Timestamp** | `timestamp` | ISO 8601, UTC (`submitted_at` in the DB) |
| **Content ID** | `content_id` | UUID returned to the creator by `/submit` |
| **Attribution result** | `attribution` | Final label: `high_confidence_ai` / `high_confidence_human` / `uncertain` |
| **Confidence score** | `confidence` | Combined score across active signals, `[0.0, 1.0]` |
| **Individual signal score 1** | `llm_score` | Signal 1 (Groq LLM), AI-probability; `null` if the signal failed |
| **Individual signal score 2** | `stylometric_score` | Signal 2 (stylometric), AI-probability; `null` if inactive (< 30 tokens) |
| **Appeal filed?** | `appeal` / `status` | `status` is `classified` until appealed, then `under_review`; `appeal` holds the timestamped creator reasoning (`null` if none) |

Additional diagnostics are included per entry: `creator_id`, the raw `signals`
map (which also carries the additive-only **watermark** score when it fires),
per-signal `signal_status` (`ok` / `failed` / `inactive`), and a flattened
`appeal_reasoning` convenience field.

### Sample entries

Three real entries produced end-to-end through `POST /submit` (both scored
signals active on long-form text) and `POST /appeals/{content_id}`. The third
was appealed, so it shows the appeal fields populated and `status:
under_review`; the first two show `appeal: null` / `status: classified`:

```json
[
  {
    "content_id": "a77d7220-e0aa-4410-9fbc-05c5820f7e9a",
    "creator_id": "creator-ai-demo",
    "timestamp": "2026-07-02T20:49:35.545348+00:00",
    "attribution": "uncertain",
    "confidence": 0.41,
    "llm_score": 0.15,
    "stylometric_score": 0.7814,
    "signals": { "groq_llm": 0.15, "stylometric": 0.7814 },
    "signal_status": { "groq_llm": "ok", "stylometric": "ok", "watermark": "ok" },
    "status": "classified",
    "appeal": null,
    "appeal_reasoning": null
  },
  {
    "content_id": "e6a7df4c-e79c-4d3c-966e-a1ddfee78d74",
    "creator_id": "creator-human-demo",
    "timestamp": "2026-07-02T20:49:36.051032+00:00",
    "attribution": "uncertain",
    "confidence": 0.2458,
    "llm_score": 0.15,
    "stylometric_score": 0.3827,
    "signals": { "groq_llm": 0.15, "stylometric": 0.3827 },
    "signal_status": { "groq_llm": "ok", "stylometric": "ok", "watermark": "ok" },
    "status": "classified",
    "appeal": null,
    "appeal_reasoning": null
  },
  {
    "content_id": "46e1287e-1937-4d47-a634-125b2a816e73",
    "creator_id": "creator-appeal-demo",
    "timestamp": "2026-07-02T20:49:36.322002+00:00",
    "attribution": "uncertain",
    "confidence": 0.2167,
    "llm_score": 0.15,
    "stylometric_score": 0.3121,
    "signals": { "groq_llm": 0.15, "stylometric": 0.3121 },
    "signal_status": { "groq_llm": "ok", "stylometric": "ok", "watermark": "ok" },
    "status": "under_review",
    "appeal": {
      "appealed_at": "2026-07-02T20:49:36.581573+00:00",
      "creator_id": "creator-appeal-demo",
      "reason": "I wrote this quarterly summary myself; the formal tone is my professional writing style, not AI generation."
    },
    "appeal_reasoning": "I wrote this quarterly summary myself; the formal tone is my professional writing style, not AI generation."
  }
]
```

> Both scored signals are populated in these examples because the inputs exceed
> the 30-token short-text guard. When a signal does not contribute, its score is
> `null` rather than a fabricated number — `llm_score` is `null` if Groq fails,
> `stylometric_score` is `null` on short text — and `signal_status` records the
> reason. The `confidence` always sits between the contributing signal scores,
> reflecting the weighted-average combiner.

### Reproduce

```bash
python app.py                     # starts the API on :5000
# ...submit content and (optionally) appeal it, then:
curl -s http://localhost:5000/log?limit=3 | python -m json.tool
```

---

## Edge cases

The pipeline explicitly handles four scenarios (full write-ups in
[planning.md §5](planning.md)):

1. **Very short content (< 30 tokens)** — a haiku's stylometric features are
   unreliable, so the stylometric signal is set **inactive**, weights renormalise
   over the remaining signals, and `metadata.low_token_warning: true` is set. The
   result usually lands in *uncertain*, which is correct — there's genuinely less
   evidence.
2. **Groq timeout/failure** — Signal 1 is marked `failed`, the score is computed
   from the remaining signals with renormalised weights, and the decision is
   still logged. A partial-evidence classification beats a silent crash.
3. **Re-submission after appeal** — each submission gets an independent
   `content_id`; the appeal on the original proceeds on its own track. Both
   records are visible to a reviewer.
4. **Watermark fires on a quoted AI passage inside human work** — a known limit of
   passage-level detection. The additive-only design + the protective *uncertain*
   label + appeals are the mitigations.

---

## Testing

Full suite: **59/59 passing** (`pytest`). Coverage includes route-level
validation and the three label variants, the Groq-failure fallback, the
short-text guard, the confidence cap, combiner renormalisation, the appeals flow
(200/404/409/422 + status flip + audit-log write), and per-signal live tests.

```bash
# Full suite
python -m pytest

# Standalone live signal tests (Signals 2 & 3 need no API key — pure Python)
python tests/test_stylometric_signal_live.py
python tests/test_watermark_signal_live.py
python tests/test_groq_signal_live.py        # needs GROQ_API_KEY
```

---

## Known limitations & what I'd change for a real deployment

### Content this system would likely get wrong

**Formal, structured human writing — flagged as AI (a false positive, the error
that matters most here).** The stylometric signal's two heaviest features
(sentence-length std dev @0.30 and AI-phrase density @0.30) both encode one
assumption: *uniform, low-variance prose is AI-like*. That assumption breaks on
whole genres of genuine human writing that are uniform **by convention** — a
corporate quarterly memo, an academic abstract, a legal summary, technical
documentation. These are written to a house style: consistent moderate sentence
length (low std dev → high AI sub-score) and connective boilerplate like
"furthermore," "moreover," "in conclusion" that sits *directly* on the
`AI_PHRASES` list (high phrase-density → high AI sub-score). A human-written
formal paragraph can therefore score ~0.6–0.78 on stylometry alone. This isn't
hypothetical — it's exactly the "formal-human" input from the M4 calibration test,
and it's why the appealed audit-log entry (a self-described *"quarterly summary…
formal tone is my professional writing style"*) exists. The **same population most
likely to write this way — non-native English speakers and professionals writing
in a register — is the population most likely to be wrongly flagged.** It's a
false positive baked into what the feature measures, not a data-volume problem.
The system's mitigations are deliberate: the high 0.85 threshold, the Groq cap,
the protective *uncertain* wording, and the appeals path — but the underlying
signal bias remains, and honestly disclosing it is part of the design.

**The mirror-image failure — lightly-edited AI text passed as human (a false
negative).** Both required signals are cheaply evadable *because of how they're
computed*. AI-phrase density is a **fixed substring list**: delete the tell-tale
phrases and that 0.30-weight feature drops to ~0. A light human paraphrase also
injects sentence-length variance, defeating the std-dev feature. Groq already
misses formulaic AI text on its own (documented above), so once stylometry is
neutralised, nothing catches it. This is the accepted side of the asymmetry — the
system trades false negatives for fewer false positives — but it means a
motivated user can get AI content to *uncertain* (never flagged) with minor edits.

### Operational limitations

- **Detection quality is bounded by a weak LLM.** `llama-3.1-8b-instant` is
  over-confident and often wrong; the 0.85 cap contains the damage but the honest
  ceiling for many inputs is *uncertain*. A stronger/detector-tuned model is the
  first upgrade. See the [scoring section](#what-id-change-before-deploying-this-for-real)
  for the full list.
- **In-memory rate-limit storage** resets on restart and isn't shared across
  workers — fine for a single-process server, needs Redis for multi-worker.
- **`GET /log` is unauthenticated** (intentional, for grading visibility) and
  unthrottled — it would need auth and a rate limit in production.
- **Audit-log write failures are swallowed** (logged internally, response still
  returned). Correct for availability, but a real system needs retry/alerting so
  a full disk doesn't silently drop the record of record.
- **The watermark detector uses a simplified KGW key schedule** — it fires
  reliably on synthetically-watermarked text but is unlikely to catch real
  production watermarks. That's why it's additive-only and never treated as
  disconfirming evidence.

---

## Spec reflection

**One way the spec guided the implementation.** Writing the spec first — the
signal weights, threshold bands, audit-log schema, and *especially* the
anticipated edge cases in [planning.md §5](planning.md) — meant the failure paths
were designed before they were hit, not bolted on afterward. Edge Case 2 ("Groq
API timeout or failure") specified up front that a failed signal should be
dropped, the weights renormalised over the survivors, and `signal_status` returned
so the caller knows the score is partial. Because that contract existed on paper,
the combiner was built to take an *arbitrary subset* of signals from day one
([combiner.py](combiner.py) renormalises over whatever is present). So when Groq
genuinely failed in testing, and separately when the short-text guard disabled
stylometry, the system degraded gracefully with **no new code** — it was already
the specified behaviour. The spec turned three separate "what if a signal is
missing?" situations into one uniform, pre-decided rule.

**One way the implementation diverged, and why.** The spec ([planning.md §5](planning.md),
Edge Case 1) originally set the short-text guard at **50 tokens** — below that,
disable the stylometric signal. On contact with real inputs that number was wrong:
a deliberate calibration test showed most genuine paragraphs run **30–60 tokens**,
so a 50-token cutoff disabled stylometry on the *majority* of realistic
submissions — including a clearly-AI sample — leaving the weak Groq signal to
decide alone, which it did badly (it mislabelled that sample `high_confidence_human`
@0.05). I lowered the threshold to **30 tokens** (`SHORT_TEXT_TOKEN_THRESHOLD` in
[app.py](app.py)): still high enough to exclude haiku-length fragments (~17 tokens)
where the features are genuinely unreliable, but low enough to keep stylometry
active on ordinary prose so it can counterbalance a confidently-wrong LLM. The
spec's *intent* (suppress the signal only where it's untrustworthy) was right; its
*number* was a guess that live evidence corrected. The divergence, and its
reasoning, are documented inline in both `app.py` and `planning.md`.

---

## AI usage

This project was built with AI assistance (Claude). The value was in **directing
it against the spec and then verifying its output** — several times the generated
code was plausible but wrong in a way only testing exposed. Specific instances:

**1. Signal 1 (Groq) confidence direction — overrode a subtle correctness bug.**
I directed the AI to wire the Groq classifier into the score combiner. It produced
working code that fed Groq's returned `confidence` value **straight into the
combiner**. Testing surfaced a contradiction: a response came back with
`attribution: human_written` but `label: high_confidence_ai`. The root cause was
that Groq's `confidence` means "how sure I am of *my own label*," not "how likely
this is AI" — so `{human_written, 0.95}` was being read as 0.95 AI-probability, the
exact opposite of the truth. I overrode the AI's direct pass-through with a
`label_to_ai_probability()` function that inverts the score for a human label
(`1 − 0.95 = 0.05`) before it reaches the combiner ([groq_signal.py](signals/groq_signal.py)).
I later extended it further than the AI's original design with a `0.85` confidence
cap, after testing showed the model was over-confident on wrong answers.

**2. Stylometric TTR normalisation — revised a miscalibrated constant.** I directed
the AI to implement the five stylometric features with each normalised to a
`[0,1]` AI sub-score. It produced tent functions, and for Type-Token Ratio it
centred the tent at a peak of **0.50**. When I ran the signal on real text, the TTR
sub-score came out **0.0 on every sample** — because realistic short prose has a
TTR of ~0.85–0.90, far outside a tent peaked at 0.50, so the feature was dead
weight that only ever biased scores toward "human." I revised the peak to **0.75**
(half-width 0.24) so ordinary prose reads ≈ neutral 0.5 instead of a hard 0
([stylometric_signal.py](signals/stylometric_signal.py)). This is a case where the
AI's structure was fine but its hand-picked constant was untested against the
actual data distribution.

**3. Test isolation — corrected two generated-test defects that hid real behaviour.**
I directed the AI to write the route and database tests. Two of its choices were
wrong in ways that made tests pass for the wrong reasons: (a) it patched the mock
at `signals.groq_signal.classify`, but `app.py` binds the name at import
(`from … import classify as groq_classify`), so the patch had no effect and the
**real Groq API was being called** during tests — I moved the patch target to
`app.groq_classify`; and (b) `database.py` read the DB path at *import* time, so
the test fixtures' `monkeypatch.setenv` was silently ignored and every test wrote
into the **real** `provenance_guard.db` — I changed it to resolve the path lazily
per-connection so each test gets an isolated temp DB ([database.py](database.py)).
Both fixes are the kind of thing an AI won't catch on its own because the tests
*appeared* green; only reasoning about import-time binding and reading the assertions
skeptically exposed them.

---

**Project docs:** architecture diagram and full spec in [planning.md](planning.md)
