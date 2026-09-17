from __future__ import annotations

import hashlib
import base64
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_COMMENT_BYTES = 16 * 1024
MAX_METADATA_BYTES = 16 * 1024
ALLOWED_KINDS = {"text", "markdown", "code", "html", "svg", "mermaid"}
DEFAULT_PRINCIPAL_ID = "principal:local"
METADATA_FIELDS = {
    "schema_version",
    "tags",
    "source_agent",
    "project",
    "purpose",
    "content_language",
}
DEFAULT_METADATA = {
    "schema_version": 1,
    "tags": [],
    "source_agent": None,
    "project": None,
    "purpose": None,
    "content_language": None,
}
DEFAULT_METADATA_JSON = json.dumps(DEFAULT_METADATA, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


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


def _validate_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {"schema_version": 1, "tags": [], "source_agent": None, "project": None, "purpose": None, "content_language": None}
    if not isinstance(value, dict):
        raise ValidationError("metadata must be an object")
    unknown = sorted(set(value) - METADATA_FIELDS)
    if unknown:
        raise ValidationError(f"unknown metadata field: {unknown[0]}")
    schema_version = value.get("schema_version", 1)
    if isinstance(schema_version, bool) or schema_version != 1:
        raise ValidationError("metadata schema_version must be 1")
    tags = value.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 32:
        raise ValidationError("metadata tags must be a list of at most 32 strings")
    checked_tags: list[str] = []
    for tag in tags:
        if not isinstance(tag, str) or not tag or len(tag.encode("utf-8")) > 64:
            raise ValidationError("metadata tags must contain non-empty strings of at most 64 bytes")
        checked_tags.append(tag)
    result: dict[str, Any] = {"schema_version": 1, "tags": checked_tags}
    limits = {"source_agent": 128, "project": 256, "purpose": 2048, "content_language": 32}
    for field, limit in limits.items():
        field_value = value.get(field)
        if field_value is not None and (not isinstance(field_value, str) or not field_value):
            raise ValidationError(f"metadata {field} must be a non-empty string or null")
        if isinstance(field_value, str) and len(field_value.encode("utf-8")) > limit:
            raise ValidationError(f"metadata {field} exceeds {limit} bytes")
        result[field] = field_value
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_METADATA_BYTES:
        raise ValidationError(f"metadata exceeds {MAX_METADATA_BYTES} bytes")
    return result


def _metadata_from_row(row: sqlite3.Row) -> dict[str, Any]:
    raw = row["metadata_json"] if "metadata_json" in row.keys() else None
    try:
        return _validate_metadata(json.loads(raw)) if raw else _validate_metadata(None)
    except (TypeError, json.JSONDecodeError) as error:
        raise StoreError("stored artifact metadata is invalid") from error


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
            stranded = connection.execute(
                """SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name IN (
                        'versions__legacy_hash_migration',
                        'comments__legacy_hash_migration',
                        'comment_events__legacy_hash_migration'
                    )"""
            ).fetchall()
            if stranded:
                names = ", ".join(row["name"] for row in stranded)
                raise StoreError(f"stranded legacy migration tables: {names}")
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
                    metadata_json TEXT NOT NULL DEFAULT '{DEFAULT_METADATA_JSON}',
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
                    UNIQUE(artifact_id, sequence)
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
                    result_version_id TEXT,
                    created_at TEXT NOT NULL DEFAULT ({_now_sql()}),
                    PRIMARY KEY(scope, key)
                );
                CREATE TABLE IF NOT EXISTS system_artifacts (
                    system_key TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL UNIQUE REFERENCES artifacts(id)
                );
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    operation_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    agent_id TEXT,
                    agent_run_id TEXT,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    source_comment_id TEXT,
                    request_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT ({_now_sql()})
                );
                CREATE TABLE IF NOT EXISTS metadata_events (
                    id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    operation_id TEXT REFERENCES operations(id),
                    actor TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT ({_now_sql()})
                );
                CREATE INDEX IF NOT EXISTS idx_operations_resource ON operations(resource_type, resource_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_operations_agent ON operations(agent_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_metadata_events_artifact ON metadata_events(artifact_id, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    artifact_id TEXT,
                    version_id TEXT,
                    comment_id TEXT,
                    operation_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT ({_now_sql()})
                );
                CREATE INDEX IF NOT EXISTS idx_events_created ON events(seq, created_at);
                CREATE TABLE IF NOT EXISTS event_deliveries (
                    event_id TEXT NOT NULL REFERENCES events(event_id),
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    response_code INTEGER,
                    error TEXT,
                    attempted_at TEXT NOT NULL DEFAULT ({_now_sql()}),
                    delivered_at TEXT,
                    PRIMARY KEY (event_id, attempt)
                );
                CREATE INDEX IF NOT EXISTS idx_event_deliveries_status ON event_deliveries(event_id, status);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES ('001_initial');
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(artifacts)").fetchall()}
            if "pinned_at" not in columns:
                connection.execute("ALTER TABLE artifacts ADD COLUMN pinned_at TEXT")
            if "owner_principal_id" not in columns:
                connection.execute("ALTER TABLE artifacts ADD COLUMN owner_principal_id TEXT")
            if "visibility" not in columns:
                connection.execute("ALTER TABLE artifacts ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'")
            if "content_updated_at" not in columns:
                connection.execute("ALTER TABLE artifacts ADD COLUMN content_updated_at TEXT")
            if "metadata_json" not in columns:
                connection.execute(
                    f"ALTER TABLE artifacts ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{DEFAULT_METADATA_JSON}'"
                )
            operation_columns = {row["name"] for row in connection.execute("PRAGMA table_info(operations)").fetchall()}
            if "source_comment_id" not in operation_columns:
                connection.execute("ALTER TABLE operations ADD COLUMN source_comment_id TEXT")
            idempotency_columns = {row["name"] for row in connection.execute("PRAGMA table_info(idempotency_keys)").fetchall()}
            if "result_version_id" not in idempotency_columns:
                connection.execute("ALTER TABLE idempotency_keys ADD COLUMN result_version_id TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_artifacts_metadata_project ON artifacts(json_extract(metadata_json, '$.project'))"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_artifacts_metadata_source_agent ON artifacts(json_extract(metadata_json, '$.source_agent'))"
            )
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS principals (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('user', 'agent', 'service')),
                    subject TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT ({_now_sql()}),
                    disabled_at TEXT
                );
                CREATE TABLE IF NOT EXISTS artifact_grants (
                    id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    principal_id TEXT NOT NULL REFERENCES principals(id),
                    role TEXT NOT NULL CHECK(role IN ('viewer', 'commenter', 'editor')),
                    granted_by_principal_id TEXT NOT NULL REFERENCES principals(id),
                    created_at TEXT NOT NULL DEFAULT ({_now_sql()}),
                    revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS artifact_preferences (
                    principal_id TEXT NOT NULL REFERENCES principals(id),
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    pinned_at TEXT,
                    comments_visible INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(principal_id, artifact_id)
                );
                CREATE TABLE IF NOT EXISTS artifact_visits (
                    principal_id TEXT NOT NULL REFERENCES principals(id),
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    last_viewed_at TEXT NOT NULL,
                    PRIMARY KEY(principal_id, artifact_id)
                );
                CREATE INDEX IF NOT EXISTS idx_artifacts_content_updated
                    ON artifacts(content_updated_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_grants_principal
                    ON artifact_grants(principal_id, revoked_at);
                INSERT OR IGNORE INTO principals(id, kind, subject, display_name)
                    VALUES ('{DEFAULT_PRINCIPAL_ID}', 'user', 'local', 'Local user');
                UPDATE artifacts
                   SET owner_principal_id = COALESCE(owner_principal_id, '{DEFAULT_PRINCIPAL_ID}'),
                       content_updated_at = COALESCE(content_updated_at, updated_at)
                 WHERE owner_principal_id IS NULL OR content_updated_at IS NULL;
                INSERT OR IGNORE INTO artifact_preferences(principal_id, artifact_id, pinned_at)
                    SELECT '{DEFAULT_PRINCIPAL_ID}', id, pinned_at
                      FROM artifacts
                     WHERE pinned_at IS NOT NULL;
                """
            )
            self._drop_legacy_content_hash_unique(connection)

    @staticmethod
    def _drop_legacy_content_hash_unique(connection: sqlite3.Connection) -> None:
        """Rebuild old databases whose content-hash uniqueness blocked restore."""
        for index in connection.execute("PRAGMA index_list('versions')").fetchall():
            if not index["unique"]:
                continue
            columns = [row["name"] for row in connection.execute(f"PRAGMA index_info('{index['name']}')").fetchall()]
            if columns != ["artifact_id", "content_hash"]:
                continue
            connection.execute("PRAGMA foreign_keys = OFF")
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("ALTER TABLE comment_events RENAME TO comment_events__legacy_hash_migration")
                connection.execute("ALTER TABLE comments RENAME TO comments__legacy_hash_migration")
                connection.execute("ALTER TABLE versions RENAME TO versions__legacy_hash_migration")
                connection.execute(f"""
                    CREATE TABLE versions (
                        id TEXT PRIMARY KEY,
                        artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                        sequence INTEGER NOT NULL,
                        parent_version_id TEXT REFERENCES versions(id),
                        content TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        change_summary TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL DEFAULT ({_now_sql()}),
                        created_by TEXT NOT NULL,
                        UNIQUE(artifact_id, sequence)
                    )""")
                connection.execute("INSERT INTO versions SELECT * FROM versions__legacy_hash_migration")
                connection.execute(f"""
                    CREATE TABLE comments (
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
                    )""")
                connection.execute("INSERT INTO comments SELECT * FROM comments__legacy_hash_migration")
                connection.execute(f"""
                    CREATE TABLE comment_events (
                        id TEXT PRIMARY KEY,
                        comment_id TEXT NOT NULL REFERENCES comments(id),
                        status TEXT NOT NULL
                            CHECK(status IN ('open', 'addressed', 'resolved')),
                        actor TEXT NOT NULL,
                        addressed_in_version_id TEXT REFERENCES versions(id),
                        created_at TEXT NOT NULL DEFAULT ({_now_sql()})
                    )""")
                connection.execute("INSERT INTO comment_events SELECT * FROM comment_events__legacy_hash_migration")
                if connection.execute("PRAGMA foreign_key_check").fetchall():
                    raise StoreError("legacy migration produced foreign-key violations")
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version) VALUES ('002_drop_versions_content_hash_unique')"
                )
                connection.execute("DROP TABLE comment_events__legacy_hash_migration")
                connection.execute("DROP TABLE comments__legacy_hash_migration")
                connection.execute("DROP TABLE versions__legacy_hash_migration")
                connection.commit()
            except Exception as error:
                if connection.in_transaction:
                    connection.rollback()
                if isinstance(error, StoreError):
                    raise
                raise StoreError("legacy content-hash migration failed") from error
            finally:
                connection.execute("PRAGMA foreign_keys = ON")
            break

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
        result["metadata"] = _metadata_from_row(row)
        result.pop("metadata_json", None)
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

    @staticmethod
    def _record_operation(
        connection: sqlite3.Connection,
        *,
        operation_type: str,
        actor: str,
        agent_id: str | None,
        agent_run_id: str | None,
        operation_id: str | None,
        resource_type: str,
        resource_id: str,
        request_hash: str,
        source_comment_id: str | None = None,
    ) -> str:
        operation_id = operation_id or str(uuid.uuid4())
        actor = _check_text(actor, "actor", 256)
        if agent_id is not None:
            agent_id = _check_text(agent_id, "agent_id", 256)
        if agent_run_id is not None:
            agent_run_id = _check_text(agent_run_id, "agent_run_id", 256)
        if source_comment_id is not None:
            source_comment_id = _check_text(source_comment_id, "source_comment_id", 256)
        existing = connection.execute("SELECT request_hash FROM operations WHERE id = ?", (operation_id,)).fetchone()
        if existing is not None:
            raise ValidationError("operation ID was already used; provide an idempotency key to retry")
        connection.execute(
            """INSERT INTO operations
               (id, operation_type, actor, agent_id, agent_run_id, resource_type, resource_id, source_comment_id, request_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (operation_id, operation_type, actor, agent_id, agent_run_id, resource_type, resource_id, source_comment_id, request_hash),
        )
        return operation_id

    @staticmethod
    def _record_metadata_event(
        connection: sqlite3.Connection,
        *,
        artifact_id: str,
        operation_id: str,
        actor: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> str:
        event_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO metadata_events(id, artifact_id, operation_id, actor, before_json, after_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                artifact_id,
                operation_id,
                _check_text(actor, "actor", 256),
                json.dumps(_validate_metadata(before), ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                json.dumps(_validate_metadata(after), ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            ),
        )
        return event_id

    def list_metadata_events(self, artifact_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._get_artifact_row(connection, artifact_id)
            rows = connection.execute(
                "SELECT * FROM metadata_events WHERE artifact_id = ? ORDER BY created_at, id", (artifact_id,)
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["before"] = json.loads(item.pop("before_json"))
                item["after"] = json.loads(item.pop("after_json"))
                result.append(item)
            return result

    def list_operations(
        self,
        *,
        resource_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValidationError("operation limit must be between 1 and 1000")
        clauses = []
        params: list[Any] = []
        if resource_id:
            clauses.append("resource_id = ?")
            params.append(resource_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                f"SELECT * FROM operations {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                [*params, limit],
            ).fetchall()]

    @staticmethod
    def _event_cursor(seq: int) -> str:
        raw = json.dumps({"v": 1, "seq": seq}, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_event_cursor(cursor: str | None) -> int:
        if not cursor:
            return 0
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationError("event cursor is invalid") from None
        if not isinstance(value, dict) or value.get("v") != 1 or not isinstance(value.get("seq"), int) or value["seq"] < 0:
            raise ValidationError("event cursor is invalid")
        return value["seq"]

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        *,
        event_type: str,
        artifact_id: str | None = None,
        version_id: str | None = None,
        comment_id: str | None = None,
        operation_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO events
               (event_id, event_type, artifact_id, version_id, comment_id, operation_id, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event_id, event_type, artifact_id, version_id, comment_id, operation_id,
             json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)),
        )
        row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        return Store._event_dict(row)

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result.update(json.loads(result.pop("payload_json")))
        return result

    def list_events(self, *, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        if not 1 <= limit <= 1000:
            raise ValidationError("event limit must be between 1 and 1000")
        after = self._decode_event_cursor(cursor)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?",
                (after, limit + 1),
            ).fetchall()
            has_more = len(rows) > limit
            page = rows[:limit]
            next_cursor = self._event_cursor(page[-1]["seq"]) if has_more and page else None
            return {
                "items": [self._event_dict(row) for row in page],
                "next_cursor": next_cursor,
            }

    def get_event(self, event_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"event not found: {event_id}")
            return self._event_dict(row)

    def record_event_delivery(
        self,
        event_id: str,
        attempt: int,
        status: str,
        response_code: int | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if attempt < 1 or attempt > 3:
            raise ValidationError("delivery attempt must be between 1 and 3")
        if status not in {"delivered", "failed"}:
            raise ValidationError("invalid delivery status")
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,)).fetchone() is None:
                raise NotFoundError(f"event not found: {event_id}")
            delivered_at = connection.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0] if status == "delivered" else None
            connection.execute(
                """INSERT INTO event_deliveries(event_id, attempt, status, response_code, error, delivered_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(event_id, attempt) DO UPDATE SET status = excluded.status,
                     response_code = excluded.response_code, error = excluded.error, delivered_at = excluded.delivered_at""",
                (event_id, attempt, status, response_code, error, delivered_at),
            )
            row = connection.execute(
                "SELECT * FROM event_deliveries WHERE event_id = ? AND attempt = ?", (event_id, attempt)
            ).fetchone()
            return dict(row)

    def list_event_deliveries(self, event_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,)).fetchone() is None:
                raise NotFoundError(f"event not found: {event_id}")
            rows = connection.execute(
                "SELECT * FROM event_deliveries WHERE event_id = ? ORDER BY attempt", (event_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def create_artifact(
        self,
        title: str,
        kind: str,
        content: str,
        created_by: str,
        idempotency_key: str | None = None,
        *,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        operation_id: str | None = None,
        operation_type: str = "artifact.create",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        title = _check_text(title, "title", 512)
        content = _check_text(content, "content", MAX_CONTENT_BYTES)
        created_by = _check_text(created_by, "created_by", 256)
        _check_kind(kind)
        metadata = _validate_metadata(metadata)
        request_hash = self._hash_request({
            "title": title,
            "kind": kind,
            "content": content,
            "created_by": created_by,
            "agent_id": agent_id,
            "agent_run_id": agent_run_id,
            "metadata": metadata,
        })
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_id = self._idempotent_result(connection, "create", idempotency_key, request_hash)
            if existing_id:
                result = self._artifact_dict(self._get_artifact_row(connection, existing_id))
                replay = connection.execute(
                    "SELECT result_version_id FROM idempotency_keys WHERE scope = 'create' AND key = ? AND request_hash = ?",
                    (idempotency_key, request_hash),
                ).fetchone()
                original_version_id = replay["result_version_id"] if replay and replay["result_version_id"] else result["current_version_id"]
                version = self._get_version_row(connection, original_version_id)
                result["created_version_id"] = original_version_id
                result["content_hash"] = version["content_hash"]
                operation = connection.execute(
                    "SELECT id FROM operations WHERE resource_type = 'artifact' AND resource_id = ? AND request_hash = ? LIMIT 1",
                    (existing_id, request_hash),
                ).fetchone()
                if operation is not None:
                    result["operation_id"] = operation["id"]
                    event = connection.execute(
                        "SELECT event_id FROM events WHERE operation_id = ? ORDER BY seq DESC LIMIT 1",
                        (operation["id"],),
                    ).fetchone()
                    if event is not None:
                        result["event_id"] = event["event_id"]
                return result
            artifact_id = str(uuid.uuid4())
            version_id = str(uuid.uuid4())
            slug = self._slug(title, artifact_id)
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            connection.execute(
                "INSERT INTO artifacts(id, slug, title, kind, metadata_json, current_version_id) VALUES (?, ?, ?, ?, ?, ?)",
                (artifact_id, slug, title, kind, json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True), version_id),
            )
            connection.execute(
                """INSERT INTO versions
                   (id, artifact_id, sequence, content, content_hash, created_by)
                   VALUES (?, ?, 1, ?, ?, ?)""",
                (version_id, artifact_id, content, content_hash, created_by),
            )
            connection.execute(
                f"""UPDATE artifacts
                       SET owner_principal_id = ?, content_updated_at = {_now_sql()}
                     WHERE id = ?""",
                (DEFAULT_PRINCIPAL_ID, artifact_id),
            )
            if idempotency_key:
                connection.execute(
                    """INSERT INTO idempotency_keys(scope, key, request_hash, result_id, result_version_id)
                       VALUES ('create', ?, ?, ?, ?)""",
                    (idempotency_key, request_hash, artifact_id, version_id),
                )
            recorded_operation_id = self._record_operation(
                connection,
                operation_type=operation_type,
                actor=created_by,
                agent_id=agent_id,
                agent_run_id=agent_run_id,
                operation_id=operation_id,
                resource_type="artifact",
                resource_id=artifact_id,
                request_hash=request_hash,
            )
            self._record_metadata_event(
                connection,
                artifact_id=artifact_id,
                operation_id=recorded_operation_id,
                actor=created_by,
                before=_validate_metadata(None),
                after=metadata,
            )
            event = self._append_event(
                connection,
                event_type="version.created",
                artifact_id=artifact_id,
                version_id=version_id,
                operation_id=recorded_operation_id,
                payload={"version_sequence": 1},
            )
            result = self._artifact_dict(self._get_artifact_row(connection, artifact_id))
            result["created_version_id"] = version_id
            result["content_hash"] = content_hash
            result["operation_id"] = recorded_operation_id
            result["event_id"] = event["event_id"]
            return result

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            return self._artifact_dict(self._get_artifact_row(connection, artifact_id))

    def list_artifacts(
        self,
        query: str | None = None,
        include_archived: bool = False,
        *,
        tags: list[str] | None = None,
        project: str | None = None,
        source_agent: str | None = None,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if not include_archived:
            clauses.append("a.archived_at IS NULL")
        if query:
            clauses.append("(a.title LIKE ? OR a.slug LIKE ?)")
            pattern = f"%{query}%"
            params.extend([pattern, pattern])
        if kind:
            _check_kind(kind)
            clauses.append("a.kind = ?")
            params.append(kind)
        if project is not None:
            project = _check_text(project, "project", 256)
            clauses.append("json_extract(a.metadata_json, '$.project') = ?")
            params.append(project)
        if source_agent is not None:
            source_agent = _check_text(source_agent, "source_agent", 128)
            clauses.append("json_extract(a.metadata_json, '$.source_agent') = ?")
            params.append(source_agent)
        for tag in tags or []:
            tag = _check_text(tag, "tag", 64)
            clauses.append("EXISTS (SELECT 1 FROM json_each(a.metadata_json, '$.tags') WHERE value = ?)")
            params.append(tag)
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

    def get_principal(self, principal_id: str = DEFAULT_PRINCIPAL_ID) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM principals WHERE id = ?", (principal_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"principal not found: {principal_id}")
            return dict(row)

    @staticmethod
    def _encode_cursor(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> dict[str, Any]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError("cursor is invalid") from error
        if not isinstance(value, dict) or not value.get("snapshot"):
            raise ValidationError("cursor is invalid")
        return value

    def list_catalog(
        self,
        *,
        scope: str = "all",
        query: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        principal_id: str = DEFAULT_PRINCIPAL_ID,
        tags: list[str] | None = None,
        project: str | None = None,
        source_agent: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        if scope not in {"all", "pinned", "yours", "shared"}:
            raise ValidationError("scope must be all, pinned, yours, or shared")
        if not 1 <= limit <= 100:
            raise ValidationError("limit must be between 1 and 100")
        query = query or None
        tags = [_check_text(tag, "tag", 64) for tag in (tags or [])]
        if project is not None:
            project = _check_text(project, "project", 256)
        if source_agent is not None:
            source_agent = _check_text(source_agent, "source_agent", 128)
        if kind is not None:
            _check_kind(kind)
        decoded = self._decode_cursor(cursor) if cursor else None
        if decoded and decoded.get("v") != 2:
            raise ValidationError("cursor version is unsupported")
        snapshot = decoded["snapshot"] if decoded else None
        if decoded and decoded.get("principal_id") != principal_id:
            raise ValidationError("cursor principal does not match request")
        if decoded and decoded.get("scope") != scope:
            raise ValidationError("cursor scope does not match request")
        if decoded and decoded.get("query") != query:
            raise ValidationError("cursor query does not match request")
        if decoded and (
            decoded.get("tags", []) != tags
            or decoded.get("project") != project
            or decoded.get("source_agent") != source_agent
            or decoded.get("kind") != kind
        ):
            raise ValidationError("cursor metadata filters do not match request")
        with self._connect() as connection:
            pin_rows = connection.execute(
                "SELECT artifact_id, COALESCE(pinned_at, '') FROM artifact_preferences WHERE principal_id = ? ORDER BY artifact_id",
                (principal_id,),
            ).fetchall()
            pin_fingerprint = hashlib.sha256(
                json.dumps([list(row) for row in pin_rows], separators=(",", ":")).encode()
            ).hexdigest()
            if decoded and decoded.get("pin_fingerprint") != pin_fingerprint:
                raise ValidationError("cursor invalidated by pin changes")
            if snapshot is None:
                snapshot = connection.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
            clauses = ["a.archived_at IS NULL", "a.content_updated_at <= ?"]
            params: list[Any] = [snapshot]
            if query:
                clauses.append("(a.title LIKE ? OR a.slug LIKE ?)")
                pattern = f"%{query}%"
                params.extend([pattern, pattern])
            if kind:
                clauses.append("a.kind = ?")
                params.append(kind)
            if project is not None:
                clauses.append("json_extract(a.metadata_json, '$.project') = ?")
                params.append(project)
            if source_agent is not None:
                clauses.append("json_extract(a.metadata_json, '$.source_agent') = ?")
                params.append(source_agent)
            for tag in tags:
                clauses.append("EXISTS (SELECT 1 FROM json_each(a.metadata_json, '$.tags') WHERE value = ?)")
                params.append(tag)
            if scope == "pinned":
                clauses.append("p.pinned_at IS NOT NULL")
            elif scope == "yours":
                clauses.append("a.owner_principal_id = ?")
                params.append(principal_id)
            elif scope == "shared":
                clauses.append("g.principal_id = ? AND g.revoked_at IS NULL")
                params.append(principal_id)
            rank_sql = "CASE WHEN p.pinned_at IS NULL THEN 1 ELSE 0 END"
            if decoded:
                after = decoded.get("after")
                if (
                    not isinstance(after, list) or len(after) != 3
                    or after[0] not in {0, 1}
                    or not isinstance(after[1], str) or not isinstance(after[2], str)
                ):
                    raise ValidationError("cursor after key is invalid")
                clauses.append(
                    f"({rank_sql} > ? OR ({rank_sql} = ? AND a.content_updated_at < ?) "
                    f"OR ({rank_sql} = ? AND a.content_updated_at = ? AND a.id < ?))"
                )
                params.extend([after[0], after[0], after[1], after[0], after[1], after[2]])
            rows = connection.execute(
                f"""SELECT a.*, v.sequence AS current_version_sequence,
                           v.id AS preview_version_id,
                           substr(v.content, 1, 512) AS preview_excerpt,
                           length(v.content) > 512 AS preview_truncated,
                           (SELECT COUNT(*) FROM comments c WHERE c.artifact_id = a.id) AS comment_count,
                           p.pinned_at AS principal_pinned_at,
                           av.last_viewed_at,
                           {rank_sql} AS pin_rank
                    FROM artifacts a
                    LEFT JOIN versions v ON v.id = a.current_version_id
                    LEFT JOIN artifact_preferences p ON p.artifact_id = a.id AND p.principal_id = ?
                    LEFT JOIN artifact_visits av ON av.artifact_id = a.id AND av.principal_id = ?
                    LEFT JOIN artifact_grants g ON g.artifact_id = a.id
                    WHERE {' AND '.join(clauses)}
                    GROUP BY a.id
                    ORDER BY pin_rank, a.content_updated_at DESC, a.id DESC
                    LIMIT ?""",
                [principal_id, principal_id, *params, limit + 1],
            ).fetchall()
            items = []
            for row in rows:
                item = self._artifact_dict(row)
                item["pinned"] = bool(row["principal_pinned_at"] or (scope == "all" and row["pinned_at"]))
                item["owner"] = {"id": row["owner_principal_id"] or DEFAULT_PRINCIPAL_ID, "display_name": "Local user"}
                item["viewer_role"] = "owner" if item["owner"]["id"] == principal_id else "viewer"
                item["visibility"] = row["visibility"] or "private"
                item["activity"] = {
                    "kind": "Viewed" if row["last_viewed_at"] and row["last_viewed_at"] > row["content_updated_at"] else "Edited",
                    "time": row["last_viewed_at"] if row["last_viewed_at"] and row["last_viewed_at"] > row["content_updated_at"] else row["content_updated_at"],
                }
                item["preview"] = {
                    "kind": row["kind"],
                    "version_id": row["preview_version_id"],
                    "excerpt": row["preview_excerpt"] or "",
                    "truncated": bool(row["preview_truncated"]),
                    "alt": f"{row['title']} preview",
                }
                item.pop("principal_pinned_at", None)
                item.pop("last_viewed_at", None)
                item.pop("preview_excerpt", None)
                item.pop("preview_truncated", None)
                items.append(item)
            page = items[:limit]
            next_cursor = None
            if len(items) > limit and page:
                last = page[-1]
                next_cursor = self._encode_cursor({
                    "v": 2,
                    "snapshot": snapshot,
                    "scope": scope,
                    "query": query,
                    "tags": tags,
                    "project": project,
                    "source_agent": source_agent,
                    "kind": kind,
                    "principal_id": principal_id,
                    "pin_fingerprint": pin_fingerprint,
                    "after": [last["pin_rank"], last["content_updated_at"], last["id"]],
                })
            for item in page:
                item.pop("pin_rank", None)
            return {"items": page, "snapshot": snapshot, "next_cursor": next_cursor}

    def record_visit(
        self,
        artifact_id: str,
        principal_id: str = DEFAULT_PRINCIPAL_ID,
        *,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._get_artifact_row(connection, artifact_id)
            now = connection.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
            connection.execute(
                """INSERT INTO artifact_visits(principal_id, artifact_id, last_viewed_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(principal_id, artifact_id) DO UPDATE SET last_viewed_at = excluded.last_viewed_at""",
                (principal_id, artifact_id, now),
            )
            recorded_operation_id = self._record_operation(
                connection,
                operation_type="artifact.visit",
                actor=agent_id or principal_id,
                agent_id=agent_id,
                agent_run_id=agent_run_id,
                operation_id=operation_id,
                resource_type="artifact",
                resource_id=artifact_id,
                request_hash=self._hash_request({"artifact_id": artifact_id, "principal_id": principal_id}),
            )
            return {"artifact_id": artifact_id, "principal_id": principal_id, "last_viewed_at": now, "operation_id": recorded_operation_id}

    def set_pinned(
        self,
        artifact_id: str,
        pinned: bool,
        *,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._get_artifact_row(connection, artifact_id)
            pinned_at = connection.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0] if pinned else None
            connection.execute(
                "UPDATE artifacts SET pinned_at = ? WHERE id = ?",
                (pinned_at, artifact_id),
            )
            connection.execute(
                """INSERT INTO artifact_preferences(principal_id, artifact_id, pinned_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(principal_id, artifact_id) DO UPDATE SET pinned_at = excluded.pinned_at""",
                (DEFAULT_PRINCIPAL_ID, artifact_id, pinned_at),
            )
            recorded_operation_id = self._record_operation(
                connection,
                operation_type="artifact.pin" if pinned else "artifact.unpin",
                actor=agent_id or DEFAULT_PRINCIPAL_ID,
                agent_id=agent_id,
                agent_run_id=agent_run_id,
                operation_id=operation_id,
                resource_type="artifact",
                resource_id=artifact_id,
                request_hash=self._hash_request({"artifact_id": artifact_id, "pinned": pinned}),
            )
            result = self._artifact_dict(self._get_artifact_row(connection, artifact_id))
            result["operation_id"] = recorded_operation_id
            return result

    def ensure_project_description(
        self,
        title: str,
        content: str,
        created_by: str = "release",
    ) -> dict[str, Any]:
        """Create or publish the canonical project-description artifact."""
        title = _check_text(title, "title", 512)
        content = _check_text(content, "content", MAX_CONTENT_BYTES)
        system_key = "project-description"
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT artifact_id FROM system_artifacts WHERE system_key = ?", (system_key,)
            ).fetchone()
            artifact_id = row["artifact_id"] if row is not None else None
            if artifact_id is None:
                legacy = connection.execute(
                    """SELECT i.result_id
                         FROM idempotency_keys i
                         JOIN artifacts a ON a.id = i.result_id
                        WHERE i.scope = 'create'
                          AND i.key = 'system:project-description'
                          AND a.kind = 'html'"""
                ).fetchone()
                artifact_id = legacy["result_id"] if legacy is not None else None
            if artifact_id is None:
                artifact_id = str(uuid.uuid4())
                version_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO artifacts(id, slug, title, kind, current_version_id) VALUES (?, ?, ?, 'html', ?)",
                    (artifact_id, self._slug(title, artifact_id), title, version_id),
                )
                connection.execute(
                    """INSERT INTO versions
                       (id, artifact_id, sequence, content, content_hash, created_by)
                       VALUES (?, ?, 1, ?, ?, ?)""",
                    (version_id, artifact_id, content, content_hash, created_by),
                )
                connection.execute(
                    f"""UPDATE artifacts
                           SET owner_principal_id = ?, content_updated_at = {_now_sql()}
                         WHERE id = ?""",
                    (DEFAULT_PRINCIPAL_ID, artifact_id),
                )
            connection.execute(
                "INSERT OR IGNORE INTO system_artifacts(system_key, artifact_id) VALUES (?, ?)",
                (system_key, artifact_id),
            )
            artifact = self._get_artifact_row(connection, artifact_id)
            current = self._get_version_row(connection, artifact["current_version_id"])
            if current["content_hash"] != content_hash:
                version_id = str(uuid.uuid4())
                sequence = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM versions WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone()[0]
                connection.execute(
                    """INSERT INTO versions
                       (id, artifact_id, sequence, parent_version_id, content, content_hash, change_summary, created_by)
                       VALUES (?, ?, ?, ?, ?, ?, 'Sync project description', ?)""",
                    (version_id, artifact_id, sequence, current["id"], content, content_hash, created_by),
                )
                connection.execute(
                    f"""UPDATE artifacts
                           SET current_version_id = ?, updated_at = {_now_sql()}, content_updated_at = {_now_sql()}
                         WHERE id = ?""",
                    (version_id, artifact_id),
                )
            pinned_at = connection.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
            connection.execute("UPDATE artifacts SET pinned_at = ? WHERE id = ?", (pinned_at, artifact_id))
            connection.execute(
                """INSERT INTO artifact_preferences(principal_id, artifact_id, pinned_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(principal_id, artifact_id) DO UPDATE SET pinned_at = excluded.pinned_at""",
                (DEFAULT_PRINCIPAL_ID, artifact_id, pinned_at),
            )
            return self._artifact_dict(self._get_artifact_row(connection, artifact_id))

    def set_metadata(
        self,
        artifact_id: str,
        metadata: dict[str, Any],
        actor: str = "api",
        *,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        metadata = _validate_metadata(metadata)
        actor = _check_text(actor, "actor", 256)
        request_hash = self._hash_request({
            "artifact_id": artifact_id,
            "metadata": metadata,
            "actor": actor,
            "agent_id": agent_id,
            "agent_run_id": agent_run_id,
        })
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            artifact = self._get_artifact_row(connection, artifact_id)
            before = _metadata_from_row(artifact)
            if before == metadata:
                return self._artifact_dict(artifact)
            connection.execute(
                f"UPDATE artifacts SET metadata_json = ?, updated_at = {_now_sql()} WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True), artifact_id),
            )
            recorded_operation_id = self._record_operation(
                connection,
                operation_type="artifact.metadata",
                actor=actor,
                agent_id=agent_id,
                agent_run_id=agent_run_id,
                operation_id=operation_id,
                resource_type="artifact",
                resource_id=artifact_id,
                request_hash=request_hash,
            )
            self._record_metadata_event(
                connection,
                artifact_id=artifact_id,
                operation_id=recorded_operation_id,
                actor=actor,
                before=before,
                after=metadata,
            )
            event = self._append_event(
                connection,
                event_type="artifact.metadata.updated",
                artifact_id=artifact_id,
                operation_id=recorded_operation_id,
                payload={"metadata_schema_version": metadata["schema_version"]},
            )
            result = self._artifact_dict(self._get_artifact_row(connection, artifact_id))
            result["operation_id"] = recorded_operation_id
            result["event_id"] = event["event_id"]
            return result

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
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        operation_id: str | None = None,
        operation_type: str = "version.publish",
        metadata: dict[str, Any] | None = None,
        source_comment_id: str | None = None,
    ) -> dict[str, Any]:
        created_by = _check_text(created_by, "created_by", 256)
        metadata = _validate_metadata(metadata) if metadata is not None else None
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
            "agent_id": agent_id,
            "agent_run_id": agent_run_id,
            "metadata": metadata,
            "source_comment_id": source_comment_id,
        })
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_id = self._idempotent_result(connection, f"publish:{artifact_id}", idempotency_key, request_hash)
            if existing_id:
                result = self.get_version(existing_id)
                result["metadata"] = _metadata_from_row(self._get_artifact_row(connection, result["artifact_id"]))
                operation = connection.execute(
                    "SELECT id FROM operations WHERE resource_type = 'version' AND resource_id = ? AND request_hash = ? LIMIT 1",
                    (existing_id, request_hash),
                ).fetchone()
                if operation is not None:
                    result["operation_id"] = operation["id"]
                    event = connection.execute(
                        "SELECT event_id FROM events WHERE operation_id = ? ORDER BY seq DESC LIMIT 1",
                        (operation["id"],),
                    ).fetchone()
                    if event is not None:
                        result["event_id"] = event["event_id"]
                return result
            artifact = self._get_artifact_row(connection, artifact_id)
            current_metadata = _metadata_from_row(artifact)
            if source_comment_id is not None:
                source_comment = connection.execute(
                    "SELECT artifact_id FROM comments WHERE id = ?", (source_comment_id,)
                ).fetchone()
                if source_comment is None:
                    raise NotFoundError(f"comment not found: {source_comment_id}")
                if source_comment["artifact_id"] != artifact_id:
                    raise ValidationError("source comment does not belong to artifact")
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
                f"""UPDATE artifacts
                    SET current_version_id = ?, updated_at = {_now_sql()}, content_updated_at = {_now_sql()}
                    WHERE id = ?""",
                (version_id, artifact_id),
            )
            if metadata is not None and metadata != current_metadata:
                connection.execute(
                    f"UPDATE artifacts SET metadata_json = ?, updated_at = {_now_sql()} WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True), artifact_id),
                )
            if idempotency_key:
                connection.execute(
                    """INSERT INTO idempotency_keys(scope, key, request_hash, result_id)
                       VALUES (?, ?, ?, ?)""",
                    (f"publish:{artifact_id}", idempotency_key, request_hash, version_id),
                )
            recorded_operation_id = self._record_operation(
                connection,
                operation_type=operation_type,
                actor=created_by,
                agent_id=agent_id,
                agent_run_id=agent_run_id,
                operation_id=operation_id,
                resource_type="version",
                resource_id=version_id,
                request_hash=request_hash,
                source_comment_id=source_comment_id,
            )
            if metadata is not None and metadata != current_metadata:
                self._record_metadata_event(
                    connection,
                    artifact_id=artifact_id,
                    operation_id=recorded_operation_id,
                    actor=created_by,
                    before=current_metadata,
                    after=metadata,
                )
            event = self._append_event(
                connection,
                event_type="version.created",
                artifact_id=artifact_id,
                version_id=version_id,
                operation_id=recorded_operation_id,
                payload={"version_sequence": sequence, "source_comment_id": source_comment_id},
            )
            result = dict(self._get_version_row(connection, version_id))
            result["metadata"] = _metadata_from_row(self._get_artifact_row(connection, artifact_id))
            result["operation_id"] = recorded_operation_id
            result["event_id"] = event["event_id"]
            return result

    def create_comment(
        self,
        artifact_id: str,
        version_id: str,
        body: str,
        anchor: dict[str, Any],
        idempotency_key: str | None = None,
        *,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        body = _check_text(body, "body", MAX_COMMENT_BYTES)
        if not isinstance(anchor, dict):
            raise ValidationError("anchor must be an object")
        request_hash = self._hash_request({
            "artifact_id": artifact_id,
            "version_id": version_id,
            "body": body,
            "anchor": anchor,
            "agent_id": agent_id,
            "agent_run_id": agent_run_id,
        })
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_id = self._idempotent_result(connection, f"comment:{version_id}", idempotency_key, request_hash)
            if existing_id:
                row = connection.execute("SELECT * FROM comments WHERE id = ?", (existing_id,)).fetchone()
                if row is None:
                    raise StoreError("idempotency result comment is missing")
                result = dict(row)
                result["anchor"] = json.loads(result.pop("anchor_json"))
                return result
            self._get_artifact_row(connection, artifact_id)
            version = self._get_version_row(connection, version_id)
            if version["artifact_id"] != artifact_id:
                raise ValidationError("version does not belong to artifact")
            comment_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO comments(id, artifact_id, version_id, body, anchor_json) VALUES (?, ?, ?, ?, ?)",
                (comment_id, artifact_id, version_id, body, json.dumps(anchor, ensure_ascii=False, sort_keys=True)),
            )
            if idempotency_key:
                connection.execute(
                    """INSERT INTO idempotency_keys(scope, key, request_hash, result_id)
                       VALUES (?, ?, ?, ?)""",
                    (f"comment:{version_id}", idempotency_key, request_hash, comment_id),
                )
            recorded_operation_id = self._record_operation(
                connection,
                operation_type="comment.create",
                actor=agent_id or "api",
                agent_id=agent_id,
                agent_run_id=agent_run_id,
                operation_id=operation_id,
                resource_type="comment",
                resource_id=comment_id,
                request_hash=request_hash,
            )
            event = self._append_event(
                connection,
                event_type="comment.created",
                artifact_id=artifact_id,
                version_id=version_id,
                comment_id=comment_id,
                operation_id=recorded_operation_id,
            )
            result = dict(connection.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone())
            result["anchor"] = json.loads(result.pop("anchor_json"))
            result["operation_id"] = recorded_operation_id
            result["event_id"] = event["event_id"]
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

    @staticmethod
    def _encode_comment_cursor(created_at: str, comment_id: str, status: str | None) -> str:
        payload = {"v": 1, "after": [created_at, comment_id], "status": status}
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_comment_cursor(cursor: str) -> dict[str, Any]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError("comment cursor is invalid") from error
        if (
            not isinstance(value, dict)
            or value.get("v") != 1
            or not isinstance(value.get("after"), list)
            or len(value["after"]) != 2
            or not all(isinstance(item, str) for item in value["after"])
        ):
            raise ValidationError("comment cursor is invalid")
        return value

    def list_comments_global(
        self,
        *,
        status: str | None = "open",
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if status is not None and status not in {"open", "addressed", "resolved"}:
            raise ValidationError("invalid comment status")
        if not 1 <= limit <= 100:
            raise ValidationError("comment limit must be between 1 and 100")
        decoded = self._decode_comment_cursor(cursor) if cursor else None
        if decoded and decoded.get("status") != status:
            raise ValidationError("comment cursor status does not match request")
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("c.status = ?")
            params.append(status)
        if decoded:
            created_at, comment_id = decoded["after"]
            clauses.append("(c.created_at > ? OR (c.created_at = ? AND c.id > ?))")
            params.extend([created_at, created_at, comment_id])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT c.*, a.title AS artifact_title, v.sequence AS version_sequence
                    FROM comments c
                    JOIN artifacts a ON a.id = c.artifact_id
                    JOIN versions v ON v.id = c.version_id
                    {where}
                    ORDER BY c.created_at ASC, c.id ASC
                    LIMIT ?""",
                [*params, limit + 1],
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["anchor"] = json.loads(item.pop("anchor_json"))
                items.append(item)
            page = items[:limit]
            next_cursor = None
            if len(items) > limit and page:
                last = page[-1]
                next_cursor = self._encode_comment_cursor(last["created_at"], last["id"], status)
            return {"items": page, "next_cursor": next_cursor}

    @staticmethod
    def _decode_search_cursor(cursor: str) -> dict[str, Any]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError("search cursor is invalid") from error
        if (
            not isinstance(value, dict)
            or value.get("v") != 1
            or not isinstance(value.get("snapshot"), str)
            or not isinstance(value.get("query"), str)
            or not isinstance(value.get("offset"), int)
            or value["offset"] < 0
        ):
            raise ValidationError("search cursor is invalid")
        return value

    @staticmethod
    def _encode_search_cursor(
        query: str,
        kind: str | None,
        include_archived: bool,
        project: str | None,
        source_agent: str | None,
        tags: list[str],
        snapshot: str,
        offset: int,
    ) -> str:
        payload = {
            "v": 1,
            "query": query,
            "kind": kind,
            "include_archived": include_archived,
            "project": project,
            "source_agent": source_agent,
            "tags": tags,
            "snapshot": snapshot,
            "offset": offset,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def search(
        self,
        query: str,
        *,
        kind: str | None = None,
        include_archived: bool = False,
        comment_status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        tags: list[str] | None = None,
        project: str | None = None,
        source_agent: str | None = None,
    ) -> dict[str, Any]:
        query = _check_text(query, "query", 512).strip()
        tags = [_check_text(tag, "tag", 64) for tag in (tags or [])]
        if project is not None:
            project = _check_text(project, "project", 256)
        if source_agent is not None:
            source_agent = _check_text(source_agent, "source_agent", 128)
        if kind is not None:
            _check_kind(kind)
        if comment_status is not None and comment_status not in {"open", "addressed", "resolved"}:
            raise ValidationError("invalid comment status")
        if not 1 <= limit <= 100:
            raise ValidationError("search limit must be between 1 and 100")
        decoded = self._decode_search_cursor(cursor) if cursor else None
        with self._connect() as connection:
            snapshot = decoded["snapshot"] if decoded else connection.execute(
                "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            ).fetchone()[0]
            if decoded and (
                decoded["query"] != query
                or decoded.get("kind") != kind
                or decoded.get("include_archived") != include_archived
                or decoded.get("project") != project
                or decoded.get("source_agent") != source_agent
                or decoded.get("tags", []) != tags
            ):
                raise ValidationError("search cursor does not match request")
            pattern = f"%{query}%"
            artifact_clauses = ["a.content_updated_at <= ?"]
            artifact_params: list[Any] = [snapshot]
            if not include_archived:
                artifact_clauses.append("a.archived_at IS NULL")
            if kind:
                artifact_clauses.append("a.kind = ?")
                artifact_params.append(kind)
            if project is not None:
                artifact_clauses.append("json_extract(a.metadata_json, '$.project') = ?")
                artifact_params.append(project)
            if source_agent is not None:
                artifact_clauses.append("json_extract(a.metadata_json, '$.source_agent') = ?")
                artifact_params.append(source_agent)
            for tag in tags:
                artifact_clauses.append("EXISTS (SELECT 1 FROM json_each(a.metadata_json, '$.tags') WHERE value = ?)")
                artifact_params.append(tag)
            artifact_clauses.append("(a.title LIKE ? OR a.slug LIKE ? OR v.content LIKE ?)")
            artifact_params.extend([pattern, pattern, pattern])
            artifact_rows = connection.execute(
                f"""SELECT a.id, a.title, a.kind, a.metadata_json, a.content_updated_at,
                           v.id AS version_id, v.sequence AS version_sequence,
                           substr(v.content, 1, 512) AS excerpt,
                           length(v.content) > 512 AS truncated,
                           CASE WHEN a.title LIKE ? THEN 'title'
                                WHEN a.slug LIKE ? THEN 'slug'
                                ELSE 'content' END AS match_field
                    FROM artifacts a
                    JOIN versions v ON v.id = a.current_version_id
                    WHERE {' AND '.join(artifact_clauses)}""",
                [pattern, pattern, *artifact_params],
            ).fetchall()
            comment_clauses = ["c.created_at <= ?", "c.body LIKE ?"]
            comment_params: list[Any] = [snapshot, pattern]
            if comment_status:
                comment_clauses.append("c.status = ?")
                comment_params.append(comment_status)
            if project is not None:
                comment_clauses.append("json_extract(a.metadata_json, '$.project') = ?")
                comment_params.append(project)
            if source_agent is not None:
                comment_clauses.append("json_extract(a.metadata_json, '$.source_agent') = ?")
                comment_params.append(source_agent)
            for tag in tags:
                comment_clauses.append("EXISTS (SELECT 1 FROM json_each(a.metadata_json, '$.tags') WHERE value = ?)")
                comment_params.append(tag)
            comment_rows = connection.execute(
                f"""SELECT c.*, a.title AS artifact_title, a.metadata_json, v.sequence AS version_sequence,
                           substr(c.body, 1, 512) AS body_excerpt,
                           length(c.body) > 512 AS body_truncated
                    FROM comments c
                    JOIN artifacts a ON a.id = c.artifact_id
                    JOIN versions v ON v.id = c.version_id
                    WHERE {' AND '.join(comment_clauses)}""",
                comment_params,
            ).fetchall()
            results: list[dict[str, Any]] = []
            for row in artifact_rows:
                results.append({
                    "type": "artifact",
                    "artifact_id": row["id"],
                    "title": row["title"],
                    "kind": row["kind"],
                    "metadata": _validate_metadata(json.loads(row["metadata_json"])),
                    "version_id": row["version_id"],
                    "version_sequence": row["version_sequence"],
                    "match": row["match_field"],
                    "excerpt": row["excerpt"] or "",
                    "truncated": bool(row["truncated"]),
                    "_sort_time": row["content_updated_at"],
                })
            for row in comment_rows:
                item = dict(row)
                item["type"] = "comment"
                item["comment_id"] = item.pop("id")
                item["body"] = item.pop("body_excerpt") or ""
                item["truncated"] = bool(item.pop("body_truncated"))
                item["metadata"] = _validate_metadata(json.loads(item.pop("metadata_json")))
                item["match"] = "comment"
                item["anchor"] = json.loads(item.pop("anchor_json"))
                item["_sort_time"] = item["created_at"]
                item.pop("updated_at", None)
                item.pop("addressed_in_version_id", None)
                item.pop("artifact_id", None)
                results.append(item)
            results.sort(key=lambda item: (item["_sort_time"], item.get("artifact_id", item.get("comment_id", ""))), reverse=True)
            offset = decoded["offset"] if decoded else 0
            page = results[offset:offset + limit]
            next_cursor = None
            if offset + limit < len(results):
                next_cursor = self._encode_search_cursor(
                    query, kind, include_archived, project, source_agent, tags, snapshot, offset + limit
                )
            for item in page:
                item.pop("_sort_time", None)
            return {"items": page, "next_cursor": next_cursor, "snapshot": snapshot}

    def add_comment_event(
        self,
        comment_id: str,
        status: str,
        actor: str,
        addressed_in_version_id: str | None = None,
        idempotency_key: str | None = None,
        *,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"open", "addressed", "resolved"}:
            raise ValidationError("invalid comment status")
        actor = _check_text(actor, "actor", 256)
        request_hash = self._hash_request({
            "comment_id": comment_id,
            "status": status,
            "actor": actor,
            "addressed_in_version_id": addressed_in_version_id,
            "agent_id": agent_id,
            "agent_run_id": agent_run_id,
        })
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_id = self._idempotent_result(connection, f"comment-event:{comment_id}", idempotency_key, request_hash)
            if existing_id:
                row = connection.execute("SELECT * FROM comment_events WHERE id = ?", (existing_id,)).fetchone()
                if row is None:
                    raise StoreError("idempotency result event is missing")
                return dict(row)
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
            if idempotency_key:
                connection.execute(
                    """INSERT INTO idempotency_keys(scope, key, request_hash, result_id)
                       VALUES (?, ?, ?, ?)""",
                    (f"comment-event:{comment_id}", idempotency_key, request_hash, event_id),
                )
            recorded_operation_id = self._record_operation(
                connection,
                operation_type="comment.status",
                actor=actor,
                agent_id=agent_id,
                agent_run_id=agent_run_id,
                operation_id=operation_id,
                resource_type="comment_event",
                resource_id=event_id,
                request_hash=request_hash,
            )
            event = self._append_event(
                connection,
                event_type="comment.updated",
                artifact_id=comment["artifact_id"],
                version_id=comment["version_id"],
                comment_id=comment_id,
                operation_id=recorded_operation_id,
                payload={"status": status, "addressed_in_version_id": addressed_in_version_id},
            )
            connection.execute(
                f"""UPDATE comments
                    SET status = ?, addressed_in_version_id = ?, updated_at = {_now_sql()}
                    WHERE id = ?""",
                (status, addressed_in_version_id, comment_id),
            )
            row = connection.execute("SELECT * FROM comment_events WHERE id = ?", (event_id,)).fetchone()
            result = dict(row)
            result["operation_id"] = recorded_operation_id
            result["event_id"] = event["event_id"]
            return result

    def list_comment_events(self, comment_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM comments WHERE id = ?", (comment_id,)).fetchone() is None:
                raise NotFoundError(f"comment not found: {comment_id}")
            rows = connection.execute(
                "SELECT * FROM comment_events WHERE comment_id = ? ORDER BY created_at", (comment_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def rename_artifact(
        self,
        artifact_id: str,
        title: str,
        *,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        title = _check_text(title, "title", 512)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            artifact = self._get_artifact_row(connection, artifact_id)
            connection.execute(
                "UPDATE artifacts SET title = ?, slug = ? WHERE id = ?",
                (title, self._slug(title, artifact_id), artifact_id),
            )
            recorded_operation_id = self._record_operation(
                connection,
                operation_type="artifact.rename",
                actor=agent_id or DEFAULT_PRINCIPAL_ID,
                agent_id=agent_id,
                agent_run_id=agent_run_id,
                operation_id=operation_id,
                resource_type="artifact",
                resource_id=artifact_id,
                request_hash=self._hash_request({"artifact_id": artifact_id, "title": title}),
            )
            result = self._artifact_dict(self._get_artifact_row(connection, artifact_id))
            result["operation_id"] = recorded_operation_id
            return result

    def duplicate_artifact(
        self,
        artifact_id: str,
        created_by: str,
        *,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        created_by = _check_text(created_by, "created_by", 256)
        with self._connect() as connection:
            artifact = self._get_artifact_row(connection, artifact_id)
            version = self._get_version_row(connection, artifact["current_version_id"])
            title = f"{artifact['title']} copy"
            kind = artifact["kind"]
            content = version["content"]
            metadata = _metadata_from_row(artifact)
        return self.create_artifact(
            title,
            kind,
            content,
            created_by,
            metadata=metadata,
            agent_id=agent_id,
            agent_run_id=agent_run_id,
            operation_id=operation_id,
            operation_type="artifact.duplicate",
        )

    def restore_version(
        self,
        artifact_id: str,
        version_id: str,
        created_by: str,
        *,
        expected_current_version_id: str,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            version = self._get_version_row(connection, version_id)
            if version["artifact_id"] != artifact_id:
                raise ValidationError("version does not belong to artifact")
        return self.publish_version(
            artifact_id,
            version["content"],
            created_by,
            expected_current_version_id=expected_current_version_id,
            change_summary=f"Restore version {version['sequence']}",
            agent_id=agent_id,
            agent_run_id=agent_run_id,
            operation_id=operation_id,
            operation_type="version.restore",
        )

    def archive_artifact(
        self,
        artifact_id: str,
        *,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._get_artifact_row(connection, artifact_id)
            connection.execute(
                f"UPDATE artifacts SET archived_at = {_now_sql()}, updated_at = {_now_sql()} WHERE id = ?",
                (artifact_id,),
            )
            recorded_operation_id = self._record_operation(
                connection,
                operation_type="artifact.archive",
                actor=agent_id or DEFAULT_PRINCIPAL_ID,
                agent_id=agent_id,
                agent_run_id=agent_run_id,
                operation_id=operation_id,
                resource_type="artifact",
                resource_id=artifact_id,
                request_hash=self._hash_request({"artifact_id": artifact_id}),
            )
            result = self._artifact_dict(self._get_artifact_row(connection, artifact_id))
            result["operation_id"] = recorded_operation_id
            return result
