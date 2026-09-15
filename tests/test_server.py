import json
import threading
import urllib.error
import urllib.request

import pytest

from open_agent_artifacts.server import create_server


def request(server, method, path, payload=None, token=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
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
    assert "https://" not in html
