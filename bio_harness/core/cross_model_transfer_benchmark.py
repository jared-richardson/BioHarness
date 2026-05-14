"""Cross-model tool-card transfer benchmark helpers.

This module defines the fixed Q→Q, Q→G, G→Q, and G→G transfer cells used by
the research plan, plus deterministic summary logic for success-rate deltas and
paired McNemar-style comparisons.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

from bio_harness.skills.registry import SkillRegistry

QWEN_MODEL = "qwen3-coder-next:latest"
GEMMA26_MODEL = "gemma4:26b"
STAGE1_TRANSFER_TASKS = (
    "metagenomics",
    "transcript-quant",
    "single-cell",
    "deseq",
    "variant-annotation",
)
EXPECTED_SKILLS_BY_TASK = {
    "transcript-quant": ("salmon_quant", "kallisto_quant", "stringtie_quant"),
    "single-cell": ("scanpy_workflow", "sc_count_and_cluster", "star_solo_count"),
    "deseq": ("deseq2_run", "featurecounts_run", "subread_align"),
    "metagenomics": ("metagenomics_kraken2_bracken_style", "sylph_classify"),
    "variant-annotation": ("snpeff_annotate", "vep_annotate"),
    "sylph_cold_case": ("sylph_classify",),
}


@dataclass(frozen=True)
class CrossModelTransferCell:
    """One builder/user cell in the cross-model transfer matrix."""

    tag: str
    label: str
    builder_model: str
    user_model: str
    transfer: bool


@dataclass(frozen=True)
class CrossModelTransferRecord:
    """One evaluated or planned cross-model transfer run."""

    task_id: str
    seed: int
    cell_tag: str
    builder_model: str
    user_model: str
    success: bool | None = None
    wrapper_first_try_success: bool | None = None
    plan_valid_without_repair: bool | None = None
    retrieval_recall_at_3: float | None = None
    builder_cards_dir: str = ""
    selected_dir: str = ""
    run_dir: str = ""
    result_json: str = ""
    official_report_bucket: str = ""
    validation_passed: bool | None = None
    auto_repair_history_count: int | None = None
    planner_failopen_used: bool | None = None
    planner_strategy_used: str = ""
    contract_validation_passed: bool | None = None
    notes: str = ""


def default_transfer_cells(
    *,
    qwen_model: str = QWEN_MODEL,
    gemma_model: str = GEMMA26_MODEL,
) -> tuple[CrossModelTransferCell, ...]:
    """Return the fixed 2x2 builder/user cell matrix.

    Args:
        qwen_model: Qwen model identifier.
        gemma_model: Gemma model identifier.

    Returns:
        Tuple of fixed transfer cells.
    """

    return (
        CrossModelTransferCell("q_to_q", "Q→Q", qwen_model, qwen_model, False),
        CrossModelTransferCell("q_to_g", "Q→G", qwen_model, gemma_model, True),
        CrossModelTransferCell("g_to_q", "G→Q", gemma_model, qwen_model, True),
        CrossModelTransferCell("g_to_g", "G→G", gemma_model, gemma_model, False),
    )


def planned_transfer_records(
    tasks: Sequence[str],
    *,
    num_seeds: int,
    qwen_model: str = QWEN_MODEL,
    gemma_model: str = GEMMA26_MODEL,
) -> tuple[CrossModelTransferRecord, ...]:
    """Build the planned run matrix for the cross-model transfer benchmark.

    Args:
        tasks: Ordered task identifiers.
        num_seeds: Number of seeds per task/cell.
        qwen_model: Qwen model identifier.
        gemma_model: Gemma model identifier.

    Returns:
        Tuple of planned run records without outcomes.
    """

    records: list[CrossModelTransferRecord] = []
    for task_id in tasks:
        normalized_task = str(task_id).strip()
        if not normalized_task:
            continue
        for seed in range(num_seeds):
            for cell in default_transfer_cells(qwen_model=qwen_model, gemma_model=gemma_model):
                records.append(
                    CrossModelTransferRecord(
                        task_id=normalized_task,
                        seed=seed,
                        cell_tag=cell.tag,
                        builder_model=cell.builder_model,
                        user_model=cell.user_model,
                    )
                )
    return tuple(records)


def stage1_transfer_tasks() -> tuple[str, ...]:
    """Return the recommended Stage 1 transfer task set.

    Returns:
        Tuple of Stage 1 task identifiers.
    """

    return STAGE1_TRANSFER_TASKS


def expected_skills_for_task(task_id: str) -> tuple[str, ...]:
    """Return the expected wrapped skills used for retrieval recall checks.

    Args:
        task_id: Benchmark task identifier.

    Returns:
        Tuple of expected skill names, possibly empty.
    """

    return tuple(EXPECTED_SKILLS_BY_TASK.get(str(task_id).strip(), ()))


def retrieval_recall_at_k(
    *,
    query: str,
    skills_dir: Path,
    expected_skill_names: Sequence[str],
    tool_cards_dir: Path | None = None,
    k: int = 3,
) -> float | None:
    """Compute retrieval recall@k for a prompt against the current registry.

    Args:
        query: Retrieval query.
        skills_dir: Skill-definition directory.
        expected_skill_names: Expected relevant skills.
        tool_cards_dir: Optional tool-card directory.
        k: Retrieval depth.

    Returns:
        ``1.0`` when any expected skill is retrieved in the top-k, ``0.0`` when
        none are retrieved, and ``None`` when no expected skills were provided.
    """

    expected = {str(item).strip() for item in expected_skill_names if str(item).strip()}
    if not expected:
        return None
    registry = SkillRegistry(skills_dir)
    results = registry.search_skills(query, limit=max(1, int(k)), tool_cards_dir=tool_cards_dir)
    names = {str(row.get("name", "")).strip() for row in results[: max(1, int(k))]}
    return 1.0 if names & expected else 0.0


def summarize_transfer_records(
    records: Iterable[CrossModelTransferRecord],
    *,
    success_margin: float = 0.10,
) -> dict[str, Any]:
    """Summarize completed cross-model transfer records.

    Args:
        records: Completed run records.
        success_margin: Allowed transfer-vs-home success-rate delta.

    Returns:
        Summary dictionary with per-cell rates and paired transfer comparisons.
    """

    rows = [record for record in records]
    by_cell: dict[str, list[CrossModelTransferRecord]] = {}
    for row in rows:
        by_cell.setdefault(row.cell_tag, []).append(row)

    cell_stats: dict[str, dict[str, Any]] = {}
    for cell in default_transfer_cells(
        qwen_model=_model_for_tag(rows, "q_to_q", default=QWEN_MODEL),
        gemma_model=_model_for_tag(rows, "g_to_g", default=GEMMA26_MODEL),
    ):
        cell_rows = by_cell.get(cell.tag, [])
        cell_stats[cell.tag] = {
            "label": cell.label,
            "builder_model": cell.builder_model,
            "user_model": cell.user_model,
            "transfer": cell.transfer,
            "runs": len(cell_rows),
            "success_rate": _bool_rate(row.success for row in cell_rows),
            "first_try_wrapper_rate": _bool_rate(row.wrapper_first_try_success for row in cell_rows),
            "plan_valid_without_repair_rate": _bool_rate(row.plan_valid_without_repair for row in cell_rows),
            "retrieval_recall_at_3_mean": _float_mean(row.retrieval_recall_at_3 for row in cell_rows),
        }

    comparisons = {
        "q_to_g_vs_g_to_g": _paired_comparison(
            by_cell.get("q_to_g", []),
            by_cell.get("g_to_g", []),
            transfer_tag="q_to_g",
            home_tag="g_to_g",
            success_margin=success_margin,
        ),
        "g_to_q_vs_q_to_q": _paired_comparison(
            by_cell.get("g_to_q", []),
            by_cell.get("q_to_q", []),
            transfer_tag="g_to_q",
            home_tag="q_to_q",
            success_margin=success_margin,
        ),
    }
    return {
        "planned_cells": [asdict(cell) for cell in default_transfer_cells()],
        "record_count": len(rows),
        "cell_stats": cell_stats,
        "comparisons": comparisons,
    }


def render_transfer_summary_markdown(summary: dict[str, Any]) -> str:
    """Render a Markdown summary for cross-model transfer results.

    Args:
        summary: Summary payload from :func:`summarize_transfer_records`.

    Returns:
        Markdown summary string.
    """

    lines = [
        "# Cross-Model Transfer Summary",
        "",
        "## Cell Statistics",
        "",
        "| Cell | Runs | Success | First Try Wrapper | Plan Valid No Repair | Recall@3 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for tag in ("q_to_q", "q_to_g", "g_to_q", "g_to_g"):
        cell = dict(summary.get("cell_stats", {}).get(tag, {}))
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{cell.get('label', tag)}`",
                    str(cell.get("runs", 0)),
                    f"{float(cell.get('success_rate', 0.0)):.3f}",
                    f"{float(cell.get('first_try_wrapper_rate', 0.0)):.3f}",
                    f"{float(cell.get('plan_valid_without_repair_rate', 0.0)):.3f}",
                    f"{float(cell.get('retrieval_recall_at_3_mean', 0.0)):.3f}",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Transfer Comparisons", ""])
    for name, payload in (summary.get("comparisons", {}) or {}).items():
        lines.append(f"### `{name}`")
        lines.append("")
        lines.append(f"- Transfer success rate: `{float(payload.get('transfer_success_rate', 0.0)):.3f}`")
        lines.append(f"- Home success rate: `{float(payload.get('home_success_rate', 0.0)):.3f}`")
        lines.append(f"- Absolute delta: `{float(payload.get('absolute_success_delta', 0.0)):.3f}`")
        lines.append(f"- Within margin: `{bool(payload.get('within_margin', False))}`")
        lines.append(f"- McNemar b/c: `{payload.get('mcnemar_b', 0)}` / `{payload.get('mcnemar_c', 0)}`")
        lines.append(f"- McNemar p-value: `{float(payload.get('mcnemar_exact_p_value', 1.0)):.6f}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_transfer_summary(
    output_json: Path,
    output_md: Path,
    summary: dict[str, Any],
) -> tuple[Path, Path]:
    """Write cross-model transfer summaries to JSON and Markdown.

    Args:
        output_json: JSON output path.
        output_md: Markdown output path.
        summary: Summary payload.

    Returns:
        Tuple of written `(json_path, markdown_path)`.
    """

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_md.write_text(render_transfer_summary_markdown(summary), encoding="utf-8")
    return output_json, output_md


def _bool_rate(values: Iterable[bool | None]) -> float:
    resolved = [value for value in values if value is not None]
    if not resolved:
        return 0.0
    return round(sum(1 for value in resolved if value) / len(resolved), 6)


def _float_mean(values: Iterable[float | None]) -> float:
    resolved = [float(value) for value in values if value is not None]
    if not resolved:
        return 0.0
    return round(sum(resolved) / len(resolved), 6)


def _record_key(record: CrossModelTransferRecord) -> tuple[str, int]:
    return str(record.task_id).strip(), int(record.seed)


def _paired_comparison(
    transfer_rows: Sequence[CrossModelTransferRecord],
    home_rows: Sequence[CrossModelTransferRecord],
    *,
    transfer_tag: str,
    home_tag: str,
    success_margin: float,
) -> dict[str, Any]:
    transfer_map = {_record_key(row): row for row in transfer_rows}
    home_map = {_record_key(row): row for row in home_rows}
    keys = sorted(set(transfer_map) & set(home_map))
    b = 0
    c = 0
    for key in keys:
        transfer_success = bool(transfer_map[key].success)
        home_success = bool(home_map[key].success)
        if transfer_success and not home_success:
            b += 1
        elif not transfer_success and home_success:
            c += 1
    transfer_success_rate = _bool_rate(row.success for row in transfer_rows)
    home_success_rate = _bool_rate(row.success for row in home_rows)
    absolute_delta = round(abs(transfer_success_rate - home_success_rate), 6)
    return {
        "transfer_tag": transfer_tag,
        "home_tag": home_tag,
        "paired_runs": len(keys),
        "transfer_success_rate": transfer_success_rate,
        "home_success_rate": home_success_rate,
        "absolute_success_delta": absolute_delta,
        "within_margin": absolute_delta <= success_margin,
        "mcnemar_b": b,
        "mcnemar_c": c,
        "mcnemar_exact_p_value": _mcnemar_exact_p_value(b, c),
    }


def _mcnemar_exact_p_value(b: int, c: int) -> float:
    n = int(b) + int(c)
    if n <= 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / (2**n)
    return round(min(1.0, 2.0 * tail), 6)


def _model_for_tag(records: Sequence[CrossModelTransferRecord], tag: str, *, default: str) -> str:
    for row in records:
        if row.cell_tag == tag:
            return row.user_model if tag.endswith("_to_q") or tag.endswith("_to_g") else row.user_model
    return default


__all__ = [
    "CrossModelTransferCell",
    "CrossModelTransferRecord",
    "GEMMA26_MODEL",
    "QWEN_MODEL",
    "default_transfer_cells",
    "planned_transfer_records",
    "render_transfer_summary_markdown",
    "summarize_transfer_records",
    "write_transfer_summary",
]
