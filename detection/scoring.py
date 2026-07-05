"""Combines the two independent signals into a verdict + confidence + label."""

from config import LIKELY_AI_THRESHOLD, LIKELY_HUMAN_THRESHOLD, LLM_WEIGHT, STYLOMETRIC_WEIGHT
from detection import llm_signal, stylometric
from detection.labels import render_label


def classify(text):
    llm_score, llm_features = llm_signal.analyze(text)
    stylo_score, stylo_features = stylometric.analyze(text)

    ai_score = LLM_WEIGHT * llm_score + STYLOMETRIC_WEIGHT * stylo_score
    confidence = abs(ai_score - 0.5) * 2

    if ai_score >= LIKELY_AI_THRESHOLD:
        verdict = "likely_ai"
    elif ai_score <= LIKELY_HUMAN_THRESHOLD:
        verdict = "likely_human"
    else:
        verdict = "uncertain"

    label = render_label(verdict, confidence)

    signals = {
        "llm": {"score": round(llm_score, 3), **llm_features},
        "stylometric": {"score": round(stylo_score, 3), **stylo_features},
        "weights": {"llm": LLM_WEIGHT, "stylometric": STYLOMETRIC_WEIGHT},
    }

    return {
        "ai_score": round(ai_score, 3),
        "confidence": round(confidence, 3),
        "verdict": verdict,
        "label": label,
        "signals": signals,
    }
