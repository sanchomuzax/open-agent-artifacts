import json
import hashlib
import hmac
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import open_agent_artifacts.server as server_module
from open_agent_artifacts.server import create_server


def request(server, method, path, payload=None, token=None, headers=None, raw_body=None):
    body = raw_body if raw_body is not None else (None if payload is None else json.dumps(payload).encode("utf-8"))
    headers = {"Content-Type": "application/json", **(headers or {})}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request_obj = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def running_server(tmp_path):
    server = create_server(tmp_path / "api.db")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_create_publish_and_comment_flow(running_server):
    status, artifact = request(
        running_server,
        "POST",
        "/api/artifacts",
        {"title": "API report", "kind": "markdown", "content": "# First", "created_by": "test"},
    )
    assert status == 201
    assert artifact["title"] == "API report"
    artifact_id = artifact["id"]
    first_version = artifact["current_version_id"]

    status, version = request(
        running_server,
        "POST",
        f"/api/artifacts/{artifact_id}/versions",
        {
            "content": "# Second",
            "created_by": "test",
            "expected_current_version_id": first_version,
            "change_summary": "Second draft",
        },
    )
    assert status == 201
    assert version["sequence"] == 2

    status, comment = request(
        running_server,
        "POST",
        f"/api/versions/{version['id']}/comments",
        {
            "artifact_id": artifact_id,
            "body": "Please expand this section.",
            "anchor": {"kind": "text", "exact": "Second"},
        },
    )
    assert status == 201
    assert comment["version_id"] == version["id"]

    status, comments = request(running_server, "GET", f"/api/artifacts/{artifact_id}/comments")
    assert status == 200
    assert [item["id"] for item in comments] == [comment["id"]]


def test_optional_webhook_is_signed_retried_and_recorded(tmp_path):
    state = {"attempts": 0, "body": b"", "signature": ""}

    class HookHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):
            state["attempts"] += 1
            state["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            state["signature"] = self.headers.get("X-OAA-Signature", "")
            self.send_response(500 if state["attempts"] < 3 else 204)
            self.end_headers()

    hook = ThreadingHTTPServer(("127.0.0.1", 0), HookHandler)
    hook_thread = threading.Thread(target=hook.serve_forever, daemon=True)
    hook_thread.start()
    app = create_server(
        tmp_path / "api.db",
        webhook_url=f"http://127.0.0.1:{hook.server_port}/events",
        webhook_secret="test-webhook-secret",
    )
    app_thread = threading.Thread(target=app.serve_forever, daemon=True)
    app_thread.start()
    try:
        status, artifact = request(
            app,
            "POST",
            "/api/artifacts",
            {"title": "Webhook", "kind": "text", "content": "payload", "created_by": "test"},
        )
        assert status == 201
        assert state["attempts"] == 3
        expected = hmac.new(b"test-webhook-secret", state["body"], hashlib.sha256).hexdigest()
        assert state["signature"] == f"sha256={expected}"
        deliveries = app.store.list_event_deliveries(artifact["event_id"])
        assert [item["status"] for item in deliveries] == ["failed", "failed", "delivered"]
    finally:
        app.shutdown()
        app.server_close()
        app_thread.join(timeout=3)
        hook.shutdown()
        hook.server_close()
        hook_thread.join(timeout=3)


def test_stale_publish_returns_conflict_without_new_version(running_server):
    status, artifact = request(
        running_server,
        "POST",
        "/api/artifacts",
        {"title": "Conflict report", "kind": "text", "content": "old", "created_by": "test"},
    )
    assert status == 201
    artifact_id = artifact["id"]
    first_version = artifact["current_version_id"]
    status, _ = request(
        running_server,
        "POST",
        f"/api/artifacts/{artifact_id}/versions",
        {"content": "new", "created_by": "test", "expected_current_version_id": first_version},
    )
    assert status == 201
    status, error = request(
        running_server,
        "POST",
        f"/api/artifacts/{artifact_id}/versions",
        {"content": "stale", "created_by": "test", "expected_current_version_id": first_version},
    )
    assert status == 409
    assert error["error"] == "conflict"


def test_configured_token_protects_api(running_server):
    running_server.api_token = "test-token"

    status, _ = request(running_server, "GET", "/api/artifacts")
    assert status == 401
    status, _ = request(running_server, "GET", "/api/artifacts", token="test-token")
    assert status == 200

    status, health = request(running_server, "GET", "/healthz")
    assert status == 200
    assert health["status"] == "ok"


def test_authenticated_agent_id_cannot_be_impersonated(tmp_path):
    server = create_server(tmp_path / "identity.db", api_token="test-token", authenticated_agent_id="agent-a")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _ = request(
            server,
            "POST",
            "/api/artifacts",
            {"title": "Identity", "kind": "text", "content": "safe", "created_by": "agent-a"},
            token="test-token",
            headers={"X-OAA-Agent-ID": "agent-b"},
        )
        assert status == 403
        status, artifact = request(
            server,
            "POST",
            "/api/artifacts",
            {"title": "Identity", "kind": "text", "content": "safe", "created_by": "agent-a"},
            token="test-token",
            headers={"X-OAA-Agent-ID": "agent-a"},
        )
        assert status == 201
        operations = server.store.list_operations(resource_id=artifact["id"])
        assert operations[0]["agent_id"] == "agent-a"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_pin_endpoint_round_trip(running_server):
    status, artifact = request(
        running_server,
        "POST",
        "/api/artifacts",
        {"title": "Pinned report", "kind": "text", "content": "content", "created_by": "test"},
    )
    assert status == 201

    status, pinned = request(running_server, "POST", f"/api/artifacts/{artifact['id']}/pin", {})
    assert status == 200
    assert pinned["pinned"] is True

    status, listed = request(running_server, "GET", "/api/artifacts")
    assert status == 200
    assert listed[0]["pinned"] is True

    status, unpinned = request(running_server, "POST", f"/api/artifacts/{artifact['id']}/unpin", {})
    assert status == 200
    assert unpinned["pinned"] is False


def test_static_catalog_shell_is_served_without_external_scripts(running_server):
    request_obj = urllib.request.Request(
        f"http://127.0.0.1:{running_server.server_port}/",
        method="GET",
    )
    with urllib.request.urlopen(request_obj, timeout=3) as response:
        html = response.read().decode("utf-8")

    assert response.status == 200
    assert "Open Agent Artifacts" in html
    assert 'src="/app.js"' in html
    assert 'href="/"' in html
    assert 'id="cards-view"' in html
    assert 'id="new-version-form"' in html
    assert 'id="comments-toggle"' in html
    assert "highlighted-quote" in html
    assert "https://" not in html


def test_post_rejects_non_json_content_type(running_server):
    status, error = request(
        running_server,
        "POST",
        "/api/artifacts",
        raw_body=b'{}',
        headers={"Content-Type": "text/plain"},
    )
    assert status == 415
    assert error["error"] == "unsupported_media_type"


def test_post_rejects_foreign_origin(running_server):
    status, error = request(
        running_server,
        "POST",
        "/api/artifacts",
        {"title": "Cross-site", "kind": "text", "content": "x"},
        headers={"Origin": "https://evil.example"},
    )
    assert status == 403
    assert error["error"] == "cross_origin_request"


@pytest.mark.parametrize("origin", ["http://127.0.0.1:9", "https://127.0.0.1"])
def test_post_rejects_origin_with_different_port_or_scheme(running_server, origin):
    status, error = request(
        running_server,
        "POST",
        "/api/artifacts",
        {"title": "Cross-site", "kind": "text", "content": "x"},
        headers={"Origin": origin},
    )
    assert status == 403
    assert error["error"] == "cross_origin_request"


def test_bracketed_ipv6_host_is_accepted(running_server):
    status, health = request(running_server, "GET", "/healthz", headers={"Host": f"[::1]:{running_server.server_port}"})
    assert status == 200
    assert health["status"] == "ok"


def test_non_integer_catalog_limit_returns_json_400(running_server):
    status, error = request(running_server, "GET", "/api/artifacts?scope=all&limit=abc")
    assert status == 400
    assert error["error"] == "invalid_request"


def test_https_reverse_proxy_origin_is_accepted(tmp_path):
    server = create_server(tmp_path / "proxy.db", allowed_hosts=["public.example"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, artifact = request(
            server,
            "POST",
            "/api/artifacts",
            {"title": "Proxy", "kind": "text", "content": "ok", "created_by": "test"},
            headers={
                "Host": "public.example",
                "Origin": "https://public.example",
                "X-Forwarded-Proto": "https",
            },
        )
        assert status == 201
        assert artifact["title"] == "Proxy"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_main_passes_allowed_hosts_from_environment(monkeypatch, tmp_path):
    captured = {}

    class FakeServer:
        store = object()
        static_dir = tmp_path

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    def fake_create_server(*args):
        captured["args"] = args
        return FakeServer()

    monkeypatch.setenv("OAA_ALLOWED_HOSTS", "localhost,public.example")
    monkeypatch.setattr(server_module, "create_server", fake_create_server)
    monkeypatch.setattr(server_module, "sync_project_description", lambda *_: None)
    monkeypatch.setattr(sys, "argv", ["oaa-server", "--db", str(tmp_path / "main.db")])
    server_module.main()
    assert captured["args"][-1] == ["localhost", "public.example"]
