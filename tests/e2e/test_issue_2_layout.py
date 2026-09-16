from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import create_artifact


pytestmark = pytest.mark.e2e


def test_long_rendered_artifact_is_reachable_by_page_scroll(page, running_server):
    artifact = create_artifact(
        running_server,
        "Full HTML page",
        "html",
        "<h1>Complete artifact</h1><div style='height: 1100px'>Bottom content</div>",
    )
    page.goto(f"http://127.0.0.1:{running_server.server_port}/#/artifact/{artifact['id']}", wait_until="networkidle")
    frame = page.locator(".html-frame")
    expect(frame).to_be_visible()
    page.wait_for_timeout(300)
    box = frame.bounding_box()
    assert box is not None
    assert box["height"] >= 1100
    assert page.evaluate("() => document.documentElement.scrollHeight") > page.viewport_size["height"]


def test_mobile_version_menu_is_compact_and_inside_viewport(page, running_server):
    artifact = create_artifact(running_server, "Versioned artifact", "markdown", "# Current")
    first = running_server.store.get_current_version(artifact["id"])
    running_server.store.publish_version(
        artifact["id"], "# Current\n\nSecond", "e2e",
        expected_current_version_id=first["id"], change_summary="Second version",
    )
    for width in (320, 360, 390):
        page.set_viewport_size({"width": width, "height": 844})
        page.goto(f"http://127.0.0.1:{running_server.server_port}/#/artifact/{artifact['id']}", wait_until="networkidle")
        page.locator("#version-switcher").click()
        menu = page.locator("#version-drawer")
        expect(menu).to_be_visible()
        box = menu.bounding_box()
        assert box is not None
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= width
        assert box["height"] < 500
        assert page.locator("#version-drawer").evaluate("element => getComputedStyle(element).position") == "absolute"
        page.keyboard.press("Escape")
        expect(menu).to_be_hidden()
        page.locator("#version-switcher").click()
        page.mouse.click(2, 2)
        expect(menu).to_be_hidden()
