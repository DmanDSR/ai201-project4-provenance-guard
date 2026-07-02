# Provenance Guard — Planning Document

## Overview

Provenance Guard is a backend API that classifies text-based creative content as AI-generated or human-written. It returns a confidence score and a plain-language transparency label, and it gives creators a structured path to appeal a verdict they believe is wrong. Any creative-sharing platform (writing communities, poetry apps, story forums) can call the API to add attribution transparency without building detection infrastructure themselves.

---

## 1. Detection Signals

### Signal 1 — Groq LLM Classification

**What it measures:** Semantic and stylistic coherence assessed holistically. The LLM reads the text as a whole and judges whether the writing patterns, word choice, and structural consistency are characteristic of an AI language model or a human author.

**How it works:** A structured prompt is sent to Groq's inference API. The model is instructed to return JSON only.

**Output format:**
```json
{
  "label": "ai_generated" | "human_written",
  "confidence": 0.0–1.0,
  "reasoning": "Brief explanation of the key signals the model noticed."
}
```

**Weight in combiner:** 0.50 (primary signal — highest information density)

> **Calibration note (M4 follow-up):** the model's stated confidence is capped at `GROQ_CONFIDENCE_CAP = 0.85` (in `groq_signal.label_to_ai_probability`) before it enters the combiner. `llama-3.1-8b-instant` proved to be an over-confident, weak detector — returning 0.95 on samples it labelled incorrectly. Because this signal carries the largest weight (and the full weight when it is the only active signal), an unchecked 0.95 let a single wrong verdict dominate. The cap lets Groq reach, but not exceed, the high-confidence boundary on its own, so a second signal is always needed to move a verdict decisively.

---

### Signal 2 — Stylometric Heuristics

**What it measures:** Statistical surface properties of the text that differ systematically between AI and human writing. AI text tends to be more uniform; human writing is more variable and idiosyncratic.

**Features computed (all in pure Python, no external calls):**

| Feature | What it captures |
|---|---|
| Type-Token Ratio (TTR) | Lexical diversity: `unique tokens / total tokens`. AI text tends toward moderately-high, uniform diversity. *(M4 follow-up: the normalisation tent was re-centred from peak 0.50 to 0.75 — the old peak sat far below the TTR of all realistic short text (~0.85–0.90), collapsing this sub-score to 0.0 on every input and biasing scores toward "human". It is the weakest, lowest-weighted feature by design.)* |
| Mean sentence length | AI tends toward consistent, moderate sentence length. |
| Sentence-length standard deviation | Human writing has higher variance. Low std dev is an AI signal. |
| AI phrase density | Frequency of phrases statistically over-represented in LLM output (e.g., "it is worth noting", "in conclusion", "it is important to"). |
| Punctuation entropy | Measure of punctuation variety. AI text tends to use fewer punctuation types. |

**Output format:** Single float, `0.0–1.0`, where higher = more AI-like.

**Weight in combiner:** 0.35

---

### Signal 3 — Watermark Z-Score (KGW Framework)

**What it measures:** Passive detection of statistically unlikely concentrations of "green list" tokens — the trace left by KGW-style watermarking embedded by some LLMs at generation time.

**How it works:** The detector reconstructs a pseudo-random token partition using a shared seed and counts the fraction of tokens in the "green list." A z-score hypothesis test determines whether this fraction is significantly above chance.

**Threshold:** z-score ≥ 4.0 is treated as evidence of LLM generation.

**Additive-only behavior:** A positive result adds to confidence. A negative result (z-score < 4.0) contributes 0 — it does not reduce the confidence score. Most AI content submitted to the platform will not carry a watermark, so absence of a watermark is not evidence of human origin.

**Why include it:** Some enterprise LLMs and C2PA-compliant tools are beginning to embed watermarks by default. Detecting them where present adds genuine signal at zero marginal cost.

**Output format:** `{"z_score": float, "fires": true | false}`. Only added to the weighted average when `fires == true`.

**Weight in combiner:** 0.15 (applied only when signal fires)

---

### Score Combiner

The final confidence score is a weighted average of the signals that fired:

```
confidence = (w1 * score1 + w2 * score2 + w3 * score3_if_fires) / total_active_weight
```

- If the watermark signal does not fire, weights renormalize over Signal 1 and Signal 2 only (0.50 / 0.85 ≈ 0.588 and 0.35 / 0.85 ≈ 0.412 respectively).
- The result is always in `[0.0, 1.0]`.

---

## 2. Uncertainty Representation

### What the confidence score means

The confidence score is a probability-like measure of how strongly the combined evidence points toward AI generation. It is **not** a binary classification dressed up as a number — it is a genuine expression of evidential weight.

| Score range | Interpretation |
|---|---|
| 0.85 – 1.00 | High-confidence AI: multiple independent signals agree strongly |
| 0.21 – 0.84 | Uncertain: evidence is mixed or weak |
| 0.00 – 0.20 | High-confidence human: signals point consistently toward human authorship |

### What 0.6 means

A score of 0.6 means the system has more evidence pointing toward AI than human, but not enough to assert it. The signals disagreed, or each individual signal was weak. **The system genuinely does not know.** This score produces the uncertain label — not a softened AI accusation.

### Asymmetric design principle

A false positive (human work flagged as AI) causes real harm to a creator's reputation and relationship with their audience. A false negative (AI content not flagged) is a missed detection. The system is designed to accept more false negatives in exchange for fewer false positives:

- The high-confidence AI threshold is set high (≥ 0.85), making it hard to trigger.
- The uncertain label is framed to protect the creator, not imply suspicion.
- The appeals workflow exists as a recovery path when the threshold still misfires.

---

## 3. Transparency Label Design

All three variants are written for a non-technical reader on a creative-sharing platform. The label appears alongside the content, not as an interstitial or warning screen.

---

**Variant A — High-confidence AI** (confidence ≥ 0.85)

> "Our system has determined with high confidence that this content was generated by an AI writing tool. It has been labeled accordingly. If you are the creator and believe this is incorrect, you can submit an appeal below."

---

**Variant B — High-confidence human** (confidence ≤ 0.20)

> "Our system has reviewed this content and found no significant indicators of AI generation. This work appears to be human-authored."

---

**Variant C — Uncertain** (0.21 ≤ confidence ≤ 0.84)

> "Our system could not confidently determine the origin of this content. This work has not been flagged as AI-generated."

---

### Design rationale

- Variant A names the verdict directly but opens with "high confidence" to signal the system is not guessing, and immediately surfaces the appeal path.
- Variant B is brief and positive — no need to over-explain a clean result.
- Variant C is intentionally protective. The phrase "has not been flagged" is the operative statement. A reader sees a non-accusation, not a hedge.

---

## 4. Appeals Workflow

### Who submits

Any creator whose content has been classified can submit an appeal. No authentication is required beyond knowing the `content_id` of their submission (which is returned in the original classification response).

### What the creator submits

`POST /appeals/{content_id}`

```json
{
  "creator_id": "string",
  "reason": "string — the creator's explanation of why they believe the classification is wrong"
}
```

### What the system does

1. Looks up `content_id` in storage and returns `404` if it does not exist.
2. Validates that the content has not already been appealed (returns `409` if a pending appeal already exists).
3. Appends the appeal to the audit log with a timestamp and the original classification record.
4. Updates the content record's status field from `classified` to `under_review`.
5. Returns `200` with a confirmation payload:
   ```json
   {
     "status": "appeal_received",
     "content_id": "...",
     "message": "Your appeal has been logged and will be reviewed by a moderator."
   }
   ```

### What a human reviewer sees (audit log entry)

```json
{
  "content_id": "abc123",
  "submitted_at": "2025-07-14T10:22:00Z",
  "original_label": "ai_generated",
  "confidence": 0.91,
  "signals": {
    "groq_llm": 0.93,
    "stylometric": 0.87,
    "watermark_z_score": 7.2
  },
  "status": "under_review",
  "appeal": {
    "appealed_at": "2025-07-14T11:05:00Z",
    "creator_id": "user_42",
    "reason": "This poem was written entirely by me during a workshop. I can provide my draft notes."
  }
}
```

No automated re-classification occurs. The appeal flags the record for a human moderator to review.

---

## 5. Anticipated Edge Cases

### Edge Case 1 — Very short content (< 30 tokens)

**Scenario:** A creator submits a haiku or a three-line poem. The stylometric signal becomes unreliable — TTR is inflated, sentence-length std dev is near-zero by construction, and AI phrase density has too few tokens to be meaningful.

**Handling:** If token count < 30 (`SHORT_TEXT_TOKEN_THRESHOLD` in `app.py`), the stylometric signal weight is reduced to 0 and the score renormalizes over Signal 1 (and Signal 3 if it fires). The response includes a `"low_token_warning": true` flag in the metadata. The confidence is more likely to land in the uncertain band, which is correct — there is genuinely less evidence.

> **Calibration note (M4 follow-up):** this threshold was originally 50. Live testing showed most real paragraphs run 30–60 tokens, so a 50-token cutoff disabled stylometric on the majority of realistic inputs and left the weaker Groq signal deciding alone — which mislabelled clearly-AI text as high-confidence human. Lowered to 30: still excludes haiku-length fragments (~17 tokens) where the features are genuinely unreliable, but keeps stylometric active on ordinary paragraphs so it can counterbalance a confidently-wrong Groq verdict.

---

### Edge Case 2 — Groq API timeout or failure

**Scenario:** The Groq LLM call times out or returns a non-200 response during a classification request.

**Handling:** Signal 1 is marked as `failed`. The system falls back to the remaining signals (stylometric + watermark if it fires). The confidence score is computed from available signals only, with renormalized weights. The response includes `"signal_status": {"groq_llm": "failed"}` so the caller knows the score is based on partial evidence. The decision is still logged — a partial-evidence classification is better than a silent failure.

---

### Edge Case 3 — Content re-submitted after appeal

**Scenario:** A creator whose content is under review submits the same or near-identical content again to get a fresh classification score while their appeal is pending.

**Handling:** At submission time, the system does not deduplicate content by text hash — each submission gets an independent `content_id` and its own classification. This is intentional: a re-submission should be independently classified, and the creator's appeal on the original record proceeds on its own track. The audit log will show both records, which gives a human reviewer more signal, not less.

---

### Edge Case 4 — Watermark signal fires on human content

**Scenario:** A creator quotes or pastes a block of LLM-generated text as an epigraph inside an otherwise human-written piece. The watermark z-score fires on the quoted section.

**Handling:** This is a known limitation of passage-level watermark detection. The system documents that the watermark signal is additive-only and that a positive result means "this text contains a watermarked passage," not "this work is entirely AI-generated." The transparency label (especially in the uncertain band) and the appeals workflow are the mitigations. A note in the API documentation advises platforms that quoted AI content may trigger this signal.

---

## Architecture

```
+------------------+
| Platform / App   |
+------------------+
        |
        | POST /submit  (content + creator_id)
        v
+------------------+
|  Rate Limiter    |  Flask-Limiter
|  10/min per IP   |
+------------------+
        |
   429 if over limit
        |
        v
+------------------+
| Detection        |
| Pipeline         |
+------------------+
   /       |       \
  v        v        v
+------+ +-------+ +----------+
|Signal| |Signal | |Signal 3  |
|  1   | |  2    | |Watermark |
|Groq  | |Stylo  | |z-score   |
|LLM   | |metric | |(additive)|
+------+ +-------+ +----------+
   \       |       /
    v       v      v
   +------------------+
   | Score Combiner   |
   | weighted average |
   +------------------+
            |
   +--------+---------+
   |                  |
 >= 0.85          <= 0.20
   |               |
[High AI]     [High Human]    (else) [Uncertain]
   |               |                    |
   +---------------+--------------------+
                   |
        +------------------+
        | Response Builder |
        | label + score +  |
        | transparency txt |
        +------------------+
           /          \
          v            v
  +----------+    +-----------+
  | Audit    |    | Structured|
  | Log      |    | Response  |
  | (SQLite) |    | to caller |
  +----------+    +-----------+

---  Appeals (separate flow, no pipeline)  ---

POST /appeals/{content_id}
        |
        v
+------------------+
| Appeals Handler  |
+------------------+
        |
   Look up content_id
        |
   Append to audit log
        |
   Set status = under_review
        |
        v
   200 Confirmed

---  Log retrieval  ---

GET /log  -->  Return last N audit entries
```

**Narrative:** A piece of content enters through `POST /submit`, clears the rate limiter, and fans out to three independent detection signals that run in parallel. Each signal returns its own score; the score combiner merges them into a single confidence value using weighted averaging (renormalizing if a signal fails or does not fire). The threshold check assigns a label, the response builder attaches the transparency text, and the result is written to the SQLite audit log and returned to the caller simultaneously. The appeals flow is entirely separate — it touches only the storage layer, not the detection pipeline.

---

## AI Tool Plan

### M3 — Submission endpoint + Signal 1 (Groq LLM)

**What to build:**
- `POST /submit` endpoint (Flask route)
- Rate limiter setup (Flask-Limiter, 10/min per IP)
- Signal 1: Groq LLM classification (structured JSON prompt + response parse)
- SQLite audit log schema + write on every decision
- Stub for Signal 2 (returns 0.5) so the score combiner can be wired end-to-end

**Sections to feed the AI assistant:**
- Section 1 (Signal 1 spec: prompt format, expected JSON output, confidence field)
- Section 2 (Confidence score design: weights, combiner formula)
- Section 4 (Audit log entry format — show the full JSON schema)

**What to generate:**
- Flask app skeleton with `/submit` route
- Groq API call with retry on timeout (2 retries, 5s timeout)
- Score combiner function that accepts a dict of `{signal_name: score}` and weight map
- SQLite schema: `content_id`, `submitted_at`, `label`, `confidence`, `signals_json`, `status`

**How to verify:**
- Submit a known AI-generated paragraph and a known human paragraph; confirm label and score direction are correct
- Force a Groq timeout (mock) and confirm the system returns a partial-evidence response rather than crashing
- Check SQLite has a new row after each `/submit` call
- Confirm `429` is returned on the 11th request in a minute from the same IP

---

### M4 — Signal 2 (Stylometric heuristics) + full confidence scoring

**What to build:**
- Signal 2: all five stylometric features (TTR, mean sentence length, std dev, AI phrase density, punctuation entropy)
- Feature-to-score mapping function (normalize each feature to 0–1, combine into one stylometric score)
- Replace the Signal 2 stub with the real implementation
- Short-text guard: if token count < 30 (see Edge Case 1 calibration note), zero out Signal 2 weight and set `low_token_warning: true`

**Sections to feed the AI assistant:**
- Section 1 (Signal 2 spec: the feature table, scoring formula)
- Section 2 (Uncertainty: what the score bands mean, renormalization rules)
- Section 5 (Edge Case 1: short content handling)

**What to generate:**
- `stylometric.py` module with one public function: `score(text: str) -> dict` returning each feature value and the combined score
- Unit tests: one AI-like text (low variance, formulaic phrases), one human-like text (high variance, unusual punctuation)
- Updated score combiner that handles missing/zero-weight signals

**How to verify:**
- Run `stylometric.score()` on a known AI paragraph and confirm score > 0.5
- Run on a haiku (< 30 tokens) and confirm `low_token_warning: true` and Signal 2 weight = 0
- End-to-end `/submit` call with both Signal 1 + Signal 2 active; confirm combined score is between the two individual scores

---

### M5 — Production layer (labels + appeals endpoint)

**What to build:**
- Transparency label selector: maps confidence score + label to verbatim label text (Variants A, B, C from Section 3)
- `POST /appeals/{content_id}` endpoint
- Appeal validation: 404 on unknown content_id, 409 on duplicate appeal
- Audit log update: append appeal record, set status = `under_review`
- `GET /log` endpoint: returns last N audit entries (default N=20, configurable via query param)

**Sections to feed the AI assistant:**
- Section 3 (exact verbatim text for all three label variants)
- Section 4 (appeals workflow: request schema, system steps, response format, audit log entry format)

**What to generate:**
- `labels.py`: pure function `get_label(confidence: float) -> dict` returning `{label, transparency_text}`
- `appeals.py`: Flask blueprint with POST handler
- `GET /log` route with pagination
- Integration test: submit content → check label text matches Section 3 verbatim → submit appeal → check audit log status = `under_review`

**How to verify:**
- Submit content with mocked confidence = 0.91 → confirm Variant A text appears verbatim in response
- Submit content with mocked confidence = 0.10 → confirm Variant B text
- Submit content with mocked confidence = 0.60 → confirm Variant C text
- Submit appeal for valid `content_id` → confirm `200` and status update in SQLite
- Submit appeal for unknown `content_id` → confirm `404`
- Submit duplicate appeal → confirm `409`
- Call `GET /log` → confirm at least 3 entries are visible with correct schema
