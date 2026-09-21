from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import open_agent_artifacts.cli as cli_module
from open_agent_artifacts.cli import main
from open_agent_artifacts.server import create_server


def request(server, method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_obj = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def start_server(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def stop_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_instance_endpoint_exposes_redacted_identity_and_storage_class(tmp_path):
    server = create_server(
        tmp_path / "instance.db",
        instance_id="test-instance",
        storage_class="persistent",
        public_url="https://artifacts.example.invalid",
    )
    thread = start_server(server)
    try:
        status, payload = request(server, "GET", "/api/instance")
    finally:
        stop_server(server, thread)

    assert status == 200
    assert payload == {
        "instance_id": "test-instance",
        "storage_class": "persistent",
        "public_url_configured": True,
        "version": "0.2.13",
    }
    assert "db" not in json.dumps(payload).lower()
    assert "token" not in json.dumps(payload).lower()


def test_create_response_includes_first_version_content_hash(tmp_path):
    server = create_server(
        tmp_path / "hash.db",
        instance_id="hash-test",
        storage_class="persistent",
    )
    thread = start_server(server)
    try:
        status, payload = request(server, "POST", "/api/artifacts", {
            "title": "Hash report",
            "kind": "text",
            "content": "hash me",
            "created_by": "test",
        })
    finally:
        stop_server(server, thread)

    assert status == 201
    assert payload["content_hash"] == hashlib.sha256(b"hash me").hexdigest()


def test_cli_refuses_write_when_instance_identity_is_missing(tmp_path, capsys):
    server = create_server(tmp_path / "unconfigured.db")
    thread = start_server(server)
    try:
        exit_code = main([
            "--base-url",
            f"http://127.0.0.1:{server.server_port}",
            "create",
            "--title",
            "Must not write",
            "--kind",
            "text",
            "--content",
            "payload",
        ])
        output = capsys.readouterr()
        assert exit_code == 1
        assert "instance" in output.err.lower()
        assert server.store.list_artifacts() == []
    finally:
        stop_server(server, thread)


def test_cli_requires_explicit_override_for_ephemeral_instance(tmp_path, capsys):
    server = create_server(
        tmp_path / "ephemeral.db",
        instance_id="test-ephemeral",
        storage_class="ephemeral",
    )
    thread = start_server(server)
    try:
        base_args = [
            "--base-url",
            f"http://127.0.0.1:{server.server_port}",
            "create",
            "--title",
            "Ephemeral",
            "--kind",
            "text",
            "--content",
            "payload",
        ]
        assert main(base_args) == 1
        assert "ephemeral" in capsys.readouterr().err.lower()
        assert server.store.list_artifacts() == []

        assert main([*base_args, "--allow-ephemeral"]) == 0
        created = json.loads(capsys.readouterr().out)
        assert created["title"] == "Ephemeral"
    finally:
        stop_server(server, thread)


def test_cli_create_verify_reads_back_hash_and_public_url(tmp_path, capsys):
    server = create_server(
        tmp_path / "verify.db",
        instance_id="verify-test",
        storage_class="persistent",
        public_url="https://artifacts.example.invalid",
    )
    thread = start_server(server)
    try:
        assert main([
            "--base-url",
            f"http://127.0.0.1:{server.server_port}",
            "--public-url",
            "https://artifacts.example.invalid",
            "create",
            "--title",
            "Verified",
            "--kind",
            "markdown",
            "--content",
            "# Verified content",
            "--verify",
        ]) == 0
        result = json.loads(capsys.readouterr().out)
    finally:
        stop_server(server, thread)

    assert result["verification"]["verified"] is True
    assert result["verification"]["content_hash_match"] is True
    assert result["verification"]["current_version_match"] is True
    assert result["artifact_url"].endswith(f"/#/artifact/{result['id']}")


def test_create_verify_hashes_readback_content_not_only_stored_hash(monkeypatch, capsys):
    expected_hash = hashlib.sha256(b"original").hexdigest()

    def fake_request(base_url, method, path, payload=None, token=None, *headers):
        if method == "GET" and path == "/api/instance":
            return 200, {"instance_id": "fake", "storage_class": "persistent"}
        if method == "POST" and path == "/api/artifacts":
            return 201, {"id": "artifact-1", "current_version_id": "version-1"}
        if method == "GET" and path == "/api/artifacts/artifact-1":
            return 200, {"id": "artifact-1", "current_version_id": "version-1"}
        if method == "GET" and path == "/api/versions/version-1":
            return 200, {"id": "version-1", "content": "tampered", "content_hash": expected_hash}
        raise AssertionError((method, path))

    monkeypatch.setattr(cli_module, "_request", fake_request)
    exit_code = main([
        "create",
        "--title",
        "Readback",
        "--kind",
        "text",
        "--content",
        "original",
        "--verify",
    ])
    assert exit_code == 1
    assert "verification failed" in capsys.readouterr().err


def test_create_verify_replay_targets_original_version_after_later_publish(tmp_path, capsys):
    server = create_server(tmp_path / "replay.db", instance_id="replay", storage_class="persistent")
    thread = start_server(server)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        first = main([
            "--base-url", base, "create", "--title", "Replay", "--kind", "text", "--content", "original",
            "--verify", "--idempotency-key", "replay-key",
        ])
        first_result = json.loads(capsys.readouterr().out)
        assert first == 0 and first_result["verification"]["verified"] is True
        assert main([
            "--base-url", base, "publish", first_result["id"], "--content", "new",
            "--expected-current-version-id", first_result["current_version_id"],
        ]) == 0
        capsys.readouterr()
        assert main([
            "--base-url", base, "create", "--title", "Replay", "--kind", "text", "--content", "original",
            "--verify", "--idempotency-key", "replay-key",
        ]) == 0
        replay = json.loads(capsys.readouterr().out)
    finally:
        stop_server(server, thread)

    assert replay["verification"]["verified"] is True
    assert replay["verification"]["idempotent_replay"] is True
    assert replay["verification"]["current_version_match"] is False


def test_smoke_does_not_send_api_token_to_public_route(tmp_path, capsys):
    seen: list[str | None] = []

    class PublicHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            if self.path == "/healthz":
                self.send_header("Content-Type", "application/json")
            else:
                self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}' if self.path == "/healthz" else b"<html>ok</html>")

    public = ThreadingHTTPServer(("127.0.0.1", 0), PublicHandler)
    public_thread = threading.Thread(target=public.serve_forever, daemon=True)
    public_thread.start()
    server = create_server(
        tmp_path / "token-route.db",
        api_token="private-token",
        instance_id="token-route",
        storage_class="persistent",
    )
    thread = start_server(server)
    try:
        assert main([
            "--base-url", f"http://127.0.0.1:{server.server_port}",
            "--token", "private-token",
            "--public-url", f"http://127.0.0.1:{public.server_port}",
            "smoke",
        ]) == 0
        report = json.loads(capsys.readouterr().out)
    finally:
        stop_server(server, thread)
        public.shutdown()
        public.server_close()
        public_thread.join(timeout=3)

    assert report["checks"]["route"]["ok"] is True
    assert seen and all(value is None for value in seen)


def test_smoke_recovers_and_cleans_artifact_when_create_response_is_lost(tmp_path, monkeypatch, capsys):
    server = create_server(tmp_path / "lost-response.db", instance_id="lost-response", storage_class="persistent")
    thread = start_server(server)
    original_request = cli_module._request

    def lose_create_response(base_url, method, path, payload=None, token=None, *headers):
        result = original_request(base_url, method, path, payload, token, *headers)
        if method == "POST" and path == "/api/artifacts":
            raise cli_module.CLIError("simulated lost response")
        return result

    monkeypatch.setattr(cli_module, "_request", lose_create_response)
    try:
        assert main(["--base-url", f"http://127.0.0.1:{server.server_port}", "smoke"]) == 1
        report = json.loads(capsys.readouterr().out)
        assert report["checks"]["cleanup"]["ok"] is True
        assert server.store.list_artifacts() == []
    finally:
        stop_server(server, thread)


def test_smoke_reports_unknown_cleanup_when_lost_response_cannot_be_recovered(tmp_path, monkeypatch, capsys):
    server = create_server(tmp_path / "unknown-cleanup.db", instance_id="unknown-cleanup", storage_class="persistent")
    thread = start_server(server)
    original_request = cli_module._request

    def lose_response_and_hide_search(base_url, method, path, payload=None, token=None, *headers):
        if method == "GET" and path.startswith("/api/search?"):
            return 200, {"items": []}
        result = original_request(base_url, method, path, payload, token, *headers)
        if method == "POST" and path == "/api/artifacts":
            raise cli_module.CLIError("simulated lost response")
        return result

    monkeypatch.setattr(cli_module, "_request", lose_response_and_hide_search)
    try:
        assert main(["--base-url", f"http://127.0.0.1:{server.server_port}", "smoke"]) == 1
        report = json.loads(capsys.readouterr().out)
    finally:
        stop_server(server, thread)

    assert report["ok"] is False
    assert report["checks"]["cleanup"] == {
        "ok": False,
        "status": "unknown",
        "reason": "synthetic_artifact_id_not_recovered",
    }


def test_cli_doctor_reports_api_readiness_instance_and_public_url(tmp_path, capsys):
    server = create_server(
        tmp_path / "doctor.db",
        instance_id="doctor-test",
        storage_class="persistent",
        public_url="https://artifacts.example.invalid",
    )
    thread = start_server(server)
    try:
        assert main([
            "--base-url",
            f"http://127.0.0.1:{server.server_port}",
            "--public-url",
            "https://artifacts.example.invalid",
            "doctor",
            "--expect-instance-id",
            "doctor-test",
        ]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["ok"] is True
        assert report["checks"]["api"]["ok"] is True
        assert report["checks"]["readiness"]["ok"] is True
        assert report["checks"]["instance"]["ok"] is True
        assert report["checks"]["public_url"]["ok"] is True
        assert server.store.list_artifacts() == []
    finally:
        stop_server(server, thread)


def test_cli_doctor_fails_closed_for_unconfigured_instance(tmp_path, capsys):
    server = create_server(tmp_path / "doctor-unconfigured.db")
    thread = start_server(server)
    try:
        assert main([
            "--base-url",
            f"http://127.0.0.1:{server.server_port}",
            "doctor",
        ]) == 1
        report = json.loads(capsys.readouterr().out)
    finally:
        stop_server(server, thread)

    assert report["ok"] is False
    assert report["checks"]["instance"]["ok"] is False
    assert report["checks"]["public_url"]["ok"] is False


def test_cli_smoke_creates_verifies_and_cleans_only_synthetic_artifact(tmp_path, capsys):
    server = create_server(
        tmp_path / "smoke.db",
        instance_id="smoke-test",
        storage_class="persistent",
    )
    thread = start_server(server)
    try:
        assert main([
            "--base-url",
            f"http://127.0.0.1:{server.server_port}",
            "--public-url",
            f"http://127.0.0.1:{server.server_port}",
            "smoke",
        ]) == 0
        report = json.loads(capsys.readouterr().out)
    finally:
        stop_server(server, thread)

    assert report["ok"] is True
    assert report["checks"]["api"]["ok"] is True
    assert report["checks"]["cleanup"]["ok"] is True
    assert report["checks"]["route"]["ok"] is True
    assert report["checks"]["route"]["artifact_url"].endswith(f"/#/artifact/{report['artifact_id']}")
    assert report["checks"]["ui"]["status"] == "not_run"
    assert report["artifact_id"]


def test_cli_smoke_requires_ephemeral_override(tmp_path, capsys):
    server = create_server(
        tmp_path / "smoke-ephemeral.db",
        instance_id="smoke-ephemeral",
        storage_class="ephemeral",
    )
    thread = start_server(server)
    try:
        assert main(["--base-url", f"http://127.0.0.1:{server.server_port}", "smoke"]) == 1
        assert "ephemeral" in capsys.readouterr().err.lower()
        assert main([
            "--base-url",
            f"http://127.0.0.1:{server.server_port}",
            "smoke",
            "--allow-ephemeral",
        ]) == 0
        report = json.loads(capsys.readouterr().out)
    finally:
        stop_server(server, thread)

    assert report["ok"] is True
