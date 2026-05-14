from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from bio_harness.core.tool_launchers import launcher_config_path, load_tool_launchers, refresh_tool_launchers


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE_PATH = PROJECT_ROOT / "bio_harness" / "capabilities" / "isolated_tool_recipes.json"


@lru_cache(maxsize=1)
def load_isolated_tool_recipes(recipe_path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = recipe_path or DEFAULT_RECIPE_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("tools", {}) if isinstance(payload, dict) else {}
    if not isinstance(rows, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for raw_name, raw_spec in rows.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_spec, dict):
            continue
        spec = dict(raw_spec)
        spec.setdefault("launcher_name", name)
        out[name] = spec
    return out


def refresh_isolated_tool_recipes() -> None:
    load_isolated_tool_recipes.cache_clear()


def isolated_tool_recipe(tool_name: str, recipe_path: Path | None = None) -> dict[str, Any] | None:
    token = str(tool_name or "").strip()
    if not token:
        return None
    rows = load_isolated_tool_recipes(recipe_path)
    if token in rows:
        return dict(rows[token])
    for name, spec in rows.items():
        aliases = [str(x).strip() for x in spec.get("aliases", []) if str(x).strip()]
        if token in aliases:
            merged = dict(spec)
            merged.setdefault("launcher_name", name)
            return merged
    return None


def _load_launcher_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "tools": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("tool launcher config must be a JSON object")
    payload.setdefault("version", 1)
    payload.setdefault("tools", {})
    if not isinstance(payload["tools"], dict):
        raise ValueError("tool launcher config 'tools' must be an object")
    return payload


def _write_launcher_payload(path: Path, payload: dict[str, Any], *, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    refresh_tool_launchers()


def _resolve_external_binary_path(spec: dict[str, Any], explicit_binary: str | Path | None = None) -> str:
    if explicit_binary is not None:
        return str(Path(explicit_binary).expanduser().resolve())
    for env_name in [str(x).strip() for x in spec.get("env_vars", []) if str(x).strip()]:
        candidate = str(os.getenv(env_name, "")).strip()
        if candidate:
            return str(Path(candidate).expanduser().resolve())
    launcher_name = str(spec.get("launcher_name", "")).strip()
    current = load_tool_launchers().get(launcher_name, {})
    argv = current.get("argv", [])
    if isinstance(argv, list) and argv:
        return str(argv[0]).strip()
    return ""


def _run_commands(commands: list[list[str]], *, dry_run: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for argv in commands:
        row: dict[str, Any] = {"argv": argv}
        if dry_run:
            row["returncode"] = 0
            row["dry_run"] = True
            rows.append(row)
            continue
        completed = subprocess.run(argv, capture_output=True, text=True, check=False)
        row.update(
            {
                "returncode": completed.returncode,
                "stdout_tail": "\n".join(completed.stdout.strip().splitlines()[-10:]),
                "stderr_tail": "\n".join(completed.stderr.strip().splitlines()[-10:]),
            }
        )
        rows.append(row)
        if completed.returncode != 0:
            break
    return rows


def _render_docker_wrapper_script(*, image: str, tool_binary: str, platform: str | None) -> str:
    platform_flag = (
        f'declare -a PLATFORM_FLAG=(--platform "{platform}")'
        if platform
        else "declare -a PLATFORM_FLAG=()"
    )
    return f"""#!/usr/bin/env bash
set -euo pipefail

IMAGE="{image}"
TOOL_BINARY="{tool_binary}"
{platform_flag}

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for $TOOL_BINARY launcher" >&2
  exit 127
fi

docker_supports_platform_flag() {{
  [ "${{#PLATFORM_FLAG[@]}}" -gt 0 ] || return 1
  local api_version=""
  api_version="$(docker version --format '{{{{.Server.APIVersion}}}}' 2>/dev/null || true)"
  [ -n "$api_version" ] || return 1
  python3 - "$api_version" <<'PY'
import sys

raw = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
parts = []
for token in raw.split("."):
    digits = "".join(ch for ch in token if ch.isdigit())
    if not digits:
        break
    parts.append(int(digits))
while len(parts) < 2:
    parts.append(0)
sys.exit(0 if tuple(parts[:2]) >= (1, 41) else 1)
PY
}}

cwd="$(pwd -P)"
mounts=()
mounted=":"

add_mount() {{
  local raw="$1"
  [ -n "$raw" ] || return 0
  local resolved=""
  if [ -e "$raw" ]; then
    resolved="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$raw" 2>/dev/null || true)"
  fi
  add_mount_dir() {{
    local probe="$1"
    [ -n "$probe" ] || return 0
    while [ ! -e "$probe" ] && [ "$probe" != "/" ]; do
      probe="$(dirname "$probe")"
    done
    local mount_dir="$probe"
    if [ -f "$mount_dir" ]; then
      mount_dir="$(dirname "$mount_dir")"
    fi
    if [ ! -d "$mount_dir" ]; then
      return 0
    fi
    mount_dir="$(cd "$mount_dir" && pwd -P)"
    case "$mounted" in
      *":$mount_dir:"*) return 0 ;;
    esac
    mounts+=(-v "$mount_dir:$mount_dir")
    mounted="${{mounted}}$mount_dir:"
  }}
  local probe="$raw"
  add_mount_dir "$probe"
  if [ -n "$resolved" ] && [ "$resolved" != "$raw" ]; then
    add_mount_dir "$resolved"
  fi
}}

add_mount "$cwd"
for arg in "$@"; do
  case "$arg" in
    /*) add_mount "$arg" ;;
  esac
done

docker_args=(run --rm -u "$(id -u):$(id -g)" -w "$cwd")
if docker_supports_platform_flag; then
  docker_args+=("${{PLATFORM_FLAG[@]}}")
fi
docker_args+=("${{mounts[@]}}")
docker_args+=("$IMAGE" "$TOOL_BINARY")

exec docker "${{docker_args[@]}}" "$@"
"""


def _resolve_recipe_path(path_text: str, *, base: Path) -> Path:
    candidate = Path(str(path_text or "").strip())
    if not str(candidate):
        return base
    if candidate.is_absolute():
        return candidate.expanduser().resolve()
    return (base / candidate).expanduser().resolve()


def setup_isolated_tool(
    tool_name: str,
    *,
    recipe_path: Path | None = None,
    config_path: Path | None = None,
    binary_path: str | Path | None = None,
    env_root: str | Path | None = None,
    env_path: str | Path | None = None,
    install: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    spec = isolated_tool_recipe(tool_name, recipe_path)
    if spec is None:
        return {"tool": str(tool_name), "success": False, "reason": "no_isolated_tool_recipe"}

    launcher_name = str(spec.get("launcher_name", tool_name)).strip() or str(tool_name).strip()
    mode = "external_binary" if binary_path is not None else str(spec.get("mode", "")).strip()
    launcher_path = (config_path or launcher_config_path()).expanduser().resolve()
    payload = _load_launcher_payload(launcher_path)
    tools = dict(payload.get("tools", {}))
    command_results: list[dict[str, Any]] = []

    if mode == "pip_venv":
        if env_path is not None:
            venv_path = Path(env_path).expanduser().resolve()
        else:
            root = Path(env_root).expanduser().resolve() if env_root is not None else PROJECT_ROOT / str(spec.get("default_env_root", ".tool-envs"))
            env_name = str(spec.get("default_env_name", launcher_name)).strip() or launcher_name
            venv_path = root / env_name
        python_name = str(spec.get("python_bin_name", "python")).strip() or "python"
        venv_python = venv_path / "bin" / python_name
        packages = [str(x).strip() for x in spec.get("packages", []) if str(x).strip()]
        commands = [
            [sys.executable, "-m", "venv", str(venv_path)],
            [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
            [str(venv_python), "-m", "pip", "install", *packages],
        ]
        command_results = _run_commands(commands, dry_run=dry_run or not install)
        if any(int(row.get("returncode", 1)) != 0 for row in command_results):
            return {
                "tool": launcher_name,
                "recipe": spec,
                "config_path": str(launcher_path),
                "commands": command_results,
                "success": False,
                "reason": "pip_venv_install_failed",
            }
        tool_relpath = str(spec.get("tool_relpath", "")).strip()
        if not tool_relpath:
            return {"tool": launcher_name, "success": False, "reason": "missing_tool_relpath"}
        tools[launcher_name] = {"argv": [str((venv_path / tool_relpath).resolve())]}
    elif mode == "docker_wrapper":
        if env_path is not None:
            wrapper_root = Path(env_path).expanduser().resolve()
        else:
            root = Path(env_root).expanduser().resolve() if env_root is not None else PROJECT_ROOT / str(spec.get("default_env_root", ".tool-envs"))
            env_name = str(spec.get("default_env_name", launcher_name)).strip() or launcher_name
            wrapper_root = root / env_name
        tool_relpath = str(spec.get("tool_relpath", "")).strip()
        image = str(spec.get("image", "")).strip()
        tool_binary = str(spec.get("tool_binary", launcher_name)).strip() or launcher_name
        platform = str(spec.get("platform", "")).strip() or None
        if not tool_relpath or not image:
            return {"tool": launcher_name, "success": False, "reason": "missing_docker_wrapper_fields"}
        wrapper_path = wrapper_root / tool_relpath
        command_results = _run_commands([["docker", "pull", image]], dry_run=dry_run or not install)
        if any(int(row.get("returncode", 1)) != 0 for row in command_results):
            return {
                "tool": launcher_name,
                "recipe": spec,
                "config_path": str(launcher_path),
                "commands": command_results,
                "success": False,
                "reason": "docker_wrapper_pull_failed",
            }
        if not dry_run:
            wrapper_path.parent.mkdir(parents=True, exist_ok=True)
            wrapper_path.write_text(
                _render_docker_wrapper_script(image=image, tool_binary=tool_binary, platform=platform),
                encoding="utf-8",
            )
            wrapper_path.chmod(0o755)
        tools[launcher_name] = {"argv": [str(wrapper_path)]}
    elif mode == "docker_build_wrapper":
        if env_path is not None:
            wrapper_root = Path(env_path).expanduser().resolve()
        else:
            root = Path(env_root).expanduser().resolve() if env_root is not None else PROJECT_ROOT / str(spec.get("default_env_root", ".tool-envs"))
            env_name = str(spec.get("default_env_name", launcher_name)).strip() or launcher_name
            wrapper_root = root / env_name
        tool_relpath = str(spec.get("tool_relpath", "")).strip()
        tool_binary = str(spec.get("tool_binary", launcher_name)).strip() or launcher_name
        platform = str(spec.get("platform", "")).strip() or None
        image = str(spec.get("image", "")).strip()
        dockerfile_relpath = str(spec.get("dockerfile_relpath", "")).strip()
        context_relpath = str(spec.get("context_relpath", "")).strip() or "."
        if not tool_relpath or not tool_binary or not image or not dockerfile_relpath:
            return {"tool": launcher_name, "success": False, "reason": "missing_docker_build_wrapper_fields"}
        dockerfile_path = _resolve_recipe_path(dockerfile_relpath, base=PROJECT_ROOT)
        context_path = _resolve_recipe_path(context_relpath, base=PROJECT_ROOT)
        wrapper_path = wrapper_root / tool_relpath
        build_cmd = ["docker", "build", "-f", str(dockerfile_path), "-t", image]
        if platform:
            build_cmd.extend(["--platform", platform])
        build_cmd.append(str(context_path))
        command_results = _run_commands([build_cmd], dry_run=dry_run or not install)
        if any(int(row.get("returncode", 1)) != 0 for row in command_results):
            return {
                "tool": launcher_name,
                "recipe": spec,
                "config_path": str(launcher_path),
                "commands": command_results,
                "success": False,
                "reason": "docker_build_wrapper_build_failed",
            }
        if not dry_run:
            wrapper_path.parent.mkdir(parents=True, exist_ok=True)
            wrapper_path.write_text(
                _render_docker_wrapper_script(image=image, tool_binary=tool_binary, platform=platform),
                encoding="utf-8",
            )
            wrapper_path.chmod(0o755)
        tools[launcher_name] = {"argv": [str(wrapper_path)]}
    elif mode == "external_binary":
        resolved = _resolve_external_binary_path(spec, explicit_binary=binary_path)
        if not resolved:
            return {
                "tool": launcher_name,
                "recipe": spec,
                "config_path": str(launcher_path),
                "success": False,
                "reason": "external_binary_path_required",
            }
        tools[launcher_name] = {"argv": [resolved]}
    else:
        return {"tool": launcher_name, "success": False, "reason": "unsupported_isolated_tool_mode"}

    payload["tools"] = tools
    _write_launcher_payload(launcher_path, payload, dry_run=dry_run)
    return {
        "tool": launcher_name,
        "recipe": spec,
        "config_path": str(launcher_path),
        "config": payload,
        "commands": command_results,
        "success": True,
    }


def setup_isolated_tools_for_missing(
    tool_names: list[str] | tuple[str, ...],
    *,
    recipe_path: Path | None = None,
    config_path: Path | None = None,
    env_root: str | Path | None = None,
    install: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    resolved: list[str] = []
    unresolved: list[str] = []
    for tool_name in tool_names:
        token = str(tool_name).strip()
        if not token:
            continue
        report = setup_isolated_tool(
            token,
            recipe_path=recipe_path,
            config_path=config_path,
            env_root=env_root,
            install=install,
            dry_run=dry_run,
        )
        reports.append(report)
        if report.get("success", False):
            resolved.append(token)
        else:
            unresolved.append(token)
    return {
        "reports": reports,
        "resolved_tools": resolved,
        "unresolved_tools": unresolved,
        "success": not unresolved,
    }
