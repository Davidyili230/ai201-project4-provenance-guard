import os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "provenance.db")

GROQ_MODEL = "llama-3.3-70b-versatile"

# Combined score = LLM_WEIGHT * llm_score + STYLO_WEIGHT * stylometric_score
LLM_WEIGHT = 0.6
STYLOMETRIC_WEIGHT = 0.4

# Asymmetric thresholds: it takes more evidence to accuse a creator of
# using AI than it does to clear them, because a false "likely_ai" verdict
# does more reputational harm to a human creator than a missed AI
# detection does. See planning.md section 2 for the full rationale.
LIKELY_AI_THRESHOLD = 0.75
LIKELY_HUMAN_THRESHOLD = 0.35

MIN_CONTENT_LENGTH = 20  # characters
MAX_CONTENT_LENGTH = 20_000  # characters

# Rate limits (see README for reasoning behind these specific numbers).
SUBMIT_RATE_LIMITS = ["5 per minute", "50 per day"]
APPEAL_RATE_LIMITS = ["5 per hour"]
