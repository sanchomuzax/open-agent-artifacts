from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from open_agent_artifacts.server import create_server


def request(server, method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            raw = response.read()
            if response.headers.get_content_type() == "application/json":
                payload = json.loads(raw) if raw else None
            else:
                payload = raw.decode("utf-8")
            return response.status, response.headers, payload
    except urllib.error.HTTPError as error:
        return error.code, error.headers, json.loads(error.read())


@pytest.fixture
def running_server(tmp_path):
    server = create_server(tmp_path / "catalog.db")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def create(server, title="Report", kind="markdown", content="# Hello\n\nA paragraph."):
    status, _, artifact = request(server, "POST", "/api/artifacts", {
        "title": title, "kind": kind, "content": content, "created_by": "test",
    })
    assert status == 201
    return artifact


def test_catalog_scopes_have_real_backend_semantics(running_server):
    yours = create(running_server, "Yours")
    shared = create(running_server, "Shared")
    status, _, catalog = request(running_server, "GET", "/api/artifacts?scope=all&view=grid")
    assert status == 200
    assert {item["id"] for item in catalog["items"]} == {yours["id"], shared["id"]}
    assert all("preview" in item and "activity" in item for item in catalog["items"])
    status, _, pinned = request(running_server, "GET", "/api/artifacts?scope=pinned&view=list")
    assert status == 200
    assert pinned["items"] == []


def test_pin_does_not_change_content_activity_timestamp(running_server):
    artifact = create(running_server)
    before = artifact["updated_at"]
    status, _, pinned = request(running_server, "POST", f"/api/artifacts/{artifact['id']}/pin", {})
    assert status == 200
    assert pinned["pinned"] is True
    status, _, after = request(running_server, "GET", f"/api/artifacts/{artifact['id']}")
    assert status == 200
    assert after["updated_at"] == before


def test_presentation_is_rendered_and_html_is_isolated(running_server):
    markdown = create(running_server, kind="markdown")
    html = create(running_server, title="HTML", kind="html", content="<h1>Hello</h1><script>throw Error()</script>")
    status, _, md_presentation = request(running_server, "GET", f"/api/versions/{markdown['current_version_id']}/presentation")
    assert status == 200
    assert md_presentation["mode"] == "rendered"
    assert md_presentation["content"] == "# Hello\n\nA paragraph."
    status, _, html_presentation = request(running_server, "GET", f"/api/versions/{html['current_version_id']}/presentation")
    assert status == 200
    assert html_presentation["mode"] == "isolated-html"
    assert "sandbox" in html_presentation["sandbox"]
    assert "srcdoc" in html_presentation["sandbox"]


def test_catalog_cursor_is_snapshot_stable(running_server):
    create(running_server, "One")
    create(running_server, "Two")
    status, _, first = request(running_server, "GET", "/api/artifacts?scope=all&limit=1")
    assert status == 200
    assert first["snapshot"]
    assert first["next_cursor"]
    create(running_server, "Three")
    status, _, second = request(running_server, "GET", f"/api/artifacts?scope=all&limit=1&cursor={first['next_cursor']}")
    assert status == 200
    assert second["snapshot"] == first["snapshot"]
    assert second["items"]


def test_static_home_has_catalog_contract(running_server):
    status, headers, _ = request(running_server, "GET", "/")
    assert status == 200
    assert headers["Content-Security-Policy"]
    html = request(running_server, "GET", "/")[2]
    for marker in ("All", "Pinned", "Yours", "Shared with you", "New artifact", "artifact-catalog"):
        assert marker in html
    assert "Ready for review" not in html
    assert 'class="source-content"' not in html
