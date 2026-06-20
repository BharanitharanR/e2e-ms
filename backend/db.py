# e2e-marqeta-simulator/backend/db.py
"""SQLite persistence layer for the simulator.

Provides a thread-safe, WAL-mode SQLite database for storing transaction history,
suite runs, scenarios, and environment configurations. Replaces the in-memory
HISTORY list with durable storage.

DB path defaults to /data/simulator.db (Docker volume) and falls back to
./simulator.db for local runs outside Docker.
"""
import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

DB_PATH = os.getenv("DB_PATH", "./simulator.db")


@contextmanager
def get_db():
    """Thread-safe SQLite connection with WAL mode and auto-commit/rollback."""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create schema if not exists. Call at application startup."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                scenario_id      TEXT    NOT NULL,
                scenario_name    TEXT,
                event_type       TEXT    DEFAULT 'authorization',
                timestamp        TEXT    NOT NULL,
                passed           INTEGER NOT NULL DEFAULT 0,
                expected_rc      TEXT,
                actual_rc        TEXT,
                expected_decision TEXT,
                actual_decision  TEXT,
                duration_ms      REAL,
                request_json     TEXT,
                response_json    TEXT,
                audit_json       TEXT
            );

            CREATE TABLE IF NOT EXISTS suite_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                suite_key    TEXT    NOT NULL,
                suite_name   TEXT,
                run_at       TEXT    NOT NULL,
                total        INTEGER DEFAULT 0,
                passed       INTEGER DEFAULT 0,
                failed       INTEGER DEFAULT 0,
                duration_ms  REAL,
                results_json TEXT
            );

            CREATE TABLE IF NOT EXISTS environments (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                name             TEXT    UNIQUE NOT NULL,
                api_url          TEXT    NOT NULL,
                customer_jit_url TEXT,
                is_active        INTEGER DEFAULT 0,
                notes            TEXT,
                created_at       TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_txn_timestamp
                ON transactions (timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_txn_scenario_id
                ON transactions (scenario_id);
            CREATE INDEX IF NOT EXISTS idx_txn_passed
                ON transactions (passed);
            CREATE INDEX IF NOT EXISTS idx_suite_run_at
                ON suite_runs (run_at DESC);
        """)
        # Seed default environment if none exist
        count = conn.execute("SELECT COUNT(*) FROM environments").fetchone()[0]
        if count == 0:
            conn.execute(
                "INSERT INTO environments (name, api_url, customer_jit_url, is_active, notes, created_at)"
                " VALUES (?, ?, ?, 1, ?, ?)",
                (
                    "Local Docker",
                    os.getenv("API_URL", "http://backend:8000"),
                    os.getenv("CUSTOMER_JIT_URL", "http://customer_jit:8001"),
                    "Default local Docker Compose environment",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )


# ── Transaction helpers ──────────────────────────────────────────────────────

def persist_transaction(trace: dict) -> int:
    """Insert a trace dict into the transactions table. Returns inserted row id."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO transactions
               (scenario_id, scenario_name, event_type, timestamp, passed,
                expected_rc, actual_rc, expected_decision, actual_decision,
                duration_ms, request_json, response_json, audit_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trace.get("scenario_id", ""),
                trace.get("scenario_name", ""),
                trace.get("event_type", "authorization"),
                trace.get("timestamp", datetime.now(timezone.utc).isoformat()),
                1 if trace.get("passed") else 0,
                trace.get("expected_network_response_code"),
                trace.get("actual_network_response_code"),
                trace.get("expected_customer_decision"),
                trace.get("actual_customer_decision"),
                trace.get("duration_ms"),
                json.dumps(trace.get("request_sent", {})),
                json.dumps(trace.get("response_received", {})),
                json.dumps(trace.get("audit_trail", [])),
            ),
        )
        return cur.lastrowid


def get_transactions_page(
    page: int = 1,
    limit: int = 20,
    scenario_id: str = None,
    event_type: str = None,
    passed: bool = None,
) -> dict:
    """Return paginated transaction history with total count."""
    conditions = []
    params = []
    if scenario_id:
        conditions.append("scenario_id = ?")
        params.append(scenario_id)
    if event_type:
        conditions.append("event_type = ?")
        params.append(event_type)
    if passed is not None:
        conditions.append("passed = ?")
        params.append(1 if passed else 0)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * limit

    with get_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM transactions {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM transactions {where}"
            " ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    items = []
    for row in rows:
        d = dict(row)
        d["passed"] = bool(d["passed"])
        # Deserialise JSON blobs
        for key in ("request_json", "response_json", "audit_json"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        items.append(d)

    return {"items": items, "total": total, "page": page, "limit": limit}


def get_recent_transactions(limit: int = 100) -> list:
    """Return the N most recent transactions as a flat list (backward compat)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["passed"] = bool(d["passed"])
        for key in ("request_json", "response_json", "audit_json"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        result.append(d)
    return result


# ── Suite run helpers ────────────────────────────────────────────────────────

def persist_suite_run(suite_key: str, suite_result: dict) -> int:
    """Insert a suite run and return the row id."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO suite_runs
               (suite_key, suite_name, run_at, total, passed, failed, duration_ms, results_json)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                suite_key,
                suite_result.get("suite_name", suite_key),
                suite_result.get("run_at", datetime.now(timezone.utc).isoformat()),
                suite_result.get("total", 0),
                suite_result.get("passed", 0),
                suite_result.get("failed", 0),
                suite_result.get("duration_ms"),
                json.dumps(suite_result.get("results", [])),
            ),
        )
        return cur.lastrowid


def get_suite_runs_page(page: int = 1, limit: int = 10) -> dict:
    """Return paginated suite run history."""
    offset = (page - 1) * limit
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM suite_runs").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM suite_runs ORDER BY run_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

    items = []
    for row in rows:
        d = dict(row)
        if d.get("results_json"):
            try:
                d["results"] = json.loads(d["results_json"])
            except (json.JSONDecodeError, TypeError):
                d["results"] = []
        items.append(d)

    return {"items": items, "total": total, "page": page, "limit": limit}


# ── Analytics helpers ────────────────────────────────────────────────────────

def get_rc_coverage() -> list:
    """Return per-response-code pass/fail counts from the transactions table."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT actual_rc as rc,
                      COUNT(*) as total,
                      SUM(passed) as passed,
                      COUNT(*) - SUM(passed) as failed
               FROM transactions
               WHERE actual_rc IS NOT NULL
               GROUP BY actual_rc
               ORDER BY actual_rc""",
        ).fetchall()
    return [dict(r) for r in rows]


def get_latency_stats(limit: int = 50) -> list:
    """Return recent transaction latencies for waterfall/bar chart."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT scenario_name, duration_ms, timestamp, passed, actual_rc
               FROM transactions
               WHERE duration_ms IS NOT NULL
               ORDER BY timestamp DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["passed"] = bool(d["passed"])
        result.append(d)
    return result


def get_daily_trends(days: int = 7) -> list:
    """Return daily pass/fail counts for the trend chart."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DATE(timestamp) as date,
                      SUM(passed) as passed,
                      COUNT(*) - SUM(passed) as failed,
                      COUNT(*) as total
               FROM transactions
               WHERE timestamp >= ?
               GROUP BY DATE(timestamp)
               ORDER BY date""",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Environment helpers ──────────────────────────────────────────────────────

def list_environments() -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM environments ORDER BY is_active DESC, id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def create_environment(name: str, api_url: str,
                        customer_jit_url: str = None, notes: str = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO environments (name, api_url, customer_jit_url, is_active, notes, created_at)
               VALUES (?,?,?,0,?,?)""",
            (name, api_url, customer_jit_url, notes,
             datetime.now(timezone.utc).isoformat()),
        )
        return cur.lastrowid


def activate_environment(env_id: int) -> bool:
    with get_db() as conn:
        conn.execute("UPDATE environments SET is_active = 0")
        affected = conn.execute(
            "UPDATE environments SET is_active = 1 WHERE id = ?", (env_id,)
        ).rowcount
    return affected > 0


def get_active_environment() -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM environments WHERE is_active = 1 LIMIT 1"
        ).fetchone()
    return dict(row) if row else None
