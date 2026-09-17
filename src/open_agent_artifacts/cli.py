from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from .agent_skill import AgentSkillInstallError, install_agent_skill

class CLIError(Exception):
    """A user-facing CLI error."""


class RawOutput(str):
    """CLI output that should not be JSON-encoded."""


MUTATING_COMMANDS = {
    "create",
    "publish",
    "feedback",
    "address",
    "archive",
    "rename",
    "duplicate",
    "restore",
    "pin",
    "unpin",
    "visit",
    "metadata",
}


def _read_content(content: str | None, file_path: str | None) -> str | None:
    if content is not None and file_path is not None:
        raise CLIError("choose either --content or --file")
    if file_path is not None:
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except OSError as error:
            raise CLIError(f"cannot read file: {error}") from error
    return content


def _read_metadata(metadata_json: str | None, file_path: str | None) -> dict[str, Any] | None:
    if metadata_json is not None and file_path is not None:
        raise CLIError("choose either --metadata-json or --metadata-file")
    if metadata_json is None and file_path is None:
        return None
    try:
        if metadata_json is not None:
            raw = metadata_json
        else:
            assert file_path is not None
            raw = Path(file_path).read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise CLIError(f"invalid metadata JSON: {error}") from error
    if not isinstance(value, dict):
        raise CLIError("metadata JSON must be an object")
    return value


def _request(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    idempotency_key: str | None = None,
    agent_id: str | None = None,
    agent_run_id: str | None = None,
    operation_id: str | None = None,
) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if agent_id:
        headers["X-OAA-Agent-ID"] = agent_id
    if agent_run_id:
        headers["X-OAA-Agent-Run-ID"] = agent_run_id
    if operation_id:
        headers["X-OAA-Operation-ID"] = operation_id
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


def _add_metadata_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--metadata-json")
    group.add_argument("--metadata-file")


def _add_operation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent-id")
    parser.add_argument("--agent-run-id")
    parser.add_argument("--operation-id")


def _add_write_safety_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allow-ephemeral",
        action="store_true",
        help="allow writes to an explicitly ephemeral instance (test/development only)",
    )


def _ensure_write_target(args: argparse.Namespace) -> dict[str, Any]:
    try:
        instance = _request(args.base_url, "GET", "/api/instance", token=args.token)[1]
    except CLIError as error:
        raise CLIError(f"write target could not be verified: {error}") from error
    instance_id = instance.get("instance_id") if isinstance(instance, dict) else None
    storage_class = instance.get("storage_class") if isinstance(instance, dict) else None
    if not instance_id or not storage_class:
        raise CLIError(
            "write target is not configured; set OAA_INSTANCE_ID and OAA_STORAGE_CLASS="
            "persistent (use --allow-ephemeral only for explicit test targets)"
        )
    if storage_class == "ephemeral" and not args.allow_ephemeral:
        raise CLIError(
            f"write target {instance_id!r} is ephemeral; use --allow-ephemeral only for explicit test/development writes"
        )
    if storage_class != "persistent" and storage_class != "ephemeral":
        raise CLIError(f"write target has unsupported storage class: {storage_class!r}")
    return instance


def _artifact_url(public_url: str | None, artifact_id: str) -> tuple[str | None, str | None]:
    if not public_url:
        return None, "public_url_not_configured"
    parsed = urlsplit(public_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, "public_url_invalid"
    return f"{public_url.rstrip('/')}/#/artifact/{quote(artifact_id, safe='')}", None


def _verify_created_artifact(args: argparse.Namespace, result: dict[str, Any], content: str) -> dict[str, Any]:
    artifact_id = result.get("id")
    version_id = result.get("created_version_id") or result.get("current_version_id")
    if not isinstance(artifact_id, str) or not isinstance(version_id, str):
        raise CLIError("create response did not contain artifact and current version IDs")
    artifact = _request(
        args.base_url,
        "GET",
        f"/api/artifacts/{quote(artifact_id, safe='')}",
        token=args.token,
    )[1]
    version = _request(
        args.base_url,
        "GET",
        f"/api/versions/{quote(version_id, safe='')}",
        token=args.token,
    )[1]
    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    readback_content = version.get("content")
    readback_hash = hashlib.sha256(readback_content.encode("utf-8")).hexdigest() if isinstance(readback_content, str) else None
    stored_hash = version.get("content_hash")
    stored_hash_match = stored_hash == readback_hash
    content_match = readback_hash == expected_hash
    hash_match = stored_hash_match and content_match
    current_match = artifact.get("current_version_id") == version_id
    replay = not current_match and result.get("created_version_id") != result.get("current_version_id")
    if not hash_match or (not current_match and not replay):
        raise CLIError(
            "create verification failed: "
            f"content_hash_match={hash_match}, current_version_match={current_match}"
        )
    public_url, public_url_reason = _artifact_url(args.public_url, artifact_id)
    return {
        **result,
        "artifact_url": public_url,
        "artifact_url_reason": public_url_reason,
        "verification": {
            "verified": True,
            "artifact_id": artifact_id,
            "version_id": version_id,
            "content_hash": expected_hash,
            "content_hash_match": True,
            "readback_content_match": content_match,
            "stored_content_hash_match": stored_hash_match,
            "current_version_match": current_match,
            "idempotent_replay": replay,
        },
    }


def _doctor(args: argparse.Namespace) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}

    def read_check(name: str, path: str, expected_status: str) -> None:
        try:
            payload = _request(args.base_url, "GET", path, token=args.token)[1]
            checks[name] = {"ok": payload.get("status") == expected_status, "status": payload.get("status")}
        except CLIError as error:
            checks[name] = {"ok": False, "error": str(error)}

    read_check("api", "/healthz", "ok")
    read_check("readiness", "/readyz", "ready")
    try:
        instance = _request(args.base_url, "GET", "/api/instance", token=args.token)[1]
        identity_ok = bool(instance.get("instance_id") and instance.get("storage_class"))
        if args.expect_instance_id:
            identity_ok = identity_ok and instance.get("instance_id") == args.expect_instance_id
        if args.expect_storage_class:
            identity_ok = identity_ok and instance.get("storage_class") == args.expect_storage_class
        checks["instance"] = {
            "ok": identity_ok,
            "instance_id": instance.get("instance_id"),
            "storage_class": instance.get("storage_class"),
        }
    except CLIError as error:
        checks["instance"] = {"ok": False, "error": str(error)}

    public_url, reason = _artifact_url(args.public_url, "diagnostic")
    checks["public_url"] = {
        "ok": public_url is not None,
        "configured": bool(args.public_url),
        "reason": reason,
    }
    return {"ok": all(check["ok"] for check in checks.values()), "checks": checks}


def _smoke(args: argparse.Namespace) -> dict[str, Any]:
    _ensure_write_target(args)
    smoke_id = str(uuid.uuid4())
    content = f"Open Agent Artifacts synthetic smoke payload {smoke_id}"
    created: dict[str, Any] | None = None
    checks: dict[str, dict[str, Any]] = {}
    try:
        created = _request(
            args.base_url,
            "POST",
            "/api/artifacts",
            {
                "title": f"Synthetic smoke {smoke_id}",
                "kind": "text",
                "content": content,
                "created_by": "artifactctl-smoke",
            },
            args.token,
            smoke_id,
            "artifactctl-smoke",
            smoke_id,
            smoke_id,
        )[1]
        assert created is not None
        verified = _verify_created_artifact(args, created, content)
        checks["api"] = {"ok": True, "verification": verified["verification"]}
        if args.public_url:
            try:
                public_health = _request(args.public_url, "GET", "/healthz", token=None)[1]
                public_root = urlopen(
                    Request(args.public_url.rstrip("/") + "/", headers={"Accept": "text/html"}),
                    timeout=10,
                )
                with public_root:
                    root_status = public_root.status
                public_artifact_url, _ = _artifact_url(args.public_url, created["id"])
                checks["route"] = {
                    "ok": public_health.get("status") == "ok" and root_status == 200,
                    "status": public_health.get("status"),
                    "root_status": root_status,
                    "artifact_url": public_artifact_url,
                }
            except (CLIError, OSError, ValueError) as error:
                checks["route"] = {"ok": False, "status": "error", "error": str(error)}
        else:
            checks["route"] = {"ok": None, "status": "not_run", "reason": "public_url_not_configured"}
        checks["ui"] = {"ok": None, "status": "not_run", "reason": "browser_check_not_configured"}
    except CLIError as error:
        checks["api"] = {"ok": False, "status": "error", "error": str(error)}
        checks["route"] = {"ok": False, "status": "not_run", "reason": "api_check_failed"}
        checks["ui"] = {"ok": None, "status": "not_run", "reason": "api_check_failed"}
    finally:
        artifact_id = created.get("id") if created else None
        if artifact_id is None:
            try:
                candidates = _request(
                    args.base_url,
                    "GET",
                    "/api/search?" + urlencode({"q": smoke_id, "limit": 20}),
                    token=args.token,
                )[1].get("items", [])
                matches = [
                    item["artifact_id"]
                    for item in candidates
                    if item.get("type") == "artifact" and item.get("title") == f"Synthetic smoke {smoke_id}"
                ]
                if len(matches) == 1:
                    artifact_id = matches[0]
            except CLIError:
                artifact_id = None
        if artifact_id:
            try:
                _request(
                    args.base_url,
                    "POST",
                    f"/api/artifacts/{quote(artifact_id, safe='')}/archive",
                    {},
                    args.token,
                    None,
                    "artifactctl-smoke",
                    smoke_id,
                    str(uuid.uuid4()),
                )
                archived = _request(
                    args.base_url,
                    "GET",
                    f"/api/artifacts/{quote(artifact_id, safe='')}",
                    token=args.token,
                )[1]
                checks["cleanup"] = {"ok": bool(archived.get("archived_at")), "archived": bool(archived.get("archived_at"))}
            except CLIError as error:
                checks["cleanup"] = {"ok": False, "error": str(error)}
        else:
            checks["cleanup"] = {
                "ok": False,
                "status": "unknown",
                "reason": "synthetic_artifact_id_not_recovered",
            }
    required_checks = ["api", "cleanup"] + (["route"] if args.public_url else [])
    return {
        "ok": all(checks[name].get("ok") is True for name in required_checks),
        "complete": all(check.get("ok") is not None for check in checks.values()),
        "artifact_id": created.get("id") if created else None,
        "checks": checks,
    }


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
    parser.add_argument("--public-url", default=os.environ.get("OAA_PUBLIC_URL"))
    parser.add_argument("--token", default=os.environ.get("OAA_API_TOKEN"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create an artifact")
    create.add_argument("--title", required=True)
    create.add_argument("--kind", required=True)
    _add_content_options(create, required=True)
    _add_metadata_options(create)
    create.add_argument("--created-by", default="artifactctl")
    create.add_argument("--idempotency-key")
    create.add_argument("--agent-id")
    create.add_argument("--agent-run-id")
    create.add_argument("--operation-id")
    _add_write_safety_options(create)
    create.add_argument("--verify", action="store_true", help="read back and verify the created version")

    listing = subparsers.add_parser("list", help="list artifacts")
    listing.add_argument("--query")
    listing.add_argument("--include-archived", action="store_true")
    listing.add_argument("--kind")
    listing.add_argument("--tag", dest="tags", action="append", default=[])
    listing.add_argument("--project")
    listing.add_argument("--source-agent")

    get = subparsers.add_parser("get", help="get an artifact")
    get.add_argument("artifact_id")

    publish = subparsers.add_parser("publish", help="publish a new immutable version")
    publish.add_argument("artifact_id")
    publish.add_argument("--expected-current-version-id", required=True)
    _add_content_options(publish)
    _add_metadata_options(publish)
    publish.add_argument("--old-str")
    publish.add_argument("--new-str")
    publish.add_argument("--change-summary", default="")
    publish.add_argument("--created-by", default="artifactctl")
    publish.add_argument("--idempotency-key")
    publish.add_argument("--agent-id")
    publish.add_argument("--agent-run-id")
    publish.add_argument("--operation-id")
    publish.add_argument("--source-comment-id")
    _add_write_safety_options(publish)

    feedback = subparsers.add_parser("feedback", help="add feedback to a version")
    feedback.add_argument("version_id")
    feedback.add_argument("--artifact-id", required=True)
    feedback.add_argument("--body", required=True)
    feedback.add_argument("--anchor-json")
    feedback.add_argument("--exact")
    feedback.add_argument("--prefix", default="")
    feedback.add_argument("--suffix", default="")
    feedback.add_argument("--idempotency-key")
    feedback.add_argument("--agent-id")
    feedback.add_argument("--agent-run-id")
    feedback.add_argument("--operation-id")
    _add_write_safety_options(feedback)

    address = subparsers.add_parser("address", help="change feedback status")
    address.add_argument("comment_id")
    address.add_argument("--status", required=True, choices=["open", "addressed", "resolved"])
    address.add_argument("--actor", default="artifactctl")
    address.add_argument("--version-id")
    address.add_argument("--idempotency-key")
    address.add_argument("--agent-id")
    address.add_argument("--agent-run-id")
    address.add_argument("--operation-id")
    _add_write_safety_options(address)
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
    inbox.add_argument("--since")
    events = subparsers.add_parser("events", help="poll durable mutation events")
    events_subparsers = events.add_subparsers(dest="events_command", required=True)
    events_list = events_subparsers.add_parser("list", help="list events after a cursor")
    events_list.add_argument("--since")
    events_list.add_argument("--limit", type=int, default=50)
    events_deliveries = events_subparsers.add_parser("deliveries", help="list webhook delivery attempts")
    events_deliveries.add_argument("event_id")
    show = subparsers.add_parser("show", help="show complete artifact context")
    show.add_argument("artifact_id")
    show.add_argument("--summary", action="store_true")
    search = subparsers.add_parser("search", help="search artifact content and feedback")
    search.add_argument("query")
    search.add_argument("--kind")
    search.add_argument("--include-archived", action="store_true")
    search.add_argument("--comment-status", choices=["open", "addressed", "resolved"])
    search.add_argument("--tag", dest="tags", action="append", default=[])
    search.add_argument("--project")
    search.add_argument("--source-agent")
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
    versions.add_argument("versions_args", nargs="+")
    archive = subparsers.add_parser("archive", help="archive an artifact")
    archive.add_argument("artifact_id")
    _add_operation_options(archive)
    _add_write_safety_options(archive)
    rename = subparsers.add_parser("rename", help="rename an artifact")
    rename.add_argument("artifact_id")
    rename.add_argument("--title", required=True)
    _add_operation_options(rename)
    _add_write_safety_options(rename)
    duplicate = subparsers.add_parser("duplicate", help="duplicate an artifact")
    duplicate.add_argument("artifact_id")
    duplicate.add_argument("--created-by", default="artifactctl")
    _add_operation_options(duplicate)
    _add_write_safety_options(duplicate)
    restore = subparsers.add_parser("restore", help="restore a version as a new version")
    restore.add_argument("artifact_id")
    restore.add_argument("--version-id", required=True)
    restore.add_argument("--expected-current-version-id", required=True)
    restore.add_argument("--created-by", default="artifactctl")
    _add_operation_options(restore)
    _add_write_safety_options(restore)
    pin = subparsers.add_parser("pin", help="pin an artifact")
    pin.add_argument("artifact_id")
    _add_operation_options(pin)
    _add_write_safety_options(pin)
    unpin = subparsers.add_parser("unpin", help="unpin an artifact")
    unpin.add_argument("artifact_id")
    _add_operation_options(unpin)
    _add_write_safety_options(unpin)
    visit = subparsers.add_parser("visit", help="record an artifact visit")
    visit.add_argument("artifact_id")
    _add_operation_options(visit)
    _add_write_safety_options(visit)
    audit = subparsers.add_parser("audit", help="inspect operation audit records")
    audit_subparsers = audit.add_subparsers(dest="audit_command", required=True)
    audit_list = audit_subparsers.add_parser("list", help="list operations")
    audit_list.add_argument("--resource-id")
    audit_list.add_argument("--agent-id")
    audit_list.add_argument("--limit", type=int, default=100)
    metadata = subparsers.add_parser("metadata", help="replace structured artifact metadata")
    metadata.add_argument("artifact_id")
    _add_metadata_options(metadata)
    metadata.add_argument("--actor", default="artifactctl")
    _add_operation_options(metadata)
    _add_write_safety_options(metadata)
    install = subparsers.add_parser("install-agent-skill", help="install the bundled agent skill")
    install.add_argument("--agent", required=True)
    install.add_argument("--hermes-home")
    install.add_argument("--force", action="store_true")
    doctor = subparsers.add_parser("doctor", help="run read-only service diagnostics")
    doctor.add_argument("--expect-instance-id")
    doctor.add_argument("--expect-storage-class", choices=["persistent", "ephemeral"])
    smoke = subparsers.add_parser("smoke", help="run an explicit synthetic write/read/cleanup check")
    _add_write_safety_options(smoke)
    return parser


def _run(args: argparse.Namespace) -> Any:
    if args.command in MUTATING_COMMANDS:
        _ensure_write_target(args)
    if args.command == "install-agent-skill":
        try:
            return install_agent_skill(args.agent, home=args.hermes_home, force=args.force)
        except AgentSkillInstallError as error:
            raise CLIError(str(error)) from error
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "smoke":
        return _smoke(args)
    if args.command == "metadata":
        metadata = _read_metadata(args.metadata_json, args.metadata_file)
        if metadata is None:
            raise CLIError("metadata needs --metadata-json or --metadata-file")
        return _request(
            args.base_url,
            "POST",
            f"/api/artifacts/{quote(args.artifact_id, safe='')}/metadata",
            {"metadata": metadata, "actor": args.actor},
            args.token,
            None,
            args.agent_id,
            args.agent_run_id,
            args.operation_id,
        )[1]
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
        if args.since:
            params = {"cursor": args.since, "limit": args.limit}
            return _request(args.base_url, "GET", "/api/events?" + urlencode(params), token=args.token)[1]
        params = {"status": args.status, "limit": args.limit}
        if args.cursor:
            params["cursor"] = args.cursor
        return _request(args.base_url, "GET", "/api/comments?" + urlencode(params), token=args.token)[1]
    if args.command == "events" and args.events_command == "list":
        params = {"limit": args.limit}
        if args.since:
            params["cursor"] = args.since
        return _request(args.base_url, "GET", "/api/events?" + urlencode(params), token=args.token)[1]
    if args.command == "events" and args.events_command == "deliveries":
        return {"items": _request(
            args.base_url, "GET", f"/api/events/{quote(args.event_id, safe='')}/deliveries", token=args.token
        )[1]}
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
        if args.project:
            params["project"] = args.project
        if args.source_agent:
            params["source_agent"] = args.source_agent
        for tag in args.tags:
            params.setdefault("tag", []).append(tag)
        if args.cursor:
            params["cursor"] = args.cursor
        return _request(args.base_url, "GET", "/api/search?" + urlencode(params, doseq=True), token=args.token)[1]
    if args.command == "versions":
        if len(args.versions_args) == 1:
            artifact_id = args.versions_args[0]
        elif len(args.versions_args) == 2 and args.versions_args[0] == "list":
            artifact_id = args.versions_args[1]
        else:
            raise CLIError("use: artifactctl versions ARTIFACT_ID (or versions list ARTIFACT_ID)")
        return _request(
            args.base_url,
            "GET",
            f"/api/artifacts/{quote(artifact_id, safe='')}/versions",
            token=args.token,
        )[1]
    if args.command == "audit" and args.audit_command == "list":
        params = {"limit": args.limit}
        if args.resource_id:
            params["resource_id"] = args.resource_id
        if args.agent_id:
            params["agent_id"] = args.agent_id
        return _request(args.base_url, "GET", "/api/operations?" + urlencode(params), token=args.token)[1]
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
            None,
            args.agent_id,
            args.agent_run_id,
            args.operation_id,
        )[1]
    if args.command == "rename":
        return _request(
            args.base_url,
            "POST",
            f"/api/artifacts/{quote(args.artifact_id, safe='')}/rename",
            {"title": args.title},
            args.token,
            None,
            args.agent_id,
            args.agent_run_id,
            args.operation_id,
        )[1]
    if args.command == "duplicate":
        return _request(
            args.base_url,
            "POST",
            f"/api/artifacts/{quote(args.artifact_id, safe='')}/duplicate",
            {"created_by": args.created_by},
            args.token,
            None,
            args.agent_id,
            args.agent_run_id,
            args.operation_id,
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
            None,
            args.agent_id,
            args.agent_run_id,
            args.operation_id,
        )[1]
    if args.command == "create":
        content = _read_content(args.content, args.file)
        metadata = _read_metadata(args.metadata_json, args.metadata_file)
        _, result = _request(
            args.base_url,
            "POST",
            "/api/artifacts",
            {"title": args.title, "kind": args.kind, "content": content, "created_by": args.created_by, "metadata": metadata},
            args.token,
            args.idempotency_key,
            args.agent_id,
            args.agent_run_id,
            args.operation_id,
        )
        if args.verify:
            if content is None:
                raise CLIError("create verification requires content or file content")
            return _verify_created_artifact(args, result, content)
        return {**result, "artifact_url": None, "artifact_url_reason": "create_verify_required"}
    if args.command == "list":
        params = {}
        if args.query:
            params["query"] = args.query
        if args.include_archived:
            params["include_archived"] = "true"
        if args.kind:
            params["kind"] = args.kind
        if args.project:
            params["project"] = args.project
        if args.source_agent:
            params["source_agent"] = args.source_agent
        for tag in args.tags:
            params.setdefault("tag", []).append(tag)
        path = "/api/artifacts" + ("?" + urlencode(params, doseq=True) if params else "")
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
            "metadata": _read_metadata(args.metadata_json, args.metadata_file),
            "source_comment_id": args.source_comment_id,
        }
        if args.old_str is not None:
            payload["content"] = None
        _, result = _request(
            args.base_url,
            "POST",
            f"/api/artifacts/{quote(args.artifact_id, safe='')}/versions",
            payload,
            args.token,
            args.idempotency_key,
            args.agent_id,
            args.agent_run_id,
            args.operation_id,
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
            args.idempotency_key,
            args.agent_id,
            args.agent_run_id,
            args.operation_id,
        )
        return result
    if args.command == "address":
        _, result = _request(
            args.base_url,
            "POST",
            f"/api/comments/{quote(args.comment_id, safe='')}/events",
            {"status": args.status, "actor": args.actor, "addressed_in_version_id": args.version_id},
            args.token,
            args.idempotency_key,
            args.agent_id,
            args.agent_run_id,
            args.operation_id,
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
        if args.command in {"doctor", "smoke"} and isinstance(result, dict) and result.get("ok") is False:
            return 1
    except CLIError as error:
        print(json.dumps({"error": "cli_error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
