from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import create_artifact


pytestmark = pytest.mark.e2e


def test_html_presentation_is_page_integrated_without_inner_scroll(page, running_server):
    artifact = create_artifact(
        running_server,
        "Long HTML layout",
        "html",
        "<h1>Integrated document</h1><div style='height: 900px'>Long page content</div>",
    )
    page.goto(f"http://127.0.0.1:{running_server.server_port}/#/artifact/{artifact['id']}", wait_until="networkidle")
    frame = page.locator(".html-frame")
    expect(frame).to_be_visible()
    expect(frame).to_have_attribute("scrolling", "no")
    expect(frame).to_have_attribute("sandbox", "allow-scripts")
    assert "allow-same-origin" not in (frame.get_attribute("sandbox") or "")
    srcdoc = frame.get_attribute("srcdoc") or ""
    assert "sandbox-bridge.js" in srcdoc
    assert "overflow:hidden" in srcdoc
    page.wait_for_timeout(300)
    height = frame.evaluate("element => parseInt(element.getAttribute('height'), 10)")
    assert height >= 900
