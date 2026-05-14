from __future__ import annotations

import shlex
import string
from pathlib import Path

from bio_harness.core.analysis_spec_support import (
    managed_python_command_parts,
    preferred_helper_python_executable,
)
from bio_harness.core.tool_env import rscript_for_requirement

BUNDLED_DESEQ2_WRAPPER = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "deseq2_wrapper.R"
BUNDLED_PYDESEQ2_WRAPPER = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "pydeseq2_wrapper.py"
COUNT_MATRIX_CANDIDATE_NAMES = (
    "counts_matrix.tsv",
    "counts.tsv",
    "gene_counts.txt",
    "featurecounts.txt",
    "count_matrix.tsv",
    "counts.txt",
)
METADATA_CANDIDATE_NAMES = (
    "metadata.tsv",
    "sample_metadata.tsv",
    "sample_sheet.tsv",
    "samples.tsv",
    "coldata.tsv",
)


def _render_template(template: str, kwargs: dict) -> str:
    rendered: dict[str, str] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        rendered[key] = shlex.quote(str(value))
    formatter = string.Formatter()
    field_names = [field_name for _, field_name, _, _ in formatter.parse(template) if field_name]
    missing = [field for field in field_names if field not in rendered]
    if missing:
        missing_args = ", ".join(sorted(set(missing)))
        raise ValueError(f"Missing required parameter(s) for template: {missing_args}")
    return template.format(**rendered).strip()


def _render_shell_parts(parts: list[str]) -> str:
    """Quote and join shell command parts."""

    return " ".join(shlex.quote(str(part)) for part in parts if str(part or "").strip())


def _candidate_roots() -> list[Path]:
    cwd = Path.cwd().resolve()
    workspace_root = cwd / "workspace"
    roots: list[Path] = [
        cwd,
        workspace_root,
        workspace_root / "output",
        workspace_root / "outputs",
        cwd / "output",
        cwd / "outputs",
    ]
    dedup: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(root)
    return dedup


def _scan_candidates(roots: list[Path], names: tuple[str, ...], *, max_scan: int = 5000) -> list[Path]:
    wanted = {name.lower() for name in names}
    hits: list[Path] = []
    scanned = 0
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            scanned += 1
            if scanned > max_scan:
                break
            if not path.is_file():
                continue
            if path.name.lower() in wanted:
                hits.append(path.resolve())
        if scanned > max_scan:
            break
    hits.sort(key=lambda p: (len(str(p)), str(p)))
    return hits


def _resolve_dataset_path(raw_value: str, names: tuple[str, ...]) -> str:
    raw = str(raw_value or "").strip()
    if not raw:
        return raw
    path = Path(raw).expanduser()
    if path.exists():
        return str(path.resolve())

    roots = _candidate_roots()
    basename = path.name.lower()
    ordered_names = list(names)
    if basename and basename not in {name.lower() for name in names}:
        ordered_names = [basename] + ordered_names
    for root in roots:
        if not root.exists():
            continue
        for name in ordered_names:
            candidate = root / name
            if candidate.exists() and candidate.is_file():
                return str(candidate.resolve())
    hits = _scan_candidates(roots, tuple(ordered_names))
    if hits:
        return str(hits[0])
    return raw


def _resolve_output_dir(raw_value: str) -> str:
    raw = str(raw_value or "").strip()
    if not raw:
        return raw
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return raw
    if str(path).startswith("/results/") or str(path) == "/results":
        rel = path.relative_to("/results")
        cwd = Path.cwd().resolve()
        base = (cwd / "workspace" / "output") if (cwd / "workspace").exists() else (cwd / "output")
        mapped = base / rel
        return str(mapped)
    return raw


def deseq2_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    kwargs = dict(kwargs)
    engine = str(kwargs.get("engine", "")).strip().lower()
    script_path = str(kwargs.get("script_path", "")).strip()
    if engine == "pydeseq2":
        kwargs["script_path"] = script_path if script_path else str(BUNDLED_PYDESEQ2_WRAPPER)
    elif (not script_path) or (not Path(script_path).expanduser().exists()):
        kwargs["script_path"] = str(BUNDLED_DESEQ2_WRAPPER)
    kwargs["counts_matrix"] = _resolve_dataset_path(str(kwargs.get("counts_matrix", "")), COUNT_MATRIX_CANDIDATE_NAMES)
    kwargs["metadata_table"] = _resolve_dataset_path(str(kwargs.get("metadata_table", "")), METADATA_CANDIDATE_NAMES)
    kwargs["output_dir"] = _resolve_output_dir(str(kwargs.get("output_dir", "")))
    use_python = str(kwargs.get("script_path", "")).strip().lower().endswith(".py") or engine == "pydeseq2"
    if use_python:
        python_parts = managed_python_command_parts(
            python_executable=str(preferred_helper_python_executable()),
            script_path=str(kwargs.get("script_path", "")),
        )
        python_parts.extend(
            [
                "--counts",
                str(kwargs.get("counts_matrix", "")),
                "--metadata",
                str(kwargs.get("metadata_table", "")),
                "--design",
                str(kwargs.get("design_formula", "")),
                "--contrast",
                str(kwargs.get("contrast", "")),
                "--outdir",
                str(kwargs.get("output_dir", "")),
            ]
        )
        return _render_shell_parts(python_parts)
    else:
        kwargs["rscript_executable"] = rscript_for_requirement("deseq2") or "Rscript"
    template = (
        "{rscript_executable} {script_path} --counts {counts_matrix} --metadata {metadata_table} --design {design_formula} --contrast {contrast} --outdir {output_dir}"
    )
    return _render_template(template, kwargs)
