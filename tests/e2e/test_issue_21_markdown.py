from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .conftest import create_artifact


pytestmark = pytest.mark.e2e


MARKDOWN = r"""# Renderer coverage

```text
| not | a table |
| --- | --- |
```

| Name | Details | Alignment |
| :--- | :---: | ---: |
| A\|B | **bold with `code`** and [safe link](https://example.com) <script>alert("cell")</script> [unsafe](javascript:alert(1)) | left |
| Missing | only two cells |
| Extra | **more** | right | preserved |

Pipe text without delimiter: alpha | beta
This remains an ordinary paragraph.

Ordinary paragraph followed by a delimiter-looking row:
| --- | --- |
This is still paragraph text.

4. Starts at four
5) Continues with another marker

<script>alert("not executable")</script>
[unsafe](javascript:alert(1))
"""


def test_markdown_renderer_supports_tables_lists_nested_inline_markup_and_safety(
    page: Page, running_server
):
    artifact = create_artifact(running_server, "Issue 21 Markdown", "markdown", MARKDOWN)
    page.goto(
        f"http://127.0.0.1:{running_server.server_port}/#/artifact/{artifact['id']}",
        wait_until="networkidle",
    )

    table = page.locator("table.markdown-table")
    expect(table).to_have_count(1)
    expect(table.locator("thead th")).to_have_count(4)
    expect(table.locator("tbody tr")).to_have_count(3)
    expect(table.locator("tbody tr").nth(0).locator("td")).to_have_count(4)
    expect(table.locator("tbody tr").nth(0).locator("td").nth(0)).to_have_text("A|B")
    expect(table.locator("tbody tr").nth(0).locator("strong")).to_contain_text("bold with")
    expect(table.locator("tbody tr").nth(0).locator("code")).to_have_text("code")
    expect(table.locator("tbody tr").nth(0).locator("a")).to_have_attribute(
        "href", "https://example.com/"
    )
    details = table.locator("tbody tr").nth(0).locator("td").nth(1)
    expect(details.locator("script")).to_have_count(0)
    expect(details).to_contain_text('<script>alert("cell")</script>')
    expect(details.locator("a")).to_have_count(1)
    expect(table.locator("tbody tr").nth(1).locator("td").nth(2)).to_have_text("")
    expect(table.locator("tbody tr").nth(2).locator("td").nth(3)).to_have_text("preserved")
    assert table.locator("thead th").nth(0).get_attribute("data-align") == "left"
    assert table.locator("thead th").nth(1).get_attribute("data-align") == "center"
    assert table.locator("thead th").nth(2).get_attribute("data-align") == "right"
    assert page.locator(".markdown-table-container").evaluate(
        "element => getComputedStyle(element).overflowX"
    ) == "auto"

    ordered = page.locator("ol.markdown-list")
    expect(ordered).to_have_count(1)
    expect(ordered).to_have_attribute("start", "4")
    expect(ordered.locator("li")).to_have_count(2)

    expect(page.locator(".rendered-markdown p").filter(has_text="Pipe text without delimiter")).to_have_count(1)
    paragraph = page.locator(".rendered-markdown p").filter(has_text="Ordinary paragraph followed")
    expect(paragraph).to_contain_text("| --- | --- |")
    expect(page.locator(".rendered-markdown script")).to_have_count(0)
    expect(page.locator(".rendered-markdown")).to_contain_text('<script>alert("not executable")</script>')
    expect(page.locator(".rendered-markdown a").filter(has_text="unsafe")).to_have_count(0)
    expect(page.locator("pre.markdown-code")).to_contain_text("not | a table")

    for color_scheme in ("light", "dark"):
        page.emulate_media(color_scheme=color_scheme)
        page.reload(wait_until="networkidle")
        expect(page.locator("table.markdown-table")).to_have_count(1)
        assert page.locator(".markdown-table th").first.evaluate(
            "element => getComputedStyle(element).backgroundColor"
        )
        assert page.locator(".markdown-table-container").evaluate(
            "element => getComputedStyle(element).overflowX"
        ) == "auto"

    page.set_viewport_size({"width": 390, "height": 844})
    page.reload(wait_until="networkidle")
    wrapper = page.locator(".markdown-table-container")
    table = page.locator("table.markdown-table")
    wrapper_box = wrapper.bounding_box()
    table_box = table.bounding_box()
    assert wrapper_box is not None and table_box is not None
    assert wrapper_box["width"] <= 390
    assert table_box["width"] > wrapper_box["width"]
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
