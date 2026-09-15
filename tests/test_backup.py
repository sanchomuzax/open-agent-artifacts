import sqlite3

import pytest

from open_agent_artifacts.backup import BackupError, backup_database, restore_database
from open_agent_artifacts.store import Store


def seed_store(tmp_path):
    source = Store(tmp_path / "source.db")
    artifact = source.create_artifact("Backup report", "markdown", "first", "test")
    version = source.get_current_version(artifact["id"])
    source.publish_version(
        artifact["id"],
        "second",
        "test",
        expected_current_version_id=version["id"],
        change_summary="second version",
    )
    current = source.get_current_version(artifact["id"])
    source.create_comment(
        artifact["id"],
        current["id"],
        "Keep this section.",
        {"kind": "text", "exact": "second"},
    )
    return source, artifact["id"]


def test_backup_preserves_versions_and_comments(tmp_path):
    source, artifact_id = seed_store(tmp_path)
    backup = tmp_path / "backup.db"

    backup_database(source.db_path, backup)
    restored = Store(backup)

    assert len(restored.list_versions(artifact_id)) == 2
    assert restored.get_current_version(artifact_id)["content"] == "second"
    assert restored.list_comments(artifact_id)[0]["body"] == "Keep this section."


def test_restore_rejects_corrupt_backup_without_creating_destination(tmp_path):
    corrupt = tmp_path / "corrupt.db"
    destination = tmp_path / "restored.db"
    corrupt.write_bytes(b"not a sqlite database")

    with pytest.raises(BackupError, match="integrity"):
        restore_database(corrupt, destination)

    assert not destination.exists()


def test_restore_does_not_overwrite_existing_destination_by_default(tmp_path):
    source, _ = seed_store(tmp_path)
    backup = tmp_path / "backup.db"
    destination = tmp_path / "existing.db"
    backup_database(source.db_path, backup)
    destination.write_bytes(b"existing")

    with pytest.raises(BackupError, match="already exists"):
        restore_database(backup, destination)

    assert destination.read_bytes() == b"existing"


def test_backup_uses_sqlite_integrity_checks(tmp_path):
    source, _ = seed_store(tmp_path)
    backup = tmp_path / "backup.db"

    backup_database(source.db_path, backup)

    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
