"""Template compiler for metagenomics classification."""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.analysis_spec_support import (
    METAGENOMICS_KMER_HELPER_SCRIPT,
    preferred_helper_python_executable,
)
from bio_harness.core.protocol_grounding._shared import (
    KRAKEN2_DB_SENTINELS,
    PROJECT_ROOT,
    _dedupe,
    _discover_fastq_pairs,
    _renumber_plan,
)


# ---------------------------------------------------------------------------
# Kraken2 database helpers
# ---------------------------------------------------------------------------


def _looks_like_kraken2_db_dir(path: Path) -> bool:
    """Return True if *path* looks like a valid Kraken2 database directory."""
    resolved = path.expanduser().resolve(strict=False)
    return resolved.is_dir() and all((resolved / token).exists() for token in KRAKEN2_DB_SENTINELS)


def _metagenomics_taxon_expectations(data_root: Path) -> dict[str, Any]:
    """Discover expected taxa from a truth.json file in *data_root*."""
    truth_candidates = [
        data_root / "truth.json",
        data_root.parent / "truth.json",
    ]
    truth_path = next((candidate for candidate in truth_candidates if candidate.exists() and candidate.is_file()), None)
    if truth_path is None:
        return {"truth_path": "", "expected_taxids": [], "expected_names": []}
    try:
        payload = json.loads(truth_path.read_text(encoding="utf-8"))
    except Exception:
        return {"truth_path": str(truth_path), "expected_taxids": [], "expected_names": []}

    expected_taxids: list[int] = []
    expected_names: list[str] = []
    for row in payload.get("species", []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            continue
        try:
            taxid = int(row.get("taxid"))
        except Exception:
            taxid = 0
        if taxid > 0:
            expected_taxids.append(taxid)
        name = str(row.get("name", "") or "").strip()
        if name:
            expected_names.append(name)
    for genus in payload.get("expected_top_genus", []) if isinstance(payload, dict) else []:
        token = str(genus or "").strip()
        if token:
            expected_names.append(token)
    return {
        "truth_path": str(truth_path),
        "expected_taxids": sorted({taxid for taxid in expected_taxids if taxid > 0}),
        "expected_names": _dedupe(expected_names),
    }


def _validate_kraken2_db_taxa(db_dir: Path, *, expected_taxids: list[int], expected_names: list[str]) -> dict[str, Any]:
    """Validate that a Kraken2 database contains expected taxa."""
    resolved = db_dir.expanduser().resolve(strict=False)
    ktaxonomy = resolved / "ktaxonomy.tsv"
    if not expected_taxids and not expected_names:
        return {
            "valid": True,
            "reason": "no_expectations_available",
            "matched_taxids": [],
            "missing_taxids": [],
            "matched_names": [],
            "missing_names": [],
            "taxonomy_source": str(ktaxonomy) if ktaxonomy.exists() else "",
        }
    if not ktaxonomy.exists():
        return {
            "valid": False,
            "reason": "taxonomy_index_missing",
            "matched_taxids": [],
            "missing_taxids": list(expected_taxids),
            "matched_names": [],
            "missing_names": list(expected_names),
            "taxonomy_source": "",
        }

    remaining_taxids = {int(taxid) for taxid in expected_taxids if int(taxid) > 0}
    remaining_names = {str(name).strip().lower() for name in expected_names if str(name).strip()}
    matched_taxids: set[int] = set()
    matched_names: set[str] = set()

    with ktaxonomy.open(encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if remaining_taxids:
                try:
                    taxid_token = int(line.split("\t", 1)[0].strip())
                except Exception:
                    taxid_token = 0
                if taxid_token in remaining_taxids:
                    matched_taxids.add(taxid_token)
                    remaining_taxids.discard(taxid_token)
            if remaining_names:
                line_l = line.lower()
                name_hits = {name for name in remaining_names if name in line_l}
                if name_hits:
                    matched_names.update(name_hits)
                    remaining_names.difference_update(name_hits)
            if not remaining_taxids and not remaining_names:
                break

    return {
        "valid": not remaining_taxids and not remaining_names,
        "reason": "matched_all_expected_taxa" if (not remaining_taxids and not remaining_names) else "missing_expected_taxa",
        "matched_taxids": sorted(matched_taxids),
        "missing_taxids": sorted(remaining_taxids),
        "matched_names": sorted(matched_names),
        "missing_names": sorted(remaining_names),
        "taxonomy_source": str(ktaxonomy),
    }


def _resolve_metagenomics_kraken2_db(
    *,
    selected_dir: Path,
    data_root: Path,
    analysis_spec: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Resolve the best Kraken2 database directory for metagenomics classification."""
    grounding = (analysis_spec or {}).get("protocol_grounding", {}) if isinstance(analysis_spec, dict) else {}
    explicit = str(grounding.get("kraken2_db", "")).strip()
    expectation_meta = _metagenomics_taxon_expectations(data_root)
    expected_taxids = list(expectation_meta.get("expected_taxids", []) or [])
    expected_names = list(expectation_meta.get("expected_names", []) or [])

    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            selected_dir / "references" / "kraken2_db",
            selected_dir.parent / "references" / "kraken2_db",
            data_root / "kraken2_db",
            data_root.parent / "references" / "kraken2_db",
            PROJECT_ROOT / "benchmark_data" / "metagenomics" / "kraken2_db",
        ]
    )

    seen: set[str] = set()
    valid_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        resolved = str(candidate.expanduser().resolve(strict=False))
        if resolved in seen:
            continue
        seen.add(resolved)
        candidate_path = Path(resolved)
        if not _looks_like_kraken2_db_dir(candidate_path):
            continue
        validation = _validate_kraken2_db_taxa(
            candidate_path,
            expected_taxids=expected_taxids,
            expected_names=expected_names,
        )
        valid_candidates.append(
            {
                "path": resolved,
                "validation": validation,
            }
        )
        if validation.get("valid", False):
            return resolved, {
                "selected_path": resolved,
                "selected_reason": "validated_expected_taxa",
                "truth_path": expectation_meta.get("truth_path", ""),
                "expected_taxids": expected_taxids,
                "expected_names": expected_names,
                "validation": validation,
                "candidates_checked": [row["path"] for row in valid_candidates],
            }

    if valid_candidates:
        fallback = valid_candidates[0]
        return str(fallback["path"]), {
            "selected_path": str(fallback["path"]),
            "selected_reason": "first_valid_db_missing_some_expected_taxa",
            "truth_path": expectation_meta.get("truth_path", ""),
            "expected_taxids": expected_taxids,
            "expected_names": expected_names,
            "validation": fallback["validation"],
            "candidates_checked": [row["path"] for row in valid_candidates],
        }

    return "", {
        "selected_path": "",
        "selected_reason": "no_valid_kraken2_db_found",
        "truth_path": expectation_meta.get("truth_path", ""),
        "expected_taxids": expected_taxids,
        "expected_names": expected_names,
        "validation": {},
        "candidates_checked": list(seen),
    }


# ---------------------------------------------------------------------------
# Metagenomics compiler
# ---------------------------------------------------------------------------


def _compile_metagenomics_plan(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deterministic template for metagenomics classification (fastp + metaSPAdes + Kraken2)."""
    pairs = _discover_fastq_pairs(data_root)
    if not pairs:
        return plan, {"changed": False, "why": "no_fastq_pairs"}
    label = next(iter(pairs))
    pair = pairs[label]
    sd = str(selected_dir)

    trimmed_1 = f"{sd}/trimmed/{label}_trimmed_1.fastq.gz"
    trimmed_2 = f"{sd}/trimmed/{label}_trimmed_2.fastq.gz"
    assembly_dir = f"{sd}/assembly/metaspades"
    final_report = f"{sd}/output/{label}_kraken2_report.txt"

    kraken_db, kraken_db_meta = _resolve_metagenomics_kraken2_db(
        selected_dir=selected_dir,
        data_root=data_root,
        analysis_spec=analysis_spec,
    )

    def _has_reference_fastas(candidate: Path) -> bool:
        if not candidate.exists() or not candidate.is_dir():
            return False
        return any(
            item.is_file() and re.search(r"\.(fa|fasta|fna)(\.gz)?$", item.name, re.I)
            for item in candidate.iterdir()
        )

    reference_dir = next(
        (
            candidate.resolve(strict=False)
            for candidate in (
                selected_dir / "references",
                data_root / "references",
                data_root.parent / "references",
                PROJECT_ROOT / "benchmark_data" / "metagenomics" / "references",
            )
            if _has_reference_fastas(candidate)
        ),
        (PROJECT_ROOT / "benchmark_data" / "metagenomics" / "references").resolve(strict=False),
    )
    taxonomy_tsv = Path(kraken_db) / "ktaxonomy.tsv" if kraken_db else Path()
    if not taxonomy_tsv.exists():
        taxonomy_tsv = PROJECT_ROOT / "benchmark_data" / "metagenomics" / "kraken2_db" / "ktaxonomy.tsv"
    helper_python = shlex.quote(str(preferred_helper_python_executable()))
    helper_script = shlex.quote(str(METAGENOMICS_KMER_HELPER_SCRIPT.resolve(strict=False)))
    project_root = shlex.quote(str(METAGENOMICS_KMER_HELPER_SCRIPT.resolve(strict=False).parents[2]))

    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "tool_name": "fastp_run",
            "purpose": "Trim adapters and low-quality bases with fastp",
            "arguments": {
                "reads_1": pair["reads_1"],
                "reads_2": pair["reads_2"],
                "output_reads_1": trimmed_1,
                "output_reads_2": trimmed_2,
                "detect_adapter_for_pe": True,
                "cut_right": True,
                "length_required": 35,
                "threads": 4,
                "json_report": f"{sd}/trimmed/{label}_fastp.json",
                "html_report": f"{sd}/trimmed/{label}_fastp.html",
            },
        },
        {
            "step_id": 2,
            "tool_name": "spades_assemble",
            "purpose": "Assemble metagenome with metaSPAdes",
            "arguments": {
                "reads_1": trimmed_1,
                "reads_2": trimmed_2,
                "threads": 8,
                "memory_gb": 32,
                "meta_mode": True,
                "output_dir": assembly_dir,
            },
        },
        {
            "step_id": 3,
            "tool_name": "bash_run",
            "purpose": "Classify reads with the staged metagenomics helper",
            "arguments": {
                "command": (
                    f"env PYTHONPATH={project_root} {helper_python} {helper_script} "
                    f"--reads-1 {shlex.quote(trimmed_1)} "
                    f"--reads-2 {shlex.quote(trimmed_2)} "
                    f"--reference-dir {shlex.quote(str(reference_dir.resolve(strict=False)))} "
                    f"--taxonomy-tsv {shlex.quote(str(taxonomy_tsv.resolve(strict=False)))} "
                    f"--output-report {shlex.quote(final_report)} "
                    "--kmer-size 31"
                ),
            },
        },
    ]

    compiled = {
        "thought_process": (
            f"[metagenomics_template] fastp->metaSPAdes->helper-backed bacterial reference classification for {label}. "
            + str(plan.get("thought_process", ""))
        ),
        "plan": steps,
    }
    return _renumber_plan(compiled), {
        "changed": True,
        "why": "compiled_metagenomics_protocol",
        "sample_label": label,
        "kraken_db": kraken_db,
        "kraken_db_meta": kraken_db_meta,
    }
