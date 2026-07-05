"""Signal 2: stylometric heuristics.

Pure-Python statistical features computed directly from the text's shape,
independent of what it means. See planning.md section 2 for the rationale
and documented blind spots of each feature.
"""

import re
import statistics

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[A-Za-z']+")
_PUNCT_RE = re.compile(r"[.,;:!?\-—–()\"']")


def _clamp(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, value))


def _sentences(text):
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    return sentences or [text.strip()]


def analyze(text):
    """Return (ai_likelihood_score, feature_breakdown) for the given text."""
    sentences = _sentences(text)
    words = _WORD_RE.findall(text)
    word_count = len(words) or 1

    sentence_lengths = [len(_WORD_RE.findall(s)) or 1 for s in sentences]
    mean_len = statistics.mean(sentence_lengths)
    stdev_len = statistics.pstdev(sentence_lengths) if len(sentence_lengths) > 1 else 0.0
    coefficient_of_variation = stdev_len / mean_len if mean_len else 0.0

    unique_words = {w.lower() for w in words}
    type_token_ratio = len(unique_words) / word_count

    punct_count = len(_PUNCT_RE.findall(text))
    punctuation_density = (punct_count / len(text)) * 100 if text else 0.0

    # Each sub-score estimates "how AI-like is this feature" on [0, 1].
    # Low sentence-length variation -> more AI-like (models tend to produce
    # evenly paced sentences).
    cv_score = _clamp(1 - coefficient_of_variation / 0.7)
    # High, very consistent vocabulary diversity -> more AI-like.
    ttr_score = _clamp((type_token_ratio - 0.4) / 0.4)
    # Sparse punctuation variety -> more AI-like.
    punct_score = _clamp(1 - punctuation_density / 3.0)

    ai_score = statistics.mean([cv_score, ttr_score, punct_score])

    features = {
        "word_count": word_count,
        "sentence_count": len(sentences),
        "mean_sentence_length": round(mean_len, 2),
        "coefficient_of_variation": round(coefficient_of_variation, 3),
        "type_token_ratio": round(type_token_ratio, 3),
        "punctuation_density_per_100_chars": round(punctuation_density, 3),
        "sub_scores": {
            "sentence_uniformity": round(cv_score, 3),
            "vocabulary_diversity": round(ttr_score, 3),
            "punctuation_sparsity": round(punct_score, 3),
        },
        "low_sample_warning": word_count < 40,
    }
    return _clamp(ai_score), features
