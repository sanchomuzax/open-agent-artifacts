from pathlib import Path


def test_readme_contains_project_description_without_raw_html_link():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "web/project-description.html" not in readme
    assert "Open Agent Artifacts is a local-first review workspace" in readme
    assert "### Magyar" not in readme
    assert "EN/HU switch" not in readme
