import json
import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from harness_config import CAPTURE_DB

# ---------------------------------------------------------------------------
# Per-scan insert/skip counters (thread-safe)
# ---------------------------------------------------------------------------
_stats_lock = threading.Lock()
_stats: Dict[str, Any] = {
    "scan_started_at": None,
    "scan_finished_at": None,
    "user_messages": {"inserted": 0, "skipped": 0},
    "tool_calls": {"inserted": 0, "skipped": 0},
    "contexts": {"inserted": 0, "skipped": 0},
    "sessions_upserted": 0,
}


def reset_stats() -> None:
    """Reset counters at the beginning of each scan pass."""
    with _stats_lock:
        _stats["scan_started_at"] = datetime.now(timezone.utc).isoformat()
        _stats["scan_finished_at"] = None
        _stats["sessions_upserted"] = 0
        for key in ("user_messages", "tool_calls", "contexts"):
            _stats[key]["inserted"] = 0
            _stats[key]["skipped"] = 0


def finish_stats() -> None:
    """Stamp the finish time after the scan pass completes."""
    with _stats_lock:
        _stats["scan_finished_at"] = datetime.now(timezone.utc).isoformat()


def get_stats() -> Dict[str, Any]:
    """Return a snapshot of the current stats dict."""
    with _stats_lock:
        return {
            "scan_started_at": _stats["scan_started_at"],
            "scan_finished_at": _stats["scan_finished_at"],
            "sessions_upserted": _stats["sessions_upserted"],
            "user_messages": dict(_stats["user_messages"]),
            "tool_calls": dict(_stats["tool_calls"]),
            "contexts": dict(_stats["contexts"]),
        }


# ---------------------------------------------------------------------------
# Connection factory — no global connection; each caller owns its connection
# ---------------------------------------------------------------------------
_schema_lock = threading.Lock()
_schema_initialized = False


def _open_conn() -> sqlite3.Connection:
    """Open a fresh SQLite connection for the calling thread.

    The caller is responsible for calling conn.close() when done.
    Schema DDL runs at most once per process lifetime.
    """
    global _schema_initialized
    CAPTURE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CAPTURE_DB), timeout=15.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    if not _schema_initialized:
        with _schema_lock:
            if not _schema_initialized:
                _init_tables(conn)
                _schema_initialized = True
    return conn


def _init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS session (
            id TEXT NOT NULL,
            agent TEXT NOT NULL,
            project TEXT,
            timestamp TEXT,
            display TEXT,
            model TEXT,
            system_prompt TEXT,
            tokens_input INTEGER DEFAULT 0,
            tokens_output INTEGER DEFAULT 0,
            tokens_cached INTEGER DEFAULT 0,
            tokens_total INTEGER DEFAULT 0,
            cost REAL DEFAULT 0,
            homogenized_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (id, agent)
        );
        CREATE TABLE IF NOT EXISTS user_message (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            dedupe_key TEXT,
            seq INTEGER NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT,
            raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS tool_call (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            dedupe_key TEXT,
            seq INTEGER NOT NULL,
            name TEXT NOT NULL,
            arguments TEXT,
            result TEXT,
            is_error INTEGER DEFAULT 0,
            timestamp TEXT,
            raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            dedupe_key TEXT,
            type TEXT,
            title TEXT,
            content TEXT,
            path TEXT,
            timestamp TEXT
        );
    """)
    _ensure_column(conn, "user_message", "dedupe_key", "TEXT")
    _ensure_column(conn, "tool_call", "dedupe_key", "TEXT")
    _ensure_column(conn, "context", "dedupe_key", "TEXT")
    conn.executescript("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_user_message_dedupe
            ON user_message(session_id, agent, dedupe_key);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_tool_call_dedupe
            ON tool_call(session_id, agent, dedupe_key);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_context_dedupe
            ON context(session_id, agent, dedupe_key);
        CREATE INDEX IF NOT EXISTS ix_user_message_lookup
            ON user_message(session_id, agent, timestamp);
        CREATE INDEX IF NOT EXISTS ix_tool_call_lookup
            ON tool_call(session_id, agent, timestamp);
        CREATE INDEX IF NOT EXISTS ix_context_lookup
            ON context(session_id, agent, timestamp);
    """)
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_ts(ts) -> Optional[str]:
    if ts is None:
        return None
    if hasattr(ts, "isoformat"):
        return ts.isoformat()
    return str(ts)


def _json_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _dedupe_key(parts: Dict[str, Any]) -> str:
    canonical = json.dumps(parts, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public write API — each function opens and closes its own connection
# ---------------------------------------------------------------------------

def upsert_session(session: Dict[str, Any], system_prompt: Optional[str] = None) -> None:
    conn = _open_conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO session
                (id, agent, project, timestamp, display, model, system_prompt,
                 tokens_input, tokens_output, tokens_cached, tokens_total, cost,
                 homogenized_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session["id"], session["agent"],
            session.get("project"),
            _fmt_ts(session.get("timestamp")),
            session.get("display"),
            session.get("model"),
            system_prompt,
            session.get("tokens", {}).get("input", 0),
            session.get("tokens", {}).get("output", 0),
            session.get("tokens", {}).get("cached", 0),
            session.get("tokens", {}).get("total", 0),
            session.get("cost", 0.0),
            json.dumps(session, default=str),
        ))
        conn.commit()
    finally:
        conn.close()
    with _stats_lock:
        _stats["sessions_upserted"] += 1


def insert_user_message(session_id: str, agent: str, seq: int, content: str, timestamp: Any, raw: Any) -> bool:
    ts = _fmt_ts(timestamp)
    raw_json = _json_text(raw)
    key = _dedupe_key({"kind": "user_message", "content": content, "timestamp": ts, "raw": raw_json})
    conn = _open_conn()
    try:
        cur = conn.execute("""
            INSERT OR IGNORE INTO user_message
                (session_id, agent, dedupe_key, seq, content, timestamp, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (session_id, agent, key, seq, content, ts, raw_json))
        conn.commit()
        inserted = cur.rowcount == 1
    finally:
        conn.close()
    with _stats_lock:
        if inserted:
            _stats["user_messages"]["inserted"] += 1
        else:
            _stats["user_messages"]["skipped"] += 1
    return inserted


def insert_tool_call(
    session_id: str,
    agent: str,
    seq: int,
    name: str,
    arguments: Any,
    result: Any,
    is_error: bool,
    timestamp: Any,
    raw: Any,
) -> bool:
    ts = _fmt_ts(timestamp)
    arguments_json = _json_text(arguments)
    result_text = _json_text(result)
    raw_json = _json_text(raw)
    key = _dedupe_key({
        "kind": "tool_call",
        "name": name,
        "arguments": arguments_json,
        "result": result_text,
        "is_error": bool(is_error),
        "timestamp": ts,
        "raw": raw_json,
    })
    conn = _open_conn()
    try:
        cur = conn.execute("""
            INSERT OR IGNORE INTO tool_call
                (session_id, agent, dedupe_key, seq, name, arguments, result, is_error, timestamp, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (session_id, agent, key, seq, name, arguments_json, result_text, 1 if is_error else 0, ts, raw_json))
        conn.commit()
        inserted = cur.rowcount == 1
    finally:
        conn.close()
    with _stats_lock:
        if inserted:
            _stats["tool_calls"]["inserted"] += 1
        else:
            _stats["tool_calls"]["skipped"] += 1
    return inserted


def insert_context(
    session_id: str,
    agent: str,
    type_: Optional[str],
    title: Optional[str],
    content: Optional[str],
    path: Optional[str],
    timestamp: Any,
) -> bool:
    ts = _fmt_ts(timestamp)
    key = _dedupe_key({
        "kind": "context",
        "type": type_,
        "title": title,
        "content": content,
        "path": path,
        "timestamp": ts,
    })
    conn = _open_conn()
    try:
        cur = conn.execute("""
            INSERT OR IGNORE INTO context
                (session_id, agent, dedupe_key, type, title, content, path, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (session_id, agent, key, type_, title, content, path, ts))
        conn.commit()
        inserted = cur.rowcount == 1
    finally:
        conn.close()
    with _stats_lock:
        if inserted:
            _stats["contexts"]["inserted"] += 1
        else:
            _stats["contexts"]["skipped"] += 1
    return inserted


def save_sessions(sessions: List[Dict[str, Any]], enrichments: Dict[tuple, Dict]) -> None:
    """Compatibility shim — used by legacy callers only."""
    for sess in sessions:
        sid = sess["id"]
        agent = sess["agent"]
        enrich = enrichments.get((agent, sid), {})
        upsert_session(sess, system_prompt=enrich.get("system_prompt"))
        for i, msg in enumerate(enrich.get("user_messages", [])):
            insert_user_message(sid, agent, i, msg.get("content", ""), msg.get("timestamp"), msg.get("raw"))
        for i, tc in enumerate(enrich.get("tool_calls", [])):
            insert_tool_call(
                sid, agent, i,
                tc.get("name", ""),
                tc.get("arguments"),
                tc.get("result"),
                bool(tc.get("is_error")),
                tc.get("timestamp"),
                tc.get("raw"),
            )
        for ctx in enrich.get("contexts", []):
            insert_context(
                sid, agent,
                ctx.get("type"),
                ctx.get("title"),
                ctx.get("content"),
                ctx.get("path"),
                ctx.get("timestamp"),
            )


def close() -> None:
    """No-op — kept for API compatibility. Connections are per-call and self-closing."""
    pass
