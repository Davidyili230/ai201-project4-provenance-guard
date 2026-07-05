import os

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import storage
from config import APPEAL_RATE_LIMITS, MAX_CONTENT_LENGTH, MIN_CONTENT_LENGTH, SUBMIT_RATE_LIMITS
from detection.scoring import classify

app = Flask(__name__)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

storage.init_db()


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/submit")
@limiter.limit(";".join(SUBMIT_RATE_LIMITS))
def submit():
    payload = request.get_json(silent=True) or {}
    content = (payload.get("content") or payload.get("text") or "").strip()
    creator_id = payload.get("creator_id")

    if len(content) < MIN_CONTENT_LENGTH:
        return jsonify({"error": f"content must be at least {MIN_CONTENT_LENGTH} characters"}), 400
    if len(content) > MAX_CONTENT_LENGTH:
        return jsonify({"error": f"content must be at most {MAX_CONTENT_LENGTH} characters"}), 400

    result = classify(content)
    content_id, created_at = storage.save_submission(
        creator_id=creator_id,
        content=content,
        ai_score=result["ai_score"],
        confidence=result["confidence"],
        verdict=result["verdict"],
        label=result["label"],
        signals=result["signals"],
    )

    return jsonify(
        {
            "content_id": content_id,
            "created_at": created_at,
            "status": "classified",
            "ai_score": result["ai_score"],
            "confidence": result["confidence"],
            "verdict": result["verdict"],
            "label": result["label"],
            "signals": result["signals"],
        }
    ), 201


@app.post("/appeal")
@limiter.limit(";".join(APPEAL_RATE_LIMITS))
def appeal():
    payload = request.get_json(silent=True) or {}
    content_id = payload.get("content_id")
    reasoning = (payload.get("reasoning") or payload.get("creator_reasoning") or "").strip()

    if not content_id:
        return jsonify({"error": "content_id is required"}), 400
    if not reasoning:
        return jsonify({"error": "reasoning is required"}), 400

    submission = storage.get_submission(content_id)
    if submission is None:
        return jsonify({"error": "content_id not found"}), 404

    appeal_id, created_at = storage.save_appeal(content_id, reasoning)

    return jsonify(
        {
            "appeal_id": appeal_id,
            "content_id": content_id,
            "status": "under_review",
            "created_at": created_at,
        }
    ), 201


@app.get("/content/<content_id>")
def get_content(content_id):
    submission = storage.get_submission(content_id)
    if submission is None:
        return jsonify({"error": "content_id not found"}), 404
    return jsonify(submission)


@app.get("/log")
def get_log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify(storage.get_log(limit=limit))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
