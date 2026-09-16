from __future__ import annotations

import sqlite3

from open_agent_artifacts.store import Store


def test_legacy_hash_unique_schema_migrates_with_comments(tmp_path):
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT);
        CREATE TABLE artifacts (
            id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL, kind TEXT NOT NULL,
            current_version_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            pinned_at TEXT, archived_at TEXT
        );
        CREATE TABLE versions (
            id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            parent_version_id TEXT, content TEXT NOT NULL, content_hash TEXT NOT NULL,
            change_summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, created_by TEXT NOT NULL,
            UNIQUE(artifact_id, sequence), UNIQUE(artifact_id, content_hash)
        );
        CREATE TABLE comments (
            id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, version_id TEXT NOT NULL,
            body TEXT NOT NULL, anchor_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
            addressed_in_version_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE comment_events (
            id TEXT PRIMARY KEY, comment_id TEXT NOT NULL, status TEXT NOT NULL,
            actor TEXT NOT NULL, addressed_in_version_id TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE idempotency_keys (
            scope TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL,
            result_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(scope, key)
        );
        INSERT INTO schema_migrations VALUES ('001_initial', '2026-01-01');
        INSERT INTO artifacts VALUES ('a', 'report-a', 'Report', 'markdown', 'v1', '2026-01-01', '2026-01-01', NULL, NULL);
        INSERT INTO versions VALUES ('v1', 'a', 1, NULL, '# One', 'hash-one', '', '2026-01-01', 'legacy');
        INSERT INTO comments VALUES ('c', 'a', 'v1', 'Note', '{"exact":"One"}', 'open', NULL, '2026-01-01', '2026-01-01');
        INSERT INTO comment_events VALUES ('e', 'c', 'open', 'legacy', NULL, '2026-01-01');
        """
    )
    connection.close()

    store = Store(db_path)
    assert store.get_version("v1")["content"] == "# One"
    assert store.get_comment("c")["body"] == "Note"
    restored = store.restore_version("a", "v1", "test", expected_current_version_id="v1")
    assert restored["sequence"] == 2
    assert restored["content"] == "# One"
    with store._connect() as check:
        assert check.execute("PRAGMA foreign_keys").fetchone()[0] == 1
