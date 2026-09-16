from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from .conftest import create_artifact

pytestmark = pytest.mark.e2e


def test_viewed_activity_shows_date_and_time(page, running_server):
    artifact = create_artifact(running_server, "Viewed timestamp", "markdown", "# Viewed")
    page.goto(f"http://127.0.0.1:{running_server.server_port}/#/artifact/{artifact['id']}", wait_until="networkidle")
    page.goto(f"http://127.0.0.1:{running_server.server_port}/", wait_until="networkidle")
    card = page.locator(".artifact-card").filter(has_text=artifact["title"])
    expect(card.locator(".artifact-activity")).to_contain_text("Viewed")
    assert re.search(r"\d{1,2}:\d{2}", card.locator(".artifact-activity").inner_text())


def test_pinned_card_group_uses_full_grid_width(page, running_server):
    pinned_one = create_artifact(running_server, "Pinned one", "markdown", "# One")
    pinned_two = create_artifact(running_server, "Pinned two", "markdown", "# Two")
    regular = create_artifact(running_server, "Regular", "markdown", "# Regular")
    running_server.store.set_pinned(pinned_one["id"], True)
    running_server.store.set_pinned(pinned_two["id"], True)

    page.goto(f"http://127.0.0.1:{running_server.server_port}/", wait_until="networkidle")
    group = page.locator(".pinned-group")
    grid = page.locator("#artifact-list")
    expect(group).to_be_visible()
    group_box = group.bounding_box()
    grid_box = grid.bounding_box()
    assert group_box is not None and grid_box is not None
    assert abs(group_box["width"] - grid_box["width"]) <= 2
    cards = group.locator(".artifact-card")
    assert cards.count() == 2
    widths = [cards.nth(index).bounding_box()["width"] for index in range(cards.count())]
    assert max(widths) - min(widths) <= 2
    expect(page.locator(".artifact-card").filter(has_text=regular["title"])).to_be_visible()
