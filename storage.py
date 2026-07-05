import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY,
    creator_id TEXT,
    content TEXT NOT NULL,
    ai_score REAL NOT NULL,
    confidence REAL NOT NULL,
    verdict TEXT NOT NULL,
    label TEXT NOT NULL,
    signals_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'classified',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appeals (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (content_id) REFERENCES submissions (id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    content_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def new_id():
    return uuid.uuid4().hex[:12]


def save_submission(creator_id, content, ai_score, confidence, verdict, label, signals):
    submission_id = new_id()
    created_at = _now()
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO submissions
               (id, creator_id, content, ai_score, confidence, verdict, label,
                signals_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'classified', ?)""",
            (
                submission_id,
                creator_id,
                content,
                ai_score,
                confidence,
                verdict,
                label,
                json.dumps(signals),
                created_at,
            ),
        )
        conn.execute(
            """INSERT INTO audit_log (id, event_type, content_id, details_json, created_at)
               VALUES (?, 'submission', ?, ?, ?)""",
            (
                new_id(),
                submission_id,
                json.dumps(
                    {
                        "ai_score": ai_score,
                        "confidence": confidence,
                        "verdict": verdict,
                        "label": label,
                        "signals": signals,
                    }
                ),
                created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return submission_id, created_at


def get_submission(content_id):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM submissions WHERE id = ?", (content_id,)).fetchone()
        if row is None:
            return None
        submission = dict(row)
        submission["signals"] = json.loads(submission.pop("signals_json"))
        appeals = conn.execute(
            "SELECT * FROM appeals WHERE content_id = ? ORDER BY created_at ASC",
            (content_id,),
        ).fetchall()
        submission["appeals"] = [dict(a) for a in appeals]
        return submission
    finally:
        conn.close()


def save_appeal(content_id, reasoning):
    appeal_id = new_id()
    created_at = _now()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE submissions SET status = 'under_review' WHERE id = ?",
            (content_id,),
        )
        conn.execute(
            """INSERT INTO appeals (id, content_id, reasoning, created_at)
               VALUES (?, ?, ?, ?)""",
            (appeal_id, content_id, reasoning, created_at),
        )
        conn.execute(
            """INSERT INTO audit_log (id, event_type, content_id, details_json, created_at)
               VALUES (?, 'appeal', ?, ?, ?)""",
            (
                new_id(),
                content_id,
                json.dumps({"appeal_id": appeal_id, "reasoning": reasoning}),
                created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return appeal_id, created_at


def get_log(limit=50):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        entries = []
        for row in rows:
            entry = dict(row)
            entry["details"] = json.loads(entry.pop("details_json"))
            entries.append(entry)
        return entries
    finally:
        conn.close()
