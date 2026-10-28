import fcntl
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from you_should_know import state as state_module
from you_should_know.state import State, review_lock


@pytest.mark.parametrize("wait", [0, 0.05])
def test_active_pruning_cannot_block_review_past_deadline(tmp_path, wait):
    with (tmp_path / ".review-prune.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys\nfrom pathlib import Path\n"
                "from you_should_know.state import review_lock\n"
                "with review_lock(Path(sys.argv[1]), 's', float(sys.argv[2])) as acquired:\n"
                "    print(acquired)\n",
                str(tmp_path),
                str(wait),
            ],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            capture_output=True,
            text=True,
            timeout=1,
            check=True,
        )
        assert result.stdout.strip() == "False"


def test_prune_and_session_lock_share_one_wait_budget(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(state_module.time, "monotonic", lambda: now[0])

    def sleep(delay):
        now[0] += delay

    def flock(descriptor, operation):
        if operation & fcntl.LOCK_SH and now[0] >= 0.1:
            return
        raise BlockingIOError

    monkeypatch.setattr(state_module.time, "sleep", sleep)
    monkeypatch.setattr(state_module.fcntl, "flock", flock)
    with review_lock(tmp_path, "s", 0.2) as acquired:
        assert not acquired
    assert now[0] == pytest.approx(0.2)


def test_model_backoff_survives_reopen_and_never_shortens(tmp_path, monkeypatch):
    monkeypatch.setattr("you_should_know.state.time.time", lambda: 1000)
    first = State(tmp_path)
    second = State(tmp_path)
    try:
        first.defer_reviewer("model", 1000, 360)
        second.defer_reviewer("model", 1000, 120)
        second.defer_reviewer(None, 1000)
        assert first.reviewer_retry_at("model") == 1360
        assert first.reviewer_retry_at(None) == 1300
        assert first.reviewer_retry_at("another-model") == 0
        assert first.prune(30, 1300)["reviewer_backoff"] == 1
        assert second.reviewer_retry_at(None) == 0
        assert second.reviewer_retry_at("model") == 1360
        assert second.prune(30, 1360)["reviewer_backoff"] == 1
        assert first.reviewer_retry_at("model") == 0
    finally:
        first.close()
        second.close()


def test_check_diagnostics_survive_restart_as_structured_data(tmp_path):
    diagnostics = {
        "transcript_errors": [{"path": "/synthetic/rollout.jsonl", "line_prefix": "\\x00"}],
        "evidence_rejection": {"reason": "not present", "quotes": ["line\\nquote"]},
    }
    state = State(tmp_path)
    state.record_check(
        "session", "Stop", "error", 100, 0.1, diagnostics=diagnostics, model_called=True
    )
    state.close()

    reopened = State(tmp_path)
    check = reopened.diagnostics()["recent_checks"][0]
    assert check["diagnostics"] == diagnostics
    assert check["model_called"] is True
    assert check["usage_reported"] is False
    assert (
        json.loads(reopened.connection.execute("SELECT diagnostics FROM checks").fetchone()[0])
        == diagnostics
    )
    reopened.close()


def test_recent_errors_are_filtered_before_the_twenty_check_limit(tmp_path):
    state = State(tmp_path)
    for index in range(25):
        state.record_check(
            "transcript",
            "Stop",
            "transcript_unavailable",
            index,
            0,
            diagnostics={"transcript_errors": [{"line_number": index}]},
        )
        state.record_check(
            "evidence",
            "Stop",
            "error",
            index,
            0,
            diagnostics={"evidence_rejection": {"reason": str(index)}},
        )
    state.record_check("empty", "Stop", "clear", 30, 0, diagnostics={"transcript_errors": []})
    for index in range(30):
        state.record_check("healthy", "Stop", "clear", 31 + index, 0)
    report = state.diagnostics()
    assert len(report["recent_checks"]) == 20
    assert all(check["session_id"] == "healthy" for check in report["recent_checks"])
    assert [
        check["diagnostics"]["transcript_errors"][0]["line_number"]
        for check in report["recent_transcript_errors"]
    ] == list(range(24, 4, -1))
    assert [
        check["diagnostics"]["evidence_rejection"]["reason"]
        for check in report["recent_evidence_rejections"]
    ] == [str(index) for index in range(24, 4, -1)]
    state.close()


def test_transcript_failure_streak_and_unknown_types_survive_restart(tmp_path):
    state = State(tmp_path)
    state.observe("session", False)
    state.observe("other", False)
    state.record_transcript_read("session", unknown_types={"retained_context": 2})
    state.record_transcript_read("session", unknown_types={"retained_context": 2})
    state.record_transcript_read("session", failed=True)
    state.record_transcript_read("session", failed=True)
    state.close()

    reopened = State(tmp_path)
    session = reopened.session("session")
    assert session.consecutive_transcript_failures == 2
    assert json.loads(session.unknown_record_types) == {"retained_context": 2}
    assert reopened.session("other").consecutive_transcript_failures == 0
    reopened.record_transcript_read("session")
    assert reopened.session("session").consecutive_transcript_failures == 0
    assert json.loads(reopened.session("session").unknown_record_types) == {"retained_context": 2}
    reopened.record_transcript_read("session", unknown_types={})
    status = {item["session_id"]: item for item in reopened.status()}
    assert status["session"]["unknown_record_types"] == {}
    assert status["session"]["consecutive_transcript_failures"] == 0
    reopened.close()


def test_transcript_and_evidence_diagnostics_respect_cwd_filter(tmp_path):
    state = State(tmp_path)
    project = str(tmp_path / "project")
    for session_id, cwd in (("here", project), ("there", str(tmp_path / "other"))):
        state.hook_seen(session_id, "Stop", cwd)
        state.record_transcript_read(session_id, unknown_types={"retained_context": 2})
        for _ in range(3):
            state.record_transcript_read(session_id, failed=True)
        state.record_check(
            session_id,
            "Stop",
            "error",
            100,
            0,
            diagnostics={
                "transcript_errors": [{"path": f"{cwd}/rollout.jsonl"}],
                "evidence_rejection": {"reason": "not present"},
            },
        )
    for key in ("recent_transcript_errors", "recent_evidence_rejections", "unreviewable_sessions"):
        assert {item["session_id"] for item in state.diagnostics()[key]} == {"here", "there"}
        scoped = state.diagnostics(cwd=project)[key]
        assert [item["session_id"] for item in scoped] == ["here"]
        assert scoped[0]["cwd"] == project
        assert state.diagnostics(cwd=str(tmp_path / "missing"))[key] == []
    assert state.diagnostics(cwd=project)["unreviewable_sessions"][0]["unknown_record_types"] == {
        "retained_context": 2
    }
    state.close()


def test_unreviewable_sessions_include_failures_outside_recent_twenty(tmp_path):
    state = State(tmp_path)
    state.observe("unreviewable", False)
    state.observe("below-threshold", False)
    for _ in range(3):
        state.record_transcript_read("unreviewable", failed=True)
    for _ in range(2):
        state.record_transcript_read("below-threshold", failed=True)
    for index in range(25):
        state.observe(f"recent-{index}", False)
        state.begin(f"recent-{index}", index + 1)
    assert "unreviewable" not in {row["session_id"] for row in state.status()}
    report = state.diagnostics()
    assert report["transcript_failure_threshold"] == 3
    assert [row["session_id"] for row in report["unreviewable_sessions"]] == ["unreviewable"]
    assert report["unreviewable_sessions"][0]["consecutive_transcript_failures"] == 3
    assert report["unreviewable_sessions"][0]["unknown_record_types"] == {}
    state.record_transcript_read("unreviewable", unknown_types={"future_record": 1})
    assert state.diagnostics()["unreviewable_sessions"] == []
    state.close()


def test_diagnostics_are_pruned_with_their_checks_and_sessions(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("you_should_know.state.time.time", lambda: now[0])
    state = State(tmp_path)
    state.observe("old", False)
    for _ in range(3):
        state.record_transcript_read("old", failed=True)
    state.record_check(
        "old",
        "Stop",
        "transcript_unavailable",
        now[0],
        0,
        diagnostics={"transcript_errors": [{"path": "/synthetic/old.jsonl"}]},
    )
    now[0] = 40 * 86_400
    state.observe("recent", False)
    state.record_check(
        "recent",
        "Stop",
        "error",
        now[0],
        0,
        diagnostics={"evidence_rejection": {"reason": "recent"}},
    )
    removed = state.prune(30, now[0])
    assert removed["checks"] == 1
    assert removed["sessions"] == 1
    report = state.diagnostics()
    assert report["recent_transcript_errors"] == []
    assert report["unreviewable_sessions"] == []
    assert len(report["recent_evidence_rejections"]) == 1
    assert report["recent_evidence_rejections"][0]["session_id"] == "recent"
    state.close()


def test_reliability_migration_keeps_legacy_checks_and_sessions(tmp_path):
    connection = sqlite3.connect(tmp_path / "state.sqlite3")
    connection.executescript("""
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            events INTEGER NOT NULL DEFAULT 0,
            checked_events INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_attempt REAL NOT NULL DEFAULT 0,
            last_success REAL NOT NULL DEFAULT 0,
            last_hash TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            event TEXT NOT NULL,
            outcome TEXT NOT NULL,
            phase TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at REAL NOT NULL,
            latency REAL NOT NULL,
            input_tokens INTEGER NOT NULL,
            cached_input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL
        );
        INSERT INTO sessions(session_id, attempts, last_error) VALUES ('old', 7, 'legacy');
        INSERT INTO checks(session_id, event, outcome, phase, reason, created_at,
                           latency, input_tokens, cached_input_tokens, output_tokens)
        VALUES ('old', 'Stop', 'error', 'review', 'legacy', 100, 1, 0, 0, 0);
    """)
    connection.close()
    state = State(tmp_path)
    assert state.session("old").attempts == 7
    assert state.session("old").last_error == "legacy"
    assert state.session("old").consecutive_transcript_failures == 0
    assert state.status()[0]["unknown_record_types"] == {}
    report = state.diagnostics()
    assert report["total_checks"] == 1
    assert report["recent_checks"][0]["diagnostics"] == {}
    assert report["recent_transcript_errors"] == []
    assert report["recent_evidence_rejections"] == []
    assert report["unreviewable_sessions"] == []
    state.close()
