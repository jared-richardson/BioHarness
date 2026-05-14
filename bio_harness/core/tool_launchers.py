from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

from bio_harness.core.tool_env import which_with_pixi


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LAUNCHER_PATH = PROJECT_ROOT / "workspace" / "tool_launchers.json"
SUPPORTED_ISOLATED_TOOLS = {"cnvkit.py", "prokka", "STAR-Fusion", "star-fusion", "vep"}


def launcher_config_path() -> Path:
    override = str(os.getenv("BIO_HARNESS_TOOL_LAUNCHERS_PATH", "")).strip()
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_LAUNCHER_PATH


@lru_cache(maxsize=1)
def load_tool_launchers() -> dict[str, dict[str, Any]]:
    path = launcher_config_path()
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("tools", {}) if isinstance(payload, dict) else {}
    if not isinstance(rows, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for raw_name, raw_spec in rows.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_spec, dict):
            continue
        out[name] = dict(raw_spec)
    return out


def refresh_tool_launchers() -> None:
    load_tool_launchers.cache_clear()


def _candidate_specs(tool_name: str) -> list[dict[str, Any]]:
    token = str(tool_name or "").strip()
    if not token:
        return []
    catalog = load_tool_launchers()
    names = [token]
    if token == "STAR-Fusion":
        names.append("star-fusion")
    elif token == "star-fusion":
        names.append("STAR-Fusion")
    return [catalog[name] for name in names if name in catalog]


def tool_launcher_spec(tool_name: str) -> dict[str, Any] | None:
    specs = _candidate_specs(tool_name)
    return dict(specs[0]) if specs else None


def _normalize_argv(values: Any) -> list[str]:
    if isinstance(values, list):
        return [str(v).strip() for v in values if str(v).strip()]
    if isinstance(values, str) and values.strip():
        return shlex.split(values)
    return []


def tool_launcher_argv(tool_name: str) -> list[str] | None:
    spec = tool_launcher_spec(tool_name)
    if not spec:
        return None
    argv = _normalize_argv(spec.get("argv"))
    return argv or None


def _availability_target(spec: dict[str, Any], argv: list[str]) -> str:
    explicit = str(spec.get("availability_check", "")).strip()
    if explicit:
        return explicit
    if not argv:
        return ""
    return str(argv[0]).strip()


def _spec_uses_container(spec: dict[str, Any], argv: list[str]) -> bool:
    runtime = str(spec.get("runtime", "")).strip().lower()
    if runtime in {"docker", "podman", "container"}:
        return True
    if not argv:
        return False
    launcher_path = Path(str(argv[0]).strip()).expanduser()
    if not launcher_path.is_absolute() or not launcher_path.is_file():
        return False
    try:
        content = launcher_path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return any(token in content for token in ("docker ", "exec docker", "podman ", "exec podman"))


def _native_override_command(tool_name: str, spec: dict[str, Any], argv: list[str]) -> str | None:
    native = str(which_with_pixi(tool_name) or "").strip()
    if not native or not _spec_uses_container(spec, argv):
        return None
    if not _command_exists(native):
        return None
    return native


def _command_exists(token: str) -> bool:
    value = str(token or "").strip()
    if not value:
        return False
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.is_file() and os.access(path, os.X_OK)
    return bool(shutil.which(value))


def tool_launcher_available(tool_name: str) -> bool:
    spec = tool_launcher_spec(tool_name)
    if not spec:
        return False
    argv = tool_launcher_argv(tool_name) or []
    native_override = _native_override_command(tool_name, spec, argv)
    if native_override:
        return True
    return _command_exists(_availability_target(spec, argv))


def tool_launcher_command(tool_name: str) -> str | None:
    spec = tool_launcher_spec(tool_name)
    if not spec:
        return None
    argv = tool_launcher_argv(tool_name)
    if not argv:
        return None
    native_override = _native_override_command(tool_name, spec, argv)
    if native_override:
        return shlex.quote(native_override)
    return " ".join(shlex.quote(part) for part in argv)


def tool_launcher_uses_container(tool_name: str) -> bool:
    """Return whether the configured launcher shells out to a container runtime.

    Args:
        tool_name: Tool name to inspect in the launcher catalog.

    Returns:
        True when the configured launcher script appears to invoke Docker or
        Podman, otherwise False.
    """

    spec = tool_launcher_spec(tool_name)
    if not spec:
        return False
    argv = tool_launcher_argv(tool_name) or []
    return _spec_uses_container(spec, argv)


def tool_launcher_guard_expr(tool_name: str) -> str | None:
    spec = tool_launcher_spec(tool_name)
    if not spec:
        return None
    argv = tool_launcher_argv(tool_name) or []
    native_override = _native_override_command(tool_name, spec, argv)
    if native_override:
        return f"[ -x {shlex.quote(native_override)} ]"
    raw_expr = str(spec.get("availability_expr", "")).strip()
    if raw_expr:
        return raw_expr
    target = _availability_target(spec, argv)
    if not target:
        return None
    path = Path(target).expanduser()
    if path.is_absolute():
        return f"[ -x {shlex.quote(str(path))} ]"
    return f"command -v {shlex.quote(target)} >/dev/null 2>&1"


def apply_tool_launcher(command: str, tool_name: str) -> str:
    rendered = str(command or "").strip()
    launcher = tool_launcher_command(tool_name)
    if not rendered or not launcher:
        return rendered
    token = "STAR-Fusion" if str(tool_name).strip() == "star-fusion" else str(tool_name).strip()
    pattern = rf"(?<![A-Za-z0-9_./-]){re.escape(token)}(?![A-Za-z0-9_./-])"
    return re.sub(pattern, launcher, rendered)
