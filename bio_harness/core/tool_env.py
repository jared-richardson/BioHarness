from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

TOOL_REQUIREMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "bcftools": ("bcftools",),
    "bowtie2": ("bowtie2",),
    "bowtie2-build": ("bowtie2-build",),
    "bracken": ("bracken", "est_abundance.py"),
    "bwa": ("bwa", "bwa-mem2"),
    "cellranger": ("cellranger",),
    "cnvkit.py": ("cnvkit.py",),
    "featurecounts": ("featureCounts", "featurecounts"),
    "gatk": ("gatk",),
    "hmmscan": ("hmmscan",),
    "iqtree": ("iqtree", "iqtree2"),
    "java": ("java",),
    "macs2": ("macs2",),
    "python": ("python3", "python"),
    "python3": ("python3",),
    "rmats": ("rmats.py", "rMATS.py", "rmats"),
    "rscript": ("Rscript", "rscript"),
    "scanpy": ("scanpy",),
    "seurat": ("Seurat",),
    "snpeff": ("snpEff", "snpeff"),
    "STAR-Fusion": ("STAR-Fusion", "star-fusion"),
    "star-fusion": ("STAR-Fusion", "star-fusion"),
    "star": ("STAR", "star"),
    "subread": ("subread-align", "subread", "subjunc"),
    "varscan": ("varscan", "VarScan"),
    "vep": ("vep",),
}
PYTHON_REQUIREMENT_MODULES: dict[str, str] = {
    "python": "sys",
    "python3": "sys",
    "scanpy": "scanpy",
}
R_REQUIREMENT_PACKAGES: dict[str, str] = {
    "deseq2": "DESeq2",
    "dexseq": "DEXSeq",
    "edger": "edgeR",
    "limma": "limma",
    "rscript": "base",
    "seurat": "Seurat",
}


def _rscript_candidates() -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate_name in candidate_tool_names("rscript"):
        resolved = shutil.which(candidate_name)
        if resolved and resolved not in seen:
            seen.add(resolved)
            ordered.append(resolved)
        for pixi_dir in pixi_env_bin_dirs():
            candidate = pixi_dir / candidate_name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                rendered = str(candidate)
                if rendered not in seen:
                    seen.add(rendered)
                    ordered.append(rendered)
    return ordered


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    ordered: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def pixi_env_bin_dirs() -> list[Path]:
    env_root = PROJECT_ROOT / ".pixi" / "envs"
    candidates: list[Path] = []
    default_dir = env_root / "default" / "bin"
    if default_dir.is_dir():
        candidates.append(default_dir)
    if env_root.is_dir():
        for env_dir in sorted(env_root.iterdir()):
            if not env_dir.is_dir() or env_dir.name == "default":
                continue
            bin_dir = env_dir / "bin"
            if bin_dir.is_dir():
                candidates.append(bin_dir)
    conda_prefix = str(os.getenv("CONDA_PREFIX", "")).strip()
    if conda_prefix:
        candidate = Path(conda_prefix) / "bin"
        if candidate.is_dir():
            candidates.append(candidate)
    return _dedupe_paths(candidates)


def pixi_bin_dir() -> Path | None:
    dirs = pixi_env_bin_dirs()
    return dirs[0] if dirs else None


def pixi_jvm_bin_dirs() -> list[Path]:
    env_root = PROJECT_ROOT / ".pixi" / "envs"
    candidates: list[Path] = []
    default_dir = env_root / "default" / "lib" / "jvm" / "bin"
    if default_dir.is_dir():
        candidates.append(default_dir)
    if env_root.is_dir():
        for env_dir in sorted(env_root.iterdir()):
            if not env_dir.is_dir() or env_dir.name == "default":
                continue
            jvm_dir = env_dir / "lib" / "jvm" / "bin"
            if jvm_dir.is_dir():
                candidates.append(jvm_dir)
    return _dedupe_paths(candidates)


def pixi_jvm_bin_dir() -> Path | None:
    dirs = pixi_jvm_bin_dirs()
    return dirs[0] if dirs else None


def shell_path_prefix(*tool_names: str) -> str:
    """Build a deterministic PATH prefix covering shared Pixi tool bins.

    This is intended for shell-rendered skill wrappers whose primary binary may
    spawn helper executables from sibling Pixi environments.
    """

    candidates: list[Path] = []
    for name in tool_names:
        resolved = which_with_pixi(name)
        if not resolved:
            continue
        candidates.append(Path(resolved).expanduser().resolve().parent)
    candidates.extend(path.expanduser().resolve() for path in pixi_env_bin_dirs())
    candidates.extend(path.expanduser().resolve() for path in pixi_jvm_bin_dirs())
    rendered = [str(path) for path in _dedupe_paths(candidates)]
    return ":".join(rendered)


def candidate_tool_names(name: str) -> tuple[str, ...]:
    token = str(name or "").strip()
    if not token:
        return ()
    aliases = TOOL_REQUIREMENT_ALIASES.get(token, ())
    if aliases:
        return aliases
    return (token,)


def python_requirement_available(name: str) -> bool:
    token = str(name or "").strip()
    if not token:
        return False
    module_name = PYTHON_REQUIREMENT_MODULES.get(token)
    if module_name is None:
        return False
    if module_name == "sys":
        return True
    return importlib.util.find_spec(module_name) is not None


def r_requirement_available(name: str) -> bool:
    token = str(name or "").strip()
    package_name = R_REQUIREMENT_PACKAGES.get(token)
    if package_name is None:
        return False
    return rscript_for_requirement(name) is not None


def rscript_for_requirement(name: str) -> str | None:
    token = str(name or "").strip()
    package_name = R_REQUIREMENT_PACKAGES.get(token)
    if package_name is None:
        return None
    rscript_paths = _rscript_candidates()
    if not rscript_paths:
        return None
    if package_name == "base":
        return rscript_paths[0]
    for rscript_path in rscript_paths:
        try:
            completed = subprocess.run(
                [rscript_path, "-e", f"cat(requireNamespace('{package_name}', quietly=TRUE))"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
        except Exception:
            continue
        if completed.returncode == 0 and completed.stdout.strip().lower() == "true":
            return rscript_path
    return None


def requirement_available(name: str) -> bool:
    from bio_harness.core.tool_launchers import tool_launcher_available

    token = str(name or "").strip()
    if token == "vep":
        if tool_launcher_available("vep"):
            return True
        candidate = _validated_tool_binary("vep")
        if candidate:
            env = dict(os.environ)
            candidate_dir = str(Path(candidate).expanduser().resolve().parent)
            env["PATH"] = candidate_dir + os.pathsep + env.get("PATH", "")
            try:
                completed = subprocess.run(
                    [candidate, "--help"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=20,
                    env=env,
                )
            except Exception:
                return False
            return completed.returncode == 0
        return False
    if tool_launcher_available(name):
        return True
    if which_with_pixi(name):
        return True
    if python_requirement_available(name):
        return True
    if r_requirement_available(name):
        return True
    return False


def ensure_pixi_tooling_on_path() -> None:
    path_text = os.environ.get("PATH", "")
    pieces = path_text.split(os.pathsep) if path_text else []
    additions: list[str] = []
    for candidate in pixi_env_bin_dirs() + pixi_jvm_bin_dirs():
        rendered = str(candidate)
        if rendered not in pieces:
            additions.append(rendered)
    if additions:
        os.environ["PATH"] = os.pathsep.join(additions + pieces) if pieces else os.pathsep.join(additions)


def build_pixi_execution_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Return an execution environment with deterministic Pixi PATH ordering.

    The harness can expose duplicate tool names across multiple Pixi
    environments. Shell execution should always prefer the shared default Pixi
    toolchain before specialty environments, regardless of the inherited PATH
    order from the caller.

    Args:
        environ: Optional base environment. Defaults to ``os.environ``.

    Returns:
        A shallow environment copy whose ``PATH`` starts with the default Pixi
        bins, followed by other Pixi/JVM bins, followed by the remaining
        inherited PATH entries in stable order.
    """

    env = dict(environ or os.environ)
    existing_path = str(env.get("PATH", "") or "")
    existing_parts = [part for part in existing_path.split(os.pathsep) if part]

    ordered_parts: list[str] = []
    seen: set[str] = set()

    def _append(part: str) -> None:
        token = str(part or "").strip()
        if not token or token in seen:
            return
        seen.add(token)
        ordered_parts.append(token)

    for candidate in pixi_env_bin_dirs() + pixi_jvm_bin_dirs():
        _append(str(candidate))
    for part in existing_parts:
        _append(part)

    if ordered_parts:
        env["PATH"] = os.pathsep.join(ordered_parts)
    return env


def which_with_pixi(name: str) -> str | None:
    token = str(name or "").strip()
    if not token:
        return None
    validated = _validated_tool_binary(token)
    if validated:
        return validated
    for candidate_name in candidate_tool_names(token):
        for pixi_dir in pixi_env_bin_dirs():
            candidate = pixi_dir / candidate_name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        resolved = shutil.which(candidate_name)
        if resolved:
            return resolved
    return None


def _validated_tool_binary(name: str) -> str | None:
    token = str(name or "").strip()
    if token != "vep":
        return None
    for candidate_name in candidate_tool_names(token):
        for pixi_dir in pixi_env_bin_dirs():
            candidate = pixi_dir / candidate_name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        resolved = shutil.which(candidate_name)
        if resolved:
            return resolved
    return None
