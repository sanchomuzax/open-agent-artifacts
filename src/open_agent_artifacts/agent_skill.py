"""Safe installation of the bundled Open Agent Artifacts agent skill."""

from __future__ import annotations

import hashlib
import importlib.resources
import os
import re
import tempfile
from pathlib import Path
from typing import Mapping


AGENT_SKILL_NAME = "open-agent-artifacts"
SUPPORTED_AGENTS = frozenset({"hermes"})
_RESOURCE = "agent_skills/hermes/SKILL.md"


class AgentSkillInstallError(Exception):
    """The requested skill installation cannot be completed safely."""


def resolve_hermes_home(home: str | Path | None = None, *, environ: Mapping[str, str] | None = None) -> Path:
    """Resolve an explicit home or the active HERMES_HOME profile."""
    environment = os.environ if environ is None else environ
    raw_home = str(home) if home is not None else environment.get("HERMES_HOME")
    resolved = Path(raw_home).expanduser() if raw_home else Path.home() / ".hermes"
    return resolved.resolve()


def _skill_text() -> str:
    try:
        return importlib.resources.files("open_agent_artifacts").joinpath(_RESOURCE).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, TypeError) as error:
        raise AgentSkillInstallError("bundled Hermes skill is missing from the release") from error


def _skill_version(content: str) -> str:
    match = re.search(r"^version:\s*['\"]?([^'\"\s]+)", content, flags=re.MULTILINE)
    if not match:
        raise AgentSkillInstallError("bundled Hermes skill has no version")
    return match.group(1)


def _error_from_os(error: OSError, action: str) -> AgentSkillInstallError:
    return AgentSkillInstallError(f"cannot {action} Hermes skill: {error.strerror or error}")


def install_agent_skill(
    agent: str,
    *,
    home: str | Path | None = None,
    force: bool = False,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Install the bundled skill into a known agent-owned directory.

    Only the Hermes profile selected by ``HERMES_HOME`` (or the default
    ``~/.hermes``) is supported. Existing, different content is never
    overwritten unless ``force`` is explicit.
    """
    if agent not in SUPPORTED_AGENTS:
        supported = ", ".join(sorted(SUPPORTED_AGENTS))
        raise AgentSkillInstallError(f"unsupported agent '{agent}'; supported agents: {supported}")

    hermes_home = resolve_hermes_home(home, environ=environ)
    skills_root = hermes_home / "skills"
    if skills_root.is_symlink():
        raise AgentSkillInstallError(f"refusing symlinked Hermes skills directory: {skills_root}")
    skill_dir = skills_root / AGENT_SKILL_NAME
    target = skill_dir / "SKILL.md"
    content = _skill_text()
    payload = content.encode("utf-8")
    checksum = hashlib.sha256(payload).hexdigest()
    version = _skill_version(content)

    if skill_dir.is_symlink():
        raise AgentSkillInstallError(f"refusing symlinked skill directory: {skill_dir}")
    if target.is_symlink():
        raise AgentSkillInstallError(f"refusing symlinked skill target: {target}")
    if skill_dir.exists() and not skill_dir.is_dir():
        raise AgentSkillInstallError(f"skill target parent is not a directory: {skill_dir}")
    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise _error_from_os(error, "create") from error

    if target.exists():
        if not target.is_file():
            raise AgentSkillInstallError(f"skill target is not a regular file: {target}")
        try:
            existing = target.read_bytes()
        except OSError as error:
            raise _error_from_os(error, "read") from error
        existing_checksum = hashlib.sha256(existing).hexdigest()
        if existing == payload:
            return {
                "agent": agent,
                "target_path": str(target),
                "skill_version": version,
                "sha256": existing_checksum,
                "changed": False,
            }
        if not force:
            raise AgentSkillInstallError(
                f"skill target contains different content: {target}; use --force to replace it"
            )

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=skill_dir, prefix=".SKILL.md.", suffix=".tmp", delete=False
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, target)
    except OSError as error:
        raise _error_from_os(error, "write") from error
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass

    return {
        "agent": agent,
        "target_path": str(target),
        "skill_version": version,
        "sha256": checksum,
        "changed": True,
    }
