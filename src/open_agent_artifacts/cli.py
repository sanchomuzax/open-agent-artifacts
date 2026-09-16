from __future__ import annotations

import argparse
import difflib
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


class RawOutput(str):
    """CLI output that should not be JSON-encoded."""


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


def _check_anchor(anchor: Any, content: str, comment_id: str, source_version_id: str, target_version_id: str) -> dict[str, Any]:
    if not isinstance(anchor, dict):
        return {
            "status": "invalid",
            "comment_id": comment_id,
            "source_version_id": source_version_id,
            "target_version_id": target_version_id,
            "match_count": 0,
            "matches": [],
        }
    quote = anchor.get("quote") if isinstance(anchor.get("quote"), dict) else {}
    exact = quote.get("exact") or anchor.get("exact")
    prefix = quote.get("prefix", anchor.get("prefix", ""))
    suffix = quote.get("suffix", anchor.get("suffix", ""))
    if not isinstance(exact, str) or not exact or not isinstance(prefix, str) or not isinstance(suffix, str):
        return {
            "status": "invalid",
            "comment_id": comment_id,
            "source_version_id": source_version_id,
            "target_version_id": target_version_id,
            "match_count": 0,
            "matches": [],
        }
    if anchor.get("schema") == 2 and (
        anchor.get("type") != "text-range"
        or anchor.get("coordinate_space") != "unicode-code-points"
        or not isinstance(anchor.get("start"), int)
        or not isinstance(anchor.get("end"), int)
        or anchor["start"] < 0
        or anchor["end"] < anchor["start"]
    ):
        return {
            "status": "invalid",
            "comment_id": comment_id,
            "source_version_id": source_version_id,
            "target_version_id": target_version_id,
            "match_count": 0,
            "matches": [],
        }
    candidates = []
    offset = 0
    while True:
        start = content.find(exact, offset)
        if start < 0:
            break
        candidates.append(start)
        offset = start + max(1, len(exact))
    matches = []
    for start in candidates:
        end = start + len(exact)
        prefix_ok = not prefix or content[max(0, start - len(prefix)):start] == prefix
        suffix_ok = not suffix or content[end:end + len(suffix)] == suffix
        if prefix_ok and suffix_ok:
            matches.append({"start": start, "end": end, "exact": exact})
    status = "exact" if len(matches) == 1 else "ambiguous" if len(matches) > 1 else "missing"
    return {
        "status": status,
        "comment_id": comment_id,
        "source_version_id": source_version_id,
        "target_version_id": target_version_id,
        "match_count": len(matches),
        "matches": matches,
    }


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
    comments = subparsers.add_parser("comments", help="inspect feedback")
    comments_subparsers = comments.add_subparsers(dest="comments_command", required=True)
    comments_list = comments_subparsers.add_parser("list", help="list comments")
    comments_list.add_argument("--artifact-id")
    comments_list.add_argument("--status", choices=["open", "addressed", "resolved", "all"], default="open")
    comments_list.add_argument("--limit", type=int, default=50)
    comments_list.add_argument("--cursor")
    comments_get = comments_subparsers.add_parser("get", help="get a comment")
    comments_get.add_argument("comment_id")
    comments_events = comments_subparsers.add_parser("events", help="list comment events")
    comments_events.add_argument("comment_id")
    inbox = subparsers.add_parser("inbox", help="list open comments across the workspace")
    inbox.add_argument("--status", choices=["open", "addressed", "resolved", "all"], default="open")
    inbox.add_argument("--limit", type=int, default=50)
    inbox.add_argument("--cursor")
    show = subparsers.add_parser("show", help="show complete artifact context")
    show.add_argument("artifact_id")
    show.add_argument("--summary", action="store_true")
    search = subparsers.add_parser("search", help="search artifact content and feedback")
    search.add_argument("query")
    search.add_argument("--kind")
    search.add_argument("--include-archived", action="store_true")
    search.add_argument("--comment-status", choices=["open", "addressed", "resolved"])
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--cursor")
    diff = subparsers.add_parser("diff", help="compare two immutable versions")
    diff.add_argument("artifact_id")
    diff.add_argument("version_a")
    diff.add_argument("version_b")
    diff.add_argument("--format", choices=["json", "unified"], default="json")
    diff.add_argument("--max-output", type=int, default=200_000)
    anchor = subparsers.add_parser("anchor", help="validate a comment anchor")
    anchor_subparsers = anchor.add_subparsers(dest="anchor_command", required=True)
    anchor_check = anchor_subparsers.add_parser("check", help="check an anchor in a version")
    anchor_check.add_argument("comment_id")
    anchor_check.add_argument("--version", required=True)
    versions = subparsers.add_parser("versions", help="inspect artifact versions")
    versions_subparsers = versions.add_subparsers(dest="versions_command", required=True)
    versions_list = versions_subparsers.add_parser("list", help="list artifact versions")
    versions_list.add_argument("artifact_id")
    archive = subparsers.add_parser("archive", help="archive an artifact")
    archive.add_argument("artifact_id")
    rename = subparsers.add_parser("rename", help="rename an artifact")
    rename.add_argument("artifact_id")
    rename.add_argument("--title", required=True)
    duplicate = subparsers.add_parser("duplicate", help="duplicate an artifact")
    duplicate.add_argument("artifact_id")
    duplicate.add_argument("--created-by", default="artifactctl")
    restore = subparsers.add_parser("restore", help="restore a version as a new version")
    restore.add_argument("artifact_id")
    restore.add_argument("--version-id", required=True)
    restore.add_argument("--expected-current-version-id", required=True)
    restore.add_argument("--created-by", default="artifactctl")
    pin = subparsers.add_parser("pin", help="pin an artifact")
    pin.add_argument("artifact_id")
    unpin = subparsers.add_parser("unpin", help="unpin an artifact")
    unpin.add_argument("artifact_id")
    visit = subparsers.add_parser("visit", help="record an artifact visit")
    visit.add_argument("artifact_id")
    return parser


def _run(args: argparse.Namespace) -> Any:
    if args.command == "comments":
        if args.comments_command == "list":
            status = None if args.status == "all" else args.status
            if args.artifact_id:
                items = _request(
                    args.base_url,
                    "GET",
                    f"/api/artifacts/{quote(args.artifact_id, safe='')}/comments" + ("?" + urlencode({"status": status}) if status else ""),
                    token=args.token,
                )[1]
                return {"items": items, "next_cursor": None}
            params = {"status": args.status, "limit": args.limit}
            if args.cursor:
                params["cursor"] = args.cursor
            return _request(args.base_url, "GET", "/api/comments?" + urlencode(params), token=args.token)[1]
        if args.comments_command == "get":
            return _request(args.base_url, "GET", f"/api/comments/{quote(args.comment_id, safe='')}", token=args.token)[1]
        if args.comments_command == "events":
            items = _request(args.base_url, "GET", f"/api/comments/{quote(args.comment_id, safe='')}/events", token=args.token)[1]
            return {"items": items}
    if args.command == "inbox":
        params = {"status": args.status, "limit": args.limit}
        if args.cursor:
            params["cursor"] = args.cursor
        return _request(args.base_url, "GET", "/api/comments?" + urlencode(params), token=args.token)[1]
    if args.command == "show":
        artifact = _request(args.base_url, "GET", f"/api/artifacts/{quote(args.artifact_id, safe='')}", token=args.token)[1]
        current_version = _request(
            args.base_url, "GET", f"/api/versions/{quote(artifact['current_version_id'], safe='')}", token=args.token
        )[1]
        versions = _request(
            args.base_url, "GET", f"/api/artifacts/{quote(args.artifact_id, safe='')}/versions", token=args.token
        )[1]
        comments = _request(
            args.base_url, "GET", f"/api/artifacts/{quote(args.artifact_id, safe='')}/comments?status=open", token=args.token
        )[1]
        if args.summary:
            current_version = {
                **current_version,
                "content": current_version["content"][:512],
                "truncated": len(current_version["content"]) > 512,
            }
        return {"artifact": artifact, "current_version": current_version, "versions": versions, "comments": comments}
    if args.command == "search":
        params = {"q": args.query, "limit": args.limit}
        if args.kind:
            params["kind"] = args.kind
        if args.include_archived:
            params["include_archived"] = "true"
        if args.comment_status:
            params["comment_status"] = args.comment_status
        if args.cursor:
            params["cursor"] = args.cursor
        return _request(args.base_url, "GET", "/api/search?" + urlencode(params), token=args.token)[1]
    if args.command == "versions":
        return _request(
            args.base_url,
            "GET",
            f"/api/artifacts/{quote(args.artifact_id, safe='')}/versions",
            token=args.token,
        )[1]
    if args.command == "diff":
        artifact = _request(args.base_url, "GET", f"/api/artifacts/{quote(args.artifact_id, safe='')}", token=args.token)[1]
        version_a = _request(args.base_url, "GET", f"/api/versions/{quote(args.version_a, safe='')}", token=args.token)[1]
        version_b = _request(args.base_url, "GET", f"/api/versions/{quote(args.version_b, safe='')}", token=args.token)[1]
        if version_a.get("artifact_id") != artifact["id"] or version_b.get("artifact_id") != artifact["id"]:
            raise CLIError("versions belong to a different artifact")
        if args.max_output < 1:
            raise CLIError("max-output must be positive")
        diff_text = "".join(difflib.unified_diff(
            version_a["content"].splitlines(keepends=True),
            version_b["content"].splitlines(keepends=True),
            fromfile=f"{args.artifact_id}:{args.version_a}",
            tofile=f"{args.artifact_id}:{args.version_b}",
        ))
        truncated = len(diff_text) > args.max_output
        if truncated:
            diff_text = diff_text[:args.max_output] + "\n[diff truncated]\n"
        if args.format == "unified":
            return RawOutput(diff_text)
        return {"format": "unified", "diff": diff_text, "truncated": truncated}
    if args.command == "anchor" and args.anchor_command == "check":
        comment = _request(args.base_url, "GET", f"/api/comments/{quote(args.comment_id, safe='')}", token=args.token)[1]
        version = _request(args.base_url, "GET", f"/api/versions/{quote(args.version, safe='')}", token=args.token)[1]
        if version.get("artifact_id") != comment.get("artifact_id"):
            raise CLIError("target version belongs to a different artifact")
        return _check_anchor(comment.get("anchor"), version["content"], comment["id"], comment["version_id"], version["id"])
    if args.command in {"archive", "pin", "unpin", "visit"}:
        suffix = args.command
        return _request(
            args.base_url,
            "POST",
            f"/api/artifacts/{quote(args.artifact_id, safe='')}/{suffix}",
            {},
            args.token,
        )[1]
    if args.command == "rename":
        return _request(
            args.base_url,
            "POST",
            f"/api/artifacts/{quote(args.artifact_id, safe='')}/rename",
            {"title": args.title},
            args.token,
        )[1]
    if args.command == "duplicate":
        return _request(
            args.base_url,
            "POST",
            f"/api/artifacts/{quote(args.artifact_id, safe='')}/duplicate",
            {"created_by": args.created_by},
            args.token,
        )[1]
    if args.command == "restore":
        return _request(
            args.base_url,
            "POST",
            f"/api/artifacts/{quote(args.artifact_id, safe='')}/restore",
            {
                "version_id": args.version_id,
                "expected_current_version_id": args.expected_current_version_id,
                "created_by": args.created_by,
            },
            args.token,
        )[1]
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
        result = _run(args)
        if isinstance(result, RawOutput):
            print(result, end="")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except CLIError as error:
        print(json.dumps({"error": "cli_error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
