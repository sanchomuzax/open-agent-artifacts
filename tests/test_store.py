import hashlib
import sqlite3

import pytest

from open_agent_artifacts.store import (
    ConflictError,
    NotFoundError,
    Store,
    ValidationError,
)


def make_store(tmp_path):
    return Store(tmp_path / "test.db")


def test_create_artifact_creates_immutable_first_version(tmp_path):
    store = make_store(tmp_path)

    artifact = store.create_artifact("A report", "markdown", "# First", "test")
    version = store.get_current_version(artifact["id"])

    assert artifact["slug"]
    assert version["sequence"] == 1
    assert version["content"] == "# First"
    assert version["content_hash"] == hashlib.sha256(b"# First").hexdigest()
    assert store.get_version(version["id"])["content"] == "# First"


def test_publish_requires_current_parent_and_preserves_history(tmp_path):
    store = make_store(tmp_path)
    artifact = store.create_artifact("A report", "text", "old", "test")
    first = store.get_current_version(artifact["id"])

    second = store.publish_version(
        artifact["id"],
        "new",
        "test",
        expected_current_version_id=first["id"],
        change_summary="Update text",
    )

    assert second["sequence"] == 2
    assert store.get_version(first["id"])["content"] == "old"
    with pytest.raises(ConflictError):
        store.publish_version(
            artifact["id"],
            "stale",
            "test",
            expected_current_version_id=first["id"],
        )


def test_exact_patch_requires_one_match(tmp_path):
    store = make_store(tmp_path)
    artifact = store.create_artifact("A report", "text", "one\none\n", "test")
    current = store.get_current_version(artifact["id"])

    with pytest.raises(ValidationError, match="exactly once"):
        store.publish_version(
            artifact["id"],
            None,
            "test",
            expected_current_version_id=current["id"],
            old_str="one",
            new_str="ONE",
        )


def test_idempotency_returns_original_create_result(tmp_path):
    store = make_store(tmp_path)
    first = store.create_artifact("A report", "text", "content", "test", "key-1")
    second = store.create_artifact("A report", "text", "content", "test", "key-1")

    assert second == first
    assert len(store.list_artifacts()) == 1


def test_comments_are_bound_to_version_and_events_are_audited(tmp_path):
    store = make_store(tmp_path)
    artifact = store.create_artifact("A report", "text", "content", "test")
    version = store.get_current_version(artifact["id"])
    comment = store.create_comment(
        artifact["id"],
        version["id"],
        "Please clarify this.",
        {"kind": "text", "exact": "content", "prefix": "", "suffix": ""},
    )

    event = store.add_comment_event(comment["id"], "addressed", "test", version["id"])

    assert comment["status"] == "open"
    assert event["status"] == "addressed"
    assert store.get_comment(comment["id"])["status"] == "addressed"
    assert len(store.list_comment_events(comment["id"])) == 1


def test_invalid_comment_version_is_rejected(tmp_path):
    store = make_store(tmp_path)
    first = store.create_artifact("First", "text", "one", "test")
    second = store.create_artifact("Second", "text", "two", "test")
    version = store.get_current_version(first["id"])

    with pytest.raises(ValidationError, match="does not belong"):
        store.create_comment(
            first["id"],
            store.get_current_version(second["id"])["id"],
            "wrong",
            {},
        )


def test_foreign_keys_are_enabled(tmp_path):
    store = make_store(tmp_path)
    with store._connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
