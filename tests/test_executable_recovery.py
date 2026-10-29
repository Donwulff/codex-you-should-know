import json
import sys
import time
from dataclasses import replace

import pytest

from you_should_know import backend, cli
from you_should_know.config import Config
from you_should_know.state import State


@pytest.fixture
def reviewer(tmp_path, monkeypatch):
    executable = tmp_path / "bin" / "codex"
    executable.parent.mkdir()
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv\n"
        "schema = Path(args[args.index('--output-schema') + 1]).name\n"
        "result = {'confirmed': True, 'reason': 'Still unresolved.', "
        "'evidence': ['Deleted settings.']} if schema == 'confirmation.schema.json' else "
        "{'finding': {'topic': 'lost settings', 'summary': 'Settings were lost.', "
        "'explanation': 'The settings are gone.', 'evidence': ['Deleted settings.'], "
        "'agent_still_working': False, 'assistant_claim': False}}\n"
        "Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps(result))\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, "
        "'output_tokens': 5}}))\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(executable.parent))
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "c",
                    "output": "Deleted settings.",
                },
            }
        )
        + "\n"
    )
    state = State(tmp_path / "state")
    yield state, executable, transcript
    state.close()


@pytest.mark.parametrize("phase", ["review", "confirmation"])
@pytest.mark.parametrize("failure", ["lookup", "launch", "format"])
def test_unavailable_reviewer_preserves_budget_and_recovers(reviewer, monkeypatch, phase, failure):
    state, executable, transcript = reviewer
    config = replace(Config(), every_n_tools=1, max_checks_per_session=2, max_checks_per_hour=2)
    if phase == "confirmation":
        first = cli.check(state, config, "s", transcript, "PostToolUse")
        assert first["status"] == "pending"
        candidate_id = first["candidate_id"]
    attempts = int(phase == "confirmation")
    retired = executable.with_name("retired")
    if failure in {"lookup", "format"}:
        executable.rename(retired)
        if failure == "format":
            executable.write_text("Invalid executable format\n")
            executable.chmod(0o700)
    else:
        which = backend.shutil.which

        def retire_after_lookup(name):
            found = which(name)
            assert found == str(executable)
            executable.rename(retired)
            return found

        monkeypatch.setattr(backend.shutil, "which", retire_after_lookup)
    result = cli.check(state, config, "s", transcript, "Stop")
    assert result["status"] == "error"
    if failure == "launch":
        monkeypatch.setattr(backend.shutil, "which", which)
    assert state.session("s").attempts == attempts
    assert state.budget_available("s", config, time.time())
    report = state.diagnostics()
    assert report["model_calls"] == attempts
    assert report["recent_checks"][0]["model_called"] is False
    assert report["recent_checks"][0]["diagnostics"]["backend"]["category"] == (
        "startup" if failure == "format" else "executable_unavailable"
    )
    if phase == "confirmation":
        assert state.pending_candidate("s")["id"] == candidate_id
    cooled = cli.check(state, config, "s", transcript, "PostToolUse")
    assert (cooled["status"], cooled["reason"]) == ("skipped", "cadence or cooldown")
    retired.rename(executable)
    recovered = cli.check(state, config, "s", transcript, "Stop")
    assert recovered["status"] == "finding"
    if phase == "confirmation":
        assert recovered["candidate_id"] == candidate_id
    assert state.session("s").attempts == 2
    assert state.diagnostics()["model_calls"] == 2
    assert not state.budget_available("s", config, time.time())
    assert state.pending_candidate("s") is None


def test_disappearance_between_review_and_confirmation_in_one_stop(reviewer):
    state, executable, transcript = reviewer
    original = executable.read_text()
    executable.write_text(
        original + "Path(sys.argv[0]).rename(Path(sys.argv[0]).with_name('retired'))\n"
    )
    config = replace(Config(), max_checks_per_session=2, max_checks_per_hour=2)
    failed = cli.check(state, config, "s", transcript, "Stop")
    assert failed["status"] == "error"
    candidate = state.pending_candidate("s")
    assert candidate is not None
    report = state.diagnostics()
    assert state.session("s").attempts == 1
    assert report["model_calls"] == 1
    assert report["input_tokens"] == 10
    assert [row["model_called"] for row in report["recent_checks"]] == [False, True]
    executable.with_name("retired").rename(executable)
    executable.write_text(original)
    recovered = cli.check(state, config, "s", transcript, "Stop")
    assert recovered["status"] == "finding"
    assert recovered["candidate_id"] == candidate["id"]
    assert state.session("s").attempts == 2
    assert state.diagnostics()["model_calls"] == 2


def test_started_reviewer_failure_still_consumes_budget(reviewer):
    state, executable, transcript = reviewer
    executable.write_text(f"#!{sys.executable}\nraise SystemExit(1)\n")
    config = replace(Config(), max_checks_per_session=1, max_checks_per_hour=1)
    result = cli.check(state, config, "s", transcript, "Stop")
    assert result["status"] == "error"
    assert state.session("s").attempts == 1
    assert state.diagnostics()["model_calls"] == 1
    assert not state.budget_available("s", config, time.time())


def test_cancelling_attempt_preserves_other_calls_and_retry_cooldown(reviewer):
    state, _, _ = reviewer
    state.observe("s", True)
    state.observe("other", True)
    first = state.begin("s", 1000)
    second = state.begin("s", 1000)
    other = state.begin("other", 1000)
    state.cancel_attempt("s", other)
    assert state.session("s").attempts == 2
    state.cancel_attempt("s", second)
    state.cancel_attempt("s", second)
    assert state.session("s").attempts == 1
    assert state.session("other").attempts == 1
    assert not state.budget_available("s", replace(Config(), max_checks_per_hour=1), 1001)
    state.cancel_attempt("s", first)
    assert state.session("s").attempts == 0
    assert state.budget_available("s", replace(Config(), max_checks_per_hour=1), 1001)
    state.observe("s", True)
    config = replace(Config(), every_n_tools=1, min_interval_seconds=120)
    assert not cli.eligible(state.session("s"), config, "PostToolUse", 1119, False)
    assert cli.eligible(state.session("s"), config, "PostToolUse", 1120, False)
