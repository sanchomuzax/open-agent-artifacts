from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .conftest import create_artifact


pytestmark = pytest.mark.e2e


MARKDOWN = """# Contrast fixture

| Content | Value |
| --- | --- |
| code | `systemctl restart api` |
| bold | **important** |
| link | [documentation](https://example.com) |
"""


CONTRAST_SCRIPT = """element => {
  function parseColor(value) {
    if (!value || value === 'transparent') return [0, 0, 0, 0];
    const match = value.match(/rgba?\\(([^)]+)\\)/);
    if (!match) return [0, 0, 0, 0];
    const parts = match[1].split(',').map(part => Number.parseFloat(part.trim()));
    return [parts[0], parts[1], parts[2], Number.isFinite(parts[3]) ? parts[3] : 1];
  }
  function composite(foreground, background) {
    const alpha = foreground[3] + background[3] * (1 - foreground[3]);
    if (!alpha) return [0, 0, 0, 0];
    return [
      (foreground[0] * foreground[3] + background[0] * background[3] * (1 - foreground[3])) / alpha,
      (foreground[1] * foreground[3] + background[1] * background[3] * (1 - foreground[3])) / alpha,
      (foreground[2] * foreground[3] + background[2] * background[3] * (1 - foreground[3])) / alpha,
      alpha,
    ];
  }
  function luminance(rgb) {
    return rgb.slice(0, 3).map(value => {
      const channel = value / 255;
      return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  }
  const backgrounds = [];
  for (let node = element; node; node = node.parentElement) backgrounds.push(node);
  let background = [0, 0, 0, 0];
  for (let index = backgrounds.length - 1; index >= 0; index -= 1) {
    background = composite(parseColor(getComputedStyle(backgrounds[index]).backgroundColor), background);
  }
  const foreground = parseColor(getComputedStyle(element).color);
  const fg = composite(foreground, background);
  const light = Math.max(luminance(fg), luminance(background));
  const dark = Math.min(luminance(fg), luminance(background));
  return (light + 0.05) / (dark + 0.05);
}"""


def test_markdown_table_inline_content_meets_wcag_contrast_in_both_schemes(
    page: Page, running_server
):
    artifact = create_artifact(running_server, "Issue 23 Contrast", "markdown", MARKDOWN)
    for width in (390, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(
            f"http://127.0.0.1:{running_server.server_port}/#/artifact/{artifact['id']}",
            wait_until="networkidle",
        )
        for color_scheme in ("dark", "light"):
            page.emulate_media(color_scheme=color_scheme)
            page.reload(wait_until="networkidle")
            table = page.locator("table.markdown-table")
            expect(table).to_have_count(1)
            for selector in (
                "tbody tr:nth-child(1) .inline-code",
                "tbody tr:nth-child(2) strong",
                "tbody tr:nth-child(3) a",
            ):
                ratio = page.locator(selector).evaluate(CONTRAST_SCRIPT)
                assert ratio >= 4.5, f"{selector} contrast {ratio:.2f} in {color_scheme} at {width}px"
            if color_scheme == "light":
                assert page.locator(".markdown-table-container").evaluate(
                    "element => getComputedStyle(element).backgroundColor"
                ) == "rgb(255, 255, 255)"
