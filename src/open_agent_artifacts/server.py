from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .store import (
    MAX_COMMENT_BYTES,
    MAX_CONTENT_BYTES,
    ConflictError,
    NotFoundError,
    Store,
    StoreError,
    ValidationError,
)

MAX_REQUEST_BYTES = MAX_CONTENT_BYTES + MAX_COMMENT_BYTES + 4096


class ArtifactHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, handler_class, store: Store, api_token: str | None, static_dir: Path):
        super().__init__(server_address, handler_class)
        self.store = store
        self.api_token = api_token
        self.static_dir = static_dir.resolve()


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _handler_for(store: Store, api_token: str | None):
    class ArtifactHandler(BaseHTTPRequestHandler):
        server: Any

        def log_message(self, format: str, *args: Any) -> None:
            # Operational logging belongs to the service wrapper, not stdout by default.
            return

        def _send_json(self, status: int, payload: Any) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _send_static(self, path: str) -> bool:
            relative = Path(unquote(path.lstrip("/"))) if path != "/" else Path("index.html")
            if relative.is_absolute() or ".." in relative.parts:
                return False
            candidate = (self.server.static_dir / relative).resolve()
            try:
                candidate.relative_to(self.server.static_dir)
            except ValueError:
                return False
            if not candidate.is_file():
                return False
            body = candidate.read_bytes()
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
            return True

        def _error(self, status: int, error: str, message: str) -> None:
            self._send_json(status, {"error": error, "message": message})

        def _authorized(self) -> bool:
            expected = self.server.api_token
            if expected is None:
                return True
            authorization = self.headers.get("Authorization", "")
            if authorization == f"Bearer {expected}":
                return True
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("WWW-Authenticate", "Bearer")
            body = _json_bytes({"error": "unauthorized", "message": "Bearer token required"})
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False

        def _read_json(self) -> dict[str, Any] | None:
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length or "0")
            except ValueError:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_request", "invalid Content-Length")
                return None
            if length < 0 or length > MAX_REQUEST_BYTES:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request_too_large", "request body is too large")
                return None
            try:
                raw = self.rfile.read(length)
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "request body must be valid JSON")
                return None
            if not isinstance(value, dict):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "request body must be a JSON object")
                return None
            return value

        @staticmethod
        def _segments(path: str) -> list[str]:
            return [unquote(part) for part in path.split("/") if part]

        def _handle_store_error(self, error: StoreError) -> None:
            if isinstance(error, NotFoundError):
                self._error(HTTPStatus.NOT_FOUND, "not_found", str(error))
            elif isinstance(error, ConflictError):
                self._error(HTTPStatus.CONFLICT, "conflict", str(error))
            elif isinstance(error, ValidationError):
                self._error(HTTPStatus.BAD_REQUEST, "validation_error", str(error))
            else:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "storage operation failed")

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._send_json(HTTPStatus.OK, {"status": "ok", "version": __version__})
                return
            if path == "/readyz":
                try:
                    self.server.store.initialize()
                except Exception:
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "not_ready", "database is not ready")
                    return
                self._send_json(HTTPStatus.OK, {"status": "ready"})
                return
            if not path.startswith("/api/"):
                if self._send_static(path):
                    return
                if path != "/":
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "resource not found")
                else:
                    self._send_text(HTTPStatus.NOT_FOUND, "Open Agent Artifacts UI is not installed\n")
                return
            if not self._authorized():
                return
            segments = self._segments(path)
            try:
                if segments == ["api", "artifacts"]:
                    query = parse_qs(parsed.query).get("query", [None])[0]
                    include_archived = parse_qs(parsed.query).get("include_archived", ["false"])[0] == "true"
                    self._send_json(HTTPStatus.OK, self.server.store.list_artifacts(query, include_archived))
                elif len(segments) == 3 and segments[1] == "artifacts":
                    self._send_json(HTTPStatus.OK, self.server.store.get_artifact(segments[2]))
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "versions":
                    self._send_json(HTTPStatus.OK, self.server.store.list_versions(segments[2]))
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "comments":
                    status = parse_qs(parsed.query).get("status", [None])[0]
                    self._send_json(HTTPStatus.OK, self.server.store.list_comments(segments[2], status))
                elif len(segments) == 3 and segments[1] == "versions":
                    self._send_json(HTTPStatus.OK, self.server.store.get_version(segments[2]))
                elif len(segments) == 4 and segments[1] == "versions" and segments[3] == "comments":
                    version = self.server.store.get_version(segments[2])
                    status = parse_qs(parsed.query).get("status", [None])[0]
                    comments = self.server.store.list_comments(version["artifact_id"], status)
                    self._send_json(HTTPStatus.OK, [item for item in comments if item["version_id"] == segments[2]])
                elif len(segments) == 3 and segments[1] == "comments":
                    self._send_json(HTTPStatus.OK, self.server.store.get_comment(segments[2]))
                elif len(segments) == 4 and segments[1] == "comments" and segments[3] == "events":
                    self._send_json(HTTPStatus.OK, self.server.store.list_comment_events(segments[2]))
                else:
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "API resource not found")
            except StoreError as error:
                self._handle_store_error(error)

        def do_POST(self) -> None:
            if not self._authorized():
                return
            path = urlsplit(self.path).path
            segments = self._segments(path)
            payload = self._read_json()
            if payload is None:
                return
            try:
                if segments == ["api", "artifacts"]:
                    result = self.server.store.create_artifact(
                        payload["title"], payload["kind"], payload["content"],
                        payload.get("created_by", "api"), self.headers.get("Idempotency-Key")
                    )
                    self._send_json(HTTPStatus.CREATED, result)
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "versions":
                    result = self.server.store.publish_version(
                        segments[2], payload.get("content"), payload.get("created_by", "api"),
                        expected_current_version_id=payload["expected_current_version_id"],
                        change_summary=payload.get("change_summary", ""),
                        old_str=payload.get("old_str"), new_str=payload.get("new_str"),
                        idempotency_key=self.headers.get("Idempotency-Key"),
                    )
                    self._send_json(HTTPStatus.CREATED, result)
                elif len(segments) == 4 and segments[1] == "versions" and segments[3] == "comments":
                    result = self.server.store.create_comment(
                        payload["artifact_id"], segments[2], payload["body"], payload["anchor"]
                    )
                    self._send_json(HTTPStatus.CREATED, result)
                elif len(segments) == 4 and segments[1] == "comments" and segments[3] == "events":
                    result = self.server.store.add_comment_event(
                        segments[2], payload["status"], payload.get("actor", "api"),
                        payload.get("addressed_in_version_id"),
                    )
                    self._send_json(HTTPStatus.CREATED, result)
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] in {"pin", "unpin"}:
                    self._send_json(
                        HTTPStatus.OK,
                        self.server.store.set_pinned(segments[2], segments[3] == "pin"),
                    )
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "archive":
                    self._send_json(HTTPStatus.OK, self.server.store.archive_artifact(segments[2]))
                else:
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "API resource not found")
            except StoreError as error:
                self._handle_store_error(error)
            except (KeyError, TypeError):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_request", "request fields are invalid")

    return ArtifactHandler


def create_server(
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 0,
    api_token: str | None = None,
    static_dir: str | Path | None = None,
) -> ArtifactHTTPServer:
    store = Store(db_path)
    if static_dir is None:
        working_directory_web = Path.cwd() / "web"
        static_dir = working_directory_web if working_directory_web.is_dir() else Path(__file__).resolve().parents[2] / "web"
    return ArtifactHTTPServer((host, port), _handler_for(store, api_token), store, api_token, Path(static_dir))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Open Agent Artifacts API")
    parser.add_argument("--db", default=os.environ.get("OAA_DB", "~/.local/share/open-agent-artifacts/artifacts.db"))
    parser.add_argument("--host", default=os.environ.get("OAA_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OAA_PORT", "8765")))
    parser.add_argument("--api-token", default=os.environ.get("OAA_API_TOKEN"))
    parser.add_argument("--static-dir", default=os.environ.get("OAA_STATIC_DIR"))
    args = parser.parse_args()
    server = create_server(Path(args.db).expanduser(), args.host, args.port, args.api_token, args.static_dir)
    print(f"Open Agent Artifacts listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
