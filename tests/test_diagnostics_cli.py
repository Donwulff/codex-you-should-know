import json
import time
from dataclasses import replace

import pytest

from you_should_know import backend, cli
from you_should_know.assessment import Finding
from you_should_know.config import Config
from you_should_know.state import State


@pytest.fixture
def state(tmp_path, monkeypatch):
    directory = tmp_path / "state"
    monkeypatch.setenv("YSK_DATA_DIR", str(directory))

    def unexpected_review(*args, **kwargs):
        pytest.fail("Listing diagnostics must not call the reviewer")

    monkeypatch.setattr(backend, "review", unexpected_review)
    store = State(directory)
    yield store
    store.close()


@pytest.fixture
def finding():
    return Finding(
        "lost settings", "Settings were lost.", "The settings are gone.", ["Deleted settings."]
    )


@pytest.fixture
def transcript(tmp_path):
    path = tmp_path / "rollout.jsonl"
    path.write_text(
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
    return path


def response(finding, **flags):
    return {
        "finding": {
            **finding.__dict__,
            "agent_still_working": False,
            "assistant_claim": False,
            **flags,
        }
    }


def test_notes_lists_pending_candidates_separately_from_confirmed_notes(
    state, finding, tmp_path, capsys
):
    project = tmp_path / "project"
    state.hook_seen("s", "Stop", str(project))
    pending = state.candidate("s", finding, time.time(), "private transcript", 18)
    withdrawn = state.candidate("s", replace(finding, topic="withdrawn"), time.time(), "secret", 6)
    state.resolve_candidate(withdrawn, "withdrawn", "already resolved", time.time())
    note = state.save("s", replace(finding, topic="published"), time.time())
    assert cli.main(["notes", "--cwd", str(project)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in result["notes"]] == [note]
    assert [row["id"] for row in result["candidates"]] == [pending]
    assert result["candidates"][0]["status"] == "pending"
    assert set(result["candidates"][0]) == {
        "id",
        "session_id",
        "cwd",
        "status",
        "reason",
        "topic",
        "summary",
        "created_at",
        "touched_at",
    }
    assert state.pending_candidate("s")["id"] == pending
    assert state.session("s").attempts == 0


def test_notes_all_includes_candidate_history_and_disposed_notes(state, finding, capsys):
    state.observe("s", False)
    confirmed = state.candidate("s", finding, time.time(), "secret", 6)
    note = state.publish_candidate(confirmed, time.time())
    state.mark(note, "known")
    suppressed = state.candidate(
        "s",
        replace(finding, topic="suppressed"),
        time.time(),
        "secret",
        6,
        status="suppressed",
        reason="still working",
    )
    withdrawn = state.candidate("s", replace(finding, topic="withdrawn"), time.time(), "secret", 6)
    state.resolve_candidate(withdrawn, "withdrawn", "fixed", time.time())
    assert cli.main(["notes", "--all"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [(row["id"], row["status"]) for row in result["notes"]] == [(note, "known")]
    assert {row["id"]: row["status"] for row in result["candidates"]} == {
        confirmed: "confirmed",
        suppressed: "suppressed",
        withdrawn: "withdrawn",
    }


def test_status_scopes_history_before_limiting_and_keeps_skips_in_totals(state, tmp_path, capsys):
    project = tmp_path / "project"
    other = tmp_path / "project-extra"
    state.hook_seen("s", "Stop", str(project))
    state.hook_seen("other", "Stop", str(other))
    now = time.time()
    state.record_check("s", "Stop", "clear", now, 1)
    state.record_check("s", "Stop", "unchanged", now, 0)
    state.record_check("s", "PostToolUse", "skipped", now, 0, reason="cadence or cooldown")
    state.record_check("other", "Stop", "error", now, 9, reason="other project error")
    assert cli.main(["status", "--cwd", str(project), "--limit", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [row["outcome"] for row in report["recent_checks"]] == ["unchanged"]
    assert report["total_checks"] == 3
    assert report["errors"] == 0
    assert [row["session_id"] for row in report["sessions"]] == ["s"]
    assert set(report["hooks"]) == {"s"}
    assert cli.main(["status", "--cwd", str(project), "--limit", "1", "--skipped"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [row["outcome"] for row in report["recent_checks"]] == ["skipped"]
    assert report["total_checks"] == 3


@pytest.mark.parametrize("limit", ["0", "-1", "abc", "1.5"])
def test_invalid_history_limit_fails_before_creating_state(tmp_path, monkeypatch, capsys, limit):
    directory = tmp_path / "absent"
    monkeypatch.setenv("YSK_DATA_DIR", str(directory))
    with pytest.raises(SystemExit) as raised:
        cli.main(["status", "--limit", limit])
    assert raised.value.code == 2
    assert "positive integer" in capsys.readouterr().err
    assert not directory.exists()


@pytest.mark.parametrize("confirmed", [True, False])
def test_check_history_links_review_and_confirmation_to_the_same_candidate(
    state, finding, transcript, monkeypatch, confirmed
):
    replies = iter(
        [
            response(finding),
            {"confirmed": confirmed, "reason": "Review decision.", "evidence": finding.evidence},
        ]
    )
    monkeypatch.setattr(backend, "review", lambda *a, **k: next(replies))
    result = cli.check(state, Config(), "s", transcript, "Stop")
    assert result["status"] == ("finding" if confirmed else "withdrawn")
    checks = state.diagnostics()["recent_checks"]
    assert len(checks) == 2
    assert {row["candidate_id"] for row in checks} == {result["candidate_id"]}
    assert {row["candidate"]["topic"] for row in checks} == {finding.topic}
    assert {row["candidate"]["status"] for row in checks} == {
        "confirmed" if confirmed else "withdrawn"
    }
    assert checks[1]["note"] is None
    if confirmed:
        assert checks[0]["note_id"] == result["note"]["id"]
        assert checks[0]["note"]["summary"] == finding.summary
    else:
        assert checks[0]["note_id"] is None
        assert checks[0]["note"] is None


@pytest.mark.parametrize("duplicate", [False, True])
def test_suppressed_review_history_links_the_suppressed_candidate(
    state, finding, transcript, monkeypatch, duplicate
):
    if duplicate:
        state.save("previous", finding, time.time())
    monkeypatch.setattr(
        backend, "review", lambda *a, **k: response(finding, agent_still_working=not duplicate)
    )
    result = cli.check(state, Config(), "s", transcript, "Stop")
    assert result["status"] == ("duplicate" if duplicate else "suppressed")
    check = state.diagnostics()["recent_checks"][0]
    assert check["candidate_id"] == result["candidate_id"]
    assert check["candidate"]["topic"] == finding.topic
    assert check["candidate"]["status"] == "suppressed"


def test_failed_confirmation_history_retains_candidate_link(
    state, finding, transcript, monkeypatch
):
    def reviewer(prompt, schema_path, **kwargs):
        if schema_path == cli.SCHEMA:
            return response(finding)
        raise backend.BackendError("reviewer unavailable")

    monkeypatch.setattr(backend, "review", reviewer)
    result = cli.check(state, Config(), "s", transcript, "Stop")
    assert result["status"] == "error"
    check = state.diagnostics()["recent_checks"][0]
    assert check["candidate_id"] == result["candidate_id"]
    assert check["candidate"]["status"] == "withdrawn"
    assert "reviewer unavailable" in check["candidate"]["reason"]


def test_deferred_confirmation_history_keeps_the_pending_candidate(
    state, finding, transcript, monkeypatch
):
    monkeypatch.setattr(backend, "review", lambda *a, **k: response(finding))
    config = replace(Config(), every_n_tools=1, final_check=False)
    initial = cli.check(state, config, "s", transcript, "PostToolUse")
    assert initial["status"] == "pending"
    result = cli.check(state, config, "s", transcript, "Stop")
    assert result["reason"] == "final_check is disabled"
    check = state.diagnostics()["recent_checks"][0]
    assert check["candidate_id"] == initial["candidate_id"]
    assert check["candidate"]["status"] == "pending"
    assert check["candidate"]["reason"] == result["reason"]
    assert state.session("s").attempts == 1


def test_manual_checks_are_visible_in_the_invoking_directory(
    state, transcript, tmp_path, monkeypatch, capsys
):
    project = tmp_path / "manual-project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(backend, "review", lambda *a, **k: {"finding": None})
    assert cli.main(["check", "--session", "manual", "--transcript", str(transcript)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "clear"
    assert cli.main(["status", "--cwd", "."]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [row["session_id"] for row in report["recent_checks"]] == ["manual"]
    assert report["sessions"][0]["cwd"] == str(project)
    assert report["hooks"] == {}
