"""Safe CLI probing helpers for tool onboarding.

This module centralizes bounded, audit-friendly command discovery for
tool-onboarding flows. It intentionally limits probing to read-only commands
such as ``--help`` and ``--version`` so onboarding can inspect unfamiliar
tools without executing arbitrary workflow logic.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_ALLOWED_LEAF_FLAGS = frozenset(
    {
        "--help",
        "-h",
        "help",
        "--version",
        "-V",
        "version",
    }
)
_HELP_FLAGS = ("--help", "-h", "help")
_VERSION_FLAGS = ("--version", "-V", "version")
_SUBCOMMAND_LINE_RE = re.compile(
    r"^\s{1,8}([A-Za-z][A-Za-z0-9_.-]*)\s{2,}(.+?)\s*$"
)
_SUBCOMMAND_SECTION_MARKERS = (
    "commands:",
    "subcommands:",
    "available commands:",
    "available subcommands:",
)
_SUBCOMMAND_SKIP_TOKENS = {
    "usage",
    "options",
    "flags",
    "commands",
    "subcommands",
    "examples",
    "version",
    "help",
}


@dataclass(frozen=True)
class SafeProbePolicy:
    """Boundaries for read-only onboarding probes.

    Attributes:
        timeout_seconds: Maximum duration for one subprocess probe.
        max_help_chars: Maximum help-text payload retained in memory.
        max_subcommands: Maximum number of parsed subcommands returned.
        max_subcommand_depth: Maximum number of nested subcommand tokens allowed.
    """

    timeout_seconds: int = 15
    max_help_chars: int = 8000
    max_subcommands: int = 24
    max_subcommand_depth: int = 2


@dataclass(frozen=True)
class ProbeObservation:
    """Result of one safe subprocess probe.

    Attributes:
        argv: Command argument vector that was executed.
        exit_code: Process exit code, or `None` when the command did not start.
        stdout: Captured standard output.
        stderr: Captured standard error.
        timed_out: Whether the probe exceeded the timeout budget.
    """

    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


def build_probe_env(tool_name: str = "") -> dict[str, str]:
    """Build an environment that can resolve repo-managed Pixi tools.

    Args:
        tool_name: Optional tool name for future policy-specific customization.

    Returns:
        Environment mapping with the repo Pixi bin prepended when available.
    """

    del tool_name
    env = dict(os.environ)
    path_entries: list[str] = []
    pixi_global_bin = Path.home() / ".pixi" / "bin"
    if pixi_global_bin.is_dir():
        path_entries.append(str(pixi_global_bin))
    candidate = Path(__file__).resolve().parent
    for _ in range(6):
        pixi_bin = candidate / ".pixi" / "envs" / "default" / "bin"
        if pixi_bin.is_dir():
            path_entries.append(str(pixi_bin))
            break
        candidate = candidate.parent
    if path_entries:
        env["PATH"] = os.pathsep.join(path_entries + [env.get("PATH", "")])
    return env


def is_safe_probe_command(
    argv: Sequence[str],
    *,
    policy: SafeProbePolicy | None = None,
) -> bool:
    """Return whether a probe command is allowed in onboarding mode.

    Args:
        argv: Argument vector for the command.
        policy: Optional probe policy override.

    Returns:
        `True` when the command is a read-only probe within policy bounds.
    """

    probe_policy = policy or SafeProbePolicy()
    tokens = [str(token).strip() for token in argv if str(token).strip()]
    if len(tokens) < 2:
        return False
    if tokens[-1] not in _ALLOWED_LEAF_FLAGS:
        return False
    if len(tokens) - 2 > probe_policy.max_subcommand_depth:
        return False
    if tokens[0].startswith("-"):
        return False
    for token in tokens[1:-1]:
        if token.startswith("-"):
            return False
        if token.lower() in {"&&", "||", ";", "|"}:
            return False
    return True


def run_safe_probe_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    policy: SafeProbePolicy | None = None,
    timeout_seconds: int | None = None,
) -> ProbeObservation:
    """Run one safe, read-only subprocess probe.

    Args:
        argv: Argument vector to execute.
        env: Optional environment mapping.
        policy: Optional probe policy override.
        timeout_seconds: Optional timeout override.

    Returns:
        Structured probe observation.

    Raises:
        ValueError: If the command is outside the safe probe policy.
    """

    probe_policy = policy or SafeProbePolicy()
    tokens = tuple(str(token).strip() for token in argv if str(token).strip())
    if not is_safe_probe_command(tokens, policy=probe_policy):
        raise ValueError(f"Unsafe onboarding probe command: {tokens}")

    try:
        result = subprocess.run(
            list(tokens),
            capture_output=True,
            text=True,
            timeout=timeout_seconds or probe_policy.timeout_seconds,
            env=dict(env) if env is not None else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ProbeObservation(
            argv=tokens,
            exit_code=None,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or ""),
            timed_out=True,
        )
    except (FileNotFoundError, OSError):
        return ProbeObservation(
            argv=tokens,
            exit_code=None,
            stdout="",
            stderr="",
            timed_out=False,
        )

    return ProbeObservation(
        argv=tokens,
        exit_code=int(result.returncode),
        stdout=str(result.stdout or ""),
        stderr=str(result.stderr or ""),
        timed_out=False,
    )


def extract_subcommands_from_help(
    help_text: str,
    *,
    policy: SafeProbePolicy | None = None,
) -> list[str]:
    """Extract likely CLI subcommands from help text.

    Args:
        help_text: Raw CLI help output.
        policy: Optional probe policy override.

    Returns:
        Ordered list of likely subcommand names.
    """

    probe_policy = policy or SafeProbePolicy()
    if not str(help_text or "").strip():
        return []

    subcommands: list[str] = []
    in_command_section = False
    for raw_line in str(help_text).splitlines():
        line = raw_line.rstrip()
        lowered = line.strip().lower()
        if lowered in _SUBCOMMAND_SECTION_MARKERS:
            in_command_section = True
            continue
        if in_command_section and not line.strip():
            break
        match = _SUBCOMMAND_LINE_RE.match(line)
        if not match:
            continue
        candidate = match.group(1).strip()
        description = match.group(2).strip()
        if candidate.lower() in _SUBCOMMAND_SKIP_TOKENS:
            continue
        if candidate.startswith("-"):
            continue
        if " " in candidate or "\t" in candidate:
            continue
        if not description:
            continue
        if candidate not in subcommands:
            subcommands.append(candidate)
        if len(subcommands) >= probe_policy.max_subcommands:
            break
    return subcommands


def discover_cli_metadata(
    tool_name: str,
    *,
    timeout: int = 15,
    policy: SafeProbePolicy | None = None,
) -> dict[str, Any]:
    """Collect bounded metadata for a CLI tool.

    Args:
        tool_name: Tool binary name.
        timeout: Timeout for each subprocess probe in seconds.
        policy: Optional probe policy override.

    Returns:
        Dictionary containing help text, version, subcommands, and lightweight
        probe metadata suitable for skill onboarding.
    """

    probe_policy = policy or SafeProbePolicy(timeout_seconds=timeout)
    env = build_probe_env(tool_name)
    executable = shutil.which(tool_name, path=env.get("PATH"))
    tool_cmd = executable or tool_name

    help_text = ""
    observed_help_flags: list[str] = []
    for flag in _HELP_FLAGS:
        observation = run_safe_probe_command(
            [tool_cmd, flag],
            env=env,
            policy=probe_policy,
            timeout_seconds=timeout,
        )
        output = f"{observation.stdout}\n{observation.stderr}".strip()
        if output and len(output) > len(help_text):
            help_text = output
        if output:
            observed_help_flags.append(flag)

    version = ""
    observed_version_flags: list[str] = []
    for flag in _VERSION_FLAGS:
        observation = run_safe_probe_command(
            [tool_cmd, flag],
            env=env,
            policy=probe_policy,
            timeout_seconds=min(timeout, 5),
        )
        output = f"{observation.stdout} {observation.stderr}".strip()
        if output:
            observed_version_flags.append(flag)
        match = re.search(r"(\d+\.\d+[\.\d]*)", output)
        if match:
            version = match.group(1)
            break

    truncated_help = help_text[: probe_policy.max_help_chars]
    supports_dry_run = bool(re.search(r"(?<![A-Za-z0-9_-])--dry-run\b", truncated_help))
    supports_examples = bool(re.search(r"(?<![A-Za-z0-9_-])--examples?\b", truncated_help))
    safe_probe_flags = list(
        dict.fromkeys(
            list(_HELP_FLAGS)
            + list(_VERSION_FLAGS)
            + (["--dry-run"] if supports_dry_run else [])
            + (["--example"] if supports_examples else [])
        )
    )
    return {
        "tool_name": str(tool_name),
        "executable": executable,
        "help_text": truncated_help,
        "version": version,
        "subcommands": extract_subcommands_from_help(truncated_help, policy=probe_policy),
        "supports_dry_run": supports_dry_run,
        "supports_examples": supports_examples,
        "safe_probe_flags": safe_probe_flags,
        "observed_help_flags": observed_help_flags,
        "observed_version_flags": observed_version_flags,
    }


__all__ = [
    "ProbeObservation",
    "SafeProbePolicy",
    "build_probe_env",
    "discover_cli_metadata",
    "extract_subcommands_from_help",
    "is_safe_probe_command",
    "run_safe_probe_command",
]
