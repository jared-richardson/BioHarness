#!/usr/bin/env python3
"""Collect planner-only Qwen emissions for the fast-signal B2 corpus.

The B2 study samples raw workflow-skeleton planner emissions without running
bioinformatics tools. Rows are study artifacts, not curated regression
fixtures; representative rows can later be promoted into ``tests/fixtures/``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.agents.orchestrator import Orchestrator  # noqa: E402
from bio_harness.core.benchmark_policy import (  # noqa: E402
    BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    SCIENTIFIC_HARNESS_POLICY,
    normalize_benchmark_policy,
)
from bio_harness.core.domain_expansion_ablation import (  # noqa: E402
    DomainExpansionCase,
    load_domain_expansion_manifest,
)
from bio_harness.core.fast_signal import (  # noqa: E402
    parse_raw_emission,
    plan_idiom_key,
    plan_idiom_summary,
)
from bio_harness.core.llm_backends import backend_default_host, normalize_backend_name  # noqa: E402
from bio_harness.harness.config import SKILLS_DEFINITIONS, SKILLS_LIBRARY  # noqa: E402

DEFAULT_MANIFEST = PROJECT_ROOT / "workspace" / "benchmark_data" / "ablation_manifest_24.json"
DEFAULT_OUTPUT_JSONL = PROJECT_ROOT / "workspace" / "studies" / "plan_shape_corpus_qwen36.jsonl"
DEFAULT_SUMMARY_JSON = PROJECT_ROOT / "workspace" / "studies" / "plan_shape_corpus_qwen36_summary.json"
DEFAULT_SELECTED_ROOT = PROJECT_ROOT / "workspace" / "studies" / "plan_shape_corpus_selected"
DEFAULT_TEMPERATURES = (0.0, 0.3, 0.7)
DEFAULT_MODEL = (
    os.getenv("BIO_HARNESS_MODEL_HEAVY", "").strip()
    or os.getenv("BIO_HARNESS_MODEL", "").strip()
    or "qwen3.6:35b-a3b"
)


@dataclass(frozen=True)
class CorpusSample:
    """One planned planner-only emission sample.

    Attributes:
        emission_id: Stable row identifier.
        case: Domain-expansion manifest case.
        temperature: Planner temperature for this sample.
        repetition: One-based repetition index.
        selected_dir: Synthetic selected directory used only for grounding.
    """

    emission_id: str
    case: DomainExpansionCase
    temperature: float
    repetition: int
    selected_dir: Path


def build_parser() -> argparse.ArgumentParser:
    """Build the plan-shape corpus CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-file", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument(
        "--case-limit",
        type=int,
        default=10,
        help="Limit representative manifest cases after --case-id filtering. Use 0 for all.",
    )
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument(
        "--temperature",
        action="append",
        type=float,
        default=[],
        help="Planner temperature. Repeatable. Defaults to 0.0, 0.3, 0.7.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--llm-backend",
        default=os.getenv("BIO_HARNESS_LLM_BACKEND", os.getenv("BIO_HARNESS_LLM_PROVIDER", "ollama")),
    )
    parser.add_argument("--host", default="")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--num-predict", type=int, default=4096)
    parser.add_argument(
        "--benchmark-policy",
        default=BIOAGENTBENCH_PLANNING_STRICT_POLICY,
        choices=(BIOAGENTBENCH_PLANNING_STRICT_POLICY, SCIENTIFIC_HARNESS_POLICY),
    )
    parser.add_argument("--selected-root", type=Path, default=DEFAULT_SELECTED_ROOT)
    parser.add_argument("--output-jsonl", type=Path, default=DEFAULT_OUTPUT_JSONL)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--max-emissions", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    """Collect planner-only corpus rows and write a summary artifact."""

    args = build_parser().parse_args()
    temperatures = tuple(args.temperature or DEFAULT_TEMPERATURES)
    cases = select_manifest_cases(
        manifest_file=args.manifest_file,
        case_ids=args.case_id,
        case_limit=args.case_limit,
    )
    samples = build_sample_grid(
        cases=cases,
        temperatures=temperatures,
        repetitions=max(1, int(args.repetitions)),
        selected_root=args.selected_root,
        max_emissions=max(0, int(args.max_emissions)),
    )
    if args.dry_run:
        payload = {
            "planned_emissions": len(samples),
            "cases": [sample.case.case_id for sample in samples],
            "unique_cases": sorted({sample.case.case_id for sample in samples}),
            "temperatures": sorted({sample.temperature for sample in samples}),
            "output_jsonl": str(args.output_jsonl),
            "summary_json": str(args.summary_json),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    completed_ids = _completed_emission_ids(args.output_jsonl) if args.resume else set()
    backend_name = normalize_backend_name(args.llm_backend)
    host = args.host.strip() or backend_default_host(backend_name)
    backend_metadata = fetch_backend_metadata(host=host, model=args.model)
    orchestrator = Orchestrator(
        skills_dir=SKILLS_DEFINITIONS,
        skill_library_dir=SKILLS_LIBRARY,
        model_name=args.model,
        host=host,
        llm_backend=backend_name,
    )
    orchestrator.biollm.request_timeout_seconds = max(10.0, float(args.timeout_seconds))
    orchestrator.biollm._backend = orchestrator.biollm._new_backend()
    rows = _load_existing_rows(args.output_jsonl) if args.resume else []
    for sample in samples:
        if sample.emission_id in completed_ids:
            continue
        row = collect_sample(
            orchestrator=orchestrator,
            sample=sample,
            model=args.model,
            backend_name=backend_name,
            host=host,
            benchmark_policy=normalize_benchmark_policy(args.benchmark_policy),
            backend_metadata=backend_metadata,
            num_ctx=max(2048, int(args.num_ctx)),
            num_predict=max(256, int(args.num_predict)),
        )
        append_jsonl(args.output_jsonl, row)
        rows.append(row)
        write_summary(args.summary_json, rows, top_n=max(1, int(args.top_n)))
        print(
            json.dumps(
                {
                    "emission_id": row["emission_id"],
                    "case_id": row["case_id"],
                    "temperature": row["temperature"],
                    "parsed_ok": row["parsed_ok"],
                    "idiom_key": row["idiom_key"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
    write_summary(args.summary_json, rows, top_n=max(1, int(args.top_n)))
    return 0


def select_manifest_cases(
    *,
    manifest_file: Path,
    case_ids: list[str],
    case_limit: int,
) -> list[DomainExpansionCase]:
    """Load representative cases from the domain-expansion manifest.

    Args:
        manifest_file: Path to the 24-case manifest.
        case_ids: Optional explicit case IDs.
        case_limit: Maximum number of cases after filtering; ``0`` means all.

    Returns:
        Ordered manifest cases.
    """

    cases = list(
        load_domain_expansion_manifest(
            manifest_path=manifest_file.expanduser().resolve(strict=False),
            project_root=PROJECT_ROOT,
        )
    )
    requested = {str(case_id).strip() for case_id in case_ids if str(case_id).strip()}
    if requested:
        cases = [case for case in cases if case.case_id in requested]
    else:
        cases = _representative_cases(cases)
    if case_limit > 0:
        cases = cases[:case_limit]
    if not cases:
        raise ValueError("No manifest cases selected for the plan-shape corpus.")
    return cases


def build_sample_grid(
    *,
    cases: list[DomainExpansionCase],
    temperatures: tuple[float, ...],
    repetitions: int,
    selected_root: Path,
    max_emissions: int = 0,
) -> list[CorpusSample]:
    """Build the deterministic emission sample grid.

    Args:
        cases: Selected manifest cases.
        temperatures: Temperatures to sample.
        repetitions: One-based repetition count per case and temperature.
        selected_root: Root for synthetic selected directories.
        max_emissions: Optional cap for smoke runs.

    Returns:
        Deterministically ordered sample list.
    """

    samples: list[CorpusSample] = []
    root = selected_root.expanduser().resolve(strict=False)
    for case in cases:
        for temperature in temperatures:
            temp_label = _temperature_label(temperature)
            for repetition in range(1, repetitions + 1):
                emission_id = f"{case.case_id}.t{temp_label}.r{repetition:02d}"
                selected_dir = root / case.case_id / f"t{temp_label}" / f"rep_{repetition:02d}"
                samples.append(
                    CorpusSample(
                        emission_id=emission_id,
                        case=case,
                        temperature=float(temperature),
                        repetition=repetition,
                        selected_dir=selected_dir,
                    )
                )
                if max_emissions and len(samples) >= max_emissions:
                    return samples
    return samples


def collect_sample(
    *,
    orchestrator: Orchestrator,
    sample: CorpusSample,
    model: str,
    backend_name: str,
    host: str,
    benchmark_policy: str,
    backend_metadata: dict[str, str],
    num_ctx: int,
    num_predict: int,
) -> dict[str, Any]:
    """Collect one raw workflow-skeleton planner emission.

    Args:
        orchestrator: Initialized orchestrator used for skill selection and
            prompt construction.
        sample: Sample specification.
        model: Planner model tag.
        backend_name: Normalized backend name.
        host: Backend host.
        benchmark_policy: Benchmark policy for deterministic analysis-spec
            grounding.
        backend_metadata: Model digest and backend version metadata.
        num_ctx: Context window.
        num_predict: Prediction token budget.

    Returns:
        JSON-compatible corpus row.
    """

    started = time.monotonic()
    prompt = Path(sample.case.prompt_file).read_text(encoding="utf-8").strip()
    sample.selected_dir.mkdir(parents=True, exist_ok=True)
    analysis_spec = orchestrator.build_analysis_spec(
        prompt,
        contract={},
        selected_dir=str(sample.selected_dir),
        data_root=sample.case.data_root,
        project_root=str(PROJECT_ROOT),
        benchmark_policy=benchmark_policy,
    )
    available = orchestrator._available_skill_metadata()
    selected_skills, selection_meta = orchestrator._select_planner_skill_metadata(
        prompt,
        available,
        analysis_spec=analysis_spec,
    )
    messages = orchestrator.biollm._build_workflow_messages(
        prompt,
        selected_skills,
        analysis_spec=analysis_spec,
    )
    raw = orchestrator.biollm._backend.chat(
        model_name=model,
        messages=messages,
        temperature=float(sample.temperature),
        num_ctx=num_ctx,
        num_predict=num_predict,
        format_spec="json",
    )
    parsed = parse_raw_emission(raw)
    idioms = plan_idiom_summary(parsed) if parsed else {}
    return {
        "schema_version": 1,
        "kind": "planner_shape_corpus_emission",
        "emission_id": sample.emission_id,
        "case_id": sample.case.case_id,
        "band": sample.case.band,
        "prompt_file": sample.case.prompt_file,
        "data_root": sample.case.data_root,
        "selected_dir": str(sample.selected_dir),
        "model": model,
        "model_digest": backend_metadata.get("model_digest", ""),
        "backend_name": backend_name,
        "backend_version": backend_metadata.get("backend_version", ""),
        "host": host,
        "temperature": sample.temperature,
        "repetition": sample.repetition,
        "num_ctx": num_ctx,
        "num_predict": num_predict,
        "benchmark_policy": benchmark_policy,
        "raw_emission": raw,
        "raw_emission_len": len(raw),
        "parsed_ok": bool(parsed),
        "idiom_summary": idioms,
        "idiom_key": plan_idiom_key(idioms) if idioms else "unparseable",
        "analysis_type": str(analysis_spec.get("analysis_type", "") or ""),
        "analysis_family": str(analysis_spec.get("analysis_family", "") or ""),
        "planner_skill_selection": selection_meta,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_backend_metadata(*, host: str, model: str) -> dict[str, str]:
    """Fetch backend version and model digest metadata when available.

    Args:
        host: Backend host.
        model: Model tag to match in the backend model list.

    Returns:
        Metadata dictionary with ``model_digest`` and ``backend_version`` keys.
    """

    metadata = {"model_digest": "", "backend_version": ""}
    base = str(host or "").rstrip("/")
    if not base:
        return metadata
    try:
        tags = httpx.get(f"{base}/api/tags", timeout=5.0)
        if tags.status_code < 400:
            for row in (tags.json() or {}).get("models", []) or []:
                if not isinstance(row, dict):
                    continue
                row_name = str(row.get("model") or row.get("name") or "").strip()
                if row_name == model or row_name.startswith(f"{model}:"):
                    metadata["model_digest"] = str(row.get("digest", "") or "")
                    break
    except Exception:
        pass
    try:
        version = httpx.get(f"{base}/api/version", timeout=5.0)
        if version.status_code < 400:
            metadata["backend_version"] = str((version.json() or {}).get("version", "") or "")
    except Exception:
        pass
    return metadata


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Append one corpus row to a JSONL artifact.

    Args:
        path: Destination JSONL file.
        row: JSON-compatible corpus row.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def write_summary(path: Path, rows: list[dict[str, Any]], *, top_n: int) -> dict[str, Any]:
    """Write a corpus idiom summary.

    Args:
        path: Destination summary JSON.
        rows: Corpus rows.
        top_n: Number of idiom buckets to include in coverage.

    Returns:
        Summary payload.
    """

    payload = summarize_rows(rows, top_n=top_n)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def summarize_rows(rows: list[dict[str, Any]], *, top_n: int = 10) -> dict[str, Any]:
    """Summarize corpus rows by plan-shape idiom.

    Args:
        rows: Corpus row mappings.
        top_n: Number of top idiom buckets used for coverage.

    Returns:
        JSON-compatible corpus summary.
    """

    counter = Counter(_row_idiom_key(row) for row in rows)
    total = sum(counter.values())
    top = counter.most_common(top_n)
    parsed_count = sum(1 for row in rows if bool(row.get("parsed_ok", False)))
    parse_success_rate = parsed_count / max(total, 1)
    top_coverage = sum(count for _key, count in top) / max(total, 1)
    coverage_sample_floor = max(int(top_n), 30)
    return {
        "schema_version": 1,
        "emission_count": total,
        "parsed_count": parsed_count,
        "unparseable_count": total - parsed_count,
        "parse_success_rate": parse_success_rate,
        "case_count": len({str(row.get("case_id", "") or "") for row in rows}),
        "cases": sorted({str(row.get("case_id", "") or "") for row in rows if row.get("case_id")}),
        "temperatures": sorted({float(row.get("temperature", 0.0) or 0.0) for row in rows}),
        "top_n": top_n,
        "top_coverage": top_coverage,
        "coverage_sample_floor": coverage_sample_floor,
        "sufficient_for_fixture_seeding": (
            total >= coverage_sample_floor
            and top_coverage >= 0.80
            and parse_success_rate >= 0.95
        ),
        "planned_full_corpus_completed": total >= 600,
        "top_idioms": [
            {"idiom": key, "count": count, "fraction": count / max(total, 1)}
            for key, count in top
        ],
        "model_digests": sorted({str(row.get("model_digest", "") or "") for row in rows if row.get("model_digest")}),
        "backend_versions": sorted({str(row.get("backend_version", "") or "") for row in rows if row.get("backend_version")}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _representative_cases(cases: list[DomainExpansionCase]) -> list[DomainExpansionCase]:
    preferred = (
        "control_evolution",
        "control_deseq",
        "control_germline",
        "control_transcript",
        "domain_longread_sv",
        "domain_spatial",
        "domain_proteomics",
        "domain_metabolomics",
        "domain_metagenomics",
        "stress_noisy_evolution",
    )
    by_id = {case.case_id: case for case in cases}
    selected = [by_id[case_id] for case_id in preferred if case_id in by_id]
    seen = {case.case_id for case in selected}
    selected.extend(case for case in cases if case.case_id not in seen)
    return selected


def _row_idiom_key(row: dict[str, Any]) -> str:
    if row.get("raw_emission"):
        parsed = parse_raw_emission(row.get("raw_emission"))
        if parsed:
            return plan_idiom_key(plan_idiom_summary(parsed))
    return str(row.get("idiom_key", "") or "unparseable")


def _temperature_label(value: float) -> str:
    return str(float(value)).replace("-", "m").replace(".", "p")


def _completed_emission_ids(path: Path) -> set[str]:
    return {
        str(row.get("emission_id", "") or "")
        for row in _load_existing_rows(path)
        if str(row.get("emission_id", "") or "").strip()
    }


def _load_existing_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
