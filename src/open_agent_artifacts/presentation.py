from __future__ import annotations

from typing import Any


def make_presentation(kind: str, content: str, version_id: str) -> dict[str, Any]:
    """Describe the safe user-facing presentation for one immutable version."""
    if kind == "markdown":
        return {
            "mode": "rendered",
            "kind": kind,
            "version_id": version_id,
            "content": content,
            "source_available": True,
        }
    if kind == "html":
        return {
            "mode": "isolated-html",
            "kind": kind,
            "version_id": version_id,
            "content": content,
            "source_available": True,
        }
    if kind in {"svg", "mermaid"}:
        return {
            "mode": "unsupported",
            "kind": kind,
            "version_id": version_id,
            "content": content,
            "fallback": "escaped-source",
            "source_available": True,
        }
    return {
        "mode": "escaped-source",
        "kind": kind,
        "version_id": version_id,
        "content": content,
        "source_available": True,
    }
