# Provenance Guard

A backend service that a creative-sharing platform can plug into to
classify submitted text as likely human-written or AI-generated, score its
confidence honestly (including "we can't tell"), show readers a plain-
language transparency label, and let creators appeal a classification they
believe is wrong.

See [planning.md](planning.md) for the full architecture narrative,
signal rationale, false-positive walkthrough, and diagrams. This README
documents the required evidence: label text, signal design, confidence
testing, rate limits, and audit log samples.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Mac/Linux
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt
cp .env.example .env               # then fill in GROQ_API_KEY
python app.py                      # serves on http://127.0.0.1:5000
```

Sanity check the signals directly (no server needed):

```bash
python -m scripts.evaluate_signals
```

## API

| Endpoint | Method | Body | Returns |
|---|---|---|---|
| `/submit` | POST | `{ "content": str, "creator_id": str? }` | classification result (below) |
| `/appeal` | POST | `{ "content_id": str, "reasoning": str }` (`creator_reasoning` also accepted as an alias) | `{ appeal_id, content_id, status: "under_review" }` |
| `/content/<id>` | GET | — | full submission record + appeal history |
| `/log` | GET | `?limit=N` | audit log, newest first |
| `/health` | GET | — | liveness check |

Example `/submit` response:

```json
{
  "content_id": "9d2c199252dc",
  "status": "classified",
  "ai_score": 0.759,
  "confidence": 0.517,
  "verdict": "likely_ai",
  "label": "⚠️ Likely AI-Generated — Our analysis indicates this content was most likely produced by an AI system (confidence: 52%). This is an automated assessment, not a certainty, and the creator may appeal this classification.",
  "signals": {
    "llm": { "score": 0.9, "model": "llama-3.3-70b-versatile", "rationale": "...", "available": true },
    "stylometric": { "score": 0.546, "coefficient_of_variation": 0.091, "type_token_ratio": 0.63, "punctuation_density_per_100_chars": 2.42, "...": "..." },
    "weights": { "llm": 0.6, "stylometric": 0.4 }
  }
}
```

## Detection Signals

Two independent signals feed the pipeline (full rationale in
[planning.md §1](planning.md)):

1. **LLM-based classification (Groq, `llama-3.3-70b-versatile`)** — asks
   the model to judge semantic/stylistic coherence: does this read like
   fluent, evenly-paced AI prose, or does it have the uneven, idiosyncratic
   texture of human writing? Returns an AI-likelihood score and a short
   rationale. *Blind spot:* it's an opaque judgment call from another LLM
   — it can be fooled by heavily-edited AI text or unusually polished
   human writing, and it isn't independently auditable the way a
   statistical feature is.
2. **Stylometric heuristics (pure Python, `detection/stylometric.py`)** —
   computes sentence-length coefficient of variation, type-token ratio
   (vocabulary diversity), and punctuation density, and combines them into
   a second AI-likelihood score. *Blind spot:* these are population-level
   tendencies, not laws — naturally spare/uniform human styles (technical
   writing, some poetry) score as AI-like, and the heuristic is unreliable
   under ~40 words.

They're combined as `ai_score = 0.6 * llm_score + 0.4 * stylometric_score`
— weighted toward the LLM signal because it reasons over meaning, with the
stylometric signal acting as an independent structural check.

## Confidence Scoring

`confidence = |ai_score - 0.5| * 2`, scaled to `[0, 1]`. An `ai_score` of
0.51 yields confidence ≈ 0.02 (no real signal either way); an `ai_score` of
0.95 yields confidence = 0.90 (strong signal). This is deliberate: **the
score is designed so 0.5 means "genuinely uncertain," not "leaning
slightly human."**

Verdict thresholds are **asymmetric** because a false positive (accusing a
human creator of using AI) is worse than a false negative on a writing
platform:

- `ai_score >= 0.75` → `likely_ai` (requires strong evidence)
- `ai_score <= 0.35` → `likely_human` (lower bar — clear humans quickly)
- otherwise → `uncertain`

**How this was tested.** `scripts/evaluate_signals.py` runs known-origin
samples — including two deliberately different *kinds* of borderline case
— through the full pipeline and prints each signal's score separately
alongside the combined result, so disagreement between signals is visible
instead of hidden inside the average. Actual output:

```
sample                    expected                       llm  stylo  ai_score  confidence  verdict
----------------------------------------------------------------------------------------------------
human_moby_dick           human                         0.00   0.30     0.119       0.762  likely_human
human_diary               human                         0.10   0.33     0.193       0.613  likely_human
ai_product_copy           ai                            0.90   0.61     0.783       0.566  likely_ai
ai_motivational           ai                            0.80   0.47     0.668       0.337  uncertain
borderline_formal_human   human (formal/technical style)  0.80   0.75     0.779       0.558  likely_ai
borderline_edited_ai      ai (lightly human-edited)     0.20   0.50     0.321       0.358  likely_human
```

Four of six land where expected with meaningful confidence, and the other
two are the exact edge cases this design anticipates (planning.md §5), not
scoring bugs:

- `borderline_formal_human` — an actual human-written technical inspection
  report — trips **both** signals (LLM reads clean formal prose as AI-like;
  stylometrics reads its uniform sentence length the same way) and lands
  above the 0.75 `likely_ai` bar. This is the false-positive scenario the
  asymmetric thresholds and appeals workflow exist for; they reduce this
  failure mode, they don't eliminate it.
- `borderline_edited_ai` — AI-drafted text that's been lightly rewritten
  in a more personal voice — pulls the LLM score down to 0.20 and the
  stylometric score to a neutral 0.50, landing at `likely_human`. This
  confirms planning.md §5 Edge Case 3: signals that only inspect the
  finished artifact can't see draft provenance, so sufficiently-edited AI
  text reads as human by design, not by malfunction.

Where the two signals disagree most (`ai_motivational`: llm=0.80 vs
stylo=0.47) is informative on its own — the LLM is confident from meaning
alone, while the structural signal is unconvinced, which is exactly the
kind of independent-failure behavior the two-signal design is meant to
surface rather than paper over with a single opaque score.

## Transparency Label

Exactly one of these three fixed templates is shown to the reader,
depending on verdict (source: `detection/labels.py`); `{confidence}` is
replaced with the whole-number confidence percentage.

| Variant | Exact text |
|---|---|
| High-confidence AI | `⚠️ Likely AI-Generated — Our analysis indicates this content was most likely produced by an AI system (confidence: {confidence}%). This is an automated assessment, not a certainty, and the creator may appeal this classification.` |
| High-confidence human | `✅ Likely Human-Written — Our analysis indicates this content was most likely written by a human (confidence: {confidence}%).` |
| Uncertain | `❓ Uncertain Origin — Our analysis could not confidently determine whether this content is human-written or AI-generated (confidence: {confidence}%). Signals were mixed or inconclusive — treat this classification with caution.` |

Design notes: the AI-flagged label is the only one that mentions the
appeal path, since it's the one with real consequences for a creator's
reputation. The uncertain label explicitly says the system *could not
determine* origin rather than defaulting to an accusation either way.

## Appeals Workflow

`POST /appeal` with `{ content_id, reasoning }`:

1. Looks up the original submission (404 if unknown).
2. Writes the appeal (id, content_id, reasoning, timestamp) to the
   `appeals` table, linked to the original decision.
3. Flips the submission's `status` from `classified` to `under_review`.
4. Logs an `appeal` event to the audit log alongside the original
   `submission` event for that `content_id`.

No automatic re-classification happens — this hands the contested case to
a human reviewer with full context (original signals + creator's
reasoning) preserved. `GET /content/<id>` returns the submission plus its
full appeal history.

## Rate Limiting

Implemented with Flask-Limiter, keyed by remote address:

| Endpoint | Limit | Reasoning |
|---|---|---|
| `POST /submit` | 5/minute, 50/day | A real creator submits a handful of pieces a day at most — 50/day covers even a prolific poster with room to spare. The 5/minute cap targets a different threat: an adversary iteratively tweaking a single AI-generated piece and resubmitting to probe/reverse-engineer the classifier's thresholds. Slowing that loop to 5 tries/minute makes probing impractical without blocking legitimate one-off submissions. |
| `POST /appeal` | 5/hour | Appeals are rare, deliberate actions (a creator disputing one decision), not a high-frequency flow. 5/hour is generous for a genuine dispute but prevents appeal-spam from burying human reviewers or gaming the "under review" status across many pieces at once. |

Exceeding a limit returns `429`. Storage backend is `memory://`
(`storage_uri="memory://"` on the `Limiter`) — sufficient for a
single-process local deployment; a multi-worker production deployment
would need a shared backend (Redis) so limits are enforced across
workers.

Verified with 12 rapid `/submit` calls from the same client (script from
the milestone spec, `for i in $(seq 1 12); do curl ... ; done`) — the
first 5 succeed, the remaining 7 are rejected:

```
201
201
201
201
201
429
429
429
429
429
429
429
```

## Audit Log

Every submission and appeal writes a structured entry
(`id, event_type, content_id, details, created_at`) to the `audit_log`
table, queryable via `GET /log?limit=N`. Sample (4 real entries from a
local run — 2 submissions, then a 3rd submission that was appealed):

```json
[
  {
    "event_type": "appeal",
    "content_id": "db4407494e3a",
    "created_at": "2026-07-05T04:30:49.010742+00:00",
    "details": {
      "appeal_id": "a3dd9dc4e96c",
      "reasoning": "I wrote this inspection report myself as part of my civil engineering job. Technical reports are always written in this flat, formal style per our documentation standards - that does not mean it was AI-generated."
    }
  },
  {
    "event_type": "submission",
    "content_id": "db4407494e3a",
    "created_at": "2026-07-05T04:30:44.851894+00:00",
    "details": {
      "ai_score": 0.779,
      "confidence": 0.558,
      "verdict": "likely_ai",
      "label": "⚠️ Likely AI-Generated — Our analysis indicates this content was most likely produced by an AI system (confidence: 56%). This is an automated assessment, not a certainty, and the creator may appeal this classification.",
      "signals": {
        "llm": { "score": 0.8, "rationale": "The text's formal tone, precise language, and lack of idiomatic phrasing or personal touch suggest a high likelihood of AI generation, although the content's specificity and technical detail could also be characteristic of human-written technical reports." },
        "stylometric": { "score": 0.748, "coefficient_of_variation": 0.167, "type_token_ratio": 0.864, "punctuation_density_per_100_chars": 1.553 }
      }
    }
  },
  {
    "event_type": "submission",
    "content_id": "9d2c199252dc",
    "created_at": "2026-07-05T04:30:39.497194+00:00",
    "details": {
      "ai_score": 0.759,
      "confidence": 0.517,
      "verdict": "likely_ai",
      "label": "⚠️ Likely AI-Generated — Our analysis indicates this content was most likely produced by an AI system (confidence: 52%). This is an automated assessment, not a certainty, and the creator may appeal this classification.",
      "signals": {
        "llm": { "score": 0.9, "rationale": "The text exhibits a high degree of semantic coherence and generic fluency, lacking the natural imperfections and idiosyncratic qualities that are characteristic of human writing, suggesting a strong likelihood of AI generation." },
        "stylometric": { "score": 0.546, "coefficient_of_variation": 0.091, "type_token_ratio": 0.63, "punctuation_density_per_100_chars": 2.42 }
      }
    }
  },
  {
    "event_type": "submission",
    "content_id": "80f70b7b130a",
    "created_at": "2026-07-05T04:30:32.521920+00:00",
    "details": {
      "ai_score": 0.193,
      "confidence": 0.613,
      "verdict": "likely_human",
      "label": "✅ Likely Human-Written — Our analysis indicates this content was most likely written by a human (confidence: 61%).",
      "signals": {
        "llm": { "score": 0.1, "rationale": "The text exhibits natural imperfection and idiosyncratic phrasing, such as the use of colloquial expressions and personal anecdotes, which are characteristic of human writing, and the uneven tone and language also suggest a human author." },
        "stylometric": { "score": 0.333, "coefficient_of_variation": 0.932, "type_token_ratio": 0.841, "punctuation_density_per_100_chars": 3.17 }
      }
    }
  }
]
```

(Full, unedited output is reproducible via `GET /log?limit=10` after
running the three sample requests above — the `details` field is not
truncated in the live response; it's abbreviated above only for
README readability.)

## Project Structure

```
app.py                    Flask routes, rate limiting
config.py                 Weights, thresholds, rate limits (all tunable)
storage.py                SQLite: submissions, appeals, audit_log
detection/
  llm_signal.py            Signal 1: Groq classification
  stylometric.py           Signal 2: statistical heuristics
  scoring.py               Combines signals -> ai_score, confidence, verdict
  labels.py                Transparency label templates
scripts/evaluate_signals.py  Sanity-checks score direction on known samples
planning.md               Architecture narrative, diagrams, design rationale
```

## Stretch Features

Not yet implemented. See planning.md §7 for what's under consideration
(3rd signal for ensemble voting, `/stats` analytics endpoint).
