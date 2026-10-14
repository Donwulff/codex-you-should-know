import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import backend
from .assessment import AssessmentError, make_prompt, parse_finding
from .config import Config, ConfigError, data_directory, load_config
from .state import Session, State, review_lock
from .transcript import TranscriptError, read_transcript

SCHEMA = Path(__file__).resolve().parents[2] / "resources" / "finding.schema.json"


def eligible(session: Session, config: Config, event: str, now: float, force: bool) -> bool:
    if not config.enabled:
        return False
    if force:
        return True
    if session.attempts >= config.max_checks_per_session:
        return False
    if session.attempts and now - session.last_attempt < config.min_interval_seconds:
        return False
    return event == "Stop" or session.events - session.checked_events >= config.every_n_tools


def check(
    state: State,
    config: Config,
    session_id: str,
    transcript_path: Path,
    event: str,
    model: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    session = state.observe(session_id, event == "PostToolUse")
    if event != "Stop" and not eligible(session, config, event, time.time(), force):
        return {"status": "skipped"}
    wait = config.timeout_seconds + 2 if event == "Stop" and not force else 0
    with review_lock(state.directory, session_id, wait) as acquired:
        if not acquired:
            return {"status": "busy"}
        session = state.session(session_id)
        if not eligible(session, config, event, time.time(), force):
            return {"status": "skipped"}
        state.begin(session_id, time.time())
        try:
            transcript = read_transcript(transcript_path, config.max_context_chars)
            if not transcript.strip():
                raise TranscriptError("Conversation has no visible messages yet")
            digest = hashlib.sha256(transcript.encode()).hexdigest()
            if digest == session.last_hash and not force:
                state.finish(session_id, time.time(), digest)
                return {"status": "unchanged"}
            response = backend.review(
                make_prompt(transcript, state.previous()),
                SCHEMA,
                model=config.model or model,
                timeout=max(1, config.timeout_seconds - (time.monotonic() - started)),
            )
            finding = parse_finding(response, transcript)
            identifier = state.save(session_id, finding, time.time()) if finding else None
            state.finish(session_id, time.time(), digest)
        except (backend.BackendError, AssessmentError, TranscriptError) as error:
            detail = str(error)[:500]
            state.finish(session_id, time.time(), "", detail)
            return {"status": "error", "error": detail, "notify": not session.last_error}
        if identifier:
            return {"status": "finding", "note": state.note(identifier)}
        return {"status": "duplicate" if finding else "clear"}


def hook_result(result: dict[str, Any]) -> dict[str, str]:
    if result["status"] == "finding":
        note = result["note"]
        return {
            "systemMessage": (
                f"Heads up [{note['id']}] · {note['summary']}\n"
                "Use $you-should-know:you-should-know to inspect or acknowledge this note."
            )
        }
    if result["status"] == "error" and result.get("notify"):
        return {
            "systemMessage": (
                f"You should know: a check failed. {result['error']}\n"
                "Use $you-should-know:you-should-know status for details."
            )
        }
    return {}


def read_event() -> dict[str, Any]:
    raw = sys.stdin.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Hook input exceeds 2 MB")
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("Hook input must be a JSON object")
    for field in ("session_id", "hook_event_name"):
        value = event.get(field)
        if not isinstance(value, str) or not value or len(value) > 256:
            raise ValueError(f"Hook input needs a valid {field}")
    model = event.get("model")
    if model is not None and (not isinstance(model, str) or len(model) > 200):
        raise ValueError("Hook model must be a string")
    return event


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="you-should-know", description="Independent advisory checks for Codex conversations."
    )
    result.add_argument("--data-dir", help="Override the private local state directory")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("hook", help="Handle a Codex lifecycle event on stdin")
    notes = commands.add_parser("notes", help="List unread notes as JSON")
    notes.add_argument("--all", action="store_true", help="Include known and dismissed notes")
    for name, help_text in (
        ("show", "Read a note"),
        ("know", "Mark a topic as known"),
        ("dismiss", "Dismiss a note"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("id", help="Exact note ID from a Heads up warning or notes")
    commands.add_parser("status", help="Show settings and recent check outcomes")
    manual = commands.add_parser("check", help="Review a transcript now, bypassing rate limits")
    manual.add_argument("--transcript", type=Path, required=True)
    manual.add_argument("--session", required=True)
    manual.add_argument("--model")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    is_hook = args.command == "hook"
    if is_hook and os.environ.get("YSK_REVIEWER_ACTIVE") == "1":
        print("{}")
        return 0
    state = None
    try:
        event = read_event() if is_hook else None
        if event and event["hook_event_name"] not in {"PostToolUse", "Stop"}:
            print("{}")
            return 0
        directory = data_directory(args.data_dir)
        config = load_config(directory)
        if is_hook and not config.enabled:
            print("{}")
            return 0
        state = State(directory)
        if event:
            path = event.get("transcript_path")
            if not isinstance(path, str) or not path:
                raise ValueError("Codex did not provide transcript_path; this check cannot run")
            output = hook_result(
                check(
                    state,
                    config,
                    event["session_id"],
                    Path(path),
                    event["hook_event_name"],
                    event.get("model"),
                )
            )
        elif args.command == "check":
            output = check(state, config, args.session, args.transcript, "Stop", args.model, True)
        elif args.command == "notes":
            output = {"notes": state.notes(args.all)}
        elif args.command == "show":
            output = state.note(args.id)
        elif args.command in {"know", "dismiss"}:
            state.mark(args.id, "known" if args.command == "know" else "dismissed")
            output = state.note(args.id)
        else:
            output = {
                "data_directory": str(directory),
                "config": asdict(config),
                "sessions": state.status(),
            }
        print(json.dumps(output, ensure_ascii=False, indent=None if is_hook else 2))
        return 1 if not is_hook and output.get("status") == "error" else 0
    except (OSError, ValueError, sqlite3.Error, ConfigError) as error:
        message = str(error)[:500]
        if is_hook:
            print(json.dumps({"systemMessage": f"You should know: {message}"}))
            return 0
        print(f"you-should-know: {message}", file=sys.stderr)
        return 1
    finally:
        if state is not None:
            state.close()
