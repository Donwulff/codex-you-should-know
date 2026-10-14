import contextlib
import fcntl
import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .assessment import Finding, topic_key


@dataclass(frozen=True)
class Session:
    session_id: str
    events: int
    checked_events: int
    attempts: int
    last_attempt: float
    last_success: float
    last_hash: str
    last_error: str


class State:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory
        path = directory / "state.sqlite3"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                events INTEGER NOT NULL DEFAULT 0,
                checked_events INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt REAL NOT NULL DEFAULT 0,
                last_success REAL NOT NULL DEFAULT 0,
                last_hash TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                summary TEXT NOT NULL,
                explanation TEXT NOT NULL,
                evidence TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                created_at REAL NOT NULL
            );
        """)

    def close(self) -> None:
        self.connection.close()

    def observe(self, session_id: str, is_tool: bool) -> Session:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO sessions(session_id) VALUES (?)", (session_id,)
            )
            if is_tool:
                self.connection.execute(
                    "UPDATE sessions SET events = events + 1 WHERE session_id = ?", (session_id,)
                )
        return self.session(session_id)

    def session(self, session_id: str) -> Session:
        row = self.connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown session")
        return Session(**dict(row))

    def begin(self, session_id: str, now: float) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE sessions SET checked_events = events, attempts = attempts + 1, "
                "last_attempt = ? WHERE session_id = ?",
                (now, session_id),
            )

    def finish(self, session_id: str, now: float, digest: str, error: str = "") -> None:
        with self.connection:
            if error:
                self.connection.execute(
                    "UPDATE sessions SET last_error = ? WHERE session_id = ?", (error, session_id)
                )
            else:
                self.connection.execute(
                    "UPDATE sessions SET last_success = ?, last_hash = ?, last_error = '' "
                    "WHERE session_id = ?",
                    (now, digest, session_id),
                )

    def previous(self) -> list[Finding]:
        rows = self.connection.execute(
            "SELECT * FROM notes ORDER BY created_at DESC LIMIT 40"
        ).fetchall()
        return [
            Finding(row["topic"], row["summary"], row["explanation"], json.loads(row["evidence"]))
            for row in rows
        ]

    def save(self, session_id: str, finding: Finding, now: float) -> str | None:
        key = hashlib.sha256(topic_key(finding.topic).encode()).hexdigest()[:12]
        with self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO notes "
                "(id, session_id, topic, summary, explanation, evidence, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    session_id,
                    finding.topic,
                    finding.summary,
                    finding.explanation,
                    json.dumps(finding.evidence, ensure_ascii=False),
                    now,
                ),
            )
        return key if cursor.rowcount else None

    def notes(self, all_notes: bool = False) -> list[dict[str, Any]]:
        condition = "" if all_notes else "WHERE status = 'new'"
        rows = self.connection.execute(
            f"SELECT * FROM notes {condition} ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["evidence"] = json.loads(result["evidence"])
        return result

    def note(self, identifier: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM notes WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown note: {identifier}")
        return self._decode(row)

    def mark(self, identifier: str, status: str) -> None:
        if status not in {"known", "dismissed"}:
            raise ValueError("Invalid note status")
        self.note(identifier)
        with self.connection:
            self.connection.execute(
                "UPDATE notes SET status = ? WHERE id = ?", (status, identifier)
            )

    def status(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM sessions ORDER BY last_attempt DESC LIMIT 20"
            )
        ]


@contextlib.contextmanager
def review_lock(directory: Path, session_id: str, wait_seconds: float = 0) -> Generator[bool]:
    key = hashlib.sha256(session_id.encode()).hexdigest()
    descriptor = os.open(directory / f"review-{key}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + wait_seconds
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    yield False
                    return
                time.sleep(min(0.05, remaining))
        yield True
    finally:
        os.close(descriptor)
