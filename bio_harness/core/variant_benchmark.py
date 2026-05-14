"""Harness variant result storage and comparison helpers.

This module keeps harness variants as first-class benchmark artifacts without
introducing a second benchmark runner. It stores results in JSONL and provides
pairwise comparison summaries over shared tasks.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_PASS_STATUSES = {
    "pass",
    "passed",
    "success",
    "completed",
    "official_blind_clean",
}
_CONFIG_OVERRIDE_ENV_KEYS = {
    "diagnostic_traces": "BIO_HARNESS_DIAGNOSTIC_TRACES",
    "nonmarkovian_repair": "BIO_HARNESS_NONMARKOVIAN_REPAIR",
    "env_bootstrap": "BIO_HARNESS_ENV_BOOTSTRAP",
    "trace_advisories": "BIO_HARNESS_TRACE_ADVISORIES",
    "protocol_template_assistance": "BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE",
}
_CONFIG_OVERRIDE_CLI_FLAGS = {
    "max_repairs": "--max-repairs",
    "execution_mode": "--execution-mode",
}


@dataclass(frozen=True)
class HarnessVariant:
    """One named harness configuration for benchmarking."""

    variant_id: str
    description: str
    env_overrides: dict[str, str] = field(default_factory=dict)
    config_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VariantResult:
    """Result of running one task under one harness variant."""

    variant_id: str
    task_name: str
    status: str
    score: float = 0.0
    runtime_seconds: float = 0.0
    repairs_needed: int = 0
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VariantComparison:
    """Comparison summary between two harness variants."""

    variant_a: str
    variant_b: str
    tasks_compared: int = 0
    a_wins: int = 0
    b_wins: int = 0
    ties: int = 0
    a_pass_rate: float = 0.0
    b_pass_rate: float = 0.0
    a_mean_runtime: float = 0.0
    b_mean_runtime: float = 0.0
    a_mean_repairs: float = 0.0
    b_mean_repairs: float = 0.0
    per_task: list[dict[str, Any]] = field(default_factory=list)


ABLATION_VARIANTS: dict[str, HarnessVariant] = {
    "baseline": HarnessVariant(
        variant_id="baseline",
        description="Full Bio-Harness configuration.",
    ),
    "qwen_baseline": HarnessVariant(
        variant_id="qwen_baseline",
        description="Qwen planner and executor baseline.",
        env_overrides={
            "BIO_HARNESS_MODEL": "qwen3-coder-next:latest",
            "BIO_HARNESS_MODEL_HEAVY": "qwen3-coder-next:latest",
        },
    ),
    "no_templates": HarnessVariant(
        variant_id="no_templates",
        description="Disable planner template fastpath.",
        env_overrides={"BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH": "0"},
    ),
    "no_recovery": HarnessVariant(
        variant_id="no_recovery",
        description="Disable automatic repair loops.",
        config_overrides={"max_repairs": 0},
    ),
    "fast_model_only": HarnessVariant(
        variant_id="fast_model_only",
        description="Use the fast model for both planning and execution.",
        env_overrides={"BIO_HARNESS_MODEL_HEAVY": ""},
    ),
    "gemma4_26b": HarnessVariant(
        variant_id="gemma4_26b",
        description="Gemma 4 26B for both planning and execution.",
        env_overrides={
            "BIO_HARNESS_MODEL": "gemma4:26b",
            "BIO_HARNESS_MODEL_HEAVY": "gemma4:26b",
        },
    ),
    "gemma4_31b": HarnessVariant(
        variant_id="gemma4_31b",
        description="Gemma 4 31B for both planning and execution.",
        env_overrides={
            "BIO_HARNESS_MODEL": "gemma4:31b",
            "BIO_HARNESS_MODEL_HEAVY": "gemma4:31b",
        },
    ),
}


class VariantBenchmarkStore:
    """Persist and compare variant benchmark results."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_result(self, result: VariantResult) -> None:
        """Append one result to the JSONL store."""

        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(result), sort_keys=True) + "\n")

    def load_results(
        self,
        *,
        variant_id: str | None = None,
        task_name: str | None = None,
    ) -> list[VariantResult]:
        """Load stored results, optionally filtered by variant and task."""

        if not self.path.is_file():
            return []
        rows: list[VariantResult] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            result = _variant_result_from_payload(payload)
            if variant_id and result.variant_id != str(variant_id):
                continue
            if task_name and result.task_name != str(task_name):
                continue
            rows.append(result)
        return rows

    def compare_variants(self, variant_a: str, variant_b: str) -> VariantComparison:
        """Compare two variants over their shared tasks."""

        a_results = {row.task_name: row for row in self.load_results(variant_id=variant_a)}
        b_results = {row.task_name: row for row in self.load_results(variant_id=variant_b)}
        shared_tasks = sorted(set(a_results).intersection(b_results))
        if not shared_tasks:
            return VariantComparison(variant_a=variant_a, variant_b=variant_b)

        per_task: list[dict[str, Any]] = []
        a_wins = 0
        b_wins = 0
        ties = 0
        a_passes = 0
        b_passes = 0
        a_runtime = 0.0
        b_runtime = 0.0
        a_repairs = 0
        b_repairs = 0

        for task_name in shared_tasks:
            a_row = a_results[task_name]
            b_row = b_results[task_name]
            winner = "tie"
            if a_row.score > b_row.score:
                a_wins += 1
                winner = variant_a
            elif b_row.score > a_row.score:
                b_wins += 1
                winner = variant_b
            else:
                ties += 1
            if _is_pass_status(a_row.status):
                a_passes += 1
            if _is_pass_status(b_row.status):
                b_passes += 1
            a_runtime += float(a_row.runtime_seconds)
            b_runtime += float(b_row.runtime_seconds)
            a_repairs += int(a_row.repairs_needed)
            b_repairs += int(b_row.repairs_needed)
            per_task.append(
                {
                    "task": task_name,
                    "task_name": task_name,
                    "winner": winner,
                    "a_status": a_row.status,
                    "b_status": b_row.status,
                    "a_score": a_row.score,
                    "b_score": b_row.score,
                }
            )

        task_count = len(shared_tasks)
        return VariantComparison(
            variant_a=variant_a,
            variant_b=variant_b,
            tasks_compared=task_count,
            a_wins=a_wins,
            b_wins=b_wins,
            ties=ties,
            a_pass_rate=round(a_passes / task_count, 4),
            b_pass_rate=round(b_passes / task_count, 4),
            a_mean_runtime=round(a_runtime / task_count, 4),
            b_mean_runtime=round(b_runtime / task_count, 4),
            a_mean_repairs=round(a_repairs / task_count, 4),
            b_mean_repairs=round(b_repairs / task_count, 4),
            per_task=per_task,
        )

    def summary_table(self) -> str:
        """Render one Markdown summary table over recorded variants."""

        results = self.load_results()
        if not results:
            return "No results recorded yet."

        grouped: dict[str, list[VariantResult]] = {}
        for row in results:
            grouped.setdefault(row.variant_id, []).append(row)

        lines = [
            "| Variant | Tasks | Pass Rate | Mean Runtime (s) | Mean Repairs |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for variant_id in sorted(grouped):
            rows = grouped[variant_id]
            task_count = len(rows)
            pass_count = sum(1 for row in rows if _is_pass_status(row.status))
            mean_runtime = sum(float(row.runtime_seconds) for row in rows) / task_count
            mean_repairs = sum(int(row.repairs_needed) for row in rows) / task_count
            lines.append(
                "| "
                f"{variant_id} | {task_count} | {pass_count / task_count:.1%} | "
                f"{mean_runtime:.1f} | {mean_repairs:.1f} |"
            )
        return "\n".join(lines)


def config_override_env(config_overrides: dict[str, Any]) -> dict[str, str]:
    """Render supported config overrides into environment variables."""

    rendered: dict[str, str] = {}
    for key, value in config_overrides.items():
        env_key = _CONFIG_OVERRIDE_ENV_KEYS.get(str(key).strip(), "")
        if not env_key:
            continue
        rendered[env_key] = str(value)
    return rendered


def config_override_cli_args(config_overrides: dict[str, Any]) -> list[str]:
    """Render supported config overrides into CLI arguments."""

    args: list[str] = []
    for key, value in config_overrides.items():
        flag = _CONFIG_OVERRIDE_CLI_FLAGS.get(str(key).strip(), "")
        if not flag:
            continue
        args.extend([flag, str(value)])
    return args


def _variant_result_from_payload(payload: dict[str, Any]) -> VariantResult:
    return VariantResult(
        variant_id=str(payload.get("variant_id", "") or "").strip(),
        task_name=str(payload.get("task_name", "") or "").strip(),
        status=str(payload.get("status", "") or "").strip(),
        score=float(payload.get("score", 0.0) or 0.0),
        runtime_seconds=float(payload.get("runtime_seconds", 0.0) or 0.0),
        repairs_needed=int(payload.get("repairs_needed", 0) or 0),
        error_message=str(payload.get("error_message", "") or "").strip(),
        metadata=dict(payload.get("metadata", {})) if isinstance(payload.get("metadata", {}), dict) else {},
    )


def _is_pass_status(status: str) -> bool:
    return str(status or "").strip().lower() in _PASS_STATUSES


__all__ = [
    "ABLATION_VARIANTS",
    "config_override_cli_args",
    "config_override_env",
    "HarnessVariant",
    "VariantBenchmarkStore",
    "VariantComparison",
    "VariantResult",
]
