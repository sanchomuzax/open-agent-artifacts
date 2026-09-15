from pathlib import Path
import tomllib

import pytest

from open_agent_artifacts.description import validate_project_description


ROOT = Path(__file__).parents[1]


def test_public_description_contains_english_hungarian_toggle_and_current_version():
    issues = validate_project_description(ROOT)

    assert issues == []
    html = (ROOT / "web" / "project-description.html").read_text(encoding="utf-8")
    assert 'data-language="en"' in html
    assert 'data-language="hu"' in html
    with (ROOT / "pyproject.toml").open("rb") as file:
        version = tomllib.load(file)["project"]["version"]
    assert f'data-version="{version}"' in html


def test_public_description_rejects_stale_version(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")
    web = tmp_path / "web"
    web.mkdir()
    (web / "project-description.html").write_text(
        '<html data-version="0.1.0"><body data-language="en"></body></html>',
        encoding="utf-8",
    )

    issues = validate_project_description(tmp_path)

    assert any("version mismatch" in issue for issue in issues)


def test_public_description_rejects_missing_language_sections(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.5"\n', encoding="utf-8")
    web = tmp_path / "web"
    web.mkdir()
    (web / "project-description.html").write_text(
        '<html data-version="0.1.5"><body data-language="en"></body></html>',
        encoding="utf-8",
    )

    issues = validate_project_description(tmp_path)

    assert any("language sections" in issue for issue in issues)
