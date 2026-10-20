import io
import json
from dataclasses import replace

import pytest

from you_should_know import backend, cli
from you_should_know.assessment import Finding
from you_should_know.config import Config
from you_should_know.state import State


@pytest.fixture
def state(tmp_path):
    store = State(tmp_path / "state")
    yield store
    store.close()


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("YSK_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "noninteractive_host", lambda: False)

    def unexpected_review(*args, **kwargs):
        pytest.fail("Test attempted an unstubbed reviewer call")

    monkeypatch.setattr(backend, "review", unexpected_review)


def append(path, kind, **payload):
    with path.open("a") as stream:
        stream.write(json.dumps({"type": "response_item", "payload": {"type": kind, **payload}}))
        stream.write("\n")


@pytest.fixture
def transcript(tmp_path):
    path = tmp_path / "rollout.jsonl"
    append(path, "message", role="user", content="Preserve my settings.")
    append(path, "function_call_output", call_id="c", output='Deleted "settings.json".')
    return path


@pytest.fixture
def finding():
    return Finding(
        "lost settings",
        "Settings were lost.",
        "The settings are gone.",
        ['Deleted "settings.json".'],
    )


def response(finding, **flags):
    return {
        "finding": {
            **finding.__dict__,
            "agent_still_working": False,
            "assistant_claim": False,
            **flags,
        }
    }


def hook(monkeypatch, capsys, event, command="hook"):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert cli.main([command]) == 0
    return json.loads(capsys.readouterr().out)


def test_unchanged_transcript_does_not_spend_an_attempt(state, transcript, monkeypatch):
    monkeypatch.setattr(backend, "review", lambda *a, **k: {"finding": None})
    config = replace(Config(), every_n_tools=1, min_interval_seconds=0)
    assert cli.check(state, config, "s", transcript, "PostToolUse")["status"] == "clear"
    assert cli.check(state, config, "s", transcript, "PostToolUse")["status"] == "unchanged"
    assert state.session("s").attempts == 1


@pytest.mark.parametrize(
    "configured,supplied,force,expected,source",
    [
        (None, "gpt-6-astra", False, "gpt-6-astra", "hook"),
        (None, "gpt-6-luna", True, "gpt-6-luna", "argument"),
        ("gpt-6.1-sol", "gpt-6-astra", False, "gpt-6.1-sol", "config"),
        ("gpt-6.1-sol", "gpt-6-astra", True, "gpt-6.1-sol", "config"),
        (None, None, True, None, "cli_default"),
    ],
)
def test_check_records_selected_model_and_source(
    state, transcript, monkeypatch, configured, supplied, force, expected, source
):
    called = []

    def reviewer(*args, **kwargs):
        called.append(kwargs["model"])
        return {"finding": None}

    monkeypatch.setattr(backend, "review", reviewer)
    result = cli.check(
        state, replace(Config(), model=configured), "s", transcript, "Stop", supplied, force
    )
    assert called == [expected]
    assert (result["model"], result["model_source"]) == (expected, source)
    recorded = state.diagnostics()["recent_checks"][0]
    assert (recorded["model"], recorded["model_source"]) == (expected, source)


def test_confirmation_records_its_own_model_after_chat_model_changes(
    state, transcript, finding, monkeypatch
):
    models = []

    def reviewer(prompt, schema, **kwargs):
        models.append(kwargs["model"])
        if schema == cli.SCHEMA:
            return response(finding)
        return {"confirmed": True, "reason": "Still unresolved", "evidence": finding.evidence}

    monkeypatch.setattr(backend, "review", reviewer)
    config = replace(Config(), every_n_tools=1)
    assert (
        cli.check(state, config, "s", transcript, "PostToolUse", "gpt-6.1-sol")["status"]
        == "pending"
    )
    assert cli.check(state, config, "s", transcript, "Stop", "gpt-6-astra")["status"] == "finding"
    assert models == ["gpt-6.1-sol", "gpt-6-astra"]
    phases = {item["phase"]: item["model"] for item in state.diagnostics()["recent_checks"]}
    assert phases == {"review": "gpt-6.1-sol", "confirmation": "gpt-6-astra"}


def test_configured_review_focus_reaches_reviewer(state, transcript, monkeypatch):
    focus = "Pay particular attention to recurring usage costs."

    def reviewer(prompt, schema, **kwargs):
        assert focus in prompt
        assert "Every finding must be demonstrated by a tool result" in prompt
        return {"finding": None}

    monkeypatch.setattr(backend, "review", reviewer)
    config = replace(Config(), review_instructions=focus)
    assert cli.check(state, config, "s", transcript, "Stop")["status"] == "clear"


@pytest.mark.parametrize(
    "question",
    [
        "Which option: 1, 2 or 3?",
        "Choose:\n1. Keep\n2. Replace\n3. Dismiss",
        "Choose:\nA) Keep\nB) Dismiss",
        "Choose:\n**1.** Keep\n**2.** Replace\n**3.** Dismiss",
        "Choose:\n[1] Keep\n[2] Replace\n[3] Dismiss",
        "Choose:\n(A) Keep\n(B) Replace\n(C) Dismiss",
    ],
)
@pytest.mark.parametrize("answer", ["3", "dismiss"])
def test_agent_answers_are_not_swallowed(
    state, transcript, finding, monkeypatch, capsys, question, answer
):
    identifier = state.save("s", finding, 100, notify=True)
    state.deliver("s", ready=True, turn_id="done")
    append(transcript, "message", role="assistant", content=question)
    event = {
        "session_id": "s",
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "next",
        "transcript_path": str(transcript),
        "prompt": answer,
    }
    assert hook(monkeypatch, capsys, event) == {}
    assert state.note(identifier)["status"] == "new"


def test_prefixed_reply_works_when_agent_has_question(
    state, transcript, finding, monkeypatch, capsys
):
    identifier = state.save("s", finding, 100, notify=True)
    state.deliver("s", ready=True, turn_id="done")
    append(transcript, "message", role="assistant", content="Which option: 1, 2 or 3?")
    event = {
        "session_id": "s",
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "next",
        "transcript_path": str(transcript),
        "prompt": "ysk 3",
    }
    assert hook(monkeypatch, capsys, event)["continue"] is False
    assert state.note(identifier)["status"] == "dismissed"


def test_stop_does_not_repeat_midturn_body(state, transcript, finding, monkeypatch, capsys):
    state.save("s", finding, 100, notify=True)
    event = {"session_id": "s", "hook_event_name": "PostToolUse"}
    assert finding.summary in hook(monkeypatch, capsys, event, "notify")["systemMessage"]
    monkeypatch.setattr(backend, "review", lambda *a, **k: {"finding": None})
    event.update(hook_event_name="Stop", turn_id="done", transcript_path=str(transcript))
    message = hook(monkeypatch, capsys, event)["systemMessage"]
    assert finding.summary not in message
    assert "After the turn ends" not in message
    assert "ysk" in message


def test_config_error_shown_once_per_session_revision(state, monkeypatch, capsys):
    path = state.directory / "config.toml"
    path.write_text("every_n_tools = 0\n")
    event = {"session_id": "s", "hook_event_name": "PostToolUse"}
    first = hook(monkeypatch, capsys, event, "notify")
    assert str(path) in first["systemMessage"]
    assert hook(monkeypatch, capsys, event) == {}
    assert hook(monkeypatch, capsys, event, "notify") == {}
    path.write_text("every_n_tools = -1\n")
    assert "systemMessage" in hook(monkeypatch, capsys, event)


def test_empty_notify_skips_process_tree(monkeypatch, capsys):
    def walk():
        pytest.fail("Empty notification traversed host process tree")

    monkeypatch.setattr(cli, "noninteractive_host", walk)
    assert (
        hook(monkeypatch, capsys, {"session_id": "s", "hook_event_name": "PostToolUse"}, "notify")
        == {}
    )


def test_finding_waits_for_next_eligible_confirmation(state, transcript, finding, monkeypatch):
    replies = iter(
        [
            response(finding),
            {
                "confirmed": True,
                "reason": "Still unresolved and undisclosed.",
                "evidence": finding.evidence,
            },
        ]
    )
    prompts = []

    def reviewer(prompt, *args, **kwargs):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(backend, "review", reviewer)
    config = replace(Config(), every_n_tools=1, min_interval_seconds=0)
    result = cli.check(state, config, "s", transcript, "PostToolUse")
    assert result["status"] == "pending"
    assert state.notes() == []
    assert cli.notifications(state, "s") == {}
    append(transcript, "message", role="assistant", content="Done.")
    result = cli.check(state, config, "s", transcript, "PostToolUse")
    assert result["status"] == "finding"
    assert result["note"]["summary"] == finding.summary
    assert len(prompts) == 2
    assert "Done." in prompts[1]
    assert state.session("s").attempts == 2


@pytest.mark.parametrize(
    "followup", ["I restored the settings.", "The old settings were lost; this remains unresolved."]
)
def test_stop_withdraws_fixed_or_disclosed_candidate(
    state, transcript, finding, monkeypatch, followup
):
    replies = iter(
        [
            response(finding),
            {
                "confirmed": False,
                "reason": "Resolved or disclosed in the latest assistant message.",
                "evidence": [followup],
            },
        ]
    )
    monkeypatch.setattr(backend, "review", lambda *a, **k: next(replies))
    config = replace(Config(), every_n_tools=1)
    assert cli.check(state, config, "s", transcript, "PostToolUse")["status"] == "pending"
    append(transcript, "message", role="assistant", content=followup)
    assert cli.check(state, config, "s", transcript, "Stop")["status"] == "withdrawn"
    assert state.notes() == []
    assert state.session("s").attempts == 2


def test_stop_confirms_new_candidate_in_same_stop(state, transcript, finding, monkeypatch):
    replies = iter(
        [
            response(finding),
            {
                "confirmed": True,
                "reason": "Unresolved and undisclosed.",
                "evidence": finding.evidence,
            },
        ]
    )
    monkeypatch.setattr(backend, "review", lambda *a, **k: next(replies))
    assert cli.check(state, Config(), "s", transcript, "Stop")["status"] == "finding"
    assert state.session("s").attempts == 2


def test_confirmation_keeps_both_sides_of_assistant_claim(state, transcript, finding, monkeypatch):
    append(transcript, "message", role="assistant", content="Your settings are preserved.")
    claim = replace(finding, evidence=[*finding.evidence, "Your settings are preserved."])
    replies = iter(
        [
            response(claim, assistant_claim=True),
            {"confirmed": True, "reason": "Contradiction remains.", "evidence": finding.evidence},
        ]
    )
    monkeypatch.setattr(backend, "review", lambda *a, **k: next(replies))
    result = cli.check(state, Config(), "s", transcript, "Stop")
    assert result["status"] == "error"
    assert "assistant" in result["error"]
    assert state.notes() == []


def test_work_during_confirmation_withdraws_stale_candidate(
    state, transcript, finding, monkeypatch
):
    def reviewer(prompt, schema_path, **kwargs):
        if schema_path == cli.SCHEMA:
            return response(finding)
        append(transcript, "message", role="assistant", content="I restored all old settings.")
        return {"confirmed": True, "reason": "Unresolved.", "evidence": finding.evidence}

    monkeypatch.setattr(backend, "review", reviewer)
    result = cli.check(state, Config(), "s", transcript, "Stop")
    assert result["status"] == "withdrawn"
    assert "advanced" in result["reason"]
    assert state.notes() == []
    assert state.session("s").attempts == 2


@pytest.mark.parametrize("bad_config", [False, True])
def test_notification_hook_cannot_mask_untrusted_review_hook(
    state, finding, monkeypatch, capsys, bad_config
):
    if bad_config:
        (state.directory / "config.toml").write_text("every_n_tools = 0\n")
    state.save("s", finding, 100, notify=True)
    hook(monkeypatch, capsys, {"session_id": "s", "hook_event_name": "PostToolUse"}, "notify")
    seen = state.diagnostics()["hooks"]["s"]
    assert "PostToolUse:notify" in seen
    assert "PostToolUse" not in seen


def test_final_check_off_retains_candidate_and_delivers_existing_note(
    state, transcript, finding, monkeypatch, capsys
):
    (state.directory / "config.toml").write_text("final_check = false\n")
    queued = replace(finding, topic="other", summary="Another confirmed note.")
    state.save("s", queued, 100, notify=True)
    state.candidate("s", finding, 100, "original", 0)
    event = {
        "session_id": "s",
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "turn_id": "done",
    }
    result = hook(monkeypatch, capsys, event)
    assert queued.summary in result["systemMessage"]
    assert "unconfirmed" in result["systemMessage"]
    assert state.pending_candidate("s")["reason"] == "final_check is disabled"
    assert state.session("s").attempts == 0


def test_failed_confirmation_is_a_recorded_withdrawal_without_note(
    state, transcript, finding, monkeypatch
):
    def reviewer(prompt, schema_path, **kwargs):
        if schema_path == cli.SCHEMA:
            return response(finding)
        raise backend.BackendError("Codex attempted tool use; review discarded")

    monkeypatch.setattr(backend, "review", reviewer)
    result = cli.check(state, Config(), "s", transcript, "Stop")
    assert result["status"] == "error"
    assert state.notes() == []
    assert state.pending_candidate("s") is None
    status = state.diagnostics()
    assert status["candidates"]["withdrawn"] == 1
    assert "Confirmation failed" in status["candidate_reasons"][0]["reason"]
    assert status["errors"] == 1
    assert state.session("s").attempts == 2


def test_suppressed_working_candidate_is_counted_without_confirmation(
    state, transcript, finding, monkeypatch
):
    monkeypatch.setattr(
        backend, "review", lambda *a, **k: response(finding, agent_still_working=True)
    )
    assert cli.check(state, Config(), "s", transcript, "Stop")["status"] == "suppressed"
    assert state.session("s").attempts == 1
    assert state.notes() == []
    assert state.diagnostics()["candidates"]["suppressed"] == 1


def test_budget_notice_once_and_pending_confirmation_recovers_after_hour(
    state, transcript, finding, monkeypatch
):
    now = [1000.0]
    monkeypatch.setattr(cli.time, "time", lambda: now[0])
    replies = iter(
        [
            response(finding),
            {"confirmed": True, "reason": "Unresolved.", "evidence": finding.evidence},
        ]
    )
    monkeypatch.setattr(backend, "review", lambda *a, **k: next(replies))
    config = replace(Config(), every_n_tools=1, min_interval_seconds=0, max_checks_per_hour=1)
    assert cli.check(state, config, "s", transcript, "PostToolUse")["status"] == "pending"
    assert cli.check(state, config, "s", transcript, "Stop")["status"] == "pending"
    message = cli.notifications(state, "s")["systemMessage"]
    assert "budget exhausted" in message
    assert state.session("s").attempts == 1
    cli.check(state, config, "s", transcript, "Stop")
    assert cli.notifications(state, "s") == {}
    now[0] += 3600
    assert cli.check(state, config, "s", transcript, "PostToolUse")["status"] == "finding"
    assert state.session("s").attempts == 2


def test_usage_status_and_debug_reach_synchronous_notification(
    state, transcript, monkeypatch, capsys
):
    monkeypatch.setenv("YSK_DEBUG", "1")
    (state.directory / "config.toml").write_text("every_n_tools = 1\nmin_interval_seconds = 0\n")
    monkeypatch.setattr(
        backend,
        "review",
        lambda *a, **k: backend.ReviewResponse(
            {"finding": None}, {"input_tokens": 11, "cached_input_tokens": 4, "output_tokens": 3}
        ),
    )
    event = {
        "session_id": "s",
        "hook_event_name": "PostToolUse",
        "transcript_path": str(transcript),
    }
    assert hook(monkeypatch, capsys, event) == {}
    message = hook(monkeypatch, capsys, event, "notify")["systemMessage"]
    assert "YSK debug: PostToolUse clear" in message
    assert "11 in/3 out tokens" in message
    assert len(message.splitlines()) == 1
    hook(monkeypatch, capsys, event)
    assert cli.main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["input_tokens"] == 11
    assert status["output_tokens"] == 3
    assert status["cached_input_tokens"] == 4
    assert status["recent_checks"][0]["outcome"] == "unchanged"
    assert status["recent_checks"][1]["latency"] >= 0


@pytest.mark.parametrize("project", [False, True])
def test_mute_and_unmute_control_real_hook_path(
    state, transcript, monkeypatch, capsys, tmp_path, project
):
    event = {
        "session_id": "s",
        "hook_event_name": "UserPromptSubmit",
        "cwd": str(tmp_path),
        "turn_id": "next",
        "prompt": "ysk off project" if project else "ysk off",
    }
    assert hook(monkeypatch, capsys, event)["continue"] is False
    stop = {
        **event,
        "session_id": "other" if project else "s",
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
    }
    assert hook(monkeypatch, capsys, stop) == {}
    event["prompt"] = "ysk on project" if project else "ysk on"
    assert hook(monkeypatch, capsys, event)["continue"] is False
    calls = []

    def reviewer(*args, **kwargs):
        calls.append(True)
        return {"finding": None}

    monkeypatch.setattr(backend, "review", reviewer)
    assert hook(monkeypatch, capsys, stop) == {}
    assert calls == [True]


def test_ambiguous_hints_only_offer_prefixed_actions(
    state, transcript, finding, monkeypatch, capsys
):
    state.save("s", finding, 100, notify=True)
    append(transcript, "message", role="assistant", content="Which choice?")
    (state.directory / "config.toml").write_text("final_check = false\n")
    event = {
        "session_id": "s",
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "turn_id": "done",
    }
    message = hook(monkeypatch, capsys, event)["systemMessage"]
    assert "[ysk 1]" in message
    assert "Reply now with a number" not in message
    event.update(hook_event_name="UserPromptSubmit", prompt="ysk 1", turn_id="next")
    message = hook(monkeypatch, capsys, event)["systemMessage"]
    assert 'reply "ysk 4 your message"' in message
    assert 'reply "4 your message"' not in message


def test_missing_transcript_never_intercepts_bare_answers(state, finding, monkeypatch, capsys):
    identifier = state.save("s", finding, 100, notify=True)
    state.deliver("s", ready=True, turn_id="done")
    event = {
        "session_id": "s",
        "hook_event_name": "UserPromptSubmit",
        "turn_id": "next",
        "prompt": "3",
    }
    assert hook(monkeypatch, capsys, event) == {}
    assert state.note(identifier)["status"] == "new"


@pytest.mark.parametrize("reported", [False, True])
def test_error_usage_totals_distinguish_known_usage_from_unknown(
    state, transcript, monkeypatch, reported
):
    def reviewer(*args, **kwargs):
        raise backend.BackendError(
            "Codex returned no valid JSON review",
            usage={"input_tokens": 1234, "output_tokens": 45} if reported else None,
        )

    monkeypatch.setattr(backend, "review", reviewer)
    assert cli.check(state, Config(), "s", transcript, "Stop")["status"] == "error"
    status = state.diagnostics()
    assert status["input_tokens"] == (1234 if reported else 0)
    assert status["output_tokens"] == (45 if reported else 0)
    assert status["model_calls"] == 1
    assert status["usage_unknown_calls"] == (0 if reported else 1)
