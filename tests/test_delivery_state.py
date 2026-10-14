import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from you_should_know.assessment import Finding
from you_should_know.state import State


@pytest.fixture
def state(tmp_path):
    store = State(tmp_path / "state")
    yield store
    store.close()


def finding(topic="settings lost"):
    return Finding(topic, "Previous settings were deleted.", "Restore the backup.", ["Deleted."])


def test_queued_note_survives_reopen_and_is_delivered_once(tmp_path):
    directory = tmp_path / "state"
    first = State(directory)
    identifier = first.save("session", finding(), 100, notify=True)
    assert identifier is not None
    first.close()
    reopened = State(directory)
    try:
        expected = reopened.note(identifier)
        assert reopened.deliver("session", ready=True, turn_id="finished-turn") == (
            [{"note": expected}],
            expected,
        )
        assert reopened.deliver("session", ready=True, turn_id="finished-turn") == ([], None)
        assert reopened.reply("session", "more", turn_id="reply-turn") == expected
    finally:
        reopened.close()


def test_manual_notes_and_duplicates_are_not_queued(state):
    manual = state.save("session", finding("manual"), 100)
    automatic = state.save("session", finding("automatic"), 100, notify=True)
    assert state.save("other", finding("manual"), 200, notify=True) is None
    assert state.save("other", finding("automatic"), 200, notify=True) is None
    assert state.deliver("session") == ([{"note": state.note(automatic)}], None)
    assert state.deliver("other", ready=True, turn_id="finished-turn") == ([], None)
    assert state.note(manual)["status"] == "new"


def test_delivery_preserves_enqueue_order_and_arms_latest_note(state):
    state.queue_error("session", "first error")
    first = state.save("session", finding("first"), 300, notify=True)
    state.queue_error("session", "second error")
    last = state.save("session", finding("last"), 100, notify=True)
    expected = [
        {"message": "first error"},
        {"note": state.note(first)},
        {"message": "second error"},
        {"note": state.note(last)},
    ]
    assert state.deliver("session", ready=True, turn_id="finished-turn") == (
        expected,
        state.note(last),
    )
    assert state.reply("session", "more", turn_id="reply-turn") == state.note(last)


def test_delivery_and_replies_are_isolated_by_session(state):
    first = state.save("one", finding("first"), 100, notify=True)
    second = state.save("two", finding("second"), 200, notify=True)
    state.queue_error("two", "second session error")
    assert state.deliver("one", ready=True, turn_id="finished-turn") == (
        [{"note": state.note(first)}],
        state.note(first),
    )
    assert state.reply("two", "known", turn_id="reply-turn") is None
    assert state.reply("one", "known", turn_id="reply-turn")["id"] == first
    assert state.deliver("two", ready=True, turn_id="finished-turn") == (
        [{"note": state.note(second)}, {"message": "second session error"}],
        state.note(second),
    )
    assert state.reply("two", "more", turn_id="reply-turn")["id"] == second
    assert state.note(second)["status"] == "new"


def test_stop_arms_midturn_notification_without_repeating_summary(state):
    identifier = state.save("session", finding(), 100, notify=True)
    expected = state.note(identifier)
    assert state.deliver("session") == ([{"note": expected}], None)
    assert state.deliver("session") == ([], None)
    assert state.deliver("session", ready=True, turn_id="finished-turn") == ([], expected)
    assert state.deliver("session", ready=True, turn_id="finished-turn") == ([], None)
    assert state.reply("session", "more", turn_id="reply-turn") == expected


@pytest.mark.parametrize("action", ["more", "known", "dismissed"])
def test_midturn_reply_cannot_act_and_clears_target(state, action):
    identifier = state.save("session", finding(), 100, notify=True)
    state.deliver("session")
    assert state.reply("session", action, turn_id="reply-turn") is None
    assert state.deliver("session", ready=True, turn_id="finished-turn") == ([], None)
    assert state.note(identifier)["status"] == "new"


def test_ordinary_prompt_clears_ready_target_without_changing_note(state):
    identifier = state.save("session", finding(), 100, notify=True)
    state.deliver("session", ready=True, turn_id="finished-turn")
    assert state.reply("session", None, turn_id="reply-turn") is None
    assert state.reply("session", "known", turn_id="reply-turn") is None
    assert state.note(identifier)["status"] == "new"


@pytest.mark.parametrize("action", ["more", "known", "dismissed"])
def test_same_turn_steering_clears_target_without_consuming_action(state, action):
    identifier = state.save("session", finding(), 100, notify=True)
    state.deliver("session", ready=True, turn_id="active-turn")
    assert state.reply("session", action, turn_id="active-turn") is None
    assert state.reply("session", action, turn_id="later-turn") is None
    assert state.note(identifier)["status"] == "new"


@pytest.mark.parametrize("turn_id", [None, ""])
def test_missing_stop_turn_id_delivers_note_without_arming(state, turn_id):
    identifier = state.save("session", finding(), 100, notify=True)
    expected = state.note(identifier)
    assert state.deliver("session", ready=True, turn_id=turn_id) == ([{"note": expected}], None)
    assert state.reply("session", "known", turn_id="reply-turn") is None
    assert state.note(identifier)["status"] == "new"


@pytest.mark.parametrize("turn_id", [None, ""])
def test_missing_reply_turn_id_clears_target_without_consuming_action(state, turn_id):
    identifier = state.save("session", finding(), 100, notify=True)
    state.deliver("session", ready=True, turn_id="finished-turn")
    assert state.reply("session", "known", turn_id=turn_id) is None
    assert state.reply("session", "known", turn_id="reply-turn") is None
    assert state.note(identifier)["status"] == "new"


def test_interaction_without_stored_turn_id_is_migrated_and_cannot_consume_reply(state):
    identifier = state.save("session", finding(), 100)
    with state.connection:
        state.connection.execute("DROP TABLE interactions")
        state.connection.execute(
            "CREATE TABLE interactions (session_id TEXT PRIMARY KEY, "
            "note_id TEXT NOT NULL, ready INTEGER NOT NULL DEFAULT 0)"
        )
        state.connection.execute("INSERT INTO interactions VALUES ('session', ?, 1)", (identifier,))
    reopened = State(state.directory)
    try:
        assert reopened.reply("session", "known", turn_id="reply-turn") is None
        assert reopened.note(identifier)["status"] == "new"
    finally:
        reopened.close()


def test_more_keeps_target_for_later_disposition(state):
    identifier = state.save("session", finding(), 100, notify=True)
    state.deliver("session", ready=True, turn_id="finished-turn")
    assert state.reply("session", "more", turn_id="reply-turn") == state.note(identifier)
    assert state.reply("session", "more", turn_id="reply-turn") == state.note(identifier)
    assert state.reply("session", "known", turn_id="reply-turn")["status"] == "known"
    assert state.reply("session", "more", turn_id="reply-turn") is None


@pytest.mark.parametrize("action", ["known", "dismissed"])
def test_disposition_updates_note_and_clears_target(state, action):
    identifier = state.save("session", finding(), 100, notify=True)
    state.deliver("session", ready=True, turn_id="finished-turn")
    result = state.reply("session", action, turn_id="reply-turn")
    assert result == state.note(identifier)
    assert result["status"] == action
    assert state.notes() == []
    assert state.reply("session", "more", turn_id="reply-turn") is None


@pytest.mark.parametrize("status", ["known", "dismissed"])
def test_resolved_pending_notes_are_skipped(state, status):
    identifier = state.save("session", finding(), 100, notify=True)
    state.mark(identifier, status)
    state.queue_error("session", "review failed")
    assert state.deliver("session", ready=True, turn_id="finished-turn") == (
        [{"message": "review failed"}],
        None,
    )
    assert state.reply("session", "more", turn_id="reply-turn") is None
    assert state.deliver("session", ready=True, turn_id="finished-turn") == ([], None)


def test_explicit_mark_invalidates_displayed_interaction(state):
    identifier = state.save("session", finding(), 100, notify=True)
    state.deliver("session", ready=True, turn_id="finished-turn")
    state.mark(identifier, "known")
    assert state.reply("session", "dismissed", turn_id="reply-turn") is None
    assert state.note(identifier)["status"] == "known"


def test_new_midturn_note_replaces_previous_ready_target(state):
    first = state.save("session", finding("first"), 100, notify=True)
    state.deliver("session", ready=True, turn_id="finished-turn")
    second = state.save("session", finding("second"), 200, notify=True)
    assert state.deliver("session") == ([{"note": state.note(second)}], None)
    assert state.reply("session", "known", turn_id="reply-turn") is None
    assert state.note(first)["status"] == "new"
    assert state.note(second)["status"] == "new"


def test_unknown_actions_are_rejected_without_losing_target(state):
    identifier = state.save("session", finding(), 100, notify=True)
    state.deliver("session", ready=True, turn_id="finished-turn")
    with pytest.raises(ValueError, match="Invalid note action"):
        state.reply("session", "delete", turn_id="reply-turn")
    assert state.reply("session", "more", turn_id="reply-turn") == state.note(identifier)


def test_note_and_notification_are_saved_atomically(state):
    state.connection.execute(
        "CREATE TRIGGER fail_notification BEFORE INSERT ON notifications "
        "BEGIN SELECT RAISE(ABORT, 'queue unavailable'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="queue unavailable"):
        state.save("session", finding(), 100, notify=True)
    assert state.notes(True) == []
    assert state.deliver("session", ready=True, turn_id="finished-turn") == ([], None)


def test_old_database_is_preserved_without_replaying_existing_notes(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir()
    connection = sqlite3.connect(directory / "state.sqlite3")
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
        CREATE TABLE notes (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            topic TEXT NOT NULL,
            summary TEXT NOT NULL,
            explanation TEXT NOT NULL,
            evidence TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            created_at REAL NOT NULL
        );
        INSERT INTO sessions(session_id, events, attempts) VALUES ('session', 42, 7);
    """)
    with connection:
        connection.execute(
            "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "old-note",
                "session",
                "old topic",
                "Old summary",
                "Old explanation",
                '["é"]',
                "new",
                1,
            ),
        )
    connection.close()
    migrated = State(directory)
    try:
        assert migrated.session("session").events == 42
        assert migrated.session("session").attempts == 7
        assert migrated.note("old-note") == {
            "id": "old-note",
            "session_id": "session",
            "topic": "old topic",
            "summary": "Old summary",
            "explanation": "Old explanation",
            "evidence": ["é"],
            "status": "new",
            "created_at": 1,
        }
        assert migrated.deliver("session", ready=True, turn_id="finished-turn") == ([], None)
        assert migrated.reply("session", "known", turn_id="reply-turn") is None
        identifier = migrated.save("session", finding(), 100, notify=True)
        assert identifier is not None
        assert migrated.deliver("session", ready=True, turn_id="finished-turn") == (
            [{"note": migrated.note(identifier)}],
            migrated.note(identifier),
        )
        raw = migrated.connection.execute(
            "SELECT evidence FROM notes WHERE id = 'old-note'"
        ).fetchone()[0]
        assert raw == '["é"]'
        assert json.loads(raw) == ["é"]
    finally:
        migrated.close()


def test_concurrent_delivery_and_disposition_claim_each_item_once(state):
    identifier = state.save("session", finding(), 100, notify=True)
    barrier = threading.Barrier(2)

    def concurrent(operation):
        store = State(state.directory)
        try:
            barrier.wait(timeout=5)
            return operation(store)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        deliveries = list(
            pool.map(
                concurrent,
                [lambda store: store.deliver("session", ready=True, turn_id="finished-turn")] * 2,
            )
        )
        assert sum(len(items) for items, _ in deliveries) == 1
        assert sum(note is not None for _, note in deliveries) == 1
        actions = list(
            pool.map(
                concurrent,
                [lambda store: store.reply("session", "known", turn_id="reply-turn")] * 2,
            )
        )
    assert [note["id"] for note in actions if note is not None] == [identifier]
    assert state.note(identifier)["status"] == "known"
