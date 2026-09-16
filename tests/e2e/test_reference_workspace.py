from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import Page, expect

from .conftest import create_artifact


pytestmark = pytest.mark.e2e


def test_reference_workspace_flow(page: Page, running_server):
    suffix = uuid.uuid4().hex[:8]
    md_heading = f"Review brief {suffix}"
    md = create_artifact(
        running_server,
        f"Reference Markdown {suffix}",
        "markdown",
        f"# {md_heading}\n\n**Rendered** paragraph.\n\n- First\n- Second",
    )
    html = create_artifact(
        running_server,
        f"Reference HTML {suffix}",
        "html",
        "<h1>Visual HTML</h1><p>Rendered safely.</p><script>window.bad=true</script>",
    )
    base = f"http://127.0.0.1:{running_server.server_port}"

    page.goto(base + "/", wait_until="networkidle")
    expect(page.locator("#catalog")).to_be_visible()
    expect(page.locator("#workspace")).to_be_hidden()
    expect(page.locator("#grid-view")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_role("button", name="New")).to_be_visible()
    expect(page.locator(".catalog-order-note")).to_have_text("Pinned first · recently updated")
    assert page.locator(".scope-tab[data-scope='all']").count() == 0
    assert page.locator(".scope-tab[data-scope='pinned']").count() == 0
    assert page.locator(".topbar").evaluate("element => getComputedStyle(element).position") == "relative"
    expect(page.locator(".scope-tab[data-scope='yours']")).to_be_hidden()
    expect(page.locator(".scope-tab[data-scope='shared']")).to_be_hidden()
    card = page.locator(".artifact-card").filter(has_text=md["title"])
    expect(card).to_have_count(1)
    expect(card.locator(".mini-source")).to_contain_text(md_heading)
    expect(card.locator("iframe")).to_have_count(0)

    page.locator("#list-view").click()
    expect(page.locator("#list-view")).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".artifact-row").filter(has_text=md["title"])).to_have_count(1)
    page.locator("#grid-view").click()
    page.locator(".artifact-card").filter(has_text=md["title"]).locator(".pin-button").click()
    expect(page.locator(".artifact-card").filter(has_text=md["title"])).to_have_class("artifact-card is-pinned")
    page.locator("#list-view").click()
    expect(page.locator(".artifact-grid.list-view .artifact-card")).to_have_count(0)
    expect(page.locator(".artifact-grid.list-view .pinned-group .group-heading")).to_have_text("Pinned")
    page.locator("#catalog-search").fill(md["title"])
    expect(page.locator(".artifact-row").filter(has_text=md["title"])).to_have_count(1)
    page.locator("#catalog-search").fill("")
    page.locator("#grid-view").click()
    page.locator(".artifact-card").filter(has_text=md["title"]).locator(".artifact-card-open").click()
    expect(page.locator("#catalog")).to_be_hidden()
    expect(page.locator("#workspace")).to_be_visible()
    expect(page.locator("#version-switcher")).to_have_text("Version 1 ▾")
    expect(page.locator(".presentation-title")).to_have_text("Rendered Markdown")
    expect(page.locator(".rendered-markdown h1")).to_have_text(md_heading)

    page.locator(".rendered-markdown h1").select_text()
    expect(page.locator("#inline-composer")).to_be_visible()
    page.locator("#inline-composer textarea").fill("Keep this heading canonical.")
    page.locator("#inline-composer button[type='submit']").click()
    expect(page.locator("#inline-composer")).to_be_hidden()
    expect(page.locator(".comments-panel .comment-card")).to_have_count(1)
    expect(page.locator(".rendered-markdown mark.highlighted-quote")).to_have_count(1)
    page.get_by_role("button", name="Comments 1").click()
    expect(page.locator(".comments-panel .comments-list")).to_be_hidden()
    page.locator("#comments-toggle-panel").click()
    expect(page.locator(".comments-panel .comments-list")).to_be_visible()

    page.locator(".title-menu-trigger").click()
    page.get_by_role("button", name="Version history").click()
    expect(page.locator("#version-drawer")).to_be_visible()
    expect(page.locator("#version-drawer .version-row")).to_have_count(1)
    page.get_by_role("button", name="Close version history").click()
    page.get_by_role("button", name="New version").click()
    expect(page.locator("#version-editor")).to_be_visible()
    page.locator("#version-content").fill(f"# {md_heading}\n\nSecond immutable draft.")
    page.locator("#version-summary").fill("Second draft")
    page.locator("#new-version-form button[type='submit']").click()
    expect(page.locator(".workspace-meta")).to_contain_text("Version 2")
    page.locator(".title-menu-trigger").click()
    page.get_by_role("button", name="Version history").click()
    expect(page.locator("#version-drawer .version-row")).to_have_count(2)
    page.locator("#version-drawer .version-row").nth(1).click()
    expect(page.locator(".historical-badge")).to_have_text("Viewing historical version · read-only")
    page.get_by_role("button", name="Restore this version").click()
    expect(page.locator(".workspace-meta")).to_contain_text("Version 3")
    expect(page.locator(".rendered-markdown h1")).to_have_text(md_heading)
    page.get_by_role("link", name="Artifacts home").click()
    expect(page.locator("#catalog")).to_be_visible()
    expect(page.locator("#workspace")).to_be_hidden()

    page.goto(base + f"#/artifact/{html['id']}", wait_until="networkidle")
    expect(page.locator(".presentation-title")).to_have_text("Rendered HTML")
    frame = page.locator(".html-frame")
    expect(frame).to_be_visible()
    assert frame.get_attribute("sandbox") == "allow-scripts"
    assert "allow-same-origin" not in (frame.get_attribute("sandbox") or "")
    srcdoc = frame.get_attribute("srcdoc") or ""
    assert "window.bad" not in srcdoc
    assert "nonce=\"oaa-bridge-v1\"" in srcdoc
