from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import create_artifact


pytestmark = pytest.mark.e2e


def test_mobile_header_stays_compact(page, running_server):
    create_artifact(running_server, f"Mobile check {uuid.uuid4().hex[:8]}", "markdown", "# Mobile")
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"http://127.0.0.1:{running_server.server_port}/", wait_until="networkidle")
    expect(page.get_by_role("button", name="New")).to_be_visible()
    expect(page.locator("#catalog-search")).to_be_visible()
    assert page.locator("#new-artifact").inner_text().strip() == "New"
    search_box = page.locator(".top-search").bounding_box()
    new_box = page.locator("#new-artifact").bounding_box()
    assert search_box is not None and new_box is not None
    assert abs(search_box["y"] - new_box["y"]) < 4
    assert page.locator(".topbar").evaluate("element => getComputedStyle(element).position") == "relative"
    assert page.locator(".brand-lockup").get_attribute("href") == "/"
    assert page.locator(".workspace-page").count() == 1
