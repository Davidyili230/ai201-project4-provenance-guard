# Planning: Provenance Guard

## 1. Architecture Narrative

A creator submits a piece of text-based content (poem, story excerpt, blog
post) to `POST /submit`. From there it flows through a fixed pipeline:

1. **API layer (Flask)** receives the raw text, validates it (non-empty,
   under a max length, applies rate limiting), and hands it to the
   detection pipeline. If the caller has exceeded their rate limit, the
   request is rejected here with `429` before any detection work happens.
2. **Signal 1 — LLM classifier (Groq, `llama-3.3-70b-versatile`)** reads the
   full text and returns a structured judgment: an AI-likelihood score in
   `[0, 1]` plus a short natural-language rationale. This captures
   *semantic and holistic* properties of the writing — coherence, cliché
   density, "does this sound like a person talking" — that only a model
   with broad language understanding can judge.
3. **Signal 2 — stylometric heuristics (pure Python)** independently
   computes statistical features of the same text: sentence-length
   variability, vocabulary diversity (type-token ratio), and punctuation
   density. These are combined into a second, independent AI-likelihood
   score in `[0, 1]`. This captures *structural* properties — it doesn't
   read the text for meaning at all, it measures its shape.
4. **Scoring engine** combines the two signal scores with fixed weights
   into a single `ai_score`, derives a `confidence` value from how far that
   score sits from the undecided midpoint (0.5), and — using thresholds
   that are deliberately asymmetric to protect human creators from false
   accusations — assigns one of three verdicts: `likely_ai`,
   `likely_human`, or `uncertain`.
5. **Label generator** maps the verdict + confidence to one of three fixed
   transparency-label templates (see README) and fills in the confidence
   percentage.
6. **Audit log** — before the response is returned, a structured record
   (timestamp, content id, both raw signal scores + their inputs, combined
   score, confidence, verdict, label text) is written to SQLite.
7. **Response** — the API returns the verdict, confidence, label text, and
   a `content_id` the creator can later use to file an appeal.

If a creator disputes the verdict, they call `POST /appeal` with the
`content_id` and their reasoning. The appeal is logged (linked to the
original decision) and the submission's `status` is flipped from
`classified` to `under_review`. No automatic re-scoring happens — a human
appeals reviewer is assumed to pick it up from there.

Every decision (submission or appeal) is queryable via `GET /log`, and a
single submission's full history (original decision + any appeals) via
`GET /content/<id>`.

## 2. Detection Signals

### Signal 1 — LLM-based holistic classification (Groq)
- **What it measures:** Whether the text *reads* like something a person
  would write — semantic coherence, natural imperfection, idiomatic
  phrasing, whether ideas develop the way human thought does, versus the
  smoothed-over, generically well-structured quality common in
  LLM-generated prose.
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
  the text: (a) coefficient of variation of sentence length (a measure of
  how *irregular* sentence lengths are), (b) type-token ratio (vocabulary
  diversity — unique words / total words), (c) punctuation density
  (punctuation marks per 100 characters).
- **Why it differs between human/AI text:** AI-generated text tends toward
  uniform sentence length and rhythm (low variance), and comparatively flat
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

**Why these two together are stronger than either alone:** they are
computed from disjoint information — one reads for meaning, the other
measures shape — so they fail independently. A text engineered to fool one
(e.g., heavily paraphrased AI text with varied sentence length) still has
to get past the other (it may still read as generically fluent to the LLM
judge).

## 3. False-Positive Scenario (drives the scoring design)

**Scenario:** A human writer with a very controlled, even prose style
(common in some literary fiction, technical blogging, or ESL writers who
learned formal written English) submits an excerpt. Their sentence lengths
are unusually uniform and their vocabulary is used consistently — the
stylometric signal leans AI. The LLM signal, reading clean and
well-structured prose, is lukewarm and slightly leans AI too, but without
strong conviction.

**How the system is designed to handle this:**
- The combined `ai_score` lands in the 0.5–0.7 range — elevated, but not
  past the (deliberately high) 0.75 threshold required to declare
  `likely_ai`. See §4 for why that threshold is set where it is.
- The verdict is `uncertain`, not `likely_ai`, and confidence is reported
  as low/moderate — the label explicitly says the system *could not
  confidently determine* origin, rather than accusing the creator.
- The creator sees a label that names the uncertainty instead of a false
  accusation, and can still file an appeal that puts a human in the loop
  and flips status to `under_review`, with their reasoning preserved
  alongside the original signal breakdown in the audit log.

This scenario is why the classification thresholds are **asymmetric**
rather than a simple `>0.5 → AI` split (see §4).

## 4. Confidence Scoring Design

- Both signals independently output an "AI-likelihood" score in `[0, 1]`.
- Combined score: `ai_score = 0.6 * llm_score + 0.4 * stylometric_score`.
  The LLM signal is weighted higher because it reasons over meaning
  (harder to game by simply varying sentence length), while the
  stylometric signal acts as an independent structural check that can
  pull the score down when the LLM is overconfident on borderline text.
- `confidence = abs(ai_score - 0.5) * 2`, i.e., how far the combined score
  sits from the maximally-uncertain midpoint, scaled to `[0, 1]`. A score
  of 0.51 yields confidence ≈ 0.02 (essentially "we have no idea"); a
  score of 0.95 yields confidence = 0.90 ("very sure").
- **Verdict thresholds are intentionally asymmetric**, because a false
  positive (calling a human's work AI-generated) is more damaging to a
  creator than a false negative (missing AI-generated content):
  - `ai_score >= 0.75` → `likely_ai` (requires strong evidence before
    accusing a creator)
  - `ai_score <= 0.35` → `likely_human` (a lower bar — we'd rather clear
    a human quickly than hold them to the same evidentiary standard)
  - otherwise → `uncertain`
- This means the band of "uncertain" (0.35–0.75) is wide and deliberately
  skewed toward `likely_human`'s side being easier to reach — see README
  for how this was validated against sample texts.

## 5. API Surface

| Endpoint | Method | Body | Returns |
|---|---|---|---|
| `/submit` | POST | `{ "content": str, "creator_id": str? }` | `{ content_id, ai_score, confidence, verdict, label, signals, status }` |
| `/appeal` | POST | `{ "content_id": str, "reasoning": str }` | `{ content_id, status: "under_review", appeal_id }` |
| `/content/<id>` | GET | — | full submission record + appeal history |
| `/log` | GET | `?limit=N` | list of audit log entries, newest first |
| `/health` | GET | — | `{ status: "ok" }` (liveness check, not rate-limited) |

## 6. Architecture Diagram

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

## 7. Stretch Features Considered

- **Ensemble detection (3+ signals):** could add a third structural signal
  (e.g., perplexity/burstiness via a local n-gram model) for a majority
  vote instead of a two-signal weighted average. Not started yet.
- **Analytics dashboard:** a `GET /stats` view aggregating verdict
  distribution and appeal rate from the audit log. Not started yet.

(Planning entries for stretch features will be updated here before each is
started, per the assignment instructions.)
