"""Transparency label templates shown to end readers.

These three exact templates are the required, documented label variants.
`{confidence}` is replaced with the confidence score as a whole-number
percentage. Keep the wording here in sync with the README, which quotes
it verbatim.
"""

LIKELY_AI_TEMPLATE = (
    "⚠️ Likely AI-Generated — Our analysis indicates this content was most "
    "likely produced by an AI system (confidence: {confidence}%). This is "
    "an automated assessment, not a certainty, and the creator may appeal "
    "this classification."
)

LIKELY_HUMAN_TEMPLATE = (
    "✅ Likely Human-Written — Our analysis indicates this content was most "
    "likely written by a human (confidence: {confidence}%)."
)

UNCERTAIN_TEMPLATE = (
    "❓ Uncertain Origin — Our analysis could not confidently determine "
    "whether this content is human-written or AI-generated (confidence: "
    "{confidence}%). Signals were mixed or inconclusive — treat this "
    "classification with caution."
)


def render_label(verdict, confidence):
    percent = round(confidence * 100)
    template = {
        "likely_ai": LIKELY_AI_TEMPLATE,
        "likely_human": LIKELY_HUMAN_TEMPLATE,
        "uncertain": UNCERTAIN_TEMPLATE,
    }[verdict]
    return template.format(confidence=percent)
