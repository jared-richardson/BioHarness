"""Bounded environment snapshotting for initial planning.

This module performs one deterministic scan of the local execution
environment before planning. The resulting snapshot is designed for prompt
conditioning and debugging, not for execution-time enforcement.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

import psutil

from bio_harness.core.analysis_spec_data import discover_data_files
from bio_harness.core.benchmark_policy import normalize_benchmark_policy
from bio_harness.core.tool_env import pixi_env_bin_dirs, pixi_jvm_bin_dirs, which_with_pixi

_MAX_DATA_FILES = 20
_MAX_MISSING_TOOLS = 12
_MAX_WORKAROUNDS = 6
_VERSION_TEXT_LIMIT = 120
_SCAN_TIMEOUT_SECONDS = 10

_TOOL_GROUPS: dict[str, tuple[str, ...]] = {
    "alignment": ("bwa", "bowtie2", "minimap2", "STAR", "hisat2"),
    "variant_calling": ("freebayes", "bcftools", "gatk", "varscan"),
    "quantification": ("salmon", "kallisto", "featureCounts"),
    "differential_expression": ("Rscript", "python3"),
    "assembly": ("spades.py", "megahit", "trinity", "flye"),
    "annotation": ("snpEff", "prokka"),
    "metagenomics": ("kraken2", "bracken"),
    "phylogenetics": ("mafft", "iqtree", "iqtree2"),
    "single_cell": ("scanpy",),
    "qc": ("fastqc", "fastp", "multiqc", "cutadapt"),
    "utilities": ("samtools", "bedtools", "bgzip", "tabix", "java"),
}

_PYTHON_MODULE_TOOLS: dict[str, str] = {
    "scanpy": "scanpy",
}

_KNOWN_WORKAROUNDS: tuple[dict[str, str], ...] = (
    {
        "tool": "spades.py",
        "issue": "--careful and --isolate are mutually exclusive",
        "workaround": "Use --careful only; never combine with --isolate.",
    },
    {
        "tool": "snpEff",
        "issue": "Some builds reject plastid codon table names in bacterial annotation paths",
        "workaround": "Use an empty codon-table override when the database does not provide one.",
    },
    {
        "tool": "STAR",
        "issue": "Pixi installations can surface zero-read failures in some environments",
        "workaround": "Verify mapped-read counts and fall back to a non-Pixi STAR binary if necessary.",
    },
    {
        "tool": "gatk",
        "issue": "Java must be discoverable on PATH for launcher-based execution",
        "workaround": "Ensure Pixi JVM bins are added to PATH before running GATK.",
    },
    {
        "tool": "gatk",
        "issue": "Mutect2 and HaplotypeCaller expect read groups in aligned BAMs",
        "workaround": "Inject @RG metadata during alignment when FASTQs do not already provide it.",
    },
)
_KNOWN_BUGS = _KNOWN_WORKAROUNDS


def bootstrap_environment(
    data_root: str | Path | None = None,
    *,
    benchmark_policy: str | None = None,
    analysis_spec: dict[str, Any] | None = None,
    check_versions: bool = False,
) -> dict[str, Any]:
    """Build a bounded snapshot of the execution environment.

    Args:
        data_root: Optional task-local input directory to inventory.
        benchmark_policy: Current benchmark policy label.
        analysis_spec: Optional analysis spec used for advisory context.
        check_versions: Whether to probe executable versions.

    Returns:
        Structured environment snapshot suitable for run-state storage.
    """

    normalized_policy = normalize_benchmark_policy(benchmark_policy)
    tool_paths = _discover_available_tools()
    tool_versions = _discover_tool_versions(tool_paths) if check_versions else {}
    inventory = _discover_data_inventory(data_root)
    workarounds = _applicable_workarounds(
        tool_paths=tool_paths,
        analysis_spec=analysis_spec,
    )
    return {
        "benchmark_policy": normalized_policy,
        "available_tools": tool_paths,
        "tool_groups": {name: list(tools) for name, tools in _TOOL_GROUPS.items()},
        "tool_versions": tool_versions,
        "pixi_bin_dirs": [str(path) for path in pixi_env_bin_dirs()],
        "pixi_jvm_bin_dirs": [str(path) for path in pixi_jvm_bin_dirs()],
        "jvm_available": bool(pixi_jvm_bin_dirs() or shutil.which("java")),
        "data_root": _render_data_root(data_root),
        "data_inventory": inventory,
        "system_resources": _system_resources(),
        "known_workarounds": workarounds,
        "known_bugs": list(workarounds),
    }


def format_bootstrap_for_prompt(snapshot: dict[str, Any]) -> str:
    """Render a compact prompt block from one environment snapshot.

    Args:
        snapshot: Snapshot returned by :func:`bootstrap_environment`.

    Returns:
        Markdown-formatted prompt text. Returns an empty string when the
        snapshot is empty or malformed.
    """

    if not isinstance(snapshot, dict) or not snapshot:
        return ""

    resources = snapshot.get("system_resources", {})
    available_tools = snapshot.get("available_tools", {})
    versions = snapshot.get("tool_versions", {})
    inventory = snapshot.get("data_inventory", [])
    workarounds = snapshot.get("known_workarounds", [])
    grouped_present = _group_present_tools(available_tools)
    missing = sorted(name for name, path in available_tools.items() if not str(path or "").strip())
    if len(missing) > _MAX_MISSING_TOOLS:
        missing = missing[:_MAX_MISSING_TOOLS]

    lines: list[str] = [
        "## Environment Snapshot",
        (
            "**System:** "
            f"{resources.get('platform', '?')} | "
            f"machine={resources.get('machine', '?')} | "
            f"cpus={resources.get('cpu_count', '?')} | "
            f"ram_gb={resources.get('ram_gb', '?')} | "
            f"disk_free_gb={resources.get('disk_free_gb', '?')}"
        ),
        (
            "**Runtime:** "
            f"jvm_available={'yes' if snapshot.get('jvm_available') else 'no'} | "
            f"pixi_bins={len(snapshot.get('pixi_bin_dirs', []) or [])} | "
            f"pixi_jvm_bins={len(snapshot.get('pixi_jvm_bin_dirs', []) or [])}"
        ),
    ]

    if grouped_present:
        lines.append("**Available tools:**")
        for domain, tools in grouped_present.items():
            rendered = []
            for tool in tools:
                version = str(versions.get(tool, "") or "").strip()
                rendered.append(f"{tool} ({version})" if version else tool)
            lines.append(f"- {domain}: {', '.join(rendered)}")
    if missing:
        lines.append(f"Likely unavailable tools: {', '.join(missing)}")

    if inventory:
        data_root = str(snapshot.get("data_root", "") or "").strip()
        if data_root:
            lines.append(f"Data inventory under {data_root}:")
        else:
            lines.append("Data inventory:")
        for entry in inventory[:_MAX_DATA_FILES]:
            relative_path = str(entry.get("relative_path", "") or entry.get("name", "")).strip()
            if relative_path:
                lines.append(f"- {relative_path}")

    if workarounds:
        lines.append("Known tool issues and workarounds:")
        for item in workarounds[:_MAX_WORKAROUNDS]:
            tool = str(item.get("tool", "") or "").strip() or "tool"
            issue = str(item.get("issue", "") or "").strip()
            workaround = str(item.get("workaround", "") or "").strip()
            lines.append(f"- {tool}: {issue} Workaround: {workaround}")

    return "\n".join(line for line in lines if line.strip())


def _discover_available_tools() -> dict[str, str]:
    tool_paths: dict[str, str] = {}
    for tools in _TOOL_GROUPS.values():
        for tool_name in tools:
            if tool_name in tool_paths:
                continue
            tool_paths[tool_name] = _resolve_tool_path(tool_name)
    return dict(sorted(tool_paths.items(), key=lambda item: item[0]))


def _resolve_tool_path(tool_name: str) -> str:
    resolved = which_with_pixi(tool_name) or shutil.which(tool_name)
    if resolved:
        return str(Path(resolved).expanduser().resolve(strict=False))
    module_name = _PYTHON_MODULE_TOOLS.get(tool_name, "")
    if module_name and importlib.util.find_spec(module_name) is not None:
        return f"python_module:{module_name}"
    return ""


def _discover_tool_versions(tool_paths: dict[str, str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for tool_name, tool_path in tool_paths.items():
        if not tool_path or tool_path.startswith("python_module:"):
            continue
        version = _tool_version(tool_path)
        if version:
            versions[tool_name] = version
    return versions


def _tool_version(tool_path: str) -> str:
    for flag in ("--version", "-version", "-v"):
        try:
            completed = subprocess.run(
                [tool_path, flag],
                capture_output=True,
                text=True,
                timeout=_SCAN_TIMEOUT_SECONDS,
                check=False,
            )
        except Exception:
            continue
        output = str(completed.stdout or completed.stderr or "").strip()
        if not output:
            continue
        first_line = next((line.strip() for line in output.splitlines() if line.strip()), "")
        if first_line:
            return first_line[:_VERSION_TEXT_LIMIT]
    return ""


def _discover_data_inventory(data_root: str | Path | None) -> list[dict[str, str]]:
    if data_root is None:
        return []
    root = Path(data_root).expanduser().resolve(strict=False)
    if not root.is_dir():
        return []

    inventory: list[dict[str, str]] = []
    for entry in discover_data_files(root, max_files=_MAX_DATA_FILES):
        path_text = str(entry.get("path", "") or "").strip()
        name = str(entry.get("name", "") or "").strip()
        if not path_text or not name:
            continue
        resolved = Path(path_text).expanduser().resolve(strict=False)
        try:
            relative_path = str(resolved.relative_to(root))
        except ValueError:
            relative_path = name
        inventory.append(
            {
                "name": name,
                "relative_path": relative_path,
            }
        )
    return inventory


def _render_data_root(data_root: str | Path | None) -> str:
    if data_root is None:
        return ""
    root = Path(data_root).expanduser().resolve(strict=False)
    if not root.exists():
        return str(root.name or root)
    return str(root.name or root)


def _system_resources() -> dict[str, Any]:
    virtual_memory = psutil.virtual_memory()
    disk_usage = shutil.disk_usage(Path.cwd())
    return {
        "platform": platform.system(),
        "machine": platform.machine(),
        "cpu_count": int(os.cpu_count() or 0),
        "ram_gb": round(float(virtual_memory.total) / float(1024**3), 1),
        "disk_free_gb": round(float(disk_usage.free) / float(1024**3), 1),
    }


def _applicable_workarounds(
    *,
    tool_paths: dict[str, str],
    analysis_spec: dict[str, Any] | None,
) -> list[dict[str, str]]:
    available_names = {
        str(name).strip().lower()
        for name, path in tool_paths.items()
        if str(path or "").strip()
    }
    analysis_type = (
        str((analysis_spec or {}).get("analysis_type", "") or "").strip().lower()
        if isinstance(analysis_spec, dict)
        else ""
    )
    workarounds: list[dict[str, str]] = []
    for item in _KNOWN_WORKAROUNDS:
        tool_name = str(item.get("tool", "") or "").strip().lower()
        if tool_name not in available_names:
            continue
        if tool_name == "gatk" and analysis_type and "variant" not in analysis_type:
            continue
        workarounds.append(dict(item))
        if len(workarounds) >= _MAX_WORKAROUNDS:
            break
    return workarounds


def _group_present_tools(available_tools: dict[str, str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for domain, tools in _TOOL_GROUPS.items():
        present = [tool for tool in tools if str(available_tools.get(tool, "") or "").strip()]
        if present:
            grouped[domain] = present
    return grouped


__all__ = [
    "bootstrap_environment",
    "format_bootstrap_for_prompt",
]
