# Requirements Document

## Introduction

Provenance Guard is a backend API system that classifies text-based creative content as AI-generated or human-written. The system runs a multi-signal detection pipeline, combines signal outputs into a calibrated confidence score, maps the score to a plain-language transparency label, persists every decision to a structured audit log, and provides creators with an appeals workflow to contest classifications they believe are wrong. The system is designed for integration by creative-sharing platforms that need attribution transparency without building detection infrastructure themselves.

---

## Glossary

- **System**: The Provenance Guard backend API as a whole.
- **API**: The HTTP interface exposed by the System.
- **Submission_Endpoint**: The `POST /submit` route that accepts content for classification.
- **Detection_Pipeline**: The orchestration layer that invokes all active signals and collects their scores.
- **Groq_Signal**: Signal 1 — the LLM-based classifier that calls the Groq inference API and returns a structured JSON assessment.
- **Stylometric_Signal**: Signal 2 — the pure-Python heuristic scorer that computes statistical surface features of the submitted text.
- **Watermark_Signal**: Signal 3 — the passive KGW-framework z-score detector that checks for statistically unlikely concentrations of green-list tokens.
- **Score_Combiner**: The module that merges per-signal scores into a single final confidence value using weighted averaging.
- **Confidence_Score**: A float in `[0.0, 1.0]` representing evidential weight toward AI generation; higher = more AI-like.
- **Label**: One of three classification outcomes: `high_confidence_ai`, `high_confidence_human`, or `uncertain`.
- **Transparency_Text**: The verbatim plain-language string surfaced to end-users alongside the Label.
- **Audit_Log**: The SQLite database that records every classification decision, signal scores, and any associated appeal.
- **Appeals_Endpoint**: The `POST /appeals/{content_id}` route that allows creators to contest a classification.
- **Appeals_Handler**: The module that validates, logs, and status-updates an appeal submission.
- **Log_Endpoint**: The `GET /log` route that returns recent Audit_Log entries.
- **Rate_Limiter**: The Flask-Limiter instance that enforces per-IP request rate limits.
- **Creator**: A human user who submitted content and may submit an appeal.
- **Content_Record**: The persisted representation of a single submitted piece of content, including its classification result and status.
- **TTR**: Type-Token Ratio — unique tokens divided by total tokens, a measure of lexical diversity.

---

## Requirements

### Requirement 1: Content Submission

**User Story:** As a platform operator, I want to submit a piece of text content and receive a structured classification response, so that I can surface attribution information to my users.

#### Acceptance Criteria

1. THE Submission_Endpoint SHALL accept HTTP POST requests containing a `content` field (string) and a `creator_id` field (string).
2. WHEN a valid submission is received, THE Submission_Endpoint SHALL return a JSON response containing `content_id`, `label`, `confidence`, `transparency_text`, and `signal_status`.
3. IF the `content` field is absent or empty, THEN THE Submission_Endpoint SHALL return HTTP 422 with a descriptive error message.
4. IF the `creator_id` field is absent or empty, THEN THE Submission_Endpoint SHALL return HTTP 422 with a descriptive error message.
5. WHEN a submission is processed, THE System SHALL assign a unique `content_id` to the Content_Record and include it in the response.

---

### Requirement 2: Rate Limiting

**User Story:** As a system operator, I want per-IP rate limits enforced on submission and appeals endpoints, so that I can protect upstream API quota and prevent abuse.

#### Acceptance Criteria

1. THE Rate_Limiter SHALL enforce a limit of 10 requests per minute per IP address on the Submission_Endpoint.
2. THE Rate_Limiter SHALL enforce a limit of 30 requests per hour per IP address on the Appeals_Endpoint.
3. WHEN a request exceeds the applicable rate limit, THE Rate_Limiter SHALL return HTTP 429 and SHALL NOT forward the request to the Detection_Pipeline or Appeals_Handler.
4. WHEN a request is within the applicable rate limit, THE Rate_Limiter SHALL pass the request through to the appropriate handler without modification.

---

### Requirement 3: Multi-Signal Detection Pipeline

**User Story:** As a system operator, I want content classified using at least two independent signals, so that the confidence score reflects more than one type of evidence.

#### Acceptance Criteria

1. WHEN a submission is received, THE Detection_Pipeline SHALL invoke the Groq_Signal, the Stylometric_Signal, and the Watermark_Signal.
2. THE Groq_Signal SHALL call the Groq inference API with a structured prompt and SHALL parse the response into a confidence float in `[0.0, 1.0]`.
3. THE Stylometric_Signal SHALL compute TTR, mean sentence length, sentence-length standard deviation, AI phrase density, and punctuation entropy from the submitted text using only in-process computation.
4. THE Stylometric_Signal SHALL combine its five feature values into a single score in `[0.0, 1.0]` where higher values indicate more AI-like surface statistics.
5. THE Watermark_Signal SHALL compute a KGW z-score for the submitted text and SHALL set its output to active only when the z-score is greater than or equal to 4.0.
6. WHEN the Watermark_Signal z-score is below 4.0, THE Watermark_Signal SHALL contribute a weight of 0 to the Score_Combiner and SHALL NOT reduce the Confidence_Score.
7. IF the Groq_Signal call fails or times out, THEN THE Detection_Pipeline SHALL mark the Groq_Signal status as `failed`, exclude it from the Score_Combiner, and continue processing with the remaining active signals.
8. WHEN the submitted text contains fewer than 50 tokens, THE Detection_Pipeline SHALL set the Stylometric_Signal weight to 0, exclude it from the Score_Combiner, and include `low_token_warning: true` in the response metadata.

---

### Requirement 4: Confidence Scoring

**User Story:** As a platform operator, I want a single calibrated confidence score returned with every classification, so that I can understand how certain the system is and communicate that to users.

#### Acceptance Criteria

1. THE Score_Combiner SHALL compute the Confidence_Score as a weighted average of active signal scores using weights: Groq_Signal 0.50, Stylometric_Signal 0.35, Watermark_Signal 0.15.
2. WHEN one or more signals are inactive or failed, THE Score_Combiner SHALL renormalize the weights of the remaining active signals so that active weights sum to 1.0.
3. THE Score_Combiner SHALL produce a Confidence_Score in `[0.0, 1.0]` for every submission regardless of which signals are active.
4. THE System SHALL assign Label `high_confidence_ai` WHEN the Confidence_Score is greater than or equal to 0.85.
5. THE System SHALL assign Label `high_confidence_human` WHEN the Confidence_Score is less than or equal to 0.20.
6. THE System SHALL assign Label `uncertain` WHEN the Confidence_Score is greater than 0.20 and less than 0.85.

---

### Requirement 5: Transparency Labels

**User Story:** As a platform operator, I want verbatim transparency label text returned with every classification, so that I can display a plain-language attribution statement to my users without writing copy myself.

#### Acceptance Criteria

1. WHEN the Label is `high_confidence_ai`, THE System SHALL include the following verbatim string as `transparency_text`: `"Our system has determined with high confidence that this content was generated by an AI writing tool. It has been labeled accordingly. If you are the creator and believe this is incorrect, you can submit an appeal below."`
2. WHEN the Label is `high_confidence_human`, THE System SHALL include the following verbatim string as `transparency_text`: `"Our system has reviewed this content and found no significant indicators of AI generation. This work appears to be human-authored."`
3. WHEN the Label is `uncertain`, THE System SHALL include the following verbatim string as `transparency_text`: `"Our system could not confidently determine the origin of this content. This work has not been flagged as AI-generated."`
4. THE System SHALL include the `transparency_text` field in every classification response regardless of Label value.

---

### Requirement 6: Audit Logging

**User Story:** As a system operator, I want every classification decision logged to a structured store, so that I have a complete record of decisions and can support appeal review.

#### Acceptance Criteria

1. WHEN a classification is complete, THE System SHALL write a record to the Audit_Log containing: `content_id`, `submitted_at` (ISO 8601 timestamp), `label`, `confidence`, per-signal scores, `signal_status`, and `status`.
2. THE Audit_Log SHALL use SQLite as its storage backend.
3. THE System SHALL write the Audit_Log entry before returning the classification response to the caller.
4. WHEN the Audit_Log write fails, THE System SHALL log the error internally and SHALL still return the classification response to the caller.
5. THE Log_Endpoint SHALL accept HTTP GET requests and SHALL return the most recent N Audit_Log entries as a JSON array, where N defaults to 20 and is configurable via a `limit` query parameter.

---

### Requirement 7: Appeals Workflow

**User Story:** As a creator, I want to submit an appeal when I believe my content has been misclassified, so that a human reviewer can examine the original decision alongside my explanation.

#### Acceptance Criteria

1. THE Appeals_Endpoint SHALL accept HTTP POST requests to `/appeals/{content_id}` containing a `creator_id` field (string) and a `reason` field (string).
2. IF the `content_id` does not correspond to an existing Content_Record, THEN THE Appeals_Handler SHALL return HTTP 404.
3. IF an appeal with status `pending` already exists for the given `content_id`, THEN THE Appeals_Handler SHALL return HTTP 409.
4. WHEN a valid appeal is received, THE Appeals_Handler SHALL append the appeal to the Audit_Log entry for the given `content_id`, including `appealed_at` timestamp, `creator_id`, and `reason`.
5. WHEN a valid appeal is received, THE Appeals_Handler SHALL update the Content_Record status from `classified` to `under_review`.
6. WHEN a valid appeal is received, THE Appeals_Handler SHALL return HTTP 200 with a JSON response containing `status: "appeal_received"`, `content_id`, and a confirmation `message`.
7. THE Appeals_Handler SHALL NOT trigger re-classification of the content.
8. IF the `reason` field is absent or empty, THEN THE Appeals_Handler SHALL return HTTP 422 with a descriptive error message.
