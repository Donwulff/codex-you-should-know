"""Read bounded, visible conversation text from Codex JSONL rollouts."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

_MAX_LINE_BYTES = 1_048_576
_TRUNCATED = "\n[... content truncated ...]\n"
_OMITTED = "[... earlier conversation omitted ...]"
_ROLLOUT_TYPES = {
    "session_meta",
    "response_item",
    "event_msg",
    "turn_context",
    "world_state",
    "compacted",
    "inter_agent_communication_metadata",
    "token_usage_record",
}


class TranscriptError(ValueError):
    """The supplied path is unreadable or is not a supported Codex rollout."""


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit < len(_TRUNCATED) + 2:
        return text[: limit - 1] + "…"
    remaining = limit - len(_TRUNCATED)
    start = (remaining + 1) // 2
    return text[:start] + _TRUNCATED + text[-(remaining - start) :]


def _records(stream: BinaryIO) -> Iterator[tuple[dict | None, str | None]]:
    line_number = 0
    while raw := stream.readline(_MAX_LINE_BYTES + 1):
        line_number += 1
        if len(raw) > _MAX_LINE_BYTES:
            while raw and not raw.endswith(b"\n"):
                raw = stream.readline(_MAX_LINE_BYTES + 1)
            yield None, "[Oversized transcript record omitted]"
            continue
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            if not raw.endswith(b"\n"):
                yield None, "[Incomplete final transcript record omitted]"
                return
            raise TranscriptError(f"Invalid JSONL at transcript line {line_number}") from exc
        if not isinstance(row, dict):
            raise TranscriptError(f"Expected a Codex rollout object at line {line_number}")
        yield row, None


def _text_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise TranscriptError("Unsupported transcript content: expected text or content items")
    text = []
    for item in value:
        if not isinstance(item, dict):
            raise TranscriptError("Unsupported transcript content item")
        if item.get("type") in ("input_text", "output_text", "text"):
            if not isinstance(item.get("text"), str):
                raise TranscriptError("Transcript text item is missing its text field")
            text.append(item["text"])
    return "\n".join(text)


def _visible_item(payload: dict) -> tuple[str, bool] | None:
    kind = payload.get("type")
    if kind == "message":
        role = payload.get("role")
        if role not in ("user", "assistant"):
            return None
        if payload.get("channel") == "analysis" or payload.get("phase") == "analysis":
            return None
        if "content" not in payload:
            raise TranscriptError("Transcript message is missing its content field")
        text = _text_content(payload["content"])
        return (f"[{role}]\n{text}", role == "user") if text else None
    if kind in {"function_call", "custom_tool_call"}:
        field = "arguments" if kind == "function_call" else "input"
        name = payload.get("name")
        if not isinstance(name, str) or field not in payload:
            raise TranscriptError("Transcript tool call is missing its name or input")
        text = _text_content(payload[field])
        call_id = payload.get("call_id", "unknown")
        return f"[tool call: {name}; id={call_id}]\n{text}", False
    if kind in {"function_call_output", "custom_tool_call_output"}:
        if "output" not in payload:
            raise TranscriptError("Transcript tool result is missing its output field")
        text = _text_content(payload["output"])
        call_id = payload.get("call_id", "unknown")
        return (f"[tool result: id={call_id}]\n{text}", False) if text else None
    return None


def read_transcript(path: Path, max_chars: int = 60_000) -> str:
    """Return visible text, retaining the first three user messages and recent work.

    A metadata-only rollout is valid empty history. Missing files, empty files,
    malformed completed records, and unsupported formats raise TranscriptError.
    An incomplete trailing record is tolerated while Codex appends to its rollout.
    """
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 256:
        raise ValueError("max_chars must be an integer of at least 256")
    recent: deque[tuple[int, str]] = deque()
    total = 0
    initial: list[tuple[int, str]] = []
    omitted = False
    recognized = False
    sequence = 0
    try:
        with path.open("rb") as stream:
            for row, marker in _records(stream):
                user = False
                if row is None:
                    block = marker or ""
                else:
                    kind = row.get("type")
                    if not isinstance(kind, str) or kind not in _ROLLOUT_TYPES:
                        raise TranscriptError(
                            "Unsupported transcript format; expected a Codex JSONL rollout"
                        )
                    if not isinstance(row.get("payload"), dict):
                        raise TranscriptError("Transcript record is missing its payload object")
                    recognized = True
                    if kind != "response_item":
                        continue
                    payload = row.get("payload")
                    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
                        raise TranscriptError(
                            "Transcript response_item is missing its payload type"
                        )
                    visible = _visible_item(payload)
                    if visible is None:
                        continue
                    block, user = visible
                sequence += 1
                if user and len(initial) < 3:
                    initial.append((sequence, _clip(block, max_chars // 3)))
                block = _clip(block, max_chars - 2)
                recent.append((sequence, block))
                total += len(block) + 2
                while total > max_chars and len(recent) > 1:
                    _, removed = recent.popleft()
                    total -= len(removed) + 2
                    omitted = True
    except OSError as exc:
        raise TranscriptError(
            f"Cannot read transcript: {exc.strerror or type(exc).__name__}"
        ) from exc
    if not recognized:
        raise TranscriptError("Empty or unsupported transcript; expected a Codex JSONL rollout")
    if not omitted:
        return "\n\n".join(block for _, block in recent)
    prefix = ""
    if initial:
        per_message_budget = (max_chars // 3 - 2 * (len(initial) - 1)) // len(initial)
        prefix = "[Initial user messages]\n"
        prefix += "\n\n".join(_clip(block, per_message_budget) for _, block in initial) + "\n\n"
        initial_indexes = {index for index, _ in initial}
        recent = deque((index, block) for index, block in recent if index not in initial_indexes)
    prefix += _OMITTED + "\n\n"
    budget = max_chars - len(prefix)
    total = sum(len(block) + 2 for _, block in recent) - 2
    while len(recent) > 1 and total > budget:
        _, removed = recent.popleft()
        total -= len(removed) + 2
    tail = "\n\n".join(block for _, block in recent)
    return prefix + _clip(tail, budget)
