from open_agent_artifacts.presentation import make_presentation


def test_markdown_presentation_is_rendered_mode():
    result = make_presentation("markdown", "# Heading\n\nParagraph", "v1")
    assert result["mode"] == "rendered"
    assert result["content"] == "# Heading\n\nParagraph"
    assert result["source_available"] is True


def test_html_presentation_has_sanitized_isolated_source():
    result = make_presentation(
        "html",
        '<h1>Hello</h1><script>window.bad=true</script><img src="https://example.test/x.png" onerror="bad()">',
        "v1",
    )
    assert result["mode"] == "isolated-html"
    assert "sandbox" in result["sandbox"]
    assert "allow-same-origin" not in result["sandbox"]
    assert "sandbox_srcdoc" in result
    assert "window.bad" not in result["sandbox_srcdoc"]
    assert "onerror" not in result["sandbox_srcdoc"]
    assert "https://example.test" not in result["sandbox_srcdoc"]


def test_unsupported_kinds_keep_source_fallback():
    result = make_presentation("mermaid", "graph TD; A-->B", "v1")
    assert result["mode"] == "unsupported"
    assert result["fallback"] == "escaped-source"
    assert result["source_available"] is True
