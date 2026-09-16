from __future__ import annotations

from html import escape
from typing import Any


HTML_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; form-action 'none'"


def _safe_html_source(content: str) -> str:
    """Return a non-executable HTML document for the sandbox srcdoc."""
    # The iframe remains sandboxed without allow-scripts. Removing active elements
    # and remote references is an additional defense-in-depth boundary.
    import re

    cleaned = re.sub(r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", "", content, flags=re.I | re.S)
    cleaned = re.sub(r"<\s*(iframe|object|embed|form|base)\b[^>]*>.*?<\s*/\s*\1\s*>", "", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"<\s*(iframe|object|embed|form|base)\b[^>]*/?\s*>", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+on[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+(?:src|href)\s*=\s*(?:\"https?://[^\"]*\"|'https?://[^']*'|https?://[^\s>]+)", "", cleaned, flags=re.I)
    meta = f'<meta http-equiv="Content-Security-Policy" content="{escape(HTML_CSP, quote=True)}">'
    return f"<!doctype html><html><head>{meta}<style>body{{font-family:system-ui,sans-serif;line-height:1.55;margin:1.25rem;color:#eaf2f2;background:#101820}}img{{max-width:100%}}</style></head><body>{cleaned}</body></html>"


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
            "sandbox": "sandbox srcdoc opaque-origin",
            "csp": HTML_CSP,
            "sandbox_srcdoc": _safe_html_source(content),
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
