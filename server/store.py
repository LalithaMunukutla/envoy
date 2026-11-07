"""SQLite: an append-only audit log of every session event, and each agent's memory stream
(retrieved by relevance + recency + importance, as in Generative Agents)."""
from __future__ import annotations

import json
import math
import sqlite3
import time

from .config import DB_PATH

_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.executescript("""
CREATE TABLE IF NOT EXISTS events (session_id TEXT, seq INTEGER, ts REAL, type TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS memory (agent_id TEXT, ts REAL, text TEXT, importance REAL, tags TEXT);
""")


def log_event(session_id: str, seq: int, etype: str, payload: dict) -> None:
    _conn.execute("INSERT INTO events VALUES (?,?,?,?,?)", (session_id, seq, time.time(), etype, json.dumps(payload, default=str)))
    _conn.commit()


def remember(agent_id: str, text: str, importance: float, tags: list[str]) -> None:
    _conn.execute("INSERT INTO memory VALUES (?,?,?,?,?)", (agent_id, time.time(), text, importance, json.dumps(tags)))
    _conn.commit()


def recall(agent_id: str, query_tags: list[str], k: int = 3) -> list[dict]:
    rows = _conn.execute("SELECT ts, text, importance, tags FROM memory WHERE agent_id=?", (agent_id,)).fetchall()
    now, q = time.time(), set(query_tags)
    scored = []
    for ts, text, imp, tags in rows:
        recency = math.pow(0.995, (now - ts) / 3600)
        relevance = len(q & set(json.loads(tags))) / (len(q) or 1)
        scored.append((recency + imp / 10 + relevance, {"text": text, "relevance": round(relevance, 2)}))
    return [m for _, m in sorted(scored, key=lambda x: -x[0])[:k]]
