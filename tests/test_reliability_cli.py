import io
import json
from dataclasses import replace

import pytest

from you_should_know import backend, cli
from you_should_know.assessment import Finding
from you_should_know.config import Config
from you_should_know.state import State


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("YSK_DATA_DIR", str(tmp_path / "state"))

    def unexpected_review(*args, **kwargs):
        pytest.fail("Test attempted an unstubbed reviewer call")

    monkeypatch.setattr(backend, "review", unexpected_review)
    store = State(tmp_path / "state")
    yield store
    store.close()


def write_rollout(path, *, unknown=False):
    rows = [
        {"type": "session_meta", "payload": {}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": "Inspect the result"},
        },
        {
            "type": "response_item",
            "payload": {"type": "function_call_output", "call_id": "c", "output": "Command failed"},
        },
    ]
    if unknown:
        rows += [
            {"type": "retained_context", "payload": {}},
            {"type": "future_record", "payload": {}},
        ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.mark.parametrize("partial", [b'{"type":\n', b"\x00" * 300 + b"\n", b'{"type":'])
def test_transient_transcript_failure_retries_and_retains_diagnostics(
    state, tmp_path, monkeypatch, partial
):
    path = tmp_path / "rollout.jsonl"
    path.write_bytes(partial)
    sleeps = []
    calls = []

    def recover(delay):
        sleeps.append(delay)
        write_rollout(path, unknown=True)

    def reviewer(*args, **kwargs):
        calls.append(kwargs["timeout"])
        return {"finding": None}

    monkeypatch.setattr(cli.time, "sleep", recover)
    monkeypatch.setattr(backend, "review", reviewer)
    result = cli.check(state, Config(), "s", path, "Stop")
    assert result["status"] == "clear"
    assert sleeps == [0.1]
    assert len(calls) == 1
    assert state.session("s").attempts == 1
    report = state.diagnostics()
    error = report["recent_transcript_errors"][0]["diagnostics"]["transcript_errors"][0]
    assert error["path"] == str(path)
    assert error["file_size"] == len(partial)
    assert error["line_number"] == 1
    assert error["line_prefix"] == repr(partial[:200])
    assert state.status()[0]["unknown_record_types"] == {"retained_context": 1, "future_record": 1}
    assert state.session("s").consecutive_transcript_failures == 0


def test_persistent_transcript_failure_skips_without_budget_and_recovers(
    state, tmp_path, monkeypatch
):
    path = tmp_path / "rollout.jsonl"
    path.write_bytes(b"\x00\n")
    sleeps = []
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    for _ in range(3):
        result = cli.check(state, Config(), "s", path, "Stop")
        assert result["status"] == "transcript_unavailable"
    report = state.diagnostics()
    assert sleeps == [0.1] * 3
    assert state.session("s").attempts == 0
    assert report["model_calls"] == 0
    assert report["unreviewable_sessions"][0]["consecutive_transcript_failures"] == 3
    assert len(report["recent_transcript_errors"][0]["diagnostics"]["transcript_errors"]) == 2
    write_rollout(path)
    monkeypatch.setattr(backend, "review", lambda *a, **k: {"finding": None})
    assert cli.check(state, Config(), "s", path, "Stop")["status"] == "clear"
    assert state.diagnostics()["unreviewable_sessions"] == []


def test_retry_does_not_outlive_check_deadline(state, tmp_path, monkeypatch):
    path = tmp_path / "rollout.jsonl"
    path.write_bytes(b"\x00\n")
    now = [0.0]
    original_read = cli.read_snapshot

    def slow_read(*args, **kwargs):
        now[0] = 0.95
        return original_read(*args, **kwargs)

    monkeypatch.setattr(cli, "read_snapshot", slow_read)
    monkeypatch.setattr(cli.time, "monotonic", lambda: now[0])
    sleeps = []
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    result = cli.check(state, replace(Config(), timeout_seconds=1), "s", path, "Stop")
    assert result["status"] == "transcript_unavailable"
    assert sleeps == []
    assert state.session("s").attempts == 0


def test_status_cli_exposes_transcript_health(state, tmp_path, monkeypatch, capsys):
    path = tmp_path / "rollout.jsonl"
    path.write_bytes(b"\x00\n")
    monkeypatch.setattr(cli.time, "sleep", lambda delay: None)
    for _ in range(3):
        cli.check(state, Config(), "s", path, "Stop")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cli.main(["status"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["transcript_failure_threshold"] == 3
    assert report["unreviewable_sessions"][0]["session_id"] == "s"
    assert report["recent_transcript_errors"][0]["diagnostics"]["transcript_errors"][0][
        "path"
    ] == str(path)


@pytest.mark.parametrize("phase", ["review", "confirmation"])
def test_successful_slow_read_cannot_start_model_after_deadline(
    state, tmp_path, monkeypatch, phase
):
    path = tmp_path / "rollout.jsonl"
    write_rollout(path)
    if phase == "confirmation":
        state.observe("s", False)
        text, offset = cli.read_snapshot(path)
        state.candidate(
            "s", Finding("failure", "Failure", "Failure", ["Command failed"]), 100, text, offset
        )
    now = [0.0]
    reader = "read_snapshot" if phase == "review" else "read_since_snapshot"
    original_read = getattr(cli, reader)

    def slow_read(*args, **kwargs):
        now[0] = 2.0
        return original_read(*args, **kwargs)

    monkeypatch.setattr(cli, reader, slow_read)
    monkeypatch.setattr(cli.time, "monotonic", lambda: now[0])
    result = cli.check(state, replace(Config(), timeout_seconds=1), "s", path, "Stop")
    assert result["status"] == ("skipped" if phase == "review" else "pending")
    assert result["reason"] == "check time limit reached"
    assert state.session("s").attempts == 0


def test_reply_reader_persists_transcript_error(state, tmp_path, monkeypatch, capsys):
    path = tmp_path / "rollout.jsonl"
    path.write_bytes(b"\x00\n")
    event = {
        "session_id": "s",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "3",
        "transcript_path": str(path),
    }
    monkeypatch.setattr(cli, "noninteractive_host", lambda: False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert cli.main(["hook"]) == 0
    assert json.loads(capsys.readouterr().out) == {}
    error = state.diagnostics()["recent_transcript_errors"][0]
    assert error["event"] == "UserPromptSubmit"
    assert error["diagnostics"]["transcript_errors"][0]["line_number"] == 1
    assert error["model_called"] is False


@pytest.mark.parametrize("after_model", [False, True])
def test_confirmation_read_failure_preserves_budget_and_candidate_safety(
    state, tmp_path, monkeypatch, after_model
):
    path = tmp_path / "rollout.jsonl"
    write_rollout(path)
    state.observe("s", False)
    text, offset = cli.read_snapshot(path)
    state.candidate(
        "s", Finding("failure", "Failure", "Failure", ["Command failed"]), 100, text, offset
    )
    monkeypatch.setattr(cli.time, "sleep", lambda delay: None)

    def corrupt():
        with path.open("ab") as stream:
            stream.write(b"\x00\n")

    if after_model:

        def reviewer(*args, **kwargs):
            corrupt()
            return {"confirmed": True, "reason": "Unresolved", "evidence": ["Command failed"]}

        monkeypatch.setattr(backend, "review", reviewer)
    else:
        original_read = cli.read_since_snapshot
        calls = []

        def corrupt_read(*args, **kwargs):
            if not calls:
                corrupt()
            calls.append(1)
            return original_read(*args, **kwargs)

        monkeypatch.setattr(cli, "read_since_snapshot", corrupt_read)
    result = cli.check(state, Config(), "s", path, "Stop")
    assert result["status"] == "transcript_unavailable"
    assert state.session("s").attempts == int(after_model)
    assert state.notes() == []
    assert (state.pending_candidate("s") is None) is after_model
    report = state.diagnostics()
    assert report["model_calls"] == int(after_model)
    assert report["candidates"]["withdrawn" if after_model else "pending"] == 1
    errors = report["recent_transcript_errors"][0]["diagnostics"]["transcript_errors"]
    assert [error["line_number"] for error in errors] == [4, 4]
