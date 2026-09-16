from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import Request as URLRequest, urlopen

from . import __version__
from .presentation import make_presentation
from .store import (
    DEFAULT_PRINCIPAL_ID,
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

    def __init__(self, server_address, handler_class, store: Store, api_token: str | None, static_dir: Path, allowed_hosts: set[str], webhook_url: str | None = None, webhook_secret: str | None = None, authenticated_agent_id: str | None = None):
        super().__init__(server_address, handler_class)
        self.store = store
        self.api_token = api_token
        self.static_dir = static_dir.resolve()
        self.allowed_hosts = allowed_hosts
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret
        self.authenticated_agent_id = authenticated_agent_id

    def dispatch_event(self, event_id: str | None) -> None:
        if not event_id or not self.webhook_url or not self.webhook_secret:
            return
        parsed = urlsplit(self.webhook_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            self.store.record_event_delivery(event_id, 1, "failed", error="webhook URL must use http or https")
            return
        body = _json_bytes({"event": self.store.get_event(event_id)})
        signature = hmac.new(self.webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        for attempt in range(1, 4):
            request = URLRequest(
                self.webhook_url,
                data=body,
                method="POST",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-OAA-Event-ID": event_id,
                    "X-OAA-Delivery-Attempt": str(attempt),
                    "X-OAA-Signature": f"sha256={signature}",
                },
            )
            try:
                with urlopen(request, timeout=2) as response:
                    status = response.status
                self.store.record_event_delivery(event_id, attempt, "delivered", response_code=status)
                if 200 <= status < 300:
                    return
                error = f"webhook returned HTTP {status}"
            except Exception as exc:
                status = getattr(exc, "code", None)
                error = f"webhook delivery failed: {type(exc).__name__}"
            self.store.record_event_delivery(event_id, attempt, "failed", response_code=status, error=error)
            if attempt < 3:
                time.sleep(0.05 * (2 ** (attempt - 1)))


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
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
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
            supplied = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
            if supplied and hmac.compare_digest(supplied, expected):
                configured_agent = self.server.authenticated_agent_id
                claimed_agent = self.headers.get("X-OAA-Agent-ID")
                if configured_agent and claimed_agent and claimed_agent != configured_agent:
                    self._error(HTTPStatus.FORBIDDEN, "agent_impersonation", "agent identity does not match the authenticated principal")
                    return False
                return True
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("WWW-Authenticate", "Bearer")
            body = _json_bytes({"error": "unauthorized", "message": "Bearer token required"})
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False

        def _request_boundary_ok(self, state_changing: bool = False) -> bool:
            authority = self.headers.get("Host", "")
            forwarded_proto = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
            request_scheme = forwarded_proto if forwarded_proto in {"http", "https"} else "http"
            try:
                request_url = urlsplit(f"//{authority}")
                host = (request_url.hostname or "").rstrip(".").lower()
                request_port = request_url.port
                if request_port is None:
                    request_port = 443 if request_scheme == "https" else 80
            except ValueError:
                host = ""
                request_port = -1
            if host not in self.server.allowed_hosts:
                self._error(HTTPStatus.MISDIRECTED_REQUEST, "invalid_host", "request host is not allowed")
                return False
            if state_changing:
                if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
                    self._error(HTTPStatus.FORBIDDEN, "cross_origin_request", "cross-site request rejected")
                    return False
                origin = self.headers.get("Origin")
                if origin:
                    try:
                        parsed = urlsplit(origin)
                        origin_host = (parsed.hostname or "").rstrip(".").lower()
                        origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
                    except ValueError:
                        parsed = urlsplit("")
                        origin_host = ""
                        origin_port = -1
                    if (parsed.scheme, origin_host, origin_port) != (request_scheme, host, request_port):
                        self._error(HTTPStatus.FORBIDDEN, "cross_origin_request", "foreign origin rejected")
                        return False
            return True

        def _operation_metadata(self) -> dict[str, str | None]:
            return {
                "agent_id": self.headers.get("X-OAA-Agent-ID") or self.server.authenticated_agent_id,
                "agent_run_id": self.headers.get("X-OAA-Agent-Run-ID"),
                "operation_id": self.headers.get("X-OAA-Operation-ID"),
            }

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
            if not self._request_boundary_ok():
                return
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
                params = parse_qs(parsed.query)
                tags = [tag for value in params.get("tag", []) + params.get("tags", []) for tag in value.split(",") if tag]
                if segments == ["api", "me"]:
                    principal = self.server.store.get_principal(DEFAULT_PRINCIPAL_ID)
                    self._send_json(HTTPStatus.OK, {
                        "principal": principal,
                        "multi_user": False,
                        "capabilities": {"identity_scopes": False, "sharing": False},
                    })
                elif segments == ["api", "comments"]:
                    status = params.get("status", ["open"])[0]
                    if status == "all":
                        status = None
                    self._send_json(HTTPStatus.OK, self.server.store.list_comments_global(
                        status=status,
                        limit=int(params.get("limit", ["50"])[0]),
                        cursor=params.get("cursor", [None])[0],
                    ))
                elif segments == ["api", "search"]:
                    try:
                        limit = int(params.get("limit", ["50"])[0])
                    except ValueError:
                        self._error(HTTPStatus.BAD_REQUEST, "invalid_request", "limit must be an integer")
                        return
                    status = params.get("comment_status", [None])[0]
                    self._send_json(HTTPStatus.OK, self.server.store.search(
                        params.get("q", [""])[0],
                        kind=params.get("kind", [None])[0],
                        include_archived=params.get("include_archived", ["false"])[0] == "true",
                        comment_status=status,
                        limit=limit,
                        cursor=params.get("cursor", [None])[0],
                        tags=tags,
                        project=params.get("project", [None])[0],
                        source_agent=params.get("source_agent", [None])[0],
                    ))
                elif segments == ["api", "operations"]:
                    self._send_json(HTTPStatus.OK, self.server.store.list_operations(
                        resource_id=params.get("resource_id", [None])[0],
                        agent_id=params.get("agent_id", [None])[0],
                        limit=int(params.get("limit", ["100"])[0]),
                    ))
                elif segments == ["api", "events"]:
                    self._send_json(HTTPStatus.OK, self.server.store.list_events(
                        cursor=params.get("cursor", [None])[0],
                        limit=int(params.get("limit", ["50"])[0]),
                    ))
                elif len(segments) == 4 and segments[1] == "events" and segments[3] == "deliveries":
                    self._send_json(HTTPStatus.OK, self.server.store.list_event_deliveries(segments[2]))
                elif segments == ["api", "artifacts"] and any(key in params for key in ("scope", "limit", "cursor", "view")):
                    query = params.get("q", params.get("query", [None]))[0]
                    scope = params.get("scope", ["all"])[0]
                    try:
                        limit = int(params.get("limit", ["50"])[0])
                    except ValueError:
                        self._error(HTTPStatus.BAD_REQUEST, "invalid_request", "limit must be an integer")
                        return
                    cursor = params.get("cursor", [None])[0]
                    self._send_json(HTTPStatus.OK, self.server.store.list_catalog(
                        scope=scope,
                        query=query,
                        limit=limit,
                        cursor=cursor,
                        tags=tags,
                        project=params.get("project", [None])[0],
                        source_agent=params.get("source_agent", [None])[0],
                        kind=params.get("kind", [None])[0],
                    ))
                elif segments == ["api", "artifacts"]:
                    query = params.get("query", [None])[0]
                    include_archived = parse_qs(parsed.query).get("include_archived", ["false"])[0] == "true"
                    self._send_json(HTTPStatus.OK, self.server.store.list_artifacts(
                        query,
                        include_archived,
                        tags=tags,
                        project=params.get("project", [None])[0],
                        source_agent=params.get("source_agent", [None])[0],
                        kind=params.get("kind", [None])[0],
                    ))
                elif len(segments) == 3 and segments[1] == "artifacts":
                    self._send_json(HTTPStatus.OK, self.server.store.get_artifact(segments[2]))
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "versions":
                    self._send_json(HTTPStatus.OK, self.server.store.list_versions(segments[2]))
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "comments":
                    status = parse_qs(parsed.query).get("status", [None])[0]
                    self._send_json(HTTPStatus.OK, self.server.store.list_comments(segments[2], status))
                elif len(segments) == 5 and segments[1] == "artifacts" and segments[3] == "metadata" and segments[4] == "events":
                    self._send_json(HTTPStatus.OK, self.server.store.list_metadata_events(segments[2]))
                elif len(segments) == 3 and segments[1] == "versions":
                    self._send_json(HTTPStatus.OK, self.server.store.get_version(segments[2]))
                elif len(segments) == 4 and segments[1] == "versions" and segments[3] == "presentation":
                    version = self.server.store.get_version(segments[2])
                    artifact = self.server.store.get_artifact(version["artifact_id"])
                    self._send_json(HTTPStatus.OK, make_presentation(artifact["kind"], version["content"], version["id"]))
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
            if not self._request_boundary_ok(True):
                return
            if not self._authorized():
                return
            if self.headers.get_content_type() != "application/json":
                self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type", "Content-Type must be application/json")
                return
            path = urlsplit(self.path).path
            segments = self._segments(path)
            payload = self._read_json()
            if payload is None:
                return
            try:
                if segments == ["api", "artifacts"]:
                    operation = self._operation_metadata()
                    result = self.server.store.create_artifact(
                        payload["title"], payload["kind"], payload["content"],
                        payload.get("created_by", "api"), self.headers.get("Idempotency-Key"),
                        agent_id=operation["agent_id"], agent_run_id=operation["agent_run_id"], operation_id=operation["operation_id"],
                        metadata=payload.get("metadata"),
                    )
                    self.server.dispatch_event(result.get("event_id"))
                    self._send_json(HTTPStatus.CREATED, result)
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "versions":
                    operation = self._operation_metadata()
                    result = self.server.store.publish_version(
                        segments[2], payload.get("content"), payload.get("created_by", "api"),
                        expected_current_version_id=payload["expected_current_version_id"],
                        change_summary=payload.get("change_summary", ""),
                        old_str=payload.get("old_str"), new_str=payload.get("new_str"),
                        idempotency_key=self.headers.get("Idempotency-Key"),
                        agent_id=operation["agent_id"], agent_run_id=operation["agent_run_id"], operation_id=operation["operation_id"],
                        metadata=payload.get("metadata"),
                        source_comment_id=payload.get("source_comment_id"),
                    )
                    self.server.dispatch_event(result.get("event_id"))
                    self._send_json(HTTPStatus.CREATED, result)
                elif len(segments) == 4 and segments[1] == "versions" and segments[3] == "comments":
                    operation = self._operation_metadata()
                    result = self.server.store.create_comment(
                        payload["artifact_id"], segments[2], payload["body"], payload["anchor"],
                        self.headers.get("Idempotency-Key"),
                        agent_id=operation["agent_id"], agent_run_id=operation["agent_run_id"], operation_id=operation["operation_id"],
                    )
                    self.server.dispatch_event(result.get("event_id"))
                    self._send_json(HTTPStatus.CREATED, result)
                elif len(segments) == 4 and segments[1] == "comments" and segments[3] == "events":
                    operation = self._operation_metadata()
                    result = self.server.store.add_comment_event(
                        segments[2], payload["status"], payload.get("actor", "api"),
                        payload.get("addressed_in_version_id"), self.headers.get("Idempotency-Key"),
                        agent_id=operation["agent_id"], agent_run_id=operation["agent_run_id"], operation_id=operation["operation_id"],
                    )
                    self.server.dispatch_event(result.get("event_id"))
                    self._send_json(HTTPStatus.CREATED, result)
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "metadata":
                    operation = self._operation_metadata()
                    result = self.server.store.set_metadata(
                        segments[2],
                        payload["metadata"],
                        payload.get("actor", "api"),
                        agent_id=operation["agent_id"],
                        agent_run_id=operation["agent_run_id"],
                        operation_id=operation["operation_id"],
                    )
                    self.server.dispatch_event(result.get("event_id"))
                    self._send_json(HTTPStatus.OK, result)
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] in {"pin", "unpin"}:
                    operation = self._operation_metadata()
                    self._send_json(
                        HTTPStatus.OK,
                        self.server.store.set_pinned(
                            segments[2], segments[3] == "pin",
                            agent_id=operation["agent_id"], agent_run_id=operation["agent_run_id"], operation_id=operation["operation_id"],
                        ),
                    )
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "archive":
                    operation = self._operation_metadata()
                    self._send_json(HTTPStatus.OK, self.server.store.archive_artifact(
                        segments[2], agent_id=operation["agent_id"], agent_run_id=operation["agent_run_id"], operation_id=operation["operation_id"],
                    ))
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "visit":
                    operation = self._operation_metadata()
                    self._send_json(HTTPStatus.OK, self.server.store.record_visit(
                        segments[2], agent_id=operation["agent_id"], agent_run_id=operation["agent_run_id"], operation_id=operation["operation_id"],
                    ))
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "rename":
                    operation = self._operation_metadata()
                    self._send_json(HTTPStatus.OK, self.server.store.rename_artifact(
                        segments[2], payload["title"], agent_id=operation["agent_id"], agent_run_id=operation["agent_run_id"], operation_id=operation["operation_id"],
                    ))
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "duplicate":
                    operation = self._operation_metadata()
                    self._send_json(HTTPStatus.CREATED, self.server.store.duplicate_artifact(
                        segments[2], payload.get("created_by", "api"), agent_id=operation["agent_id"], agent_run_id=operation["agent_run_id"], operation_id=operation["operation_id"],
                    ))
                elif len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "restore":
                    operation = self._operation_metadata()
                    self._send_json(HTTPStatus.CREATED, self.server.store.restore_version(
                        segments[2], payload["version_id"], payload.get("created_by", "api"),
                        expected_current_version_id=payload["expected_current_version_id"],
                        agent_id=operation["agent_id"], agent_run_id=operation["agent_run_id"], operation_id=operation["operation_id"],
                    ))
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
    allowed_hosts: list[str] | None = None,
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
    authenticated_agent_id: str | None = None,
) -> ArtifactHTTPServer:
    if webhook_url is None:
        webhook_url = os.environ.get("OAA_WEBHOOK_URL")
    if webhook_secret is None:
        webhook_secret = os.environ.get("OAA_WEBHOOK_SECRET")
    if authenticated_agent_id is None:
        authenticated_agent_id = os.environ.get("OAA_AGENT_ID")
    store = Store(db_path)
    if static_dir is None:
        working_directory_web = Path.cwd() / "web"
        static_dir = working_directory_web if working_directory_web.is_dir() else Path(__file__).resolve().parents[2] / "web"
    normalized_hosts = {item.lower() for item in (allowed_hosts or [host, "127.0.0.1", "localhost", "::1"])}
    return ArtifactHTTPServer((host, port), _handler_for(store, api_token), store, api_token, Path(static_dir), normalized_hosts, webhook_url, webhook_secret, authenticated_agent_id)


def sync_project_description(store: Store, static_dir: str | Path) -> dict[str, Any] | None:
    """Keep the rendered project page represented as a canonical artifact."""
    description_path = Path(static_dir) / "project-description.html"
    if not description_path.is_file():
        return None
    return store.ensure_project_description(
        "Open Agent Artifacts — Project description",
        description_path.read_text(encoding="utf-8"),
        "release",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Open Agent Artifacts API")
    parser.add_argument("--db", default=os.environ.get("OAA_DB", "~/.local/share/open-agent-artifacts/artifacts.db"))
    parser.add_argument("--host", default=os.environ.get("OAA_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OAA_PORT", "8765")))
    parser.add_argument("--api-token", default=os.environ.get("OAA_API_TOKEN"))
    parser.add_argument("--static-dir", default=os.environ.get("OAA_STATIC_DIR"))
    parser.add_argument("--allowed-host", action="append")
    args = parser.parse_args()
    env_allowed_hosts = [
        item.strip() for item in os.environ.get("OAA_ALLOWED_HOSTS", "").split(",") if item.strip()
    ]
    allowed_hosts = args.allowed_host or env_allowed_hosts or None
    server = create_server(
        Path(args.db).expanduser(), args.host, args.port, args.api_token, args.static_dir, allowed_hosts
    )
    sync_project_description(server.store, server.static_dir)
    print(f"Open Agent Artifacts listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
