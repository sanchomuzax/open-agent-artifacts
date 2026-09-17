from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from open_agent_artifacts.server import create_server


def request(server, method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def server(tmp_path):
    value = create_server(tmp_path / "api.db", instance_id="test-mutation-api", storage_class="persistent")
    thread = threading.Thread(target=value.serve_forever, daemon=True)
    thread.start()
    try:
        yield value
    finally:
        value.shutdown()
        value.server_close()
        thread.join(timeout=3)


def test_me_and_title_mutation_endpoints(server):
    status, me = request(server, "GET", "/api/me")
    assert status == 200
    assert me["multi_user"] is False
    assert me["capabilities"]["identity_scopes"] is False
    status, artifact = request(server, "POST", "/api/artifacts", {
        "title": "Report", "kind": "markdown", "content": "# One", "created_by": "test",
    })
    assert status == 201
    status, renamed = request(server, "POST", f"/api/artifacts/{artifact['id']}/rename", {"title": "Renamed"})
    assert status == 200
    assert renamed["title"] == "Renamed"
    status, duplicate = request(server, "POST", f"/api/artifacts/{artifact['id']}/duplicate", {"created_by": "test"})
    assert status == 201
    assert duplicate["title"] == "Renamed copy"
    assert duplicate["id"] != artifact["id"]


def test_restore_endpoint_is_expected_parent_checked(server):
    status, artifact = request(server, "POST", "/api/artifacts", {
        "title": "Report", "kind": "text", "content": "one", "created_by": "test",
    })
    first = artifact["current_version_id"]
    status, second = request(server, "POST", f"/api/artifacts/{artifact['id']}/versions", {
        "content": "two", "created_by": "test", "expected_current_version_id": first,
    })
    assert status == 201
    status, restored = request(server, "POST", f"/api/artifacts/{artifact['id']}/restore", {
        "version_id": first, "created_by": "test", "expected_current_version_id": second["id"],
    })
    assert status == 201
    assert restored["sequence"] == 3
    assert restored["content"] == "one"
    status, _ = request(server, "POST", f"/api/artifacts/{artifact['id']}/restore", {
        "version_id": first, "created_by": "test", "expected_current_version_id": second["id"],
    })
    assert status == 409
