import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

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


def test_pinning_and_catalog_preview_are_persisted(tmp_path):
    store = make_store(tmp_path)
    first = store.create_artifact("First report", "markdown", "# First\nA useful preview.", "test")
    second = store.create_artifact("Second report", "text", "Second content.", "test")

    pinned = store.set_pinned(second["id"], True)
    assert pinned["pinned"] is True
    assert store.get_artifact(second["id"])["pinned"] is True

    catalog = store.list_artifacts()
    assert catalog[0]["id"] == second["id"]
    assert catalog[0]["preview"] == "Second content."
    assert catalog[0]["comment_count"] == 0
    assert catalog[1]["id"] == first["id"]

    unpinned = store.set_pinned(second["id"], False)
    assert unpinned["pinned"] is False


def test_project_description_is_a_pinned_immutable_artifact(tmp_path):
    store = make_store(tmp_path)
    title = "Open Agent Artifacts — Project description"
    first = store.ensure_project_description(title, "<html>v1</html>", "release")

    assert first["title"] == title
    assert first["pinned"] is True
    assert first["kind"] == "html"
    assert store.get_current_version(first["id"])["content"] == "<html>v1</html>"

    unchanged = store.ensure_project_description(title, "<html>v1</html>", "release")
    assert unchanged["id"] == first["id"]
    assert len(store.list_versions(first["id"])) == 1

    updated = store.ensure_project_description(title, "<html>v2</html>", "release")
    assert updated["id"] == first["id"]
    assert store.get_current_version(first["id"])["content"] == "<html>v2</html>"
    assert len(store.list_versions(first["id"])) == 2


def test_project_description_rollback_and_rename_keep_stable_identity(tmp_path):
    store = make_store(tmp_path)
    title = "Open Agent Artifacts — Project description"
    artifact = store.ensure_project_description(title, "A")
    store.ensure_project_description(title, "B")
    store.ensure_project_description(title, "C")
    store.rename_artifact(artifact["id"], "Renamed system description")

    rolled_back = store.ensure_project_description(title, "B")

    assert rolled_back["id"] == artifact["id"]
    assert rolled_back["title"] == "Renamed system description"
    assert store.get_current_version(artifact["id"])["content"] == "B"
    assert len(store.list_versions(artifact["id"])) == 4


def test_project_description_does_not_adopt_same_titled_user_artifact(tmp_path):
    store = make_store(tmp_path)
    title = "Open Agent Artifacts — Project description"
    user_artifact = store.create_artifact(title, "html", "user content", "user")

    system_artifact = store.ensure_project_description(title, "system content")

    assert system_artifact["id"] != user_artifact["id"]
    assert store.get_current_version(user_artifact["id"])["content"] == "user content"


def test_project_description_sync_is_atomic_and_keeps_single_identity(tmp_path):
    store = make_store(tmp_path)
    title = "Open Agent Artifacts — Project description"
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.ensure_project_description(title, "content"), range(8)))
    assert len({item["id"] for item in results}) == 1
    assert len(store.list_artifacts(include_archived=True)) == 1


def test_project_description_is_repinned_on_sync(tmp_path):
    store = make_store(tmp_path)
    title = "Open Agent Artifacts — Project description"
    artifact = store.ensure_project_description(title, "content")
    store.set_pinned(artifact["id"], False)
    synced = store.ensure_project_description(title, "content")
    assert synced["pinned"] is True


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
