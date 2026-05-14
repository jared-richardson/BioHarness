from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from bio_harness.core.isolated_tool_recipes import load_isolated_tool_recipes, setup_isolated_tools_for_missing
from bio_harness.core.tool_env import requirement_available


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_INDEX_PATH = PROJECT_ROOT / "bio_harness" / "skills" / "definitions" / "index.json"
VENV_REQUIREMENTS_PATH = PROJECT_ROOT / "requirements" / "venv-core.txt"

PIXI_ENVIRONMENT_TOOLS: dict[str, tuple[str, ...]] = {
    "reports": ("multiqc", "quarto"),
    "alignment-extra": ("bowtie2", "bowtie2-build", "hisat2", "kallisto", "stringtie"),
    "variant-extra": ("freebayes",),
    "r-bulk": ("edger", "limma"),
    "r-splicing": ("dexseq",),
    "r-singlecell": ("seurat",),
    "specialty-general": (
        "bismark",
        "bracken",
        "hmmscan",
        "macs2",
    ),
    "specialty-assembly": ("trinity",),
    "specialty-annotation": ("vep",),
}
MANUAL_TOOL_NOTES: dict[str, str] = {
    "cellranger": "Install Cell Ranger manually from 10x Genomics because it is not distributed through the configured pixi channels.",
    "majiq": "Install MAJIQ manually because it is not currently available in the configured pixi channels.",
    "mixcr": "Install MiXCR manually because it is not currently available in the configured pixi channels.",
}


@dataclass(frozen=True)
class BootstrapCommand:
    label: str
    argv: tuple[str, ...]


def _pixi_command_available() -> bool:
    return shutil.which("pixi") is not None


def probe_llm_backend(**kwargs: Any) -> dict[str, Any]:
    probe_fn = getattr(import_module("bio_harness.core.llm_backend_probe"), "probe_llm_backend")
    return probe_fn(**kwargs)


def _load_skill_rows() -> list[dict[str, Any]]:
    payload = json.loads(SKILL_INDEX_PATH.read_text(encoding="utf-8"))
    rows = payload.get("skills", [])
    return [row for row in rows if isinstance(row, dict)]


def skill_requirements_by_name() -> dict[str, tuple[str, ...]]:
    mapping: dict[str, tuple[str, ...]] = {}
    for row in _load_skill_rows():
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        tools = tuple(
            str(tool).strip()
            for tool in row.get("tools_required", []) or []
            if str(tool).strip()
        )
        mapping[name] = tools
    return mapping


def required_tools_for_skills(skill_names: list[str] | tuple[str, ...]) -> list[str]:
    requirements = skill_requirements_by_name()
    ordered: list[str] = []
    seen: set[str] = set()
    for skill_name in skill_names:
        for tool_name in requirements.get(str(skill_name).strip(), ()):
            if tool_name in seen:
                continue
            seen.add(tool_name)
            ordered.append(tool_name)
    return ordered


def pixi_environment_names_for_tools(tool_names: list[str] | tuple[str, ...]) -> list[str]:
    requested = {str(tool).strip() for tool in tool_names if str(tool).strip()}
    selected: list[str] = []
    for env_name, supported_tools in PIXI_ENVIRONMENT_TOOLS.items():
        if requested.intersection(supported_tools):
            selected.append(env_name)
    return selected


def manual_only_tools(tool_names: list[str] | tuple[str, ...]) -> list[str]:
    return sorted(
        {
            str(tool).strip()
            for tool in tool_names
            if str(tool).strip() in MANUAL_TOOL_NOTES
        }
    )


def isolated_recipe_tools(tool_names: list[str] | tuple[str, ...]) -> list[str]:
    supported = set(load_isolated_tool_recipes().keys())
    return sorted(
        {
            str(tool).strip()
            for tool in tool_names
            if str(tool).strip() in supported
        }
    )


def build_tool_installation_plan(
    *,
    tool_names: list[str] | tuple[str, ...] | None = None,
    skill_names: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    requested_tools = list(tool_names or [])
    if skill_names:
        requested_tools.extend(required_tools_for_skills(list(skill_names)))
    normalized_tools: list[str] = []
    seen: set[str] = set()
    for tool_name in requested_tools:
        token = str(tool_name).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized_tools.append(token)

    already_available = sorted(tool for tool in normalized_tools if requirement_available(tool))
    unresolved = [tool for tool in normalized_tools if tool not in already_available]
    isolated = isolated_recipe_tools(unresolved)
    manual = manual_only_tools([tool for tool in unresolved if tool not in set(isolated)])
    pixi_installable = sorted(tool for tool in unresolved if tool not in set(manual) | set(isolated))
    pixi_envs = pixi_environment_names_for_tools(pixi_installable)

    return {
        "requested_skills": list(skill_names or []),
        "requested_tools": normalized_tools,
        "already_available_tools": already_available,
        "pixi_installable_missing_tools": pixi_installable,
        "isolated_recipe_missing_tools": isolated,
        "pixi_environments": pixi_envs,
        "manual_install_required_tools": manual,
        "manual_install_notes": {tool: MANUAL_TOOL_NOTES[tool] for tool in manual},
    }


def bootstrap_commands(
    *,
    project_root: str | Path,
    python_bin: str,
    venv_path: str | Path,
    install_python: bool,
    install_pixi: bool,
    pixi_environments: list[str] | tuple[str, ...],
) -> list[BootstrapCommand]:
    root = Path(project_root).expanduser().resolve()
    venv_dir = Path(venv_path).expanduser()
    if not venv_dir.is_absolute():
        venv_dir = (root / venv_dir).resolve()
    commands: list[BootstrapCommand] = []
    if install_python:
        venv_python = venv_dir / "bin" / "python"
        commands.extend(
            [
                BootstrapCommand(
                    label="create_venv",
                    argv=(python_bin, "-m", "venv", str(venv_dir)),
                ),
                BootstrapCommand(
                    label="upgrade_pip",
                    argv=(
                        str(venv_python),
                        "-m",
                        "pip",
                        "install",
                        "--upgrade",
                        "pip",
                        "setuptools",
                        "wheel",
                    ),
                ),
                BootstrapCommand(
                    label="install_venv_requirements",
                    argv=(
                        str(venv_python),
                        "-m",
                        "pip",
                        "install",
                        "-r",
                        str(VENV_REQUIREMENTS_PATH),
                    ),
                ),
                BootstrapCommand(
                    label="install_editable_package",
                    argv=(
                        str(venv_python),
                        "-m",
                        "pip",
                        "install",
                        "-e",
                        str(root),
                        "--no-deps",
                    ),
                ),
            ]
        )
    if install_pixi:
        commands.append(
            BootstrapCommand(
                label="install_pixi_default",
                argv=("pixi", "install", "--manifest-path", str(root / "pixi.toml")),
            )
        )
        for env_name in pixi_environments:
            commands.append(
                BootstrapCommand(
                    label=f"install_pixi_{env_name}",
                    argv=(
                        "pixi",
                        "install",
                        "--manifest-path",
                        str(root / "pixi.toml"),
                        "--environment",
                        str(env_name),
                    ),
                )
            )
    return commands


def run_bootstrap_commands(
    commands: list[BootstrapCommand],
    *,
    cwd: str | Path,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        row: dict[str, Any] = {"label": command.label, "argv": list(command.argv)}
        if dry_run:
            row["returncode"] = 0
            row["dry_run"] = True
            results.append(row)
            continue
        try:
            completed = subprocess.run(
                list(command.argv),
                cwd=Path(cwd).expanduser().resolve(),
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            missing_command = str(command.argv[0]).strip()
            stderr_tail = f"Command not found: {missing_command}"
            if missing_command == "pixi":
                stderr_tail = (
                    "pixi command not found on PATH; install pixi or rerun with "
                    "--skip-pixi for a Python-only bootstrap."
                )
            row.update(
                {
                    "returncode": 127,
                    "stdout_tail": "",
                    "stderr_tail": stderr_tail,
                    "status": "command_not_found",
                    "missing_command": missing_command,
                    "exception_class": exc.__class__.__name__,
                }
            )
            results.append(row)
            break
        row.update(
            {
                "returncode": completed.returncode,
                "stdout_tail": "\n".join(completed.stdout.strip().splitlines()[-10:]),
                "stderr_tail": "\n".join(completed.stderr.strip().splitlines()[-10:]),
            }
        )
        results.append(row)
        if completed.returncode != 0:
            break
    return results


def _requested_tool_status(tool_names: list[str] | tuple[str, ...]) -> dict[str, bool]:
    return {
        str(tool_name): requirement_available(str(tool_name))
        for tool_name in tool_names
        if str(tool_name).strip()
    }


def bootstrap_bioharness_environment(
    *,
    project_root: str | Path = PROJECT_ROOT,
    python_bin: str = sys.executable,
    venv_path: str | Path = ".venv",
    tool_names: list[str] | tuple[str, ...] | None = None,
    skill_names: list[str] | tuple[str, ...] | None = None,
    install_python: bool = True,
    install_pixi: bool = True,
    install_isolated: bool = True,
    install_all_known_pixi_envs: bool = False,
    dry_run: bool = False,
    probe_llm_backend_status: bool = False,
    llm_backend: str | None = None,
    model_name: str | None = None,
    host: str | None = None,
    llm_probe_text: bool = False,
    llm_probe_plan: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    pixi_command_available = _pixi_command_available()
    install_plan = build_tool_installation_plan(
        tool_names=list(tool_names or []),
        skill_names=list(skill_names or []),
    )
    pixi_envs = list(PIXI_ENVIRONMENT_TOOLS.keys()) if install_all_known_pixi_envs else list(install_plan["pixi_environments"])
    commands = bootstrap_commands(
        project_root=root,
        python_bin=python_bin,
        venv_path=venv_path,
        install_python=install_python,
        install_pixi=install_pixi,
        pixi_environments=pixi_envs,
    )
    command_results = run_bootstrap_commands(commands, cwd=root, dry_run=dry_run)
    commands_ok = all(int(row.get("returncode", 1)) == 0 for row in command_results)
    isolated_report = {
        "reports": [],
        "resolved_tools": [],
        "unresolved_tools": [],
        "success": True,
    }
    isolated_ok = True
    if install_isolated and install_plan["isolated_recipe_missing_tools"]:
        isolated_report = setup_isolated_tools_for_missing(
            install_plan["isolated_recipe_missing_tools"],
            config_path=root / "workspace" / "tool_launchers.json",
            env_root=root / ".tool-envs",
            install=not dry_run,
            dry_run=dry_run,
        )
        isolated_ok = bool(isolated_report.get("success", False))
    llm_report = None
    warnings: list[str] = []
    if install_pixi and not pixi_command_available:
        warnings.append(
            "pixi command unavailable: install pixi to provision the repo-managed "
            "toolchain, or rerun with --skip-pixi for Python-only setup."
        )
    if probe_llm_backend_status:
        llm_report = probe_llm_backend(
            llm_backend=llm_backend,
            model_name=model_name,
            host=host,
            probe_text=llm_probe_text,
            probe_plan=llm_probe_plan,
        )
        if not bool(llm_report.get("available", False)):
            warnings.append(
                "llm backend unavailable: "
                + str(llm_report.get("message", "") or "backend probe failed")
            )

    final_tool_status = _requested_tool_status(install_plan["requested_tools"])
    remaining_missing_tools = [
        tool_name
        for tool_name, available in final_tool_status.items()
        if not available
    ]
    if not dry_run and remaining_missing_tools:
        warnings.append(
            "requested tools still unavailable after bootstrap: "
            + ", ".join(remaining_missing_tools)
        )

    success = (
        commands_ok
        and isolated_ok
        and not bool(install_plan["manual_install_required_tools"])
        and (dry_run or not bool(remaining_missing_tools))
    )
    return {
        "project_root": str(root),
        "venv_path": str((root / venv_path).resolve() if not Path(venv_path).is_absolute() else Path(venv_path).resolve()),
        "requirements_file": str(VENV_REQUIREMENTS_PATH),
        "install_plan": install_plan,
        "pixi_command_available": pixi_command_available,
        "pixi_command_missing": install_pixi and not pixi_command_available,
        "pixi_environments_requested": pixi_envs,
        "commands": command_results,
        "isolated_tool_setup": isolated_report,
        "post_install_verification_performed": not dry_run,
        "final_tool_status": final_tool_status,
        "remaining_missing_tools": remaining_missing_tools,
        "llm_backend": llm_report,
        "warnings": warnings,
        "success": success,
    }
