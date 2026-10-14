"""Synthetic Codex rollout fixtures; no captured user conversations."""

import json
import tracemalloc
from pathlib import Path

import pytest

from you_should_know.transcript import TranscriptError, read_transcript


def rollout(tmp_path: Path, *payloads: dict) -> Path:
    path = tmp_path / "synthetic-rollout.jsonl"
    rows = [{"type": "session_meta", "payload": {"cli_version": "0.162.1"}}]
    rows.extend({"type": "response_item", "payload": payload} for payload in payloads)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def message(role: str, text: str) -> dict:
    return {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}


def test_extracts_messages_and_both_tool_call_formats(tmp_path: Path) -> None:
    path = rollout(
        tmp_path,
        message("user", "Please inspect the synthetic fixture"),
        message("assistant", "I will inspect it"),
        {"type": "function_call", "name": "inspect", "call_id": "f1", "arguments": '{"a": 1}'},
        {"type": "function_call_output", "call_id": "f1", "output": "first result"},
        {"type": "custom_tool_call", "name": "patch", "call_id": "c2", "input": "patch input"},
        {
            "type": "custom_tool_call_output",
            "call_id": "c2",
            "output": [{"type": "input_text", "text": "second result"}],
        },
    )
    assert read_transcript(path) == (
        "[user]\nPlease inspect the synthetic fixture\n\n"
        "[assistant]\nI will inspect it\n\n"
        '[tool call: inspect; id=f1]\n{"a": 1}\n\n'
        "[tool result: id=f1]\nfirst result\n\n"
        "[tool call: patch; id=c2]\npatch input\n\n"
        "[tool result: id=c2]\nsecond result"
    )


def test_excludes_private_and_nontext_items(tmp_path: Path) -> None:
    forbidden = "FORBIDDEN_SYNTHETIC_REASONING_2d95"
    path = rollout(
        tmp_path,
        {"type": "reasoning", "summary": [{"text": forbidden}], "encrypted_content": forbidden},
        {**message("assistant", forbidden), "channel": "analysis"},
        message("developer", forbidden),
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": forbidden},
                {"type": "encrypted_content", "encrypted_content": forbidden},
                {"type": "input_text", "text": "Visible synthetic question"},
            ],
        },
    )
    assert forbidden not in read_transcript(path)
    assert read_transcript(path) == "[user]\nVisible synthetic question"


def test_valid_empty_history_is_distinct_from_missing_input(tmp_path: Path) -> None:
    assert read_transcript(rollout(tmp_path)) == ""
    with pytest.raises(TranscriptError, match="Cannot read transcript"):
        read_transcript(tmp_path / "missing.jsonl")


@pytest.mark.parametrize(
    "data",
    ["", "{}\n", "[]\n", '{"messages": []}\n', '{"type": "session_meta"}\n', '{"type": []}\n'],
)
def test_rejects_unsupported_or_empty_files(tmp_path: Path, data: str) -> None:
    path = tmp_path / "unsupported.jsonl"
    path.write_text(data, encoding="utf-8")
    with pytest.raises(TranscriptError):
        read_transcript(path)


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "message", "role": "user"},
        {"type": "message", "role": "user", "content": None},
        {"type": "message", "role": "user", "content": [{"type": "input_text"}]},
        {"type": "function_call", "name": "inspect"},
        {"type": "function_call_output"},
    ],
)
def test_rejects_malformed_visible_records(tmp_path: Path, payload: dict) -> None:
    with pytest.raises(TranscriptError):
        read_transcript(rollout(tmp_path, payload))


def test_retains_initial_intent_and_latest_work_when_bounded(tmp_path: Path) -> None:
    path = rollout(
        tmp_path,
        message("user", "Initial synthetic intent: repair the exporter"),
        *(message("assistant", f"Intermediate work {i}: " + "x" * 80) for i in range(100)),
        message("assistant", "Latest synthetic result: exporter verified"),
    )
    text = read_transcript(path, max_chars=512)
    assert len(text) <= 512
    assert "Initial synthetic intent: repair the exporter" in text
    assert "Latest synthetic result: exporter verified" in text
    assert "earlier conversation omitted" in text
    assert "Intermediate work 0:" not in text


def test_preserves_request_after_initial_user_instruction_messages(tmp_path: Path) -> None:
    path = rollout(
        tmp_path,
        message("user", "Synthetic repository instructions: " + "x" * 4_000),
        message("user", "Synthetic environment context: " + "y" * 4_000),
        message("user", "Actual request: repair exporter"),
        *(message("assistant", "Intermediate synthetic work: " + "z" * 80) for _ in range(100)),
        message("assistant", "Latest synthetic result: exporter verified"),
    )
    text = read_transcript(path, max_chars=512)
    assert len(text) <= 512
    assert "[Initial user messages]" in text
    assert "Actual request: repair exporter" in text
    assert "Latest synthetic result: exporter verified" in text


def test_three_initial_messages_fit_the_minimum_budget(tmp_path: Path) -> None:
    path = rollout(
        tmp_path,
        *(message("user", str(i) * 1_000) for i in range(3)),
        message("assistant", "Latest result"),
    )
    text = read_transcript(path, max_chars=256)
    assert len(text) <= 256
    assert "Latest result" in text
    assert "[Initial user messages]" in text


def test_marks_truncation_inside_a_large_message(tmp_path: Path) -> None:
    text = read_transcript(rollout(tmp_path, message("user", "START" + "x" * 4_000 + "END")), 256)
    assert len(text) <= 256
    assert "START" in text
    assert "END" in text
    assert "content truncated" in text


def test_tolerates_an_incomplete_final_record(tmp_path: Path) -> None:
    path = rollout(tmp_path, message("user", "Complete synthetic message"))
    with path.open("ab") as stream:
        stream.write(b'{"type":"response_item","payload":')
    text = read_transcript(path)
    assert "Complete synthetic message" in text
    assert "Incomplete final transcript record omitted" in text


def test_accepts_complete_final_record_without_newline(tmp_path: Path) -> None:
    path = rollout(tmp_path, message("user", "No newline required"))
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    assert read_transcript(path) == "[user]\nNo newline required"


def test_rejects_malformed_completed_record(tmp_path: Path) -> None:
    path = rollout(tmp_path, message("user", "Complete synthetic message"))
    with path.open("ab") as stream:
        stream.write(b'{"broken":\n')
    with pytest.raises(TranscriptError, match="Invalid JSONL"):
        read_transcript(path)


def test_skips_oversized_record_and_reads_later_work(tmp_path: Path) -> None:
    path = rollout(
        tmp_path,
        message("user", "Initial synthetic request"),
        message("assistant", "x" * 1_100_000),
        message("assistant", "Latest small result"),
    )
    text = read_transcript(path, 512)
    assert "Oversized transcript record omitted" in text
    assert "Latest small result" in text
    assert len(text) <= 512


def test_large_record_does_not_require_loading_the_file_into_memory(tmp_path: Path) -> None:
    path = rollout(tmp_path, message("user", "Initial synthetic request"))
    with path.open("ab") as stream:
        stream.write(b'{"type":"response_item","payload":{"type":"reasoning","encrypted_content":"')
        for _ in range(4_096):
            stream.write(b"x" * 4_096)
        stream.write(b'"}}\n')
        stream.write(
            json.dumps(
                {"type": "response_item", "payload": message("assistant", "Latest result")}
            ).encode()
            + b"\n"
        )
    tracemalloc.start()
    try:
        text = read_transcript(path, 512)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert "Latest result" in text
    assert "Oversized transcript record omitted" in text
    assert peak < 8 * 1_024 * 1_024


def test_does_not_duplicate_event_messages(tmp_path: Path) -> None:
    path = rollout(tmp_path, message("assistant", "Unique result"))
    with path.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "Unique result"},
                }
            )
            + "\n"
        )
    assert read_transcript(path).count("Unique result") == 1


@pytest.mark.parametrize("max_chars", [0, 255, True])
def test_rejects_impossibly_small_budgets(tmp_path: Path, max_chars: int) -> None:
    with pytest.raises(ValueError, match="at least 256"):
        read_transcript(rollout(tmp_path), max_chars)
