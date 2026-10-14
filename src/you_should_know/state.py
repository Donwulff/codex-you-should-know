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
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                note_id TEXT,
                message TEXT
            );
            CREATE INDEX IF NOT EXISTS notifications_session
                ON notifications(session_id, id);
            CREATE TABLE IF NOT EXISTS interactions (
                session_id TEXT PRIMARY KEY,
                note_id TEXT NOT NULL,
                ready INTEGER NOT NULL DEFAULT 0,
                turn_id TEXT NOT NULL DEFAULT ''
            );
        """)
        with self._transaction():
            columns = self.connection.execute("PRAGMA table_info(interactions)").fetchall()
            if not any(column["name"] == "turn_id" for column in columns):
                self.connection.execute(
                    "ALTER TABLE interactions ADD COLUMN turn_id TEXT NOT NULL DEFAULT ''"
                )
            reminders = self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'reminders'"
            ).fetchone()
            if reminders is None:
                self.connection.execute(
                    "CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "session_id TEXT NOT NULL, note_id TEXT NOT NULL UNIQUE, "
                    "last_turn_id TEXT NOT NULL DEFAULT '', "
                    "expanded INTEGER NOT NULL DEFAULT 0)"
                )
                self.connection.execute(
                    "CREATE INDEX reminders_session ON reminders(session_id, id)"
                )
                self.connection.execute(
                    "INSERT INTO reminders(session_id, note_id, last_turn_id) "
                    "SELECT interactions.session_id, interactions.note_id, "
                    "CASE WHEN interactions.ready = 1 THEN interactions.turn_id ELSE '' END "
                    "FROM interactions JOIN notes ON notes.id = interactions.note_id "
                    "WHERE notes.status = 'new'"
                )
            else:
                columns = self.connection.execute("PRAGMA table_info(reminders)").fetchall()
                if not any(column["name"] == "expanded" for column in columns):
                    self.connection.execute(
                        "ALTER TABLE reminders ADD COLUMN expanded INTEGER NOT NULL DEFAULT 0"
                    )

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

    @contextlib.contextmanager
    def _transaction(self) -> Generator[None]:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            yield

    def save(
        self, session_id: str, finding: Finding, now: float, notify: bool = False
    ) -> str | None:
        key = hashlib.sha256(topic_key(finding.topic).encode()).hexdigest()[:12]
        with self._transaction():
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
            if cursor.rowcount and notify:
                self.connection.execute(
                    "INSERT INTO notifications(session_id, note_id) VALUES (?, ?)",
                    (session_id, key),
                )
        return key if cursor.rowcount else None

    def queue_error(self, session_id: str, message: str) -> None:
        with self._transaction():
            self.connection.execute(
                "INSERT INTO notifications(session_id, message) VALUES (?, ?)",
                (session_id, message),
            )

    def deliver(
        self, session_id: str, ready: bool = False, turn_id: str | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        notifications = []
        armed_note = None
        with self._transaction():
            rows = self.connection.execute(
                "SELECT * FROM notifications WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
            for row in rows:
                if row["note_id"] is None:
                    notifications.append({"message": row["message"]})
                    continue
                note = self.connection.execute(
                    "SELECT * FROM notes WHERE id = ? AND status = 'new'", (row["note_id"],)
                ).fetchone()
                if note is None:
                    continue
                notifications.append({"note": self._decode(note)})
                self.connection.execute(
                    "INSERT OR IGNORE INTO reminders(session_id, note_id) VALUES (?, ?)",
                    (session_id, note["id"]),
                )
                self.connection.execute(
                    "INSERT INTO interactions(session_id, note_id, ready) VALUES (?, ?, 0) "
                    "ON CONFLICT(session_id) DO UPDATE SET "
                    "note_id = excluded.note_id, ready = 0, turn_id = ''",
                    (session_id, note["id"]),
                )
            self.connection.execute("DELETE FROM notifications WHERE session_id = ?", (session_id,))
            if ready and turn_id:
                note = self.connection.execute(
                    "SELECT notes.*, reminders.last_turn_id, reminders.expanded FROM reminders "
                    "JOIN notes ON notes.id = reminders.note_id "
                    "WHERE reminders.session_id = ? AND notes.status = 'new' "
                    "ORDER BY reminders.id DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
                if note is not None:
                    self.connection.execute(
                        "INSERT INTO interactions(session_id, note_id, ready, turn_id) "
                        "VALUES (?, ?, 1, ?) ON CONFLICT(session_id) DO UPDATE SET "
                        "note_id = excluded.note_id, ready = 1, turn_id = excluded.turn_id",
                        (session_id, note["id"], turn_id),
                    )
                    if note["last_turn_id"] != turn_id:
                        armed_note = self._decode(note)
                        armed_note["expanded"] = bool(armed_note["expanded"])
                        last_turn_id = armed_note.pop("last_turn_id")
                        if last_turn_id:
                            notifications.append({"note": armed_note, "reminder": True})
                        self.connection.execute(
                            "UPDATE reminders SET last_turn_id = ? WHERE note_id = ?",
                            (turn_id, note["id"]),
                        )
        return notifications, armed_note

    def reply(
        self, session_id: str, action: str | None, turn_id: str | None = None
    ) -> dict[str, Any] | None:
        if action not in {None, "more", "known", "dismissed", "chat"}:
            raise ValueError("Invalid note action")
        with self._transaction():
            # A Stop hook can resume the same turn, whose steering must reach the agent.
            note = self.connection.execute(
                "SELECT notes.*, reminders.expanded FROM interactions "
                "JOIN notes ON notes.id = interactions.note_id "
                "JOIN reminders ON reminders.note_id = notes.id "
                "WHERE interactions.session_id = ? AND interactions.ready = 1 "
                "AND interactions.turn_id != '' AND interactions.turn_id != ? "
                "AND ? != '' "
                "AND notes.status = 'new'",
                (session_id, turn_id, turn_id),
            ).fetchone()
            if (
                action is None
                or note is None
                or (note["expanded"] and action in {"more", "known"})
                or (not note["expanded"] and action == "chat")
            ):
                self.connection.execute(
                    "UPDATE interactions SET ready = 0 WHERE session_id = ?", (session_id,)
                )
                return None
            result = self._decode(note)
            result["expanded"] = bool(result["expanded"])
            if action == "more":
                self.connection.execute(
                    "UPDATE reminders SET expanded = 1 WHERE note_id = ?", (note["id"],)
                )
                result["expanded"] = True
            if action in {"known", "dismissed"}:
                self.connection.execute(
                    "UPDATE notes SET status = ? WHERE id = ?", (action, note["id"])
                )
                result["status"] = action
            if action != "more":
                self.connection.execute("DELETE FROM reminders WHERE note_id = ?", (note["id"],))
                self.connection.execute(
                    "DELETE FROM interactions WHERE session_id = ?", (session_id,)
                )
            return result

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
        with self._transaction():
            self.note(identifier)
            self.connection.execute(
                "UPDATE notes SET status = ? WHERE id = ?", (status, identifier)
            )
            self.connection.execute("DELETE FROM interactions WHERE note_id = ?", (identifier,))
            self.connection.execute("DELETE FROM reminders WHERE note_id = ?", (identifier,))

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
