from open_agent_artifacts.presentation import make_presentation


def test_markdown_presentation_is_rendered_mode():
    result = make_presentation("markdown", "# Heading\n\nParagraph", "v1")
    assert result["mode"] == "rendered"
    assert result["content"] == "# Heading\n\nParagraph"
    assert result["source_available"] is True


def test_html_presentation_returns_only_client_consumed_fields():
    result = make_presentation(
        "html",
        '<h1>Hello</h1><script>window.bad=true</script><img src="https://example.test/x.png" onerror="bad()">',
        "v1",
    )
    assert result["mode"] == "isolated-html"
    assert result == {
        "mode": "isolated-html",
        "kind": "html",
        "version_id": "v1",
        "content": '<h1>Hello</h1><script>window.bad=true</script><img src="https://example.test/x.png" onerror="bad()">',
        "source_available": True,
    }


def test_unsupported_kinds_keep_source_fallback():
    result = make_presentation("mermaid", "graph TD; A-->B", "v1")
    assert result["mode"] == "unsupported"
    assert result["fallback"] == "escaped-source"
    assert result["source_available"] is True
