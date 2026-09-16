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
ALLOWED_KINDS = {"text", "markdown", "code", "html", "svg", "mermaid"}
DEFAULT_PRINCIPAL_ID = "principal:local"


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
                    created_at TEXT NOT NULL DEFAULT ({_now_sql()}),
                    PRIMARY KEY(scope, key)
                );
                CREATE TABLE IF NOT EXISTS system_artifacts (
                    system_key TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL UNIQUE REFERENCES artifacts(id)
                );
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
            connection.execute(
                f"""UPDATE artifacts
                       SET owner_principal_id = ?, content_updated_at = {_now_sql()}
                     WHERE id = ?""",
                (DEFAULT_PRINCIPAL_ID, artifact_id),
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
    ) -> dict[str, Any]:
        if scope not in {"all", "pinned", "yours", "shared"}:
            raise ValidationError("scope must be all, pinned, yours, or shared")
        if not 1 <= limit <= 100:
            raise ValidationError("limit must be between 1 and 100")
        query = query or None
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
                    "principal_id": principal_id,
                    "pin_fingerprint": pin_fingerprint,
                    "after": [last["pin_rank"], last["content_updated_at"], last["id"]],
                })
            for item in page:
                item.pop("pin_rank", None)
            return {"items": page, "snapshot": snapshot, "next_cursor": next_cursor}

    def record_visit(self, artifact_id: str, principal_id: str = DEFAULT_PRINCIPAL_ID) -> dict[str, Any]:
        with self._connect() as connection:
            self._get_artifact_row(connection, artifact_id)
            now = connection.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
            connection.execute(
                """INSERT INTO artifact_visits(principal_id, artifact_id, last_viewed_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(principal_id, artifact_id) DO UPDATE SET last_viewed_at = excluded.last_viewed_at""",
                (principal_id, artifact_id, now),
            )
            return {"artifact_id": artifact_id, "principal_id": principal_id, "last_viewed_at": now}

    def set_pinned(self, artifact_id: str, pinned: bool) -> dict[str, Any]:
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
            return self._artifact_dict(self._get_artifact_row(connection, artifact_id))

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
                f"""UPDATE artifacts
                    SET current_version_id = ?, updated_at = {_now_sql()}, content_updated_at = {_now_sql()}
                    WHERE id = ?""",
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
    def _encode_search_cursor(query: str, kind: str | None, include_archived: bool, snapshot: str, offset: int) -> str:
        payload = {
            "v": 1,
            "query": query,
            "kind": kind,
            "include_archived": include_archived,
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
    ) -> dict[str, Any]:
        query = _check_text(query, "query", 512).strip()
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
            artifact_clauses.append("(a.title LIKE ? OR a.slug LIKE ? OR v.content LIKE ?)")
            artifact_params.extend([pattern, pattern, pattern])
            artifact_rows = connection.execute(
                f"""SELECT a.id, a.title, a.kind, a.content_updated_at,
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
            comment_rows = connection.execute(
                f"""SELECT c.*, a.title AS artifact_title, v.sequence AS version_sequence,
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
                next_cursor = self._encode_search_cursor(query, kind, include_archived, snapshot, offset + limit)
            for item in page:
                item.pop("_sort_time", None)
            return {"items": page, "next_cursor": next_cursor, "snapshot": snapshot}

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

    def rename_artifact(self, artifact_id: str, title: str) -> dict[str, Any]:
        title = _check_text(title, "title", 512)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            artifact = self._get_artifact_row(connection, artifact_id)
            connection.execute(
                "UPDATE artifacts SET title = ?, slug = ? WHERE id = ?",
                (title, self._slug(title, artifact_id), artifact_id),
            )
            return self._artifact_dict(self._get_artifact_row(connection, artifact_id))

    def duplicate_artifact(self, artifact_id: str, created_by: str) -> dict[str, Any]:
        created_by = _check_text(created_by, "created_by", 256)
        with self._connect() as connection:
            artifact = self._get_artifact_row(connection, artifact_id)
            version = self._get_version_row(connection, artifact["current_version_id"])
            title = f"{artifact['title']} copy"
            kind = artifact["kind"]
            content = version["content"]
        return self.create_artifact(title, kind, content, created_by)

    def restore_version(
        self,
        artifact_id: str,
        version_id: str,
        created_by: str,
        *,
        expected_current_version_id: str,
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
        )

    def archive_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._get_artifact_row(connection, artifact_id)
            connection.execute(
                f"UPDATE artifacts SET archived_at = {_now_sql()}, updated_at = {_now_sql()} WHERE id = ?",
                (artifact_id,),
            )
            return self._artifact_dict(self._get_artifact_row(connection, artifact_id))
