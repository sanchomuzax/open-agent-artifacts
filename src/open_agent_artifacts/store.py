from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_COMMENT_BYTES = 16 * 1024
ALLOWED_KINDS = {"text", "markdown", "code", "html", "svg", "mermaid"}


class StoreError(Exception):
    """Base class for storage errors."""


class NotFoundError(StoreError):
    """The requested object does not exist."""


class ValidationError(StoreError):
    """The request violates the storage contract."""


class ConflictError(StoreError):
    """The request was based on a stale version."""


def _now_sql() -> str:
    return "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def _as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _check_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field} must be a non-empty string")
    if len(value.encode("utf-8")) > limit:
        raise ValidationError(f"{field} exceeds {limit} bytes")
    return value


def _check_kind(kind: str) -> str:
    if kind not in ALLOWED_KINDS:
        allowed = ", ".join(sorted(ALLOWED_KINDS))
        raise ValidationError(f"kind must be one of: {allowed}")
    return kind


class Store:
    """SQLite persistence for immutable, reviewable artifacts."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT ({_now_sql()})
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    current_version_id TEXT,
                    created_at TEXT NOT NULL DEFAULT ({_now_sql()}),
                    updated_at TEXT NOT NULL DEFAULT ({_now_sql()}),
                    pinned_at TEXT,
                    archived_at TEXT
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    sequence INTEGER NOT NULL,
                    parent_version_id TEXT REFERENCES versions(id),
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    change_summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT ({_now_sql()}),
                    created_by TEXT NOT NULL,
                    UNIQUE(artifact_id, sequence),
                    UNIQUE(artifact_id, content_hash)
                );
                CREATE TABLE IF NOT EXISTS comments (
                    id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    version_id TEXT NOT NULL REFERENCES versions(id),
                    body TEXT NOT NULL,
                    anchor_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK(status IN ('open', 'addressed', 'resolved')),
                    addressed_in_version_id TEXT REFERENCES versions(id),
                    created_at TEXT NOT NULL DEFAULT ({_now_sql()}),
                    updated_at TEXT NOT NULL DEFAULT ({_now_sql()})
                );
                CREATE TABLE IF NOT EXISTS comment_events (
                    id TEXT PRIMARY KEY,
                    comment_id TEXT NOT NULL REFERENCES comments(id),
                    status TEXT NOT NULL
                        CHECK(status IN ('open', 'addressed', 'resolved')),
                    actor TEXT NOT NULL,
                    addressed_in_version_id TEXT REFERENCES versions(id),
                    created_at TEXT NOT NULL DEFAULT ({_now_sql()})
                );
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    result_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT ({_now_sql()}),
                    PRIMARY KEY(scope, key)
                );
                INSERT OR IGNORE INTO schema_migrations(version) VALUES ('001_initial');
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(artifacts)").fetchall()}
            if "pinned_at" not in columns:
                connection.execute("ALTER TABLE artifacts ADD COLUMN pinned_at TEXT")

    def _slug(self, title: str, artifact_id: str) -> str:
        base = "-".join(title.lower().split())
        safe = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in base)
        safe = "-".join(part for part in safe.split("-") if part)[:48] or "artifact"
        return f"{safe}-{artifact_id[:8]}"

    def _get_artifact_row(self, connection: sqlite3.Connection, artifact_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"artifact not found: {artifact_id}")
        return row

    def _get_version_row(self, connection: sqlite3.Connection, version_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM versions WHERE id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"version not found: {version_id}")
        return row

    @staticmethod
    def _artifact_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["pinned"] = bool(result.pop("pinned_at", None))
        return result

    @staticmethod
    def _hash_request(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _idempotent_result(
        self,
        connection: sqlite3.Connection,
        scope: str,
        key: str | None,
        request_hash: str,
    ) -> str | None:
        if not key:
            return None
        row = connection.execute(
            "SELECT request_hash, result_id FROM idempotency_keys WHERE scope = ? AND key = ?",
            (scope, key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ValidationError("idempotency key was reused with a different request")
        return row["result_id"]

    def create_artifact(
        self,
        title: str,
        kind: str,
        content: str,
        created_by: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        title = _check_text(title, "title", 512)
        content = _check_text(content, "content", MAX_CONTENT_BYTES)
        created_by = _check_text(created_by, "created_by", 256)
        _check_kind(kind)
        request_hash = self._hash_request({"title": title, "kind": kind, "content": content, "created_by": created_by})
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_id = self._idempotent_result(connection, "create", idempotency_key, request_hash)
            if existing_id:
                return self.get_artifact(existing_id)
            artifact_id = str(uuid.uuid4())
            version_id = str(uuid.uuid4())
            slug = self._slug(title, artifact_id)
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            connection.execute(
                "INSERT INTO artifacts(id, slug, title, kind, current_version_id) VALUES (?, ?, ?, ?, ?)",
                (artifact_id, slug, title, kind, version_id),
            )
            connection.execute(
                """INSERT INTO versions
                   (id, artifact_id, sequence, content, content_hash, created_by)
                   VALUES (?, ?, 1, ?, ?, ?)""",
                (version_id, artifact_id, content, content_hash, created_by),
            )
            if idempotency_key:
                connection.execute(
                    """INSERT INTO idempotency_keys(scope, key, request_hash, result_id)
                       VALUES ('create', ?, ?, ?)""",
                    (idempotency_key, request_hash, artifact_id),
                )
            return self._artifact_dict(self._get_artifact_row(connection, artifact_id))

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            return self._artifact_dict(self._get_artifact_row(connection, artifact_id))

    def list_artifacts(self, query: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if not include_archived:
            clauses.append("a.archived_at IS NULL")
        if query:
            clauses.append("(a.title LIKE ? OR a.slug LIKE ?)")
            pattern = f"%{query}%"
            params.extend([pattern, pattern])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT a.*, v.sequence AS current_version_sequence,
                           substr(v.content, 1, 320) AS preview,
                           (SELECT COUNT(*) FROM comments c WHERE c.artifact_id = a.id) AS comment_count
                    FROM artifacts a
                    LEFT JOIN versions v ON v.id = a.current_version_id
                    {where}
                    ORDER BY CASE WHEN a.pinned_at IS NULL THEN 1 ELSE 0 END,
                             a.updated_at DESC, a.id DESC""", params
            ).fetchall()
            return [self._artifact_dict(row) for row in rows]

    def set_pinned(self, artifact_id: str, pinned: bool) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._get_artifact_row(connection, artifact_id)
            pinned_at = _now_sql() if pinned else "NULL"
            connection.execute(
                f"UPDATE artifacts SET pinned_at = {pinned_at}, updated_at = {_now_sql()} WHERE id = ?",
                (artifact_id,),
            )
            return self._artifact_dict(self._get_artifact_row(connection, artifact_id))

    def get_version(self, version_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            return _as_dict(self._get_version_row(connection, version_id))  # type: ignore[return-value]

    def get_current_version(self, artifact_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            artifact = self._get_artifact_row(connection, artifact_id)
            return _as_dict(self._get_version_row(connection, artifact["current_version_id"]))  # type: ignore[return-value]

    def list_versions(self, artifact_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._get_artifact_row(connection, artifact_id)
            rows = connection.execute(
                "SELECT * FROM versions WHERE artifact_id = ? ORDER BY sequence DESC", (artifact_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def publish_version(
        self,
        artifact_id: str,
        content: str | None,
        created_by: str,
        *,
        expected_current_version_id: str,
        change_summary: str = "",
        old_str: str | None = None,
        new_str: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        created_by = _check_text(created_by, "created_by", 256)
        change_summary = _check_text(change_summary, "change_summary", 2048) if change_summary else ""
        if old_str is not None:
            if not old_str:
                raise ValidationError("old_str must be non-empty")
            if new_str is None:
                raise ValidationError("new_str is required with old_str")
            if content is not None:
                raise ValidationError("content cannot be combined with old_str")
        elif new_str is not None:
            raise ValidationError("new_str requires old_str")
        elif content is None:
            raise ValidationError("content or old_str/new_str is required")
        if content is not None:
            _check_text(content, "content", MAX_CONTENT_BYTES)
        request_hash = self._hash_request({
            "artifact_id": artifact_id,
            "content": content,
            "created_by": created_by,
            "expected_current_version_id": expected_current_version_id,
            "change_summary": change_summary,
            "old_str": old_str,
            "new_str": new_str,
        })
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_id = self._idempotent_result(connection, f"publish:{artifact_id}", idempotency_key, request_hash)
            if existing_id:
                return self.get_version(existing_id)
            artifact = self._get_artifact_row(connection, artifact_id)
            current_id = artifact["current_version_id"]
            if current_id != expected_current_version_id:
                raise ConflictError(
                    f"current version is {current_id}; expected {expected_current_version_id}"
                )
            current = self._get_version_row(connection, current_id)
            if old_str is not None:
                occurrences = current["content"].count(old_str)
                if occurrences != 1:
                    raise ValidationError("old_str must occur exactly once")
                content = current["content"].replace(old_str, new_str, 1)
            assert content is not None
            _check_text(content, "content", MAX_CONTENT_BYTES)
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if content_hash == current["content_hash"]:
                raise ValidationError("new version has identical content")
            version_id = str(uuid.uuid4())
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM versions WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO versions
                   (id, artifact_id, sequence, parent_version_id, content, content_hash, change_summary, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (version_id, artifact_id, sequence, current_id, content, content_hash, change_summary, created_by),
            )
            connection.execute(
                f"UPDATE artifacts SET current_version_id = ?, updated_at = {_now_sql()} WHERE id = ?",
                (version_id, artifact_id),
            )
            if idempotency_key:
                connection.execute(
                    """INSERT INTO idempotency_keys(scope, key, request_hash, result_id)
                       VALUES (?, ?, ?, ?)""",
                    (f"publish:{artifact_id}", idempotency_key, request_hash, version_id),
                )
            return dict(self._get_version_row(connection, version_id))

    def create_comment(
        self,
        artifact_id: str,
        version_id: str,
        body: str,
        anchor: dict[str, Any],
    ) -> dict[str, Any]:
        body = _check_text(body, "body", MAX_COMMENT_BYTES)
        if not isinstance(anchor, dict):
            raise ValidationError("anchor must be an object")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._get_artifact_row(connection, artifact_id)
            version = self._get_version_row(connection, version_id)
            if version["artifact_id"] != artifact_id:
                raise ValidationError("version does not belong to artifact")
            comment_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO comments(id, artifact_id, version_id, body, anchor_json) VALUES (?, ?, ?, ?, ?)",
                (comment_id, artifact_id, version_id, body, json.dumps(anchor, ensure_ascii=False, sort_keys=True)),
            )
            result = dict(connection.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone())
            result["anchor"] = json.loads(result.pop("anchor_json"))
            return result

    def get_comment(self, comment_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"comment not found: {comment_id}")
            result = dict(row)
            result["anchor"] = json.loads(result.pop("anchor_json"))
            return result

    def list_comments(self, artifact_id: str, status: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._get_artifact_row(connection, artifact_id)
            if status is not None and status not in {"open", "addressed", "resolved"}:
                raise ValidationError("invalid comment status")
            if status:
                rows = connection.execute(
                    "SELECT * FROM comments WHERE artifact_id = ? AND status = ? ORDER BY created_at",
                    (artifact_id, status),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM comments WHERE artifact_id = ? ORDER BY created_at", (artifact_id,)
                ).fetchall()
            results = []
            for row in rows:
                result = dict(row)
                result["anchor"] = json.loads(result.pop("anchor_json"))
                results.append(result)
            return results

    def add_comment_event(
        self,
        comment_id: str,
        status: str,
        actor: str,
        addressed_in_version_id: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"open", "addressed", "resolved"}:
            raise ValidationError("invalid comment status")
        actor = _check_text(actor, "actor", 256)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            comment = connection.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
            if comment is None:
                raise NotFoundError(f"comment not found: {comment_id}")
            if addressed_in_version_id:
                version = self._get_version_row(connection, addressed_in_version_id)
                if version["artifact_id"] != comment["artifact_id"]:
                    raise ValidationError("addressed version does not belong to artifact")
            event_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO comment_events(id, comment_id, status, actor, addressed_in_version_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (event_id, comment_id, status, actor, addressed_in_version_id),
            )
            connection.execute(
                f"""UPDATE comments
                    SET status = ?, addressed_in_version_id = ?, updated_at = {_now_sql()}
                    WHERE id = ?""",
                (status, addressed_in_version_id, comment_id),
            )
            row = connection.execute("SELECT * FROM comment_events WHERE id = ?", (event_id,)).fetchone()
            return dict(row)

    def list_comment_events(self, comment_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM comments WHERE id = ?", (comment_id,)).fetchone() is None:
                raise NotFoundError(f"comment not found: {comment_id}")
            rows = connection.execute(
                "SELECT * FROM comment_events WHERE comment_id = ? ORDER BY created_at", (comment_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def archive_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._get_artifact_row(connection, artifact_id)
            connection.execute(
                f"UPDATE artifacts SET archived_at = {_now_sql()}, updated_at = {_now_sql()} WHERE id = ?",
                (artifact_id,),
            )
            return self._artifact_dict(self._get_artifact_row(connection, artifact_id))
