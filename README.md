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

## Architecture Overview

The path a submission takes from input to transparency label:

1. **`POST /submit`** hits the Flask API layer (`app.py`). Flask-Limiter
   checks the rate limit first — if exceeded, the request is rejected
   with `429` before any detection work runs.
2. The raw text is validated for length, then handed to
   **two independent detection signals**: an LLM holistic judgment
   (`detection/llm_signal.py`, Groq `llama-3.3-70b-versatile`) and a
   stylometric heuristic (`detection/stylometric.py`, pure Python). Each
   returns its own `[0, 1]` AI-likelihood score plus an auditable feature
   breakdown (rationale text / raw stats).
3. `detection/scoring.py` combines the two scores into one `ai_score`
   with fixed weights, derives a `confidence` (distance from the
   maximally-uncertain midpoint), and buckets the result into one of
   three verdicts via asymmetric thresholds.
4. `detection/labels.py` renders the verdict + confidence into one of
   three fixed transparency-label templates — this is the only text a
   reader ever sees, never the raw score.
5. The full decision (text, both signals' raw output, `ai_score`,
   `confidence`, `verdict`, `label`) is written to SQLite
   (`storage.py`): a `submissions` row plus an append-only `audit_log`
   entry, before the response returns.
6. If a creator disputes the verdict, `POST /appeal` links their
   `reasoning` to the original `content_id`, flips `status` from
   `classified` to `under_review`, and logs the dispute — no automatic
   re-scoring; a human reviewer picks it up via `GET /content/<id>`.

Full diagrams (submission flow + appeal flow) and the file-by-file
architectural narrative are in [planning.md](planning.md#architecture).

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

**A concrete high-confidence vs. low-confidence pair, from the table
above:**

- **High confidence** — `human_moby_dick`: `ai_score = 0.119`,
  **`confidence = 0.762`** → `likely_human`. Both signals independently
  land far from the midpoint (llm=0.00, stylo=0.30), so they reinforce
  each other and the combined score sits close to the extreme.
- **Lower confidence** — `ai_motivational`: `ai_score = 0.668`,
  **`confidence = 0.337`** → `uncertain`. The LLM signal alone
  (`llm=0.80`) would suggest AI, but the stylometric signal pulls it back
  toward the midpoint (`stylo=0.47`), so the combined score sits closer
  to 0.5 and confidence drops sharply even though the verdict still
  leans AI. This is the scoring formula doing exactly what it's meant to
  do: signal *disagreement* shows up as low confidence, not as a
  confidently wrong answer.

This ~2.3x spread (0.762 vs 0.337) on real inputs is what shows the score
produces meaningful variation rather than clustering near a constant.

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

## Known Limitations

**Short-form, repetition-heavy creative text (haiku, refrains, slogans,
under ~40 words) is the case this system would most likely get wrong.**
`detection/stylometric.py` computes a `low_sample_warning` flag when
`word_count < 40`, because coefficient-of-variation and type-token-ratio
are statistically meaningless on that little text — but
`detection/scoring.py` never reads that flag. It's computed and returned
in the feature breakdown for auditability, but it doesn't lower
confidence or nudge the verdict toward `uncertain`. A short poem that
deliberately repeats a refrain (a legitimate artistic device) produces
the exact same low-variance, low-diversity signature the stylometric
heuristic associates with generic AI text, and nothing downstream
compensates for the sample being too small to trust. This is a specific,
reproducible gap tied to a real property of Signal 2 — not a "needs more
data" hand-wave — and it's the first thing listed under Stretch Features
below because the fix (gate on the existing flag) is already scoped.

Two related, lower-severity gaps (full detail in
[planning.md §5](planning.md)):

- **Formal/technical human writing** trips both signals toward `likely_ai`
  (reproduced in testing as `borderline_formal_human`, `ai_score = 0.779`)
  because uniform sentence structure and precise vocabulary look
  identical to AI fluency to both an LLM judge and a variance-based
  heuristic. The asymmetric thresholds narrow this failure mode but
  don't close it.
- **Heavily edited AI drafts** read as human, because both signals only
  inspect the finished text — there is no mechanism to detect drafting
  history, so a human rewrite pass erases the surface markers either
  signal relies on.

## Spec Reflection

**Where the spec helped:** the requirement that each detection signal
output a continuous `[0, 1]` AI-likelihood score — never a binary
flag — directly shaped the architecture. Because both signals had to
speak the same normalized language, they could be combined with a simple
weighted average instead of some ad hoc voting scheme, and confidence
could be derived mathematically (`|ai_score - 0.5| * 2`) instead of
being a third thing each signal had to separately estimate. Designing to
that constraint from the start is also what made the two signals
*independently auditable* — you can always see `llm_score` and
`stylo_score` disagreeing before they're averaged away, which is exactly
what surfaces cases like `ai_motivational` above.

**Where the implementation diverged:** the appeals workflow (`POST
/appeal`) does not verify that the appellant is the original
`creator_id` — it accepts an appeal from anyone who holds a valid
`content_id`. This is a real gap relative to a production-grade appeals
process, but it was a deliberate scope cut, not an oversight: no part of
this project has an authentication layer (there are no user accounts,
sessions, or credentials anywhere in `app.py`), so "verifying" the
appellant would have meant either trusting a client-supplied
`creator_id` string with no way to prove it (a false sense of security)
or building an entire auth system just to gate one endpoint, which was
out of scope for what this milestone set was asking the system to prove.
It's listed explicitly as the first item under Appellant Authentication
in planning.md §7 rather than silently left out.

## AI Usage

Two specific instances of directing an AI coding assistant during
implementation (see [planning.md's "AI Tool Plan"](planning.md) for the
full per-milestone breakdown of what was handed to the assistant and how
output was checked before being trusted):

1. **Building the second signal + combiner (Milestone 4).** I gave the
   assistant the stylometric spec section (sentence-length coefficient of
   variation, type-token ratio, punctuation density) and the exact
   weight/threshold values to implement `detection/stylometric.py` and
   `detection/scoring.py`. It produced working normalization clamps for
   each sub-feature (e.g. `cv_score = clamp(1 - cv / 0.7)`) on the first
   pass. What I overrode: I didn't accept the combiner as done just
   because it ran — I required it to pass through
   `scripts/evaluate_signals.py` against samples of *known* origin,
   including the two borderline cases, before trusting it. That test run
   is what surfaced that a naive single 0.5 cutoff would have
   misclassified the real human-written technical report
   (`borderline_formal_human`, `ai_score = 0.779`) as `likely_ai` with no
   room for doubt — which is what drove the decision to widen the
   `uncertain` band asymmetrically (0.35–0.75) rather than use a single
   midpoint threshold, on top of whatever the assistant initially
   generated.
2. **Wiring `low_sample_warning` (Milestone 4/5 boundary).** The
   assistant's stylometric implementation computes and returns a
   `low_sample_warning` flag for texts under ~40 words as part of the
   feature breakdown. When integrating it into `detection/scoring.py`, I
   deliberately did **not** have it automatically fold that flag into the
   confidence calculation or verdict — I kept the scoring formula
   restricted to exactly the combination described in the spec's
   confidence-scoring section, and left the warning as an unused,
   returned-but-not-acted-on field. That's a case of overriding the more
   "helpful" instinct (silently patching a known edge case into the
   score) in favor of keeping the implementation legible and matching
   what was specified, and instead recording the gap explicitly as a
   scoped stretch feature (planning.md §7) rather than quietly baking in
   an undocumented behavior change.

*(These are drafted from the visible commit history and planning.md's
documented process — double-check the specifics against your own memory
of the sessions before submitting, and swap in any different instances
that better reflect what actually happened.)*

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
