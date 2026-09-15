from __future__ import annotations

import re
import tomllib
from pathlib import Path

VERSION_PATTERN = re.compile(r'data-version="([^"]+)"')
LANGUAGE_PATTERN = re.compile(r'data-language="(en|hu)"')


def _project_version(root: Path) -> str:
    try:
        with (root / "pyproject.toml").open("rb") as file:
            return tomllib.load(file)["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return ""


def validate_project_description(root: str | Path) -> list[str]:
    root = Path(root)
    description_path = root / "web" / "project-description.html"
    try:
        html = description_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [f"{description_path}: cannot read project description"]

    issues: list[str] = []
    project_version = _project_version(root)
    version_match = VERSION_PATTERN.search(html)
    if not version_match:
        issues.append("project description has no data-version")
    elif not project_version:
        issues.append("project version cannot be read")
    elif version_match.group(1) != project_version:
        issues.append(
            f"version mismatch: description={version_match.group(1)}, project={project_version}"
        )

    languages = set(LANGUAGE_PATTERN.findall(html))
    if languages != {"en", "hu"}:
        issues.append("project description must contain both language sections")
    for language in ("en", "hu"):
        if f'data-set-language="{language}"' not in html:
            issues.append(f"project description has no {language} language control")
    return sorted(issues)
