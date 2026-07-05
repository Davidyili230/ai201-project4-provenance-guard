# Planning: Provenance Guard

## 1. Detection Signals

### Signal 1 — LLM-based holistic classification (Groq)
- **What it measures:** Whether the text *reads* like something a person
  would write — semantic coherence, natural imperfection, idiomatic
  phrasing, whether ideas develop the way human thought does, versus the
  smoothed-over, generically well-structured quality common in
  LLM-generated prose.
- **Output shape:** A single float in `[0, 1]` (`llm_score`, "AI-likelihood")
  plus a short natural-language `rationale` string. The model
  (`llama-3.3-70b-versatile` via Groq) is prompted to return this score
  directly, not derived from some other measurement.
- **Why it differs between human/AI text:** LLMs are trained to produce
  fluent, well-formed, low-surprise continuations. Human writing is
  "lumpier" — digressions, uneven pacing, idiosyncratic word choice,
  sometimes grammatically imperfect but semantically alive.
- **Blind spot:** It's a single opaque judgment from another LLM — it can
  be confidently wrong, it can be fooled by heavily-edited AI text or by
  unusually polished/formal human writing (technical writers, ESL writers
  following textbook grammar), and it gives no verifiable mechanism beyond
  "trust the model." It also can't be audited the way a statistical
  feature can — we can't point to *which words* triggered it.

### Signal 2 — Stylometric heuristics (pure Python)
- **What it measures:** Three structural features computed directly from
  the text: (a) coefficient of variation of sentence length (how
  *irregular* sentence lengths are), (b) type-token ratio (vocabulary
  diversity — unique words / total words), (c) punctuation density
  (punctuation marks per 100 characters).
- **Output shape:** Each feature is normalized into a `[0, 1]` sub-score
  (`sentence_uniformity`, `vocabulary_diversity`, `punctuation_sparsity`),
  then averaged into a single `stylo_score` in `[0, 1]`. The feature
  breakdown (raw stats + sub-scores + a `low_sample_warning` flag for
  texts under ~40 words) is returned alongside the score for auditability.
- **Why it differs between human/AI text:** AI-generated text tends toward
  uniform sentence length and rhythm (low variance) and comparatively flat
  punctuation usage. Human writing tends to vary sentence length more
  (short punchy sentences next to long winding ones) and use punctuation
  (dashes, ellipses, semicolons) more idiosyncratically.
- **Blind spot:** These are population-level tendencies, not laws — poetry
  and short-form writing can be naturally uniform (a sonnet has a meter!),
  a human author with a spare, minimalist style can score as "AI-like,"
  and the heuristic is unreliable on very short texts (under ~40 words)
  where sample size is too small for variance/TTR to mean anything. It
  also has zero understanding of *meaning*, so it cannot catch content
  that is semantically nonsensical but structurally "human-shaped."

### Combining the two into one score
Both signals independently output a continuous AI-likelihood score in
`[0, 1]` — there are no binary flags anywhere in the pipeline. They are
combined with **fixed weights**:

```
ai_score = 0.6 * llm_score + 0.4 * stylometric_score
```

The LLM signal is weighted higher because it reasons over meaning (harder
to game by simply varying sentence length), while the stylometric signal
acts as an independent structural check that can pull the score down when
the LLM is overconfident on borderline text.

**Why these two together are stronger than either alone:** they are
computed from disjoint information — one reads for meaning, the other
measures shape — so they fail independently. A text engineered to fool one
(e.g., heavily paraphrased AI text with varied sentence length) still has
to get past the other (it may still read as generically fluent to the LLM
judge).

## 2. Uncertainty Representation & Confidence Scoring

- Both signals independently output an "AI-likelihood" score in `[0, 1]`;
  no separate calibration step is applied to either raw signal — the LLM
  is prompted to emit its score already normalized to `[0, 1]`, and the
  stylometric sub-scores are normalized by the linear clamps in
  `detection/stylometric.py` (e.g. `cv_score = clamp(1 - cv / 0.7)`).
  Calibration instead happens at the *combination* step, described below.
- `confidence = abs(ai_score - 0.5) * 2`, i.e., how far the combined score
  sits from the maximally-uncertain midpoint, scaled to `[0, 1]`. **What a
  score means, concretely:**
  - `confidence = 0.0` (`ai_score = 0.5`): the two signals cancel out —
    the system has *no* usable signal either way.
  - `confidence ≈ 0.6` (`ai_score ≈ 0.2` or `≈ 0.8`): a moderately strong
    lean — enough to cross a verdict threshold, but the label still reads
    as a probabilistic assessment, not a certainty.
  - `confidence = 1.0` (`ai_score = 0.0` or `1.0`): both signals agree
    completely at the extreme.
  - This is deliberate: **0.5 is defined as "genuinely uncertain," not
    "leaning slightly human."** A score of 0.51 yields confidence ≈ 0.02
    ("we have no idea"); a score of 0.95 yields confidence = 0.90 ("very
    sure").
- **Verdict thresholds are intentionally asymmetric**, because a false
  positive (calling a human's work AI-generated) is more damaging to a
  creator than a false negative (missing AI-generated content):
  - `ai_score >= 0.75` → `likely_ai` (requires strong evidence before
    accusing a creator)
  - `ai_score <= 0.35` → `likely_human` (a lower bar — we'd rather clear
    a human quickly than hold them to the same evidentiary standard)
  - otherwise → `uncertain`
- This means the band of "uncertain" (0.35–0.75) is wide and deliberately
  skewed toward `likely_human`'s side being easier to reach. Validated
  against sample texts of known origin — see `scripts/evaluate_signals.py`
  and the README's "How this was tested" table.

## 3. Transparency Label Design

Exactly one of three fixed templates (`detection/labels.py`) is shown to
the reader. `{confidence}` is replaced with the whole-number confidence
percentage — never the raw `ai_score`, since the score's direction
(toward AI or toward human) is already baked into which template is
chosen.

| Variant | Exact text |
|---|---|
| High-confidence AI (`likely_ai`) | `⚠️ Likely AI-Generated — Our analysis indicates this content was most likely produced by an AI system (confidence: {confidence}%). This is an automated assessment, not a certainty, and the creator may appeal this classification.` |
| High-confidence human (`likely_human`) | `✅ Likely Human-Written — Our analysis indicates this content was most likely written by a human (confidence: {confidence}%).` |
| Uncertain (`uncertain`) | `❓ Uncertain Origin — Our analysis could not confidently determine whether this content is human-written or AI-generated (confidence: {confidence}%). Signals were mixed or inconclusive — treat this classification with caution.` |

**Design notes:**
- The AI-flagged label is the *only* one that mentions the appeal path,
  since it's the one with real reputational consequences for a creator —
  the other two variants don't need to advertise a remedy for a decision
  that isn't accusing anyone of anything.
- The uncertain label explicitly states the system *could not determine*
  origin rather than defaulting to an accusation either way — it names
  the uncertainty instead of hiding it behind a forced binary choice.
- The human-written label carries no hedging language about appeals or
  caveats — a confident, low-friction "this is fine" result shouldn't read
  as suspicious.
- All three variants always show a confidence percentage, even when it's
  low — hiding a low confidence number would be a transparency failure
  disguised as UX polish.

## 4. Appeals Workflow

- **Who can submit an appeal:** Any party holding a valid `content_id`
  (the reader, the platform, or the creator) — the current implementation
  does not check that the appellant is the original `creator_id`. This is
  a known, deliberate scope cut for this project, not an oversight —
  requiring the appellant to match `creator_id` (or hold some other
  credential) is the obvious hardening step before this could be used in
  production, listed under §7 Stretch Features.
- **What information they provide:** `content_id` (which submission is
  being disputed) and `reasoning` (free-text explanation of why the
  verdict is believed wrong) via `POST /appeal`.
- **What the system does on receipt:**
  1. Looks up the original submission by `content_id` — `404` if unknown.
  2. Writes a new row to the `appeals` table: `id`, `content_id`,
     `reasoning`, `created_at`, linked by foreign key to the original
     submission.
  3. Flips the submission's `status` column from `classified` to
     `under_review` — this is the only state transition in the system;
     there is no automatic re-scoring.
  4. Writes an `appeal` event to the `audit_log`, alongside the original
     `submission` event for that same `content_id`, so both live in the
     same append-only history.
  5. Returns `{ appeal_id, content_id, status: "under_review", created_at }`
     to the caller.
- **What a human reviewer sees when they open the appeal queue:**
  `GET /content/<content_id>` returns the full record needed to adjudicate
  a single case without re-deriving anything: the original text, both raw
  signal outputs (LLM rationale + stylometric feature breakdown), the
  computed `ai_score`/`confidence`/`verdict`/`label`, current `status`, and
  the full list of appeals with their `reasoning` and timestamps, ordered
  oldest-first. There is no dedicated "list all under_review submissions"
  endpoint yet — a reviewer currently has to know the `content_id` (e.g.
  from a support ticket) or scan `GET /log` for `appeal` events and follow
  each one to its `content_id`. A `GET /appeals?status=under_review`
  listing endpoint is the natural next addition (see §7 Stretch Features).

## 5. Anticipated Edge Cases

**Edge case 1 — the controlled/uniform human writer (drives the
asymmetric thresholds).** A human writer with a very controlled, even
prose style (literary fiction, technical blogging, or an ESL writer who
learned formal written English) submits an excerpt. Sentence lengths are
unusually uniform and vocabulary is used consistently — the stylometric
signal leans AI. The LLM signal, reading clean and well-structured prose,
leans AI too, without strong conviction. *This is not hypothetical*: it
was reproduced during testing (`borderline_formal_human` in
`scripts/evaluate_signals.py` — a real human-written technical inspection
report scored `ai_score = 0.779`, above the 0.75 `likely_ai` threshold).
Mitigation: asymmetric thresholds keep most such cases in `uncertain`
rather than `likely_ai`, but they don't eliminate the false positive
entirely — the label always names the uncertainty and the appeal path is
the real backstop for the cases that do cross the line.

**Edge case 2 — short-form or repetition-heavy poetry.** A haiku, a
villanelle, or a children's rhyme that leans on refrain and simple,
repeated vocabulary as a deliberate artistic device. Under ~40 words, the
stylometric signal's statistics (coefficient of variation, type-token
ratio) are computed from too small a sample to mean anything, and
intentional repetition looks identical to the "flat, uniform" signature
the heuristic associates with AI text. The code already detects this
(`low_sample_warning: word_count < 40` in `detection/stylometric.py`) but
**does not currently act on it** — the warning is returned in the feature
breakdown for auditability, but `detection/scoring.py` does not lower
confidence or otherwise flag the verdict when it's set. This is a known
gap: the system will confidently mislabel very short creative text today.

**Edge case 3 — a heavily human-edited or paraphrased AI draft.** A writer
uses an LLM for a first draft, then substantially rewrites it — varying
sentence length, injecting idiosyncratic word choice, breaking up the
"smoothed" rhythm. This defeats the stylometric signal (which now reads
as human-shaped) and weakens the LLM judge's read too, since the surface
markers it was trained to notice have been edited away, even though the
ideas and structure originated with an AI. The system has no mechanism to
detect provenance *history* — only the properties of the final text — so
this content will likely land as `likely_human` or `uncertain`. This is a
fundamental limitation of any signal that only inspects the finished
artifact, not a bug to fix within this design.

## 6. API Surface

| Endpoint | Method | Body | Returns |
|---|---|---|---|
| `/submit` | POST | `{ "content": str, "creator_id": str? }` | `{ content_id, ai_score, confidence, verdict, label, signals, status }` |
| `/appeal` | POST | `{ "content_id": str, "reasoning": str }` | `{ content_id, status: "under_review", appeal_id }` |
| `/content/<id>` | GET | — | full submission record + appeal history |
| `/log` | GET | `?limit=N` | list of audit log entries, newest first |
| `/health` | GET | — | `{ status: "ok" }` (liveness check, not rate-limited) |

## Architecture

A creator submits text to `POST /submit`. The Flask API validates and
rate-limits the request, then runs it through two independent detection
signals in parallel-in-spirit (sequentially in code): an LLM holistic
judgment and a stylometric heuristic. Their scores are combined into a
single `ai_score`, from which a `confidence` and one of three verdicts is
derived, a transparency label is rendered, and the full decision is
written to an append-only audit log before the response returns. A
disputed verdict goes through `POST /appeal`, which links a reasoning
string to the original decision, flips `status` to `under_review`, and
logs the dispute — no automatic re-scoring, by design; a human reviewer is
assumed to pick it up from there using `GET /content/<id>`.

### Submission flow
```
                POST /submit { content, creator_id }
                        │
                        ▼
              ┌───────────────────┐
              │   Flask API layer  │  ← Flask-Limiter checks rate limit
              │   (app.py)          │     (429 if exceeded, pipeline never runs)
              └─────────┬──────────┘
                        │ raw text
          ┌─────────────┼──────────────┐
          ▼                             ▼
┌─────────────────────┐      ┌───────────────────────┐
│ Signal 1: LLM        │      │ Signal 2: Stylometric  │
│ (Groq llama-3.3-70b) │      │ heuristics (pure Py)   │
│ → llm_score [0,1]    │      │ → stylo_score [0,1]    │
│ → rationale text     │      │ → feature breakdown    │
└──────────┬───────────┘      └───────────┬───────────┘
           │ llm_score                     │ stylo_score
           └───────────────┬───────────────┘
                            ▼
                ┌───────────────────────┐
                │   Scoring engine        │
                │  ai_score = 0.6·llm     │
                │           + 0.4·stylo   │
                │  confidence = |s-.5|·2  │
                │  verdict via thresholds │
                └───────────┬─────────────┘
                            │ ai_score, confidence, verdict
                            ▼
                ┌───────────────────────┐
                │   Label generator       │
                │  → transparency label   │
                │    text (one of 3)      │
                └───────────┬─────────────┘
                            │ full decision record
                            ▼
                ┌───────────────────────┐
                │   Audit log (SQLite)    │
                │  INSERT decision row    │
                └───────────┬─────────────┘
                            ▼
              Response: { content_id, ai_score,
               confidence, verdict, label, signals }
```

### Appeal flow
```
        POST /appeal { content_id, reasoning }
                       │
                       ▼
             ┌───────────────────┐
             │  Flask API layer    │  ← Flask-Limiter (stricter limit)
             └─────────┬──────────┘
                       │ content_id, reasoning
                       ▼
             ┌───────────────────┐
             │  Lookup submission  │  ← 404 if content_id unknown
             │  by content_id       │
             └─────────┬──────────┘
                       │ found
                       ▼
             ┌───────────────────┐
             │  Update status:     │
             │  classified →       │
             │  under_review       │
             └─────────┬──────────┘
                       │
                       ▼
             ┌───────────────────┐
             │  Audit log (SQLite) │
             │  INSERT appeal row  │
             │  (linked to         │
             │   original decision)│
             └─────────┬──────────┘
                       ▼
        Response: { content_id, status: "under_review",
                     appeal_id }
```

## AI Tool Plan

How this spec gets handed to an AI coding tool across the three
implementation milestones — each step names exactly which spec sections
go in the prompt, what's requested, and how the output gets checked before
it's trusted.

**M3 — submission endpoint + first signal.**
- *Sections provided:* §1 Detection Signals (Signal 1 subsection only) +
  the Architecture diagram's submission flow.
- *What's requested:* a Flask app skeleton (`app.py`, `config.py`) with a
  `POST /submit` route that validates input length and rate-limits, plus
  `detection/llm_signal.py`'s `analyze(text) -> (score, features)`
  function implementing the Groq call described in §1.
- *How it's verified:* call `llm_signal.analyze()` directly from a Python
  shell (no server, no rate limiting in the way) on 2–3 hand-picked
  inputs — one obviously AI-sounding, one obviously human, one ambiguous —
  and confirm the score moves in the expected direction and stays inside
  `[0, 1]` *before* wiring it into the endpoint. Only after that passes
  does `/submit` get exercised end-to-end with `curl`.

**M4 — second signal + confidence scoring.**
- *Sections provided:* §1 Detection Signals (Signal 2 + the combination
  formula) + §2 Uncertainty Representation & Confidence Scoring + the
  Architecture diagram.
- *What's requested:* `detection/stylometric.py`'s `analyze(text)`
  function implementing the three structural features, plus
  `detection/scoring.py`'s `classify(text)` that combines both signals per
  the exact weights and threshold values in §2.
- *How it's checked:* run `scripts/evaluate_signals.py` (or an equivalent
  ad hoc script) over a small set of known-origin samples and confirm two
  things independently — (a) scores meaningfully separate clearly-AI text
  from clearly-human text (not clustered near 0.5 for everything), and (b)
  the three verdict bands are all actually reachable given the configured
  thresholds, not just two of them.

**M5 — production layer (labels + appeals).**
- *Sections provided:* §3 Transparency Label Design + §4 Appeals Workflow
  + the Architecture diagram (appeal flow).
- *What's requested:* `detection/labels.py`'s `render_label(verdict,
  confidence)` implementing the three exact templates from §3 verbatim,
  and the `POST /appeal` route plus `storage.py` functions for the
  `appeals` table and the `status` transition described in §4.
- *How it's verified:* force each of the three verdict bands (by adjusting
  thresholds temporarily or picking inputs known to land in each band) and
  confirm all three label variants render with the correct emoji, wording,
  and confidence percentage, and that only the `likely_ai` variant
  mentions appeals. Then submit a real appeal against a live
  `content_id` and confirm via `GET /content/<id>` that `status` flipped
  to `under_review` and the appeal's `reasoning` is present in the
  returned appeal history.

## 7. Stretch Features Considered

- **Ensemble detection (3+ signals):** could add a third structural signal
  (e.g., perplexity/burstiness via a local n-gram model) for a majority
  vote instead of a two-signal weighted average. Not started yet.
- **Analytics dashboard:** a `GET /stats` view aggregating verdict
  distribution and appeal rate from the audit log. Not started yet.
- **Appeal queue listing:** a `GET /appeals?status=under_review` endpoint
  so a human reviewer doesn't have to derive the queue from `GET /log`
  (see §4). Not started yet.
- **Low-sample confidence penalty:** wire the existing
  `low_sample_warning` flag (§5 Edge Case 2) into `detection/scoring.py`
  so very short submissions are pushed toward `uncertain` regardless of
  raw score. Not started yet.
- **Appellant authentication:** require the appeal request to match the
  original `creator_id` (see §4) instead of accepting any `content_id`
  holder's appeal. Not started yet.

(Planning entries for stretch features will be updated here before each is
started, per the assignment instructions.)
