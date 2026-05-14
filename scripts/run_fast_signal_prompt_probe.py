#!/usr/bin/env python3
"""Collect paired planner emissions for A3 prompt-sensitivity probes."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.agents.orchestrator import Orchestrator  # noqa: E402
from bio_harness.core.benchmark_policy import (  # noqa: E402
    BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    SCIENTIFIC_HARNESS_POLICY,
    normalize_benchmark_policy,
)
from bio_harness.core.domain_expansion_ablation import DomainExpansionCase  # noqa: E402
from bio_harness.core.fast_signal import (  # noqa: E402
    parse_raw_emission,
    plan_idiom_key,
    plan_idiom_summary,
)
from bio_harness.core.fast_signal_prompt_sensitivity import (  # noqa: E402
    measure_prompt_sensitivity,
)
from bio_harness.core.llm_backends import backend_default_host, normalize_backend_name  # noqa: E402
from bio_harness.harness.config import SKILLS_DEFINITIONS, SKILLS_LIBRARY  # noqa: E402
from scripts.run_fast_signal_plan_shape_corpus import (  # noqa: E402
    append_jsonl,
    fetch_backend_metadata,
    select_manifest_cases,
)

DEFAULT_MANIFEST = PROJECT_ROOT / "workspace" / "benchmark_data" / "ablation_manifest_24.json"
DEFAULT_OUTPUT_JSONL = PROJECT_ROOT / "workspace" / "studies" / "prompt_sensitivity_qwen36.jsonl"
DEFAULT_SUMMARY_JSON = PROJECT_ROOT / "workspace" / "studies" / "prompt_sensitivity_qwen36_summary.json"
DEFAULT_SELECTED_ROOT = PROJECT_ROOT / "workspace" / "studies" / "prompt_sensitivity_selected"
DEFAULT_FEATURES = ("branch_stage_hint", "forbidden_work_wording")
DEFAULT_TEMPERATURES = (0.3,)
DEFAULT_MODEL = (
    os.getenv("BIO_HARNESS_MODEL_HEAVY", "").strip()
    or os.getenv("BIO_HARNESS_MODEL", "").strip()
    or "qwen3.6:35b-a3b"
)


@dataclass(frozen=True)
class PromptProbePair:
    """One paired control/treatment planner-emission probe.

    Attributes:
        probe_id: Stable row identifier.
        feature_name: Prompt feature being probed.
        case: Manifest case used for the base prompt.
        temperature: Planner temperature for both paired emissions.
        repetition: One-based repetition index.
        selected_dir: Synthetic selected directory used only for grounding.
    """

    probe_id: str
    feature_name: str
    case: DomainExpansionCase
    temperature: float
    repetition: int
    selected_dir: Path


def build_parser() -> argparse.ArgumentParser:
    """Build the prompt-probe CLI.

    Returns:
        Configured argument parser.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-file", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument(
        "--case-limit",
        type=int,
        default=1,
        help="Limit representative manifest cases after --case-id filtering. Use 0 for all.",
    )
    parser.add_argument(
        "--feature",
        action="append",
        choices=DEFAULT_FEATURES,
        default=[],
        help="Prompt feature to probe. Defaults to branch-stage and forbidden-work probes.",
    )
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument(
        "--temperature",
        action="append",
        type=float,
        default=[],
        help="Planner temperature. Repeatable. Defaults to 0.3.",
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
    parser.add_argument("--keep-threshold", type=float, default=0.15)
    parser.add_argument("--max-pairs", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    """Collect paired prompt-probe rows and write a summary artifact.

    Returns:
        Process exit code.
    """

    args = build_parser().parse_args()
    features = tuple(args.feature or DEFAULT_FEATURES)
    temperatures = tuple(args.temperature or DEFAULT_TEMPERATURES)
    cases = select_manifest_cases(
        manifest_file=args.manifest_file,
        case_ids=args.case_id,
        case_limit=args.case_limit,
    )
    pairs = build_probe_pairs(
        cases=cases,
        features=features,
        temperatures=temperatures,
        repetitions=max(1, int(args.repetitions)),
        selected_root=args.selected_root,
        max_pairs=max(0, int(args.max_pairs)),
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "planned_pairs": len(pairs),
                    "features": sorted({pair.feature_name for pair in pairs}),
                    "unique_cases": sorted({pair.case.case_id for pair in pairs}),
                    "temperatures": sorted({pair.temperature for pair in pairs}),
                    "output_jsonl": str(args.output_jsonl),
                    "summary_json": str(args.summary_json),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    completed_ids = _completed_probe_ids(args.output_jsonl) if args.resume else set()
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
    for pair in pairs:
        if pair.probe_id in completed_ids:
            continue
        row = collect_probe_pair(
            orchestrator=orchestrator,
            pair=pair,
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
        write_summary(
            args.summary_json,
            rows,
            keep_threshold=max(0.0, float(args.keep_threshold)),
        )
        print(
            json.dumps(
                {
                    "probe_id": row["probe_id"],
                    "feature_name": row["feature_name"],
                    "case_id": row["case_id"],
                    "temperature": row["temperature"],
                    "shape_changed": row["shape_changed"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
    write_summary(
        args.summary_json,
        rows,
        keep_threshold=max(0.0, float(args.keep_threshold)),
    )
    return 0


def build_probe_pairs(
    *,
    cases: list[DomainExpansionCase],
    features: tuple[str, ...],
    temperatures: tuple[float, ...],
    repetitions: int,
    selected_root: Path,
    max_pairs: int = 0,
) -> list[PromptProbePair]:
    """Build a deterministic grid of paired prompt probes.

    Args:
        cases: Manifest cases used as base prompts.
        features: Prompt features to compare.
        temperatures: Temperatures to sample.
        repetitions: One-based repetition count.
        selected_root: Root for synthetic selected directories.
        max_pairs: Optional smoke cap.

    Returns:
        Ordered prompt-probe pair list.
    """

    pairs: list[PromptProbePair] = []
    root = selected_root.expanduser().resolve(strict=False)
    for feature in features:
        for case in cases:
            for temperature in temperatures:
                temp_label = _temperature_label(temperature)
                for repetition in range(1, repetitions + 1):
                    probe_id = (
                        f"{feature}.{case.case_id}.t{temp_label}.r{repetition:02d}"
                    )
                    pairs.append(
                        PromptProbePair(
                            probe_id=probe_id,
                            feature_name=feature,
                            case=case,
                            temperature=float(temperature),
                            repetition=repetition,
                            selected_dir=(
                                root
                                / feature
                                / case.case_id
                                / f"t{temp_label}"
                                / f"rep_{repetition:02d}"
                            ),
                        )
                    )
                    if max_pairs and len(pairs) >= max_pairs:
                        return pairs
    return pairs


def collect_probe_pair(
    *,
    orchestrator: Orchestrator,
    pair: PromptProbePair,
    model: str,
    backend_name: str,
    host: str,
    benchmark_policy: str,
    backend_metadata: dict[str, str],
    num_ctx: int,
    num_predict: int,
) -> dict[str, Any]:
    """Collect one paired control/treatment planner-emission probe.

    Args:
        orchestrator: Initialized orchestrator used for prompt construction.
        pair: Pair specification.
        model: Planner model tag.
        backend_name: Normalized backend name.
        host: Backend host.
        benchmark_policy: Benchmark policy for analysis-spec grounding.
        backend_metadata: Backend/model metadata.
        num_ctx: Context window.
        num_predict: Prediction token budget.

    Returns:
        JSON-compatible paired probe row.
    """

    base_prompt = Path(pair.case.prompt_file).read_text(encoding="utf-8").strip()
    pair.selected_dir.mkdir(parents=True, exist_ok=True)
    analysis_spec = orchestrator.build_analysis_spec(
        base_prompt,
        contract={},
        selected_dir=str(pair.selected_dir),
        data_root=pair.case.data_root,
        project_root=str(PROJECT_ROOT),
        benchmark_policy=benchmark_policy,
    )
    available = orchestrator._available_skill_metadata()
    selected_skills, selection_meta = orchestrator._select_planner_skill_metadata(
        base_prompt,
        available,
        analysis_spec=analysis_spec,
    )
    control = collect_probe_variant(
        orchestrator=orchestrator,
        base_prompt=base_prompt,
        selected_skills=selected_skills,
        analysis_spec=analysis_spec,
        feature_name=pair.feature_name,
        variant="control",
        model=model,
        temperature=pair.temperature,
        num_ctx=num_ctx,
        num_predict=num_predict,
    )
    treatment = collect_probe_variant(
        orchestrator=orchestrator,
        base_prompt=base_prompt,
        selected_skills=selected_skills,
        analysis_spec=analysis_spec,
        feature_name=pair.feature_name,
        variant="treatment",
        model=model,
        temperature=pair.temperature,
        num_ctx=num_ctx,
        num_predict=num_predict,
    )
    control_key = str(control.get("idiom_key", "") or "")
    treatment_key = str(treatment.get("idiom_key", "") or "")
    return {
        "schema_version": 1,
        "kind": "prompt_sensitivity_probe_pair",
        "probe_id": pair.probe_id,
        "feature_name": pair.feature_name,
        "case_id": pair.case.case_id,
        "band": pair.case.band,
        "prompt_file": pair.case.prompt_file,
        "data_root": pair.case.data_root,
        "selected_dir": str(pair.selected_dir),
        "model": model,
        "model_digest": backend_metadata.get("model_digest", ""),
        "backend_name": backend_name,
        "backend_version": backend_metadata.get("backend_version", ""),
        "host": host,
        "temperature": pair.temperature,
        "repetition": pair.repetition,
        "num_ctx": num_ctx,
        "num_predict": num_predict,
        "benchmark_policy": benchmark_policy,
        "analysis_type": str(analysis_spec.get("analysis_type", "") or ""),
        "analysis_family": str(analysis_spec.get("analysis_family", "") or ""),
        "planner_skill_selection": selection_meta,
        "control": control,
        "treatment": treatment,
        "shape_changed": bool(control_key and treatment_key and control_key != treatment_key),
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def collect_probe_variant(
    *,
    orchestrator: Orchestrator,
    base_prompt: str,
    selected_skills: list[dict[str, Any]],
    analysis_spec: dict[str, Any],
    feature_name: str,
    variant: str,
    model: str,
    temperature: float,
    num_ctx: int,
    num_predict: int,
) -> dict[str, Any]:
    """Collect one control or treatment emission for a prompt probe.

    Args:
        orchestrator: Initialized orchestrator used for prompt construction.
        base_prompt: Original manifest prompt.
        selected_skills: Skill metadata selected from the unmodified prompt.
        analysis_spec: Analysis spec built from the unmodified prompt.
        feature_name: Prompt feature being tested.
        variant: Either ``control`` or ``treatment``.
        model: Planner model tag.
        temperature: Planner temperature.
        num_ctx: Context window.
        num_predict: Prediction token budget.

    Returns:
        JSON-compatible variant payload.
    """

    started = time.monotonic()
    extra = feature_prompt_extra(feature_name=feature_name, variant=variant)
    planner_prompt = f"{base_prompt.rstrip()}\n\n{extra}".strip()
    messages = orchestrator.biollm._build_workflow_messages(
        planner_prompt,
        selected_skills,
        analysis_spec=analysis_spec,
    )
    raw = orchestrator.biollm._backend.chat(
        model_name=model,
        messages=messages,
        temperature=float(temperature),
        num_ctx=num_ctx,
        num_predict=num_predict,
        format_spec="json",
    )
    parsed = parse_raw_emission(raw)
    idioms = plan_idiom_summary(parsed) if parsed else {}
    return {
        "variant": variant,
        "prompt_extra": extra,
        "raw_emission": raw,
        "raw_emission_len": len(raw),
        "parsed_ok": bool(parsed),
        "idiom_summary": idioms,
        "idiom_key": plan_idiom_key(idioms) if idioms else "unparseable",
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def feature_prompt_extra(*, feature_name: str, variant: str) -> str:
    """Return the prompt text added for one probe variant.

    Args:
        feature_name: Prompt feature being tested.
        variant: Either ``control`` or ``treatment``.

    Returns:
        Prompt text appended to the base manifest prompt.

    Raises:
        ValueError: If the feature or variant is unknown.
    """

    if variant not in {"control", "treatment"}:
        raise ValueError(f"Unknown prompt-probe variant: {variant}")
    if feature_name == "branch_stage_hint":
        if variant == "control":
            return (
                "Stepwise progress note: no structured branch-stage frontier "
                "is available for this turn."
            )
        return (
            "Stepwise branch-stage frontier for this turn:\n"
            "- Current next incomplete cell: branch `evol2`, stage `align`.\n"
            "- Required next tool: `bwa_mem_align`.\n"
            "- Do not race ahead to variant calling, normalization, intersection, "
            "or annotation until this branch-local alignment exists."
        )
    if feature_name == "forbidden_work_wording":
        if variant == "control":
            return (
                "Forbidden tools for this turn: `bwa_mem_align`. Pick a different "
                "tool that advances unfinished work."
            )
        return (
            "Forbidden repeated work for this turn: `bwa_mem_align` is already "
            "completed with identical arguments; re-proposing it will be rejected. "
            "Emit a different concrete unfinished branch-local step instead."
        )
    raise ValueError(f"Unknown prompt-probe feature: {feature_name}")


def write_summary(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    keep_threshold: float,
) -> dict[str, Any]:
    """Write a prompt-probe summary artifact.

    Args:
        path: Destination summary JSON.
        rows: Prompt-probe pair rows.
        keep_threshold: Minimum changed-pair fraction for feature retention.

    Returns:
        Summary payload.
    """

    payload = summarize_probe_rows(rows, keep_threshold=keep_threshold)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def summarize_probe_rows(
    rows: list[dict[str, Any]],
    *,
    keep_threshold: float = 0.15,
) -> dict[str, Any]:
    """Summarize paired prompt-probe rows by feature.

    Args:
        rows: Prompt-probe rows.
        keep_threshold: Minimum changed-pair fraction for feature retention.

    Returns:
        JSON-compatible summary payload.
    """

    rows_by_feature: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        feature_name = str(row.get("feature_name", "") or "")
        if feature_name:
            rows_by_feature[feature_name].append(row)

    feature_summaries: list[dict[str, Any]] = []
    for feature_name in sorted(rows_by_feature):
        feature_rows = rows_by_feature[feature_name]
        control_payloads = [
            parse_raw_emission(row.get("control", {}).get("raw_emission"))
            for row in feature_rows
        ]
        treatment_payloads = [
            parse_raw_emission(row.get("treatment", {}).get("raw_emission"))
            for row in feature_rows
        ]
        measurement = measure_prompt_sensitivity(
            feature_name=feature_name,
            control_payloads=control_payloads,
            treatment_payloads=treatment_payloads,
            keep_threshold=keep_threshold,
        )
        feature_summaries.append(
            {
                **asdict(measurement),
                "case_ids": sorted({str(row.get("case_id", "") or "") for row in feature_rows}),
                "temperatures": sorted(
                    {float(row.get("temperature", 0.0) or 0.0) for row in feature_rows}
                ),
                "control_parse_successes": sum(
                    1 for row in feature_rows if bool(row.get("control", {}).get("parsed_ok"))
                ),
                "treatment_parse_successes": sum(
                    1 for row in feature_rows if bool(row.get("treatment", {}).get("parsed_ok"))
                ),
            }
        )

    return {
        "schema_version": 1,
        "kind": "prompt_sensitivity_summary",
        "pair_count": len(rows),
        "feature_count": len(feature_summaries),
        "keep_threshold": keep_threshold,
        "features": feature_summaries,
        "model_digests": sorted({str(row.get("model_digest", "") or "") for row in rows if row.get("model_digest")}),
        "backend_versions": sorted({str(row.get("backend_version", "") or "") for row in rows if row.get("backend_version")}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _temperature_label(value: float) -> str:
    return str(float(value)).replace("-", "m").replace(".", "p")


def _completed_probe_ids(path: Path) -> set[str]:
    return {
        str(row.get("probe_id", "") or "")
        for row in _load_existing_rows(path)
        if str(row.get("probe_id", "") or "").strip()
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
