from __future__ import annotations

import argparse
import re
from pathlib import Path


_HEADING = re.compile(r"^##\s+v?(\d+\.\d+\.\d+)(?:\s+—.*)?\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+\S")


def extract_release_notes(changelog: str, version: str) -> str:
    """Extract one non-empty version section from a Markdown changelog."""
    lines = changelog.splitlines()
    start = None
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if match and match.group(1) == version:
            start = index
            break
    if start is None:
        raise ValueError(f"CHANGELOG.md has no section for version {version}")

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _HEADING.match(lines[index]):
            end = index
            break
    section = "\n".join(lines[start:end]).strip()
    if not any(_BULLET.match(line) for line in section.splitlines()):
        raise ValueError(f"CHANGELOG.md section for version {version} has no release items")
    return f"{section}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract release notes from CHANGELOG.md")
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", default="CHANGELOG.md")
    args = parser.parse_args()
    try:
        notes = extract_release_notes(Path(args.changelog).read_text(encoding="utf-8"), args.version)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(notes, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
