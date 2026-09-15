from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path


class BackupError(Exception):
    """The backup or restore operation could not be safely completed."""


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _validate_database(path: Path) -> None:
    if not path.is_file():
        raise BackupError(f"database does not exist: {path}")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError(f"database integrity check failed: {path}")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise BackupError(f"database foreign-key check failed: {path}")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        required = {"artifacts", "versions", "comments", "comment_events"}
        if not required.issubset(tables):
            raise BackupError(f"database schema is incomplete: {path}")
    except sqlite3.DatabaseError as error:
        raise BackupError(f"database integrity check failed: {path}") from error
    finally:
        if connection is not None:
            connection.close()


def _temporary_path(destination: Path, label: str) -> Path:
    return destination.with_name(f".{destination.name}.{label}-{uuid.uuid4().hex}.tmp")


def backup_database(source: str | Path, destination: str | Path) -> Path:
    """Create a validated online backup and atomically publish it."""
    source_path = _resolved(source)
    destination_path = _resolved(destination)
    if source_path == destination_path:
        raise BackupError("source and destination must differ")
    _validate_database(source_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(destination_path, "backup")
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(source_path)
        destination_connection = sqlite3.connect(temporary)
        with destination_connection:
            source_connection.backup(destination_connection, pages=64)
        _validate_database(temporary)
        os.replace(temporary, destination_path)
        return destination_path
    except sqlite3.DatabaseError as error:
        raise BackupError(f"backup failed: {source_path}") from error
    finally:
        if source_connection is not None:
            source_connection.close()
        if destination_connection is not None:
            destination_connection.close()
        temporary.unlink(missing_ok=True)


def restore_database(
    backup: str | Path,
    destination: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Restore a validated backup through an atomic temporary database."""
    backup_path = _resolved(backup)
    destination_path = _resolved(destination)
    if backup_path == destination_path:
        raise BackupError("backup and destination must differ")
    _validate_database(backup_path)
    if destination_path.exists() and not overwrite:
        raise BackupError(f"destination already exists: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(destination_path, "restore")
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(backup_path)
        destination_connection = sqlite3.connect(temporary)
        with destination_connection:
            source_connection.backup(destination_connection, pages=64)
        _validate_database(temporary)
        os.replace(temporary, destination_path)
        return destination_path
    except sqlite3.DatabaseError as error:
        raise BackupError(f"restore failed: {backup_path}") from error
    finally:
        if source_connection is not None:
            source_connection.close()
        if destination_connection is not None:
            destination_connection.close()
        temporary.unlink(missing_ok=True)
