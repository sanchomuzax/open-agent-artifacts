from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class CLIError(Exception):
    """A user-facing CLI error."""


def _read_content(content: str | None, file_path: str | None) -> str | None:
    if content is not None and file_path is not None:
        raise CLIError("choose either --content or --file")
    if file_path is not None:
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except OSError as error:
            raise CLIError(f"cannot read file: {error}") from error
    return content


def _request(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(base_url.rstrip("/") + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = {"message": str(error)}
        raise CLIError(json.dumps(detail, ensure_ascii=False)) from error
    except URLError as error:
        raise CLIError(f"API request failed: {error.reason}") from error


def _add_content_options(parser: argparse.ArgumentParser, required: bool = False) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--content")
    group.add_argument("--file")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open Agent Artifacts API client")
    parser.add_argument("--base-url", default=os.environ.get("OAA_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("OAA_API_TOKEN"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create an artifact")
    create.add_argument("--title", required=True)
    create.add_argument("--kind", required=True)
    _add_content_options(create, required=True)
    create.add_argument("--created-by", default="artifactctl")

    listing = subparsers.add_parser("list", help="list artifacts")
    listing.add_argument("--query")
    listing.add_argument("--include-archived", action="store_true")

    get = subparsers.add_parser("get", help="get an artifact")
    get.add_argument("artifact_id")

    publish = subparsers.add_parser("publish", help="publish a new immutable version")
    publish.add_argument("artifact_id")
    publish.add_argument("--expected-current-version-id", required=True)
    _add_content_options(publish)
    publish.add_argument("--old-str")
    publish.add_argument("--new-str")
    publish.add_argument("--change-summary", default="")
    publish.add_argument("--created-by", default="artifactctl")

    feedback = subparsers.add_parser("feedback", help="add feedback to a version")
    feedback.add_argument("version_id")
    feedback.add_argument("--artifact-id", required=True)
    feedback.add_argument("--body", required=True)
    feedback.add_argument("--anchor-json")
    feedback.add_argument("--exact")
    feedback.add_argument("--prefix", default="")
    feedback.add_argument("--suffix", default="")

    address = subparsers.add_parser("address", help="change feedback status")
    address.add_argument("comment_id")
    address.add_argument("--status", required=True, choices=["open", "addressed", "resolved"])
    address.add_argument("--actor", default="artifactctl")
    address.add_argument("--version-id")
    return parser


def _run(args: argparse.Namespace) -> Any:
    if args.command == "create":
        content = _read_content(args.content, args.file)
        _, result = _request(
            args.base_url,
            "POST",
            "/api/artifacts",
            {"title": args.title, "kind": args.kind, "content": content, "created_by": args.created_by},
            args.token,
        )
        return result
    if args.command == "list":
        params = {}
        if args.query:
            params["query"] = args.query
        if args.include_archived:
            params["include_archived"] = "true"
        path = "/api/artifacts" + ("?" + urlencode(params) if params else "")
        _, result = _request(args.base_url, "GET", path, token=args.token)
        return result
    if args.command == "get":
        _, result = _request(args.base_url, "GET", f"/api/artifacts/{quote(args.artifact_id, safe='')}", token=args.token)
        return result
    if args.command == "publish":
        content = _read_content(args.content, args.file)
        if content is None and args.old_str is None:
            raise CLIError("publish needs --content, --file, or --old-str/--new-str")
        if args.old_str is not None and args.new_str is None:
            raise CLIError("--new-str is required with --old-str")
        payload = {
            "content": content,
            "old_str": args.old_str,
            "new_str": args.new_str,
            "created_by": args.created_by,
            "expected_current_version_id": args.expected_current_version_id,
            "change_summary": args.change_summary,
        }
        if args.old_str is not None:
            payload["content"] = None
        _, result = _request(
            args.base_url,
            "POST",
            f"/api/artifacts/{quote(args.artifact_id, safe='')}/versions",
            payload,
            args.token,
        )
        return result
    if args.command == "feedback":
        if args.anchor_json:
            try:
                anchor = json.loads(args.anchor_json)
            except json.JSONDecodeError as error:
                raise CLIError(f"invalid --anchor-json: {error}") from error
        else:
            anchor = {"kind": "text", "exact": args.exact or "", "prefix": args.prefix, "suffix": args.suffix}
        _, result = _request(
            args.base_url,
            "POST",
            f"/api/versions/{quote(args.version_id, safe='')}/comments",
            {"artifact_id": args.artifact_id, "body": args.body, "anchor": anchor},
            args.token,
        )
        return result
    if args.command == "address":
        _, result = _request(
            args.base_url,
            "POST",
            f"/api/comments/{quote(args.comment_id, safe='')}/events",
            {"status": args.status, "actor": args.actor, "addressed_in_version_id": args.version_id},
            args.token,
        )
        return result
    raise CLIError(f"unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        print(json.dumps(_run(args), ensure_ascii=False, indent=2))
    except CLIError as error:
        print(json.dumps({"error": "cli_error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
