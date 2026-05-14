from __future__ import annotations

import shlex
import string
from pathlib import Path

from bio_harness.core.analysis_spec_support import (
    managed_python_command_parts,
    preferred_helper_python_executable,
)

BUNDLED_METABOLOMICS_WORKFLOW = (
    Path(__file__).resolve().parents[2] / "pipeline_scripts" / "metabolomics_diff_abundance.py"
)
FEATURE_TABLE_CANDIDATE_NAMES = (
    "feature_table.csv",
    "feature_table.tsv",
    "peak_table.csv",
    "peak_table.tsv",
    "metabolite_abundance.csv",
    "metabolite_abundance.tsv",
    "intensity_matrix.csv",
    "intensity_matrix.tsv",
)
METADATA_CANDIDATE_NAMES = (
    "metadata.csv",
    "metadata.tsv",
    "sample_metadata.csv",
    "sample_metadata.tsv",
    "samples.csv",
    "samples.tsv",
)


def _render_template(template: str, kwargs: dict[str, object]) -> str:
    """Render one shell-safe command template.

    Args:
        template: Command template with named placeholders.
        kwargs: Placeholder values.

    Returns:
        Fully rendered shell command.

    Raises:
        ValueError: If one required placeholder is missing.
    """

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
    """Quote and join command parts.

    Args:
        parts: Raw command parts.

    Returns:
        Shell-safe command string.
    """

    return " ".join(shlex.quote(str(part)) for part in parts if str(part or "").strip())


def _candidate_roots() -> list[Path]:
    """Return deterministic search roots for input discovery."""

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
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def _scan_candidates(roots: list[Path], names: tuple[str, ...], *, max_scan: int = 5000) -> list[Path]:
    """Scan deterministic roots for candidate files.

    Args:
        roots: Search roots.
        names: Candidate basenames.
        max_scan: Upper bound on traversed filesystem entries.

    Returns:
        Sorted candidate file paths.
    """

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
            if path.is_file() and path.name.lower() in wanted:
                hits.append(path.resolve())
        if scanned > max_scan:
            break
    hits.sort(key=lambda path: (len(str(path)), str(path)))
    return hits


def _resolve_dataset_path(raw_value: str, names: tuple[str, ...]) -> str:
    """Resolve one user-provided or inferred dataset path.

    Args:
        raw_value: Raw argument value.
        names: Candidate basenames used for deterministic discovery.

    Returns:
        Resolved path string when a real file is found, otherwise the original
        raw value.
    """

    raw = str(raw_value or "").strip()
    if not raw:
        return raw
    path = Path(raw).expanduser()
    if path.exists():
        return str(path.resolve())
    if path.is_absolute():
        return raw

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
    """Map special absolute output roots to the local workspace when needed."""

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
        return str(base / rel)
    return raw


def metabolomics_diff_abundance(**kwargs: object) -> str:
    """Render one deterministic metabolomics differential-abundance command.

    Args:
        **kwargs: Wrapper arguments from the harness plan.

    Returns:
        Rendered shell command.

    Raises:
        ValueError: If required parameters are missing.
    """

    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    params = dict(kwargs)
    script_path = str(params.get("script_path", "")).strip()
    if (not script_path) or (not Path(script_path).expanduser().exists()):
        params["script_path"] = str(BUNDLED_METABOLOMICS_WORKFLOW)
    params["feature_table"] = _resolve_dataset_path(
        str(params.get("feature_table", "")),
        FEATURE_TABLE_CANDIDATE_NAMES,
    )
    params["metadata_table"] = _resolve_dataset_path(
        str(params.get("metadata_table", "")),
        METADATA_CANDIDATE_NAMES,
    )
    missing: list[str] = []
    for field in ("feature_table", "metadata_table", "output_dir"):
        if not str(params.get(field, "")).strip():
            missing.append(field)
    if missing:
        missing_args = ", ".join(sorted(missing))
        raise ValueError(f"Missing required parameter(s) for template: {missing_args}")
    params["output_dir"] = _resolve_output_dir(str(params.get("output_dir", "")))
    params.setdefault("normalization_method", "median_center")
    params.setdefault("min_present_fraction", 0.5)
    params.setdefault("impute_method", "feature_median")

    command_parts = managed_python_command_parts(
        python_executable=str(preferred_helper_python_executable()),
        script_path=str(params.get("script_path", "")),
    )
    command_parts.extend(
        [
            "--feature-table",
            str(params.get("feature_table", "")),
            "--metadata-table",
            str(params.get("metadata_table", "")),
            "--output-dir",
            str(params.get("output_dir", "")),
            "--normalization-method",
            str(params.get("normalization_method", "")),
            "--min-present-fraction",
            str(params.get("min_present_fraction", "")),
            "--impute-method",
            str(params.get("impute_method", "")),
        ]
    )
    if str(params.get("output_csv", "")).strip():
        command_parts.extend(["--output-csv", str(params.get("output_csv", ""))])
    if str(params.get("sample_id_column", "")).strip():
        command_parts.extend(["--sample-id-column", str(params.get("sample_id_column", ""))])
    if str(params.get("group_column", "")).strip():
        command_parts.extend(["--group-column", str(params.get("group_column", ""))])
    if str(params.get("group_a", "")).strip():
        command_parts.extend(["--group-a", str(params.get("group_a", ""))])
    if str(params.get("group_b", "")).strip():
        command_parts.extend(["--group-b", str(params.get("group_b", ""))])
    return _render_shell_parts(command_parts)
