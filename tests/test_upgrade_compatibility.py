from dataclasses import make_dataclass

from you_should_know.state import State


def test_previous_release_can_read_sessions_after_upgrade(tmp_path):
    legacy_session = make_dataclass(
        "LegacySession",
        [
            "session_id",
            "events",
            "checked_events",
            "attempts",
            "last_attempt",
            "last_success",
            "last_hash",
            "last_error",
            "touched_at",
            "cwd",
            "consecutive_transcript_failures",
            "unknown_record_types",
        ],
    )
    state = State(tmp_path)
    try:
        state.observe("s", False)
        state.record_transcript_read("s", failed=True)
        row = state.connection.execute("SELECT * FROM sessions WHERE session_id = 's'").fetchone()
        legacy = legacy_session(**dict(row))
        assert legacy.session_id == "s"
        assert legacy.consecutive_transcript_failures == 1
        assert state.session("s").last_transcript_failure > 0
        state.record_transcript_read("s")
        assert state.session("s").last_transcript_failure == 0
    finally:
        state.close()


def test_pruning_removes_retry_state_with_expired_session(tmp_path):
    state = State(tmp_path)
    try:
        state.observe("s", False)
        state.record_transcript_read("s", failed=True)
        state.connection.execute("UPDATE sessions SET touched_at = 1 WHERE session_id = 's'")
        state.connection.commit()
        state.prune(1, 100000)
        state.observe("s", False)
        assert state.session("s").last_transcript_failure == 0
    finally:
        state.close()
