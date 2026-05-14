"""Audit and materialize staged reference bundles deterministically."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from bio_harness.core.tool_env import requirement_available, which_with_pixi


DEFAULT_REFERENCE_TARGETS = ("faidx", "dict", "bwa", "bowtie2", "minimap2")
EXTENDED_REFERENCE_TARGETS = ("star", "salmon", "kallisto")
REFERENCE_MANIFEST_NAME = "reference_manifest.json"
_TRANSCRIPTOME_FASTA_MARKERS = ("transcriptome", "cdna", "transcript", "transcripts", "mrna")


def _relative_paths(root: Path, paths: list[Path]) -> list[str]:
    return [str(path.relative_to(root)) for path in sorted(paths)]


def _relative_path(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    return str(path.relative_to(root))


def _is_fasta_path(path: Path) -> bool:
    return "".join(path.suffixes).lower() in {
        ".fa",
        ".fasta",
        ".fna",
        ".fa.gz",
        ".fasta.gz",
        ".fna.gz",
    }


def _fasta_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and _is_fasta_path(path)
    ]


def _is_transcriptome_fasta_path(path: Path) -> bool:
    if not _is_fasta_path(path):
        return False
    name_l = path.name.lower()
    return any(marker in name_l for marker in _TRANSCRIPTOME_FASTA_MARKERS)


def _annotation_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and "".join(path.suffixes).lower() in {".gtf", ".gff", ".gff3", ".gtf.gz", ".gff.gz", ".gff3.gz"}
    ]


def _load_reference_manifest(root: Path) -> tuple[Path | None, dict[str, Any], list[dict[str, Any]]]:
    manifest_path = root / REFERENCE_MANIFEST_NAME
    if not manifest_path.is_file():
        return None, {}, []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return manifest_path, {}, [{"code": "invalid_manifest_json", "message": str(exc), "severity": "error"}]
    if not isinstance(payload, dict):
        return manifest_path, {}, [{"code": "invalid_manifest_type", "message": "Manifest must be a JSON object.", "severity": "error"}]
    return manifest_path, payload, []


def _resolve_manifest_asset(root: Path, raw: Any) -> Path | None:
    token = str(raw or "").strip()
    if not token:
        return None
    candidate = (root / token).resolve() if not Path(token).is_absolute() else Path(token).expanduser().resolve()
    return candidate


def _select_primary_asset(
    *,
    root: Path,
    files: list[Path],
    manifest: dict[str, Any],
    manifest_key: str,
    asset_label: str,
    validator,
) -> tuple[Path | None, list[dict[str, Any]], str]:
    issues: list[dict[str, Any]] = []
    manifest_value = manifest.get(manifest_key)
    if manifest_value is not None:
        resolved = _resolve_manifest_asset(root, manifest_value)
        if resolved is None or not resolved.is_file():
            issues.append(
                {
                    "code": f"manifest_{manifest_key}_missing",
                    "message": f"Manifest-selected {asset_label} does not exist: {manifest_value}",
                    "severity": "error",
                }
            )
            return None, issues, "manifest"
        if not validator(resolved):
            issues.append(
                {
                    "code": f"manifest_{manifest_key}_invalid",
                    "message": f"Manifest-selected {asset_label} is not a valid {asset_label} file: {manifest_value}",
                    "severity": "error",
                }
            )
            return None, issues, "manifest"
        return resolved, issues, "manifest"

    if not files:
        return None, issues, "none"
    if len(files) > 1:
        issues.append(
            {
                "code": f"ambiguous_primary_{manifest_key}",
                "message": f"Multiple candidate {asset_label} files found and no manifest selected one.",
                "severity": "error",
                "candidates": [str(path.relative_to(root)) for path in files],
            }
        )
        return None, issues, "ambiguous"
    return files[0], issues, "unambiguous"


def _validate_primary_index_consistency(root: Path, primary_fasta: Path | None) -> list[dict[str, Any]]:
    if primary_fasta is None:
        return []

    issues: list[dict[str, Any]] = []
    checks = {
        "faidx": bool(list(root.rglob("*.fai"))),
        "dict": bool(list(root.rglob("*.dict"))),
        "bwa": bool(_bwa_prefixes(root)),
        "minimap2": bool(_minimap2_indices(root)),
    }
    expected_paths = {
        "faidx": primary_fasta.with_suffix(f"{primary_fasta.suffix}.fai"),
        "dict": _canonical_dict_path(primary_fasta),
        "bwa": _canonical_fasta_prefix(primary_fasta),
        "minimap2": _canonical_minimap2_index(root, primary_fasta),
    }

    if checks["faidx"] and not expected_paths["faidx"].exists():
        issues.append(
            {
                "code": "manifest_index_mismatch_faidx",
                "message": f"Reference bundle contains faidx files, but not for the selected primary FASTA {primary_fasta.name}.",
                "severity": "error",
            }
        )
    if checks["dict"] and not expected_paths["dict"].exists():
        issues.append(
            {
                "code": "manifest_index_mismatch_dict",
                "message": f"Reference bundle contains sequence dictionaries, but not for the selected primary FASTA {primary_fasta.name}.",
                "severity": "error",
            }
        )
    if checks["bwa"] and str(_canonical_fasta_prefix(primary_fasta).relative_to(root)) not in _bwa_prefixes(root):
        issues.append(
            {
                "code": "manifest_index_mismatch_bwa",
                "message": f"Reference bundle contains BWA indexes, but not for the selected primary FASTA {primary_fasta.name}.",
                "severity": "error",
            }
        )
    if checks["minimap2"] and str(_canonical_minimap2_index(root, primary_fasta).relative_to(root)) not in _minimap2_indices(root):
        issues.append(
            {
                "code": "manifest_index_mismatch_minimap2",
                "message": f"Reference bundle contains minimap2 indexes, but not for the selected primary FASTA {primary_fasta.name}.",
                "severity": "error",
            }
        )
    return issues


def _resolve_reference_bundle(root: Path) -> dict[str, Any]:
    fastas = sorted(_fasta_files(root))
    transcriptome_fastas = sorted(path for path in fastas if _is_transcriptome_fasta_path(path))
    genome_fastas = [path for path in fastas if path not in set(transcriptome_fastas)]
    annotations = sorted(_annotation_files(root))
    manifest_path, manifest, manifest_issues = _load_reference_manifest(root)
    primary_fasta, fasta_issues, fasta_mode = _select_primary_asset(
        root=root,
        files=genome_fastas,
        manifest=manifest,
        manifest_key="primary_fasta",
        asset_label="FASTA",
        validator=_is_fasta_path,
    )
    primary_transcriptome_fasta, transcriptome_issues, transcriptome_mode = _select_primary_asset(
        root=root,
        files=transcriptome_fastas,
        manifest=manifest,
        manifest_key="primary_transcriptome_fasta",
        asset_label="transcriptome FASTA",
        validator=_is_fasta_path,
    )
    primary_annotation, annotation_issues, annotation_mode = _select_primary_asset(
        root=root,
        files=annotations,
        manifest=manifest,
        manifest_key="primary_annotation",
        asset_label="annotation",
        validator=lambda path: "".join(path.suffixes).lower() in {".gtf", ".gff", ".gff3", ".gtf.gz", ".gff.gz", ".gff3.gz"},
    )
    issues = manifest_issues + fasta_issues + transcriptome_issues + annotation_issues
    issues.extend(_validate_primary_index_consistency(root, primary_fasta))
    selection_mode = "manifest" if manifest_path is not None else ("unambiguous" if not issues else "ambiguous")
    return {
        "manifest_path": manifest_path,
        "manifest": manifest,
        "selection_mode": selection_mode,
        "primary_fasta": primary_fasta,
        "primary_transcriptome_fasta": primary_transcriptome_fasta,
        "primary_annotation": primary_annotation,
        "fasta_files": fastas,
        "transcriptome_fasta_files": transcriptome_fastas,
        "annotation_files": annotations,
        "issues": issues,
        "fasta_selection_mode": fasta_mode,
        "transcriptome_selection_mode": transcriptome_mode,
        "annotation_selection_mode": annotation_mode,
    }


def _canonical_fasta_prefix(fasta: Path) -> Path:
    return fasta.with_suffix("")


def _canonical_dict_path(fasta: Path) -> Path:
    return fasta.with_suffix(".dict")


def _canonical_bowtie2_prefix(root: Path, fasta: Path) -> Path:
    return root / "bowtie2_index" / fasta.stem


def _canonical_minimap2_index(root: Path, fasta: Path) -> Path:
    return root / f"{fasta.stem}.mmi"


def _canonical_star_index_dir(root: Path) -> Path:
    return root / "star_index"


def _canonical_salmon_index_dir(root: Path) -> Path:
    return root / "salmon_index"


def _canonical_kallisto_index_path(root: Path) -> Path:
    return root / "kallisto_index" / "transcripts.idx"


def _command_for_tool(tool_name: str) -> str | None:
    if not requirement_available(tool_name):
        return None
    return which_with_pixi(tool_name) or tool_name


def _bwa_prefixes(root: Path) -> list[str]:
    prefixes: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name.endswith((".amb", ".ann", ".pac", ".sa")):
            prefixes.add(str(path.with_suffix("").relative_to(root)))
        elif ".bwt" in name:
            prefixes.add(str(Path(str(path).split(".bwt", 1)[0]).relative_to(root)))
    return sorted(prefixes)


def _bowtie2_prefixes(root: Path) -> list[str]:
    prefixes: set[str] = set()
    for suffix in (".1.bt2", ".1.bt2l"):
        for path in root.rglob(f"*{suffix}"):
            token = path.name.split(".1.bt2", 1)[0]
            prefixes.add(str((path.parent / token).relative_to(root)))
    return sorted(prefixes)


def _salmon_indices(root: Path) -> list[str]:
    return _relative_paths(root, [path.parent for path in root.rglob("hash.bin") if path.parent.is_dir()])


def _kallisto_indices(root: Path) -> list[str]:
    candidates: list[Path] = []
    for path in root.rglob("*.idx"):
        if not path.is_file():
            continue
        name_l = path.name.lower()
        parent_l = path.parent.name.lower()
        if (
            parent_l == "kallisto_index"
            or "kallisto" in parent_l
            or "kallisto" in name_l
            or name_l in {"transcripts.idx", "transcriptome.idx"}
            or any(marker in name_l for marker in _TRANSCRIPTOME_FASTA_MARKERS)
        ):
            candidates.append(path)
    return _relative_paths(root, candidates)


def _star_indices(root: Path) -> list[str]:
    candidates = []
    for path in root.rglob("genomeParameters.txt"):
        parent = path.parent
        if (parent / "SA").exists() or (parent / "Genome").exists():
            candidates.append(parent)
    return _relative_paths(root, candidates)


def _minimap2_indices(root: Path) -> list[str]:
    return _relative_paths(root, list(root.rglob("*.mmi")))


def audit_reference_bundle(root_path: str | Path) -> dict[str, Any]:
    """Inventory common reference assets and index structures under a root."""
    root = Path(root_path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Reference root does not exist: {root}")

    bundle = _resolve_reference_bundle(root)
    fastas = bundle["fasta_files"]
    annotations = bundle["annotation_files"]
    fasta_set = set(fastas)
    fai = [path for path in root.rglob("*.fai") if path.with_suffix("") in fasta_set]
    dicts = [path for path in root.rglob("*.dict")]

    return {
        "reference_root": str(root),
        "manifest_path": str(bundle["manifest_path"]) if bundle["manifest_path"] else "",
        "selection_mode": bundle["selection_mode"],
        "primary_fasta": _relative_path(root, bundle["primary_fasta"]),
        "primary_transcriptome_fasta": _relative_path(root, bundle["primary_transcriptome_fasta"]),
        "primary_annotation": _relative_path(root, bundle["primary_annotation"]),
        "selection_issues": bundle["issues"],
        "fasta_files": _relative_paths(root, fastas),
        "transcriptome_fasta_files": _relative_paths(root, bundle["transcriptome_fasta_files"]),
        "annotation_files": _relative_paths(root, annotations),
        "faidx_files": _relative_paths(root, fai),
        "dict_files": _relative_paths(root, dicts),
        "bwa_index_prefixes": _bwa_prefixes(root),
        "bowtie2_index_prefixes": _bowtie2_prefixes(root),
        "salmon_index_dirs": _salmon_indices(root),
        "kallisto_index_files": _kallisto_indices(root),
        "star_index_dirs": _star_indices(root),
        "minimap2_indices": _minimap2_indices(root),
    }


def build_reference_materialization_plan(
    root_path: str | Path,
    *,
    targets: list[str] | tuple[str, ...] | None = None,
    include_extended: bool = False,
) -> dict[str, Any]:
    root = Path(root_path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Reference root does not exist: {root}")

    bundle = _resolve_reference_bundle(root)
    fasta = bundle["primary_fasta"]
    transcriptome_fasta = bundle["primary_transcriptome_fasta"]
    annotation = bundle["primary_annotation"]
    requested_targets = [str(target).strip().lower() for target in (targets or ()) if str(target).strip()]
    if not requested_targets:
        requested_targets = list(DEFAULT_REFERENCE_TARGETS)
        if include_extended:
            requested_targets.extend(EXTENDED_REFERENCE_TARGETS)

    rows: list[dict[str, Any]] = []
    selection_issues = [dict(issue) for issue in bundle["issues"]]
    blocking_issue_codes = [str(issue.get("code", "") or "").strip() for issue in selection_issues if str(issue.get("severity", "")).strip().lower() == "error"]
    if blocking_issue_codes:
        return {
            "reference_root": str(root),
            "manifest_path": str(bundle["manifest_path"]) if bundle["manifest_path"] else "",
            "selection_mode": bundle["selection_mode"],
            "selection_issues": selection_issues,
            "primary_fasta": str(fasta) if fasta is not None else None,
            "primary_transcriptome_fasta": str(transcriptome_fasta) if transcriptome_fasta is not None else None,
            "primary_annotation": str(annotation) if annotation is not None else None,
            "targets": requested_targets,
            "steps": [],
            "pending_targets": [],
            "unavailable_targets": [],
            "ready": False,
            "reason": blocking_issue_codes[0],
        }

    target_specs: dict[str, dict[str, Any]] = {
        "faidx": {
            "tool": "samtools",
            "reference_kind": "genome",
            "outputs": lambda ref: [ref.with_suffix(f"{ref.suffix}.fai")],
            "command": lambda tool, ref: [tool, "faidx", str(ref)],
        },
        "dict": {
            "tool": "gatk",
            "reference_kind": "genome",
            "outputs": lambda ref: [_canonical_dict_path(ref)],
            "command": lambda tool, ref: [tool, "CreateSequenceDictionary", "-R", str(ref), "-O", str(_canonical_dict_path(ref))],
        },
        "bwa": {
            "tool": "bwa",
            "reference_kind": "genome",
            "outputs": lambda ref: [
                _canonical_fasta_prefix(ref).with_suffix(".amb"),
                _canonical_fasta_prefix(ref).with_suffix(".ann"),
                _canonical_fasta_prefix(ref).with_suffix(".pac"),
                _canonical_fasta_prefix(ref).with_suffix(".sa"),
            ],
            "command": lambda tool, ref: [tool, "index", str(ref)],
        },
        "bowtie2": {
            "tool": "bowtie2-build",
            "reference_kind": "genome",
            "outputs": lambda ref: [_canonical_bowtie2_prefix(root, ref).with_name(_canonical_bowtie2_prefix(root, ref).name + ".1.bt2")],
            "command": lambda tool, ref: [tool, str(ref), str(_canonical_bowtie2_prefix(root, ref))],
        },
        "minimap2": {
            "tool": "minimap2",
            "reference_kind": "genome",
            "outputs": lambda ref: [_canonical_minimap2_index(root, ref)],
            "command": lambda tool, ref: [tool, "-d", str(_canonical_minimap2_index(root, ref)), str(ref)],
        },
        "star": {
            "tool": "star",
            "reference_kind": "genome",
            "outputs": lambda ref: [_canonical_star_index_dir(root) / "genomeParameters.txt"],
            "command": lambda tool, ref: [
                tool,
                "--runMode",
                "genomeGenerate",
                "--runThreadN",
                "2",
                "--genomeDir",
                str(_canonical_star_index_dir(root)),
                "--genomeFastaFiles",
                str(ref),
                "--sjdbGTFfile",
                str(annotation),
            ],
            "requires_annotation": True,
        },
        "salmon": {
            "tool": "salmon",
            "reference_kind": "transcriptome",
            "outputs": lambda ref: [_canonical_salmon_index_dir(root) / "hash.bin"],
            "command": lambda tool, ref: [tool, "index", "-t", str(ref), "-i", str(_canonical_salmon_index_dir(root))],
            "missing_outputs": [_canonical_salmon_index_dir(root) / "hash.bin"],
        },
        "kallisto": {
            "tool": "kallisto",
            "reference_kind": "transcriptome",
            "outputs": lambda ref: [_canonical_kallisto_index_path(root)],
            "command": lambda tool, ref: [tool, "index", "-i", str(_canonical_kallisto_index_path(root)), str(ref)],
            "missing_outputs": [_canonical_kallisto_index_path(root)],
        },
    }

    pending_targets: list[str] = []
    unavailable_targets: list[str] = []
    for target in requested_targets:
        spec = target_specs.get(target)
        if spec is None:
            rows.append({"target": target, "status": "unknown_target"})
            unavailable_targets.append(target)
            continue

        reference_kind = str(spec.get("reference_kind", "genome")).strip().lower()
        reference_asset = transcriptome_fasta if reference_kind == "transcriptome" else fasta
        if reference_asset is None:
            missing_status = "missing_transcriptome_fasta" if reference_kind == "transcriptome" else "missing_primary_fasta"
            missing_asset = "primary_transcriptome_fasta" if reference_kind == "transcriptome" else "primary_fasta"
            missing_outputs = [
                str(Path(path).relative_to(root))
                for path in spec.get("missing_outputs", [])
                if Path(path).is_absolute() and root in Path(path).parents
            ]
            rows.append(
                {
                    "target": target,
                    "status": missing_status,
                    "required_asset": missing_asset,
                    "outputs": missing_outputs,
                }
            )
            unavailable_targets.append(target)
            continue

        outputs = [Path(path) for path in spec["outputs"](reference_asset)]
        present = all(path.exists() for path in outputs)
        tool_cmd = _command_for_tool(str(spec["tool"]))
        if spec.get("requires_annotation", False) and annotation is None:
            rows.append(
                {
                    "target": target,
                    "status": "missing_annotation",
                    "outputs": [str(path.relative_to(root)) for path in outputs],
                }
            )
            unavailable_targets.append(target)
            continue
        if present:
            rows.append(
                {
                    "target": target,
                    "status": "present",
                    "outputs": [str(path.relative_to(root)) for path in outputs],
                }
            )
            continue
        if tool_cmd is None:
            rows.append(
                {
                    "target": target,
                    "status": "missing_tool",
                    "tool": spec["tool"],
                    "outputs": [str(path.relative_to(root)) for path in outputs],
                }
            )
            unavailable_targets.append(target)
            continue

        pending_targets.append(target)
        rows.append(
            {
                "target": target,
                "status": "pending",
                "tool": spec["tool"],
                "command": spec["command"](tool_cmd, reference_asset),
                "outputs": [str(path.relative_to(root)) for path in outputs],
            }
        )

    return {
        "reference_root": str(root),
        "manifest_path": str(bundle["manifest_path"]) if bundle["manifest_path"] else "",
        "selection_mode": bundle["selection_mode"],
        "selection_issues": selection_issues,
        "primary_fasta": str(fasta) if fasta is not None else None,
        "primary_transcriptome_fasta": str(transcriptome_fasta) if transcriptome_fasta is not None else None,
        "primary_annotation": str(annotation) if annotation is not None else None,
        "targets": requested_targets,
        "steps": rows,
        "pending_targets": pending_targets,
        "unavailable_targets": unavailable_targets,
        "ready": not pending_targets and not unavailable_targets,
    }


def materialize_reference_bundle(
    root_path: str | Path,
    *,
    targets: list[str] | tuple[str, ...] | None = None,
    include_extended: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    plan = build_reference_materialization_plan(
        root_path,
        targets=targets,
        include_extended=include_extended,
    )
    root = Path(plan["reference_root"])
    execution_rows: list[dict[str, Any]] = []
    success = True
    for step in plan["steps"]:
        if step.get("status") != "pending":
            execution_rows.append(dict(step))
            continue
        command = [str(part) for part in step.get("command", [])]
        row = dict(step)
        if dry_run:
            row["returncode"] = 0
            row["dry_run"] = True
            execution_rows.append(row)
            continue
        outputs = [root / relpath for relpath in step.get("outputs", [])]
        for output_path in outputs:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        row["returncode"] = completed.returncode
        row["stdout_tail"] = "\n".join(completed.stdout.strip().splitlines()[-10:])
        row["stderr_tail"] = "\n".join(completed.stderr.strip().splitlines()[-10:])
        execution_rows.append(row)
        if completed.returncode != 0:
            success = False
            break

    plan["steps"] = execution_rows
    plan["success"] = success and not any(
        step.get("status") in {
            "missing_annotation",
            "missing_primary_fasta",
            "missing_tool",
            "missing_transcriptome_fasta",
            "unknown_target",
        }
        for step in execution_rows
    )
    return plan


def write_reference_audit(root_path: str | Path, output_path: str | Path | None = None) -> Path:
    """Write a JSON audit of discovered reference assets."""
    root = Path(root_path).expanduser().resolve()
    target = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else root / "reference_audit.json"
    )
    payload = audit_reference_bundle(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def write_reference_materialization_report(
    root_path: str | Path,
    output_path: str | Path | None = None,
    *,
    targets: list[str] | tuple[str, ...] | None = None,
    include_extended: bool = False,
    dry_run: bool = False,
) -> Path:
    root = Path(root_path).expanduser().resolve()
    target = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else root / "reference_materialization.json"
    )
    payload = materialize_reference_bundle(
        root,
        targets=targets,
        include_extended=include_extended,
        dry_run=dry_run,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
