import io
import json
import os
import threading
from dataclasses import replace

import pytest

from you_should_know import backend
from you_should_know.assessment import Finding
from you_should_know.cli import check, eligible, hook_result, main
from you_should_know.config import Config, ConfigError, data_directory, load_config
from you_should_know.state import State, review_lock


@pytest.fixture
def state(tmp_path):
    store = State(tmp_path / "state")
    yield store
    store.close()


@pytest.fixture
def transcript(tmp_path):
    path = tmp_path / "rollout.jsonl"
    rows = [
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Keep the previous settings."}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "c1",
                "output": "Deleted settings.json and wrote an empty object.",
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


@pytest.fixture
def finding():
    return Finding(
        "settings.json previous settings deleted",
        "Your previous settings were deleted.",
        "The tool output contradicts your request to preserve the settings.",
        ["Deleted settings.json and wrote an empty object."],
    )


def test_cadence_cooldown_stop_and_limit(state):
    config = Config()
    for _ in range(5):
        session = state.observe("session", True)
        assert not eligible(session, config, "PostToolUse", 1000, False)
    assert eligible(session, config, "Stop", 1000, False)
    session = state.observe("session", True)
    assert eligible(session, config, "PostToolUse", 1000, False)
    state.begin("session", 1000)
    session = state.observe("session", True)
    assert not eligible(session, config, "Stop", 1119, False)
    assert eligible(session, config, "Stop", 1120, False)
    assert not eligible(session, replace(config, max_checks_per_session=1), "Stop", 2000, False)
    assert eligible(session, config, "Stop", 1001, True)
    assert not eligible(session, replace(config, enabled=False), "Stop", 2000, True)


def test_manual_check_saves_validated_note_and_survives_reopen(
    state,
    transcript,
    finding,
    monkeypatch,
):
    def reviewer(prompt, schema_path, model, timeout):
        assert "Keep the previous settings." in prompt
        assert schema_path.is_file()
        assert model == "session-model"
        assert 89 < timeout <= 90
        return {"finding": finding.__dict__}

    monkeypatch.setattr(backend, "review", reviewer)
    result = check(state, Config(), "s1", transcript, "Stop", "session-model", force=True)
    assert result["status"] == "finding"
    note = result["note"]
    assert note["evidence"] == finding.evidence
    assert len(note["id"]) == 12
    assert set(hook_result(result)) == {"systemMessage"}
    other = State(state.directory)
    try:
        assert other.note(note["id"])["summary"] == finding.summary
        assert other.session("s1").last_success > 0
        assert other.session("s1").last_error == ""
    finally:
        other.close()


def test_duplicate_across_sessions_and_disposition(state, finding):
    identifier = state.save("one", finding, 100)
    assert identifier is not None
    assert state.save("two", replace(finding, topic="  " + finding.topic + "  "), 200) is None
    assert len(state.notes()) == 1
    state.mark(identifier, "known")
    assert state.notes() == []
    assert state.notes(True)[0]["status"] == "known"
    assert state.previous() == [finding]
    state.mark(identifier, "dismissed")
    assert state.note(identifier)["status"] == "dismissed"
    assert state.save("three", finding, 300) is None
    with pytest.raises(ValueError, match="Unknown note"):
        state.mark("missing", "known")
    with pytest.raises(ValueError, match="Invalid note status"):
        state.mark(identifier, "invalid")


def test_distinct_paths_remain_distinct_notes(state, finding):
    identifiers = [
        state.save("session", replace(finding, topic=topic), 100)
        for topic in (
            "src/a-b.py lost changes",
            "src/a/b.py lost changes",
            "src/A-b.py lost changes",
        )
    ]
    assert None not in identifiers
    assert len(set(identifiers)) == 3


def test_other_sessions_can_review_while_one_is_busy(state, transcript, monkeypatch):
    monkeypatch.setattr(backend, "review", lambda *args, **kwargs: {"finding": None})
    with review_lock(state.directory, "first-session") as locked:
        assert locked
        result = check(state, Config(), "second-session", transcript, "Stop", force=True)
    assert result["status"] == "clear"


def test_stop_checks_conversation_without_tool_calls(state, transcript, monkeypatch):
    monkeypatch.setattr(backend, "review", lambda *args, **kwargs: {"finding": None})
    result = check(state, Config(), "s", transcript, "Stop")
    assert result["status"] == "clear"
    assert state.session("s").events == 0


def test_stop_checks_later_assistant_message_after_cooldown(state, transcript, monkeypatch):
    calls = []

    def reviewer(*args, **kwargs):
        calls.append(args[0])
        return {"finding": None}

    monkeypatch.setattr(backend, "review", reviewer)
    config = replace(Config(), min_interval_seconds=0, every_n_tools=1)
    assert check(state, config, "s", transcript, "PostToolUse")["status"] == "clear"
    with transcript.open("a") as file:
        file.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "The final answer claims every setting was preserved.",
                            }
                        ],
                    },
                }
            )
            + "\n"
        )
    assert check(state, config, "s", transcript, "Stop")["status"] == "clear"
    assert len(calls) == 2
    assert "The final answer claims every setting was preserved." in calls[1]


def test_lock_waits_for_same_session_and_has_a_deadline(state):
    ready, release = threading.Event(), threading.Event()

    def worker():
        with review_lock(state.directory, "same"):
            ready.set()
            release.wait(2)

    thread = threading.Thread(target=worker)
    thread.start()
    assert ready.wait(2)
    try:
        with review_lock(state.directory, "same", 0.01) as acquired:
            assert not acquired
        release.set()
        with review_lock(state.directory, "same", 2) as acquired:
            assert acquired
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()


def test_busy_worker_does_not_spend_check_budget(state, transcript, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("busy check must not invoke a model")

    monkeypatch.setattr(backend, "review", forbidden)
    with review_lock(state.directory, "s1") as locked:
        assert locked
        result = check(state, Config(), "s1", transcript, "Stop", force=True)
    assert result == {"status": "busy"}
    assert state.session("s1").attempts == 0
    with review_lock(state.directory, "s1") as locked:
        assert locked


def test_cleared_and_unchanged_are_distinct_from_failure(state, transcript, monkeypatch):
    calls = []

    def reviewer(*args, **kwargs):
        calls.append(True)
        return {"finding": None}

    monkeypatch.setattr(backend, "review", reviewer)
    config = replace(Config(), min_interval_seconds=0, every_n_tools=1)
    assert check(state, config, "s", transcript, "PostToolUse")["status"] == "clear"
    assert check(state, config, "s", transcript, "PostToolUse")["status"] == "unchanged"
    assert len(calls) == 1
    assert state.notes() == []


def test_backend_failure_is_recorded_without_repeated_notifications(state, transcript, monkeypatch):
    def failed(*args, **kwargs):
        raise backend.BackendError("Codex review timed out")

    monkeypatch.setattr(backend, "review", failed)
    first = check(state, Config(), "s", transcript, "Stop", force=True)
    assert first["status"] == "error"
    assert "failed" in hook_result(first)["systemMessage"]
    assert state.session("s").last_error == "Codex review timed out"
    assert state.session("s").last_success == 0
    second = check(state, Config(), "s", transcript, "Stop", force=True)
    assert hook_result(second) == {}


def test_invalid_assessment_never_becomes_a_clear_check(state, transcript, monkeypatch):
    monkeypatch.setattr(backend, "review", lambda *args, **kwargs: {})
    result = check(state, Config(), "s", transcript, "Stop", force=True)
    assert result["status"] == "error"
    assert state.session("s").last_success == 0


def test_hook_protocol_and_cli_note_actions(state, transcript, finding, monkeypatch, capsys):
    monkeypatch.setattr(backend, "review", lambda *args, **kwargs: {"finding": finding.__dict__})
    (state.directory / "config.toml").write_text("every_n_tools = 1\n")
    event = {
        "session_id": "s",
        "hook_event_name": "PostToolUse",
        "transcript_path": str(transcript),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    prefix = ["--data-dir", str(state.directory)]
    assert main([*prefix, "hook"]) == 0
    warning = json.loads(capsys.readouterr().out)
    assert set(warning) == {"systemMessage"}
    assert finding.summary in warning["systemMessage"]
    assert main([*prefix, "notes"]) == 0
    identifier = json.loads(capsys.readouterr().out)["notes"][0]["id"]
    assert main([*prefix, "show", identifier]) == 0
    assert json.loads(capsys.readouterr().out)["evidence"] == finding.evidence
    assert main([*prefix, "know", identifier]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "known"
    assert main([*prefix, "dismiss", identifier]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dismissed"
    assert main([*prefix, "status"]) == 0
    assert json.loads(capsys.readouterr().out)["sessions"][0]["attempts"] == 1


@pytest.mark.parametrize("raw", ["[]", "{}", '{"session_id":4}', "broken"])
def test_bad_hook_input_is_advisory_and_not_a_turn_block(state, monkeypatch, capsys, raw):
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    assert main(["--data-dir", str(state.directory), "hook"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"systemMessage"}


def test_recursion_guard_does_not_read_stdin_or_create_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("YSK_REVIEWER_ACTIVE", "1")
    directory = tmp_path / "unused"
    assert main(["--data-dir", str(directory), "hook"]) == 0
    assert capsys.readouterr().out.strip() == "{}"
    assert not directory.exists()


def test_manual_failure_has_nonzero_exit(state, tmp_path, capsys):
    assert (
        main(
            [
                "--data-dir",
                str(state.directory),
                "check",
                "--transcript",
                str(tmp_path / "missing"),
                "--session",
                "s",
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_data_paths_and_permissions(state, monkeypatch, tmp_path):
    monkeypatch.setenv("YSK_DATA_DIR", str(tmp_path / "override"))
    assert data_directory() == tmp_path / "override"
    assert data_directory(str(tmp_path / "explicit")) == tmp_path / "explicit"
    assert os.stat(state.directory).st_mode & 0o777 == 0o700
    assert os.stat(state.directory / "state.sqlite3").st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "text",
    [
        "enabled = 1",
        "every_n_tools = true",
        "unknown = 4",
        "timeout_seconds = 120",
        "model = ''",
        "broken = [",
    ],
)
def test_invalid_settings_are_actionable(tmp_path, text):
    (tmp_path / "config.toml").write_text(text)
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_config_overrides_and_default(tmp_path):
    assert load_config(tmp_path) == Config()
    (tmp_path / "config.toml").write_text('model = "custom"\nenabled = false\n')
    assert load_config(tmp_path) == replace(Config(), model="custom", enabled=False)
