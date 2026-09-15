from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from release_notes import extract_release_notes


def test_extract_release_notes_returns_nonempty_version_section():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    notes = extract_release_notes(changelog, "0.1.9")

    assert notes.startswith("## 0.1.9")
    assert "list/card catalog views" in notes
    assert "## 0.1.8" not in notes


def test_extract_release_notes_rejects_missing_version():
    with pytest.raises(ValueError, match="no section"):
        extract_release_notes("# Changelog\n\n## 1.0.0\n\n- Something\n", "0.1.9")


def test_extract_release_notes_rejects_empty_version_section():
    with pytest.raises(ValueError, match="no release items"):
        extract_release_notes("# Changelog\n\n## 0.1.9 — 2026-09-15\n", "0.1.9")
