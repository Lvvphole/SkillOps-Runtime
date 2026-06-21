"""SQLite-backed persistence for runs, steps, checkpoints, decisions, artifacts.

Every executable step writes a step record; every completed step writes a
checkpoint; every loop-direction choice writes a decision record; every
evidence artifact is registered here with a sha256 tied to the run id.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    loop_id TEXT NOT NULL,
    loop_path TEXT NOT NULL,
    status TEXT NOT NULL,
    terminal_state TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    artifacts_dir TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 0,
    parent_run_id TEXT
);
CREATE TABLE IF NOT EXISTS step_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    owner_role TEXT NOT NULL,
    status TEXT NOT NULL,
    inputs TEXT,
    outputs TEXT,
    evidence TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    state_snapshot TEXT NOT NULL,
    resume_pointer INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step_id TEXT,
    decision TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    input_state_hash TEXT NOT NULL,
    evidence TEXT,
    next_action TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step_id TEXT,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    kind TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:12]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_state(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class Store:
    """Thin typed wrapper over a SQLite database."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        # Backward-compatible: add columns absent in DBs created by older versions.
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(runs)")}
        if "parent_run_id" not in cols:
            self.conn.execute("ALTER TABLE runs ADD COLUMN parent_run_id TEXT")

    def close(self) -> None:
        self.conn.close()

    # ---- runs -------------------------------------------------------------
    def create_run(self, run_id: str, loop_id: str, loop_path: str,
                   artifacts_dir: str, parent_run_id: Optional[str] = None) -> None:
        self.conn.execute(
            "INSERT INTO runs (run_id, loop_id, loop_path, status, started_at,"
            " artifacts_dir, iteration, parent_run_id) VALUES (?,?,?,?,?,?,0,?)",
            (run_id, loop_id, loop_path, "RUNNING", now_iso(), artifacts_dir,
             parent_run_id),
        )
        self.conn.commit()

    def get_children(self, run_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM runs WHERE parent_run_id=? ORDER BY started_at",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE runs SET {cols} WHERE run_id=?",
            (*fields.values(), run_id),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    # ---- step_runs --------------------------------------------------------
    def start_step(self, run_id: str, step_id: str, owner_role: str,
                   inputs: Any, attempt: int = 1) -> int:
        cur = self.conn.execute(
            "INSERT INTO step_runs (run_id, step_id, owner_role, status, inputs,"
            " attempt, started_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, step_id, owner_role, "RUNNING",
             json.dumps(inputs, default=str), attempt, now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def complete_step(self, step_row_id: int, status: str, outputs: Any,
                      evidence: List[str]) -> None:
        self.conn.execute(
            "UPDATE step_runs SET status=?, outputs=?, evidence=?, completed_at=?"
            " WHERE id=?",
            (status, json.dumps(outputs, default=str),
             json.dumps(evidence, default=str), now_iso(), step_row_id),
        )
        self.conn.commit()

    def get_steps(self, run_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM step_runs WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def completed_step_ids(self, run_id: str) -> List[str]:
        rows = self.conn.execute(
            "SELECT step_id FROM step_runs WHERE run_id=? AND status='COMPLETED'"
            " ORDER BY id", (run_id,)
        ).fetchall()
        return [r["step_id"] for r in rows]

    # ---- checkpoints ------------------------------------------------------
    def add_checkpoint(self, run_id: str, step_id: str, state_snapshot: Any,
                       resume_pointer: int) -> int:
        seq = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM checkpoints WHERE run_id=?",
            (run_id,),
        ).fetchone()["n"]
        cur = self.conn.execute(
            "INSERT INTO checkpoints (run_id, step_id, seq, state_snapshot,"
            " resume_pointer, created_at) VALUES (?,?,?,?,?,?)",
            (run_id, step_id, seq, json.dumps(state_snapshot, default=str),
             resume_pointer, now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_checkpoints(self, run_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM checkpoints WHERE run_id=? ORDER BY seq", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def last_checkpoint(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM checkpoints WHERE run_id=? ORDER BY seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    # ---- decisions --------------------------------------------------------
    def add_decision(self, run_id: str, step_id: Optional[str], decision: str,
                     reason_code: str, input_state_hash: str,
                     evidence: List[str], next_action: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO decisions (run_id, step_id, decision, reason_code,"
            " input_state_hash, evidence, next_action, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (run_id, step_id, decision, reason_code, input_state_hash,
             json.dumps(evidence, default=str), next_action, now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_decisions(self, run_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM decisions WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- artifacts --------------------------------------------------------
    def register_artifact(self, run_id: str, step_id: Optional[str], name: str,
                          path: str, kind: str = "file") -> int:
        digest = sha256_file(path) if os.path.exists(path) else ""
        cur = self.conn.execute(
            "INSERT INTO artifacts (run_id, step_id, name, path, sha256, kind,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, step_id, name, path, digest, kind, now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM artifacts WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def has_artifact(self, run_id: str, name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM artifacts WHERE run_id=? AND name=? LIMIT 1",
            (run_id, name),
        ).fetchone()
        return row is not None
