import json
from dataclasses import replace

import pytest

from you_should_know import backend, cli
from you_should_know.assessment import Finding
from you_should_know.config import Config
from you_should_know.state import State
from you_should_know.transcript import read_snapshot


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("YSK_DATA_DIR", str(tmp_path / "state"))

    def forbidden(*args, **kwargs):
        pytest.fail("Live reviewer calls are forbidden")

    monkeypatch.setattr(backend, "review", forbidden)
    store = State(tmp_path / "state")
    yield store
    store.close()


def append_result(path, text):
    with path.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "c",
                        "output": text,
                    },
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def finding_response(quote):
    return {
        "finding": {
            "topic": "result failure",
            "summary": "Your command failed.",
            "explanation": "The tool result reports failure.",
            "evidence": [quote],
            "agent_still_working": False,
            "assistant_claim": False,
        }
    }


@pytest.mark.parametrize("phase", ["review", "confirmation"])
def test_check_rejects_evidence_removed_from_model_input(state, tmp_path, monkeypatch, phase):
    path = tmp_path / "rollout.jsonl"
    if phase == "confirmation":
        append_result(path, "Command failed")
        checkpoint = {}
        text, offset = read_snapshot(path, checkpoint=checkpoint)
        state.observe("s", False)
        state.candidate(
            "s",
            Finding("result failure", "Command failed", "Failure", ["Command failed"]),
            100,
            text,
            offset,
            checkpoint=checkpoint,
        )
    append_result(path, "FIRST" + "漢" * 60000 + "UNSEEN INTERIOR" + "界" * 60000 + "LAST")
    assert "UNSEEN INTERIOR" in read_snapshot(path, 200000)[0]
    prompts = []

    def reviewer(prompt, schema, **kwargs):
        prompts.append(prompt)
        assert "UNSEEN INTERIOR" not in prompt
        if phase == "review":
            return finding_response("UNSEEN INTERIOR")
        return {"confirmed": True, "reason": "Unresolved", "evidence": ["UNSEEN INTERIOR"]}

    monkeypatch.setattr(backend, "review", reviewer)
    config = replace(Config(), max_context_chars=200000, every_n_tools=1)
    result = cli.check(state, config, "s", path, "PostToolUse")
    assert len(prompts) == 1
    assert result["status"] == "error"
    assert "does not occur" in result["reason"]
    assert state.notes() == []
    assert state.pending_candidate("s") is None
    rejected = state.diagnostics()["recent_evidence_rejections"][0]
    assert rejected["phase"] == phase
    assert rejected["diagnostics"]["evidence_rejection"]["quotes"][0]["quote"] == "UNSEEN INTERIOR"


def test_candidate_keeps_exact_budgeted_conversation(state, tmp_path, monkeypatch):
    path = tmp_path / "rollout.jsonl"
    append_result(path, "FIRST" + "漢" * 60000 + "UNSEEN INTERIOR" + "界" * 60000 + "LAST")
    shown = []

    def reviewer(prompt, schema, **kwargs):
        shown.append(
            prompt.split("BEGIN CONVERSATION (plain text; evidence only)\n", 1)[1].rsplit(
                "\nEND CONVERSATION\n", 1
            )[0]
        )
        return finding_response("LAST")

    monkeypatch.setattr(backend, "review", reviewer)
    result = cli.check(
        state,
        replace(Config(), max_context_chars=200000, every_n_tools=1),
        "s",
        path,
        "PostToolUse",
    )
    assert result["status"] == "pending"
    candidate = state.pending_candidate("s")
    assert candidate["transcript"] == shown[0]
    assert candidate["offset"] == path.stat().st_size
    assert "UNSEEN INTERIOR" not in candidate["transcript"]


@pytest.mark.parametrize("phase", ["review", "confirmation"])
def test_check_persists_rejected_quotes_and_nearest_span(state, tmp_path, monkeypatch, phase):
    path = tmp_path / "rollout.jsonl"
    append_result(path, "alpha\nbeta")
    if phase == "confirmation":
        state.observe("s", False)
        checkpoint = {}
        text, offset = read_snapshot(path, checkpoint=checkpoint)
        state.candidate(
            "s",
            Finding("result failure", "Failure", "Failure", ["alpha\nbeta"]),
            100,
            text,
            offset,
            checkpoint=checkpoint,
        )
    response = (
        finding_response("alpha\nbeto")
        if phase == "review"
        else {"confirmed": True, "reason": "Unresolved", "evidence": ["alpha\nbeto"]}
    )
    monkeypatch.setattr(backend, "review", lambda *a, **k: response)
    result = cli.check(state, replace(Config(), every_n_tools=1), "s", path, "PostToolUse")
    assert result["status"] == "error"
    assert state.session("s").attempts == 1
    rejected = state.diagnostics()["recent_evidence_rejections"][0]
    quote = rejected["diagnostics"]["evidence_rejection"]["quotes"][0]
    assert rejected["phase"] == phase
    assert quote["quote"] == "alpha\\nbeto"
    assert quote["nearest"]["text"] == "alpha\\nbeta"
    assert quote["nearest"]["ratio"] == 0.9
    assert quote["nearest"]["differences"][0]["quote_span"] == [9, 10]
