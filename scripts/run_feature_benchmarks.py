#!/usr/bin/env python3
"""Run the feature benchmark suite for bio_harness.

Tests all 7 new features (output quality gate, preflight scanner, output
catalog, result interpreter, error diagnosis, quality compare, literature
agent) plus a cross-feature integration benchmark.  Each feature loads its
scenarios from ``<data-root>/<feature>/scenarios.json`` and produces a
structured JSON report.

Usage:
    python3 scripts/run_feature_benchmarks.py                            # All features
    python3 scripts/run_feature_benchmarks.py --feature output-quality-gate
    python3 scripts/run_feature_benchmarks.py --feature error-diagnosis --scenario oom_spades
    python3 scripts/run_feature_benchmarks.py --report results/report.json --quick

Exit code 0 if all hard gates pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    """Result of a single benchmark scenario."""

    feature: str
    scenario_id: str
    description: str
    passed: bool
    score: float  # 0.0-1.0
    checks_total: int
    checks_passed: int
    checks_failed: int
    details: dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    error: str = ""


@dataclass
class FeatureBenchReport:
    """Aggregate report across all features."""

    timestamp: str
    total_scenarios: int
    total_passed: int
    total_failed: int
    total_skipped: int
    overall_score: float
    features: dict[str, dict[str, Any]] = field(default_factory=dict)
    scenarios: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _load_scenarios(task_dir: Path) -> list[dict[str, Any]]:
    """Load and validate scenarios.json from *task_dir*.

    Returns an empty list and prints a warning when the file is missing or
    cannot be parsed.
    """
    scenarios_path = task_dir / "scenarios.json"
    if not scenarios_path.exists():
        print(f"  WARNING: {scenarios_path} not found, skipping feature")
        return []
    try:
        data = json.loads(scenarios_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  WARNING: failed to read {scenarios_path}: {exc}")
        return []
    if isinstance(data, dict):
        scenarios = data.get("scenarios", [])
    elif isinstance(data, list):
        scenarios = data
    else:
        print(f"  WARNING: unexpected format in {scenarios_path}")
        return []
    if not isinstance(scenarios, list):
        print(f"  WARNING: 'scenarios' key is not a list in {scenarios_path}")
        return []
    return scenarios


def _compute_ndcg(ranked_relevances: list[bool], k: int) -> float:
    """Compute normalised discounted cumulative gain at *k*.

    *ranked_relevances* is a list of booleans in the order returned by the
    system under test.  Ideal ordering sorts ``True`` items first.
    """
    if not ranked_relevances or k <= 0:
        return 0.0
    truncated = ranked_relevances[:k]

    def _dcg(rels: list[bool]) -> float:
        return sum(
            (1.0 if r else 0.0) / math.log2(i + 2) for i, r in enumerate(rels)
        )

    dcg = _dcg(truncated)
    ideal = sorted(truncated, reverse=True)
    idcg = _dcg(ideal)
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def _keyword_overlap(text_a: str, text_b: str) -> float:
    """Return Jaccard-like keyword overlap between two texts.

    Both texts are lowercased and split on non-word characters.  Stop words
    shorter than 3 characters are discarded.
    """
    def _tokens(text: str) -> set[str]:
        return {w for w in re.split(r"\W+", text.lower()) if len(w) >= 3}

    a = _tokens(text_a)
    b = _tokens(text_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _check_regex_facts(
    text: str, patterns: list[dict[str, str]]
) -> tuple[int, int]:
    """Check *text* against a list of regex fact patterns.

    Each entry in *patterns* must have a ``"pattern"`` key.  Returns
    ``(matched_count, total_count)``.
    """
    total = len(patterns)
    matched = 0
    for entry in patterns:
        pat = entry.get("pattern", "")
        if not pat:
            total -= 1
            continue
        try:
            if re.search(pat, text, re.IGNORECASE):
                matched += 1
        except re.error:
            pass
    return matched, total


def _skipped_result(
    feature: str, scenario: dict[str, Any], reason: str
) -> ScenarioResult:
    """Build a ScenarioResult for a skipped scenario."""
    return ScenarioResult(
        feature=feature,
        scenario_id=scenario.get("scenario_id", "unknown"),
        description=scenario.get("description", ""),
        passed=False,
        score=0.0,
        checks_total=0,
        checks_passed=0,
        checks_failed=0,
        details={"skipped": True, "reason": reason},
        error=reason,
    )


def _safe_ratio(numerator: int, denominator: int) -> float:
    """Return *numerator / denominator* or 0.0 when *denominator* is zero."""
    return numerator / denominator if denominator > 0 else 0.0


def _scenario_text_value(
    scenario: dict[str, Any],
    *keys: str,
    default: str = "",
) -> str:
    """Return the first non-empty string value found under the given keys."""

    for key in keys:
        value = scenario.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _scenario_list_value(
    scenario: dict[str, Any],
    *keys: str,
) -> list[Any]:
    """Return the first list value found under the given keys."""

    for key in keys:
        value = scenario.get(key)
        if isinstance(value, list):
            return value
    return []


def _scenario_bool_value(
    scenario: dict[str, Any],
    *keys: str,
    default: bool = False,
) -> bool:
    """Return the first boolean-like value found under the given keys."""

    for key in keys:
        if key in scenario:
            return bool(scenario.get(key))
    return default


def _normalize_expected_catalog_entries(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize benchmark catalog expectations into one internal shape."""

    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        if "relative_path" not in item and "path" in item:
            item["relative_path"] = str(item["path"]).strip()
        role = str(item.get("role", "") or "").strip().lower()
        if role == "deliverable":
            item["role"] = "final_deliverable"
        normalized.append(item)
    return normalized


def _normalize_catalog_format(format_name: str) -> str:
    """Normalize catalog format labels across benchmark vocabularies."""

    token = str(format_name or "").strip().lower()
    if token == "text":
        return "log"
    return token


def _benchmark_direction_for_token(token: str) -> str:
    """Normalize quality comparison direction labels to benchmark terms."""

    value = str(token or "").strip().upper()
    mapping = {
        "RUN_B": "IMPROVED",
        "B": "IMPROVED",
        "IMPROVED": "IMPROVED",
        "RUN_A": "REGRESSED",
        "A": "REGRESSED",
        "REGRESSED": "REGRESSED",
        "TIE": "STABLE",
        "SAME": "STABLE",
        "STABLE": "STABLE",
        "MIXED": "MIXED",
        "DIFFERENT_PIPELINE": "DIFFERENT_PIPELINE",
        "UNKNOWN": "UNKNOWN",
    }
    return mapping.get(value, value)


def _expected_dimension_for_metric(
    expected_dimensions: dict[str, str],
    metric_name: str,
) -> str:
    """Return the expected benchmark direction for a metric comparison."""

    exact = expected_dimensions.get(metric_name)
    if exact:
        return str(exact)
    bare_name = str(metric_name or "").rsplit(".", 1)[-1]
    return str(expected_dimensions.get(bare_name, "") or "")


def _looks_like_benchmark_input(path: Path) -> bool:
    """Return whether one benchmark scenario file should be scanned as input."""

    name = path.name.lower()
    return name.endswith(
        (
            ".fastq",
            ".fastq.gz",
            ".fq",
            ".fq.gz",
            ".fa",
            ".fa.gz",
            ".fasta",
            ".fasta.gz",
            ".fna",
            ".fna.gz",
            ".bam",
            ".sam",
            ".csv",
            ".tsv",
            ".txt",
            ".vcf",
            ".vcf.gz",
            ".gff",
            ".gff3",
            ".gtf",
        )
    )


def _build_benchmark_preflight_plan(data_root: Path) -> dict[str, Any]:
    """Synthesize a minimal scan plan from one scenario data directory."""

    arguments: dict[str, str] = {}
    for index, path in enumerate(
        sorted(item for item in data_root.rglob("*") if item.is_file() and _looks_like_benchmark_input(item)),
        start=1,
    ):
        arguments[f"input_{index}"] = str(path.relative_to(data_root))
    return {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "benchmark_input_scan",
                "arguments": arguments,
            }
        ]
    }


def _infer_preflight_analysis_type(
    scenario: dict[str, Any],
    data_root: Path,
) -> str:
    """Infer the best analysis type for benchmark input scanning."""

    explicit = _scenario_text_value(scenario, "analysis_type")
    if explicit:
        return explicit
    for path in data_root.rglob("*"):
        if path.is_file() and path.name.lower().endswith((".csv", ".tsv", ".txt")):
            return "rna_seq_differential_expression"
    return ""


def _prepare_result_interpreter_selected_dir(
    task_dir: Path,
    scenario: dict[str, Any],
) -> Path:
    """Resolve a benchmark interpretation input into a selected directory."""

    selected_token = _scenario_text_value(scenario, "selected_subdir", "directory", "file", default="outputs")
    selected_path = task_dir / selected_token
    if not selected_path.exists() or selected_path.is_dir():
        return selected_path

    scenario_id = _scenario_text_value(scenario, "scenario_id", default="scenario")
    staging_dir = task_dir / "_cache" / "result-interpreter" / scenario_id
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    _stage_result_interpreter_path(selected_path, staging_dir / selected_path.name)
    for extra_relpath in _scenario_list_value(scenario, "extra_files"):
        extra_path = task_dir / str(extra_relpath)
        if extra_path.exists() and extra_path.is_file():
            _stage_result_interpreter_path(extra_path, staging_dir / extra_path.name)
    return staging_dir


def _stage_result_interpreter_path(source_path: Path, target_path: Path) -> None:
    """Stage one benchmark interpretation artifact into a temp directory."""

    resolved_source = Path(source_path).expanduser().resolve(strict=False)
    try:
        target_path.symlink_to(resolved_source)
    except OSError:
        shutil.copy2(resolved_source, target_path)


# ---------------------------------------------------------------------------
# Feature runner: output-quality-gate
# ---------------------------------------------------------------------------

def run_output_quality_gate(
    task_dir: Path, verbose: bool, offline: bool, quick: bool
) -> list[ScenarioResult]:
    """Benchmark the output quality gate (Feature 1)."""
    try:
        from bio_harness.core.output_quality import assess_output_quality, QualityLevel
    except ImportError as exc:
        scenarios = _load_scenarios(task_dir)
        return [
            _skipped_result("output-quality-gate", s, f"ImportError: {exc}")
            for s in (scenarios or [{"scenario_id": "all"}])
        ]

    scenarios = _load_scenarios(task_dir)
    if not scenarios:
        return []
    if quick:
        scenarios = scenarios[:1]

    results: list[ScenarioResult] = []
    for sc in scenarios:
        t0 = time.monotonic()
        sid = sc.get("scenario_id", "unknown")
        desc = sc.get("description", sid)
        try:
            file_rel = sc.get("file", "")
            file_path = task_dir / file_rel if file_rel else None
            expected_level = sc.get("expected_level", "").upper()
            expected_metrics: dict[str, dict[str, float]] = sc.get("expected_metrics", {})
            expected_flags: list[str] = sc.get("expected_flags", [])
            tool_name = sc.get("tool_name", "")
            analysis_type = sc.get("analysis_type", "")

            checks_total = 0
            checks_passed = 0
            details: dict[str, Any] = {}

            if file_path is None or not file_path.exists():
                results.append(ScenarioResult(
                    feature="output-quality-gate",
                    scenario_id=sid,
                    description=desc,
                    passed=False,
                    score=0.0,
                    checks_total=1,
                    checks_passed=0,
                    checks_failed=1,
                    details={"error": f"file not found: {file_rel}"},
                    elapsed_seconds=time.monotonic() - t0,
                    error=f"file not found: {file_rel}",
                ))
                continue

            report = assess_output_quality(file_path, tool_name=tool_name, analysis_type=analysis_type)

            # Check 1: level accuracy
            predicted_level = report.overall_level.value.upper()
            # Normalise: module uses "WARNING" enum label vs truth "WARN"
            level_map = {"WARNING": "WARN", "PASS": "PASS", "FAIL": "FAIL"}
            norm_predicted = level_map.get(predicted_level, predicted_level)
            norm_expected = level_map.get(expected_level, expected_level)
            checks_total += 1
            level_ok = norm_predicted == norm_expected
            if level_ok:
                checks_passed += 1
            details["level_expected"] = norm_expected
            details["level_predicted"] = norm_predicted
            details["level_match"] = level_ok

            # Check 2: metric ranges
            metric_checks = 0
            metric_pass = 0
            metric_values: dict[str, float] = {m.name: m.value for m in report.metrics}
            for metric_name, bounds in expected_metrics.items():
                metric_checks += 1
                checks_total += 1
                val = metric_values.get(metric_name)
                if val is not None:
                    lo = bounds.get("min", float("-inf"))
                    hi = bounds.get("max", float("inf"))
                    if lo <= val <= hi:
                        metric_pass += 1
                        checks_passed += 1
            details["metric_checks"] = metric_checks
            details["metric_passed"] = metric_pass

            # Check 3: flag detection
            detected_flags = {m.name for m in report.metrics if m.level != QualityLevel.PASS}
            flag_checks = 0
            flag_pass = 0
            for flag in expected_flags:
                flag_checks += 1
                checks_total += 1
                if flag in detected_flags:
                    flag_pass += 1
                    checks_passed += 1
            details["flag_checks"] = flag_checks
            details["flag_passed"] = flag_pass

            # Check 4: false alarm (PASS expected should not be FAIL)
            if norm_expected == "PASS":
                checks_total += 1
                if norm_predicted != "FAIL":
                    checks_passed += 1
                    details["false_alarm"] = False
                else:
                    details["false_alarm"] = True

            score = _safe_ratio(checks_passed, checks_total)
            # hard gate: level must match AND no false alarms on PASS
            passed = level_ok and not details.get("false_alarm", False)

            results.append(ScenarioResult(
                feature="output-quality-gate",
                scenario_id=sid,
                description=desc,
                passed=passed,
                score=round(score, 4),
                checks_total=checks_total,
                checks_passed=checks_passed,
                checks_failed=checks_total - checks_passed,
                details=details,
                elapsed_seconds=time.monotonic() - t0,
            ))
        except Exception as exc:
            results.append(ScenarioResult(
                feature="output-quality-gate",
                scenario_id=sid,
                description=desc,
                passed=False,
                score=0.0,
                checks_total=1,
                checks_passed=0,
                checks_failed=1,
                details={},
                elapsed_seconds=time.monotonic() - t0,
                error=str(exc),
            ))
    return results


# ---------------------------------------------------------------------------
# Feature runner: preflight-scanner
# ---------------------------------------------------------------------------

def run_preflight_scanner(
    task_dir: Path, verbose: bool, offline: bool, quick: bool
) -> list[ScenarioResult]:
    """Benchmark the data-aware preflight scanner (Feature 2)."""
    try:
        from bio_harness.core.input_quality import scan_plan_inputs
    except ImportError as exc:
        scenarios = _load_scenarios(task_dir)
        return [
            _skipped_result("preflight-scanner", s, f"ImportError: {exc}")
            for s in (scenarios or [{"scenario_id": "all"}])
        ]

    scenarios = _load_scenarios(task_dir)
    if not scenarios:
        return []
    if quick:
        scenarios = scenarios[:1]

    results: list[ScenarioResult] = []
    for sc in scenarios:
        t0 = time.monotonic()
        sid = sc.get("scenario_id", "unknown")
        desc = sc.get("description", sid)
        try:
            data_root = task_dir / _scenario_text_value(sc, "data_subdir", "directory", default="data")
            plan = sc.get("plan") if isinstance(sc.get("plan"), dict) else _build_benchmark_preflight_plan(data_root)
            selected_dir_token = _scenario_text_value(sc, "selected_subdir")
            selected_dir = task_dir / selected_dir_token if selected_dir_token else None
            analysis_type = _infer_preflight_analysis_type(sc, data_root)
            planted_defects = _scenario_list_value(sc, "planted_defects", "expected_detections")
            is_positive_control = _scenario_bool_value(sc, "positive_control", "is_positive_control", default=False)
            expected_severity: dict[str, str] = sc.get("expected_severity", {})

            scan_result = scan_plan_inputs(
                plan=plan,
                data_root=data_root,
                selected_dir=selected_dir,
                analysis_type=analysis_type,
            )
            detected_categories = {issue.category for issue in scan_result.issues}

            checks_total = 0
            checks_passed = 0
            details: dict[str, Any] = {}

            # Recall: each planted defect should be detected
            recall_hit = 0
            for defect in planted_defects:
                checks_total += 1
                if defect in detected_categories:
                    recall_hit += 1
                    checks_passed += 1
            details["planted"] = planted_defects
            details["detected"] = sorted(detected_categories)
            details["recall_hit"] = recall_hit
            details["recall_total"] = len(planted_defects)

            # Precision: positive control should have 0 detections
            if is_positive_control:
                checks_total += 1
                if len(detected_categories) == 0:
                    checks_passed += 1
                    details["false_positive"] = False
                else:
                    details["false_positive"] = True

            # Severity accuracy
            severity_checks = 0
            severity_pass = 0
            for issue in scan_result.issues:
                exp_sev = expected_severity.get(issue.category)
                if exp_sev:
                    severity_checks += 1
                    checks_total += 1
                    if issue.severity == exp_sev:
                        severity_pass += 1
                        checks_passed += 1
            details["severity_checks"] = severity_checks
            details["severity_passed"] = severity_pass

            # Actionability: every detection should have a non-empty message
            for issue in scan_result.issues:
                checks_total += 1
                if issue.message.strip():
                    checks_passed += 1

            score = _safe_ratio(checks_passed, checks_total)
            # Hard gates: recall >=90% and no false positives on positive controls
            recall_ok = (recall_hit >= 0.9 * len(planted_defects)) if planted_defects else True
            fp_ok = not details.get("false_positive", False)
            passed = recall_ok and fp_ok

            results.append(ScenarioResult(
                feature="preflight-scanner",
                scenario_id=sid,
                description=desc,
                passed=passed,
                score=round(score, 4),
                checks_total=checks_total,
                checks_passed=checks_passed,
                checks_failed=checks_total - checks_passed,
                details=details,
                elapsed_seconds=time.monotonic() - t0,
            ))
        except Exception as exc:
            results.append(ScenarioResult(
                feature="preflight-scanner",
                scenario_id=sid,
                description=desc,
                passed=False,
                score=0.0,
                checks_total=1,
                checks_passed=0,
                checks_failed=1,
                details={},
                elapsed_seconds=time.monotonic() - t0,
                error=str(exc),
            ))
    return results


# ---------------------------------------------------------------------------
# Feature runner: output-catalog
# ---------------------------------------------------------------------------

def run_output_catalog(
    task_dir: Path, verbose: bool, offline: bool, quick: bool
) -> list[ScenarioResult]:
    """Benchmark the output file catalog (Feature 3)."""
    try:
        from bio_harness.core.output_catalog import build_output_catalog
    except ImportError as exc:
        scenarios = _load_scenarios(task_dir)
        return [
            _skipped_result("output-catalog", s, f"ImportError: {exc}")
            for s in (scenarios or [{"scenario_id": "all"}])
        ]

    scenarios = _load_scenarios(task_dir)
    if not scenarios:
        return []
    if quick:
        scenarios = scenarios[:1]

    results: list[ScenarioResult] = []
    for sc in scenarios:
        t0 = time.monotonic()
        sid = sc.get("scenario_id", "unknown")
        desc = sc.get("description", sid)
        try:
            selected_subdir = _scenario_text_value(sc, "selected_subdir", "directory", default="outputs")
            selected_dir = task_dir / selected_subdir
            plan = sc.get("plan", {"plan": []})
            step_statuses: list[str] | None = sc.get("step_statuses")
            expected_entries = _normalize_expected_catalog_entries(
                _scenario_list_value(sc, "expected_entries", "expected_files")
            )

            catalog = build_output_catalog(
                selected_dir=selected_dir,
                plan=plan,
                step_statuses=step_statuses,
            )

            cataloged_relpaths = {e.relative_path for e in catalog.entries}
            expected_relpaths = {e["relative_path"] for e in expected_entries if "relative_path" in e}
            expected_by_relpath: dict[str, dict[str, Any]] = {
                e["relative_path"]: e for e in expected_entries if "relative_path" in e
            }

            checks_total = 0
            checks_passed = 0
            details: dict[str, Any] = {}

            # Completeness: every expected file appears in catalog
            completeness_hit = 0
            for rp in expected_relpaths:
                checks_total += 1
                if rp in cataloged_relpaths:
                    completeness_hit += 1
                    checks_passed += 1
            details["completeness_hit"] = completeness_hit
            details["completeness_total"] = len(expected_relpaths)

            # Role accuracy
            role_checks = 0
            role_pass = 0
            catalog_by_relpath = {e.relative_path: e for e in catalog.entries}
            for rp, exp in expected_by_relpath.items():
                expected_role = str(exp.get("role", "") or "").strip().lower()
                entry = catalog_by_relpath.get(rp)
                if entry and expected_role:
                    role_checks += 1
                    checks_total += 1
                    if str(entry.role or "").strip().lower() == expected_role:
                        role_pass += 1
                        checks_passed += 1
            details["role_checks"] = role_checks
            details["role_passed"] = role_pass

            # Format detection
            fmt_checks = 0
            fmt_pass = 0
            for rp, exp in expected_by_relpath.items():
                expected_fmt = _normalize_catalog_format(exp.get("format", ""))
                entry = catalog_by_relpath.get(rp)
                if entry and expected_fmt:
                    fmt_checks += 1
                    checks_total += 1
                    if _normalize_catalog_format(entry.format) == expected_fmt:
                        fmt_pass += 1
                        checks_passed += 1
            details["format_checks"] = fmt_checks
            details["format_passed"] = fmt_pass

            # No phantom entries: cataloged files that don't exist on disk
            phantom_count = 0
            for entry in catalog.entries:
                p = Path(entry.path)
                checks_total += 1
                if p.exists():
                    checks_passed += 1
                else:
                    phantom_count += 1
            details["phantom_entries"] = phantom_count

            score_parts = [
                _safe_ratio(completeness_hit, len(expected_relpaths)) * 0.4,
                _safe_ratio(role_pass, role_checks) * 0.3 if role_checks else 0.3,
                _safe_ratio(fmt_pass, fmt_checks) * 0.2 if fmt_checks else 0.2,
                (1.0 if phantom_count == 0 else 0.0) * 0.1,
            ]
            score = sum(score_parts)

            # Hard gates: completeness >=95%, zero phantoms
            completeness_pct = _safe_ratio(completeness_hit, len(expected_relpaths))
            passed = completeness_pct >= 0.95 and phantom_count == 0

            results.append(ScenarioResult(
                feature="output-catalog",
                scenario_id=sid,
                description=desc,
                passed=passed,
                score=round(score, 4),
                checks_total=checks_total,
                checks_passed=checks_passed,
                checks_failed=checks_total - checks_passed,
                details=details,
                elapsed_seconds=time.monotonic() - t0,
            ))
        except Exception as exc:
            results.append(ScenarioResult(
                feature="output-catalog",
                scenario_id=sid,
                description=desc,
                passed=False,
                score=0.0,
                checks_total=1,
                checks_passed=0,
                checks_failed=1,
                details={},
                elapsed_seconds=time.monotonic() - t0,
                error=str(exc),
            ))
    return results


# ---------------------------------------------------------------------------
# Feature runner: result-interpreter
# ---------------------------------------------------------------------------

def run_result_interpreter(
    task_dir: Path, verbose: bool, offline: bool, quick: bool
) -> list[ScenarioResult]:
    """Benchmark the result interpretation summary (Feature 4)."""
    try:
        from bio_harness.core.result_interpreter import interpret_run_results
    except ImportError as exc:
        scenarios = _load_scenarios(task_dir)
        return [
            _skipped_result("result-interpreter", s, f"ImportError: {exc}")
            for s in (scenarios or [{"scenario_id": "all"}])
        ]

    scenarios = _load_scenarios(task_dir)
    if not scenarios:
        return []
    if quick:
        scenarios = scenarios[:1]

    results: list[ScenarioResult] = []
    for sc in scenarios:
        t0 = time.monotonic()
        sid = sc.get("scenario_id", "unknown")
        desc = sc.get("description", sid)
        try:
            selected_dir = _prepare_result_interpreter_selected_dir(task_dir, sc)
            analysis_type = sc.get("analysis_type", "")
            plan = sc.get("plan", {"plan": []})
            required_facts: list[dict[str, str]] = sc.get("required_facts", [])
            required_numbers: list[dict[str, Any]] = sc.get("required_numbers", [])
            forbidden_phrases: list[str] = sc.get("forbidden_phrases", [])
            min_words = sc.get("min_words", 20)
            max_words = sc.get("max_words", 1000)

            interp = interpret_run_results(
                selected_dir=selected_dir,
                analysis_type=analysis_type,
                plan=plan,
                llm=None,  # template fallback only in benchmark
            )
            text = interp.interpretation

            checks_total = 0
            checks_passed = 0
            details: dict[str, Any] = {}

            # Fact coverage (regex)
            fact_matched, fact_total = _check_regex_facts(text, required_facts)
            checks_total += fact_total
            checks_passed += fact_matched
            details["fact_matched"] = fact_matched
            details["fact_total"] = fact_total

            # Numerical accuracy
            num_checks = 0
            num_pass = 0
            for numspec in required_numbers:
                expected_val = numspec.get("value")
                tolerance = numspec.get("tolerance", 0.05)
                if expected_val is None:
                    continue
                num_checks += 1
                checks_total += 1
                # Search text for a number close to expected
                numbers_in_text = re.findall(r"[\d]+(?:\.[\d]+)?", text)
                found_close = False
                for nstr in numbers_in_text:
                    try:
                        nval = float(nstr)
                        if abs(nval - expected_val) <= abs(expected_val * tolerance) + 1e-9:
                            found_close = True
                            break
                    except ValueError:
                        continue
                if found_close:
                    num_pass += 1
                    checks_passed += 1
            details["num_checks"] = num_checks
            details["num_passed"] = num_pass

            # Forbidden phrases (hallucination check)
            hallucination_found = False
            for phrase in forbidden_phrases:
                checks_total += 1
                if phrase.lower() in text.lower():
                    hallucination_found = True
                else:
                    checks_passed += 1
            details["hallucination_found"] = hallucination_found

            # Readability: word count within bounds
            word_count = len(text.split())
            checks_total += 1
            readability_ok = min_words <= word_count <= max_words
            if readability_ok:
                checks_passed += 1
            details["word_count"] = word_count
            details["readability_ok"] = readability_ok

            # Weighted score
            fact_score = _safe_ratio(fact_matched, fact_total) * 0.4
            num_score = _safe_ratio(num_pass, num_checks) * 0.3 if num_checks else 0.3
            halluc_score = (0.0 if hallucination_found else 1.0) * 0.2
            read_score = (1.0 if readability_ok else 0.0) * 0.1
            score = fact_score + num_score + halluc_score + read_score

            # Hard gates: fact coverage >=80%, no hallucinations
            fact_coverage = _safe_ratio(fact_matched, fact_total)
            passed = fact_coverage >= 0.80 and not hallucination_found

            results.append(ScenarioResult(
                feature="result-interpreter",
                scenario_id=sid,
                description=desc,
                passed=passed,
                score=round(score, 4),
                checks_total=checks_total,
                checks_passed=checks_passed,
                checks_failed=checks_total - checks_passed,
                details=details,
                elapsed_seconds=time.monotonic() - t0,
            ))
        except Exception as exc:
            results.append(ScenarioResult(
                feature="result-interpreter",
                scenario_id=sid,
                description=desc,
                passed=False,
                score=0.0,
                checks_total=1,
                checks_passed=0,
                checks_failed=1,
                details={},
                elapsed_seconds=time.monotonic() - t0,
                error=str(exc),
            ))
    return results


# ---------------------------------------------------------------------------
# Feature runner: error-diagnosis
# ---------------------------------------------------------------------------

def run_error_diagnosis(
    task_dir: Path, verbose: bool, offline: bool, quick: bool
) -> list[ScenarioResult]:
    """Benchmark the LLM-assisted error diagnosis (Feature 5)."""
    try:
        from bio_harness.core.error_diagnosis import diagnose_step_failure
    except ImportError as exc:
        scenarios = _load_scenarios(task_dir)
        return [
            _skipped_result("error-diagnosis", s, f"ImportError: {exc}")
            for s in (scenarios or [{"scenario_id": "all"}])
        ]

    scenarios = _load_scenarios(task_dir)
    if not scenarios:
        return []
    if quick:
        scenarios = scenarios[:1]

    results: list[ScenarioResult] = []
    for sc in scenarios:
        t0 = time.monotonic()
        sid = sc.get("scenario_id", sc.get("error_id", "unknown"))
        desc = sc.get("description", sid)
        try:
            tool_name = sc.get("tool", sc.get("tool_name", ""))
            failure_class = sc.get("failure_class", "tool_error")
            exit_code = sc.get("exit_code", 1)
            stderr = sc.get("stderr", "")
            stdout = sc.get("stdout", "")
            expected_root_cause = sc.get("root_cause", "")
            expected_fix = sc.get("fix", "")
            is_novel = sc.get("novel", False)
            dangerous_patterns = sc.get("dangerous_patterns", [
                "rm -rf", "delete all", "force-push", "drop database",
            ])

            diagnosis = diagnose_step_failure(
                tool_name=tool_name,
                failure_class=failure_class,
                exit_code=exit_code,
                stderr=stderr,
                stdout=stdout,
                llm=None,  # heuristic only in offline benchmark
            )

            checks_total = 0
            checks_passed = 0
            details: dict[str, Any] = {}

            # Root cause match: check if expected root cause string appears
            # in the diagnosed root_cause or failure_class
            checks_total += 1
            root_cause_text = f"{diagnosis.root_cause} {diagnosis.failure_class}".lower()
            root_cause_match = expected_root_cause.lower() in root_cause_text
            if not root_cause_match:
                # Also accept keyword overlap > 0.3
                root_cause_match = _keyword_overlap(expected_root_cause, root_cause_text) >= 0.3
            if root_cause_match:
                checks_passed += 1
            details["root_cause_match"] = root_cause_match
            details["diagnosed_root_cause"] = diagnosis.root_cause
            details["expected_root_cause"] = expected_root_cause

            # Fix relevance: keyword overlap between expected fix and suggested fix
            checks_total += 1
            fix_overlap = _keyword_overlap(expected_fix, diagnosis.suggested_fix)
            fix_relevant = fix_overlap >= 0.15
            if fix_relevant:
                checks_passed += 1
            details["fix_overlap"] = round(fix_overlap, 4)
            details["fix_relevant"] = fix_relevant
            details["suggested_fix"] = diagnosis.suggested_fix

            # Heuristic coverage: non-novel errors should be caught by heuristic
            if not is_novel:
                checks_total += 1
                heuristic_hit = diagnosis.diagnosed_by == "heuristic"
                if heuristic_hit:
                    checks_passed += 1
                details["heuristic_hit"] = heuristic_hit

            # No dangerous fix
            checks_total += 1
            dangerous = False
            fix_lower = diagnosis.suggested_fix.lower()
            for dp in dangerous_patterns:
                if dp.lower() in fix_lower:
                    dangerous = True
                    break
            if not dangerous:
                checks_passed += 1
            details["dangerous_fix"] = dangerous

            # Score
            score_parts = []
            score_parts.append((1.0 if root_cause_match else 0.0) * 0.5)
            score_parts.append(min(fix_overlap / 0.3, 1.0) * 0.3)
            if not is_novel:
                score_parts.append((1.0 if details.get("heuristic_hit", False) else 0.0) * 0.2)
            else:
                score_parts.append(0.2)  # no heuristic penalty for novel errors
            score = sum(score_parts)

            # Hard gate: root cause must match and no dangerous fix
            passed = root_cause_match and not dangerous

            results.append(ScenarioResult(
                feature="error-diagnosis",
                scenario_id=sid,
                description=desc,
                passed=passed,
                score=round(score, 4),
                checks_total=checks_total,
                checks_passed=checks_passed,
                checks_failed=checks_total - checks_passed,
                details=details,
                elapsed_seconds=time.monotonic() - t0,
            ))
        except Exception as exc:
            results.append(ScenarioResult(
                feature="error-diagnosis",
                scenario_id=sid,
                description=desc,
                passed=False,
                score=0.0,
                checks_total=1,
                checks_passed=0,
                checks_failed=1,
                details={},
                elapsed_seconds=time.monotonic() - t0,
                error=str(exc),
            ))
    return results


# ---------------------------------------------------------------------------
# Feature runner: quality-compare
# ---------------------------------------------------------------------------

def run_quality_compare(
    task_dir: Path, verbose: bool, offline: bool, quick: bool
) -> list[ScenarioResult]:
    """Benchmark the multi-run quality comparison (Feature 6)."""
    try:
        from bio_harness.reporting.quality_compare import compare_run_quality
    except ImportError as exc:
        scenarios = _load_scenarios(task_dir)
        return [
            _skipped_result("quality-compare", s, f"ImportError: {exc}")
            for s in (scenarios or [{"scenario_id": "all"}])
        ]

    scenarios = _load_scenarios(task_dir)
    if not scenarios:
        return []
    if quick:
        scenarios = scenarios[:1]

    results: list[ScenarioResult] = []
    for sc in scenarios:
        t0 = time.monotonic()
        sid = sc.get("scenario_id", "unknown")
        desc = sc.get("description", sid)
        try:
            base_subdir = _scenario_text_value(sc, "directory")
            base_dir = task_dir / base_subdir if base_subdir else task_dir
            run_a_subdir = _scenario_text_value(sc, "run_a_subdir", default="run_a")
            run_b_subdir = _scenario_text_value(sc, "run_b_subdir", default="run_b")
            run_a_dir = base_dir / run_a_subdir
            run_b_dir = base_dir / run_b_subdir
            plan_a = sc.get("plan_a")
            plan_b = sc.get("plan_b")
            expected_verdict = _benchmark_direction_for_token(sc.get("expected_verdict", ""))
            expected_dimensions: dict[str, str] = sc.get("expected_dimensions", {})
            is_edge_case: bool = sc.get("edge_case", False)

            comparison = compare_run_quality(
                run_a_dir=run_a_dir,
                run_b_dir=run_b_dir,
                plan_a=plan_a,
                plan_b=plan_b,
            )

            checks_total = 0
            checks_passed = 0
            details: dict[str, Any] = {}

            # Overall verdict match
            norm_verdict = _benchmark_direction_for_token(comparison.overall_winner)
            checks_total += 1
            verdict_match = norm_verdict == expected_verdict
            if verdict_match:
                checks_passed += 1
            details["verdict_expected"] = expected_verdict
            details["verdict_actual"] = norm_verdict
            details["verdict_match"] = verdict_match

            # Dimension accuracy
            dim_checks = 0
            dim_pass = 0
            for metric_comp in comparison.metric_comparisons:
                expected_dir = _benchmark_direction_for_token(
                    _expected_dimension_for_metric(expected_dimensions, metric_comp.metric_name)
                )
                if expected_dir:
                    dim_checks += 1
                    checks_total += 1
                    actual_dir = _benchmark_direction_for_token(metric_comp.better_run)
                    if actual_dir == expected_dir:
                        dim_pass += 1
                        checks_passed += 1
            details["dimension_checks"] = dim_checks
            details["dimension_passed"] = dim_pass

            # Delta direction (sign check)
            delta_checks = 0
            delta_pass = 0
            for metric_comp in comparison.metric_comparisons:
                if metric_comp.delta is not None and metric_comp.delta != 0.0:
                    delta_checks += 1
                    checks_total += 1
                    # Positive delta means run_b is larger
                    exp_dir = _benchmark_direction_for_token(
                        _expected_dimension_for_metric(expected_dimensions, metric_comp.metric_name)
                    )
                    if exp_dir:
                        expected_positive = exp_dir == "IMPROVED"
                        actual_positive = metric_comp.delta > 0
                        if expected_positive == actual_positive:
                            delta_pass += 1
                            checks_passed += 1
                    else:
                        # No expectation; auto-pass
                        delta_pass += 1
                        checks_passed += 1
            details["delta_checks"] = delta_checks
            details["delta_passed"] = delta_pass

            # Edge case handling: no crash is a pass
            if is_edge_case:
                checks_total += 1
                checks_passed += 1  # reaching here means no crash
                details["edge_case_handled"] = True

            score_parts = [
                (1.0 if verdict_match else 0.0) * 0.5,
                _safe_ratio(dim_pass, dim_checks) * 0.3 if dim_checks else 0.3,
                (1.0 if is_edge_case else _safe_ratio(delta_pass, delta_checks)) * 0.2 if delta_checks or is_edge_case else 0.2,
            ]
            score = sum(score_parts)

            # Hard gate: verdict must match; edge cases must not crash
            passed = verdict_match

            results.append(ScenarioResult(
                feature="quality-compare",
                scenario_id=sid,
                description=desc,
                passed=passed,
                score=round(score, 4),
                checks_total=checks_total,
                checks_passed=checks_passed,
                checks_failed=checks_total - checks_passed,
                details=details,
                elapsed_seconds=time.monotonic() - t0,
            ))
        except Exception as exc:
            results.append(ScenarioResult(
                feature="quality-compare",
                scenario_id=sid,
                description=desc,
                passed=False,
                score=0.0,
                checks_total=1,
                checks_passed=0,
                checks_failed=1,
                details={},
                elapsed_seconds=time.monotonic() - t0,
                error=str(exc),
            ))
    return results


# ---------------------------------------------------------------------------
# Feature runner: literature-agent
# ---------------------------------------------------------------------------

def run_literature_agent(
    task_dir: Path, verbose: bool, offline: bool, quick: bool
) -> list[ScenarioResult]:
    """Benchmark the literature research agent (Feature 7)."""
    try:
        from bio_harness.core.literature_agent import (
            LiteratureAgent,
            LiteratureHit,
            ResearchQuery,
        )
    except ImportError as exc:
        scenarios = _load_scenarios(task_dir)
        return [
            _skipped_result("literature-agent", s, f"ImportError: {exc}")
            for s in (scenarios or [{"scenario_id": "all"}])
        ]

    scenarios = _load_scenarios(task_dir)
    if not scenarios:
        return []
    if quick:
        scenarios = scenarios[:1]

    results: list[ScenarioResult] = []
    for sc in scenarios:
        t0 = time.monotonic()
        sid = sc.get("scenario_id", sc.get("question_id", "unknown"))
        desc = sc.get("description", sc.get("question", sid))
        try:
            question = sc.get("question", "")
            analysis_type = sc.get("analysis_type", "")
            canned_abstracts: list[dict[str, Any]] = sc.get("canned_abstracts", [])
            expected_recommendations: list[dict[str, str]] = sc.get("expected_recommendations", [])
            forbidden_claims: list[str] = sc.get("forbidden_claims", [])
            expected_tool_preference: str = sc.get("expected_tool_preference", "")
            expected_confidence = str(sc.get("expected_confidence", "") or "").strip().lower()
            is_live = sc.get("live", False)

            # Skip live scenarios in offline mode
            if is_live and offline:
                results.append(_skipped_result("literature-agent", sc, "live scenario skipped in offline mode"))
                continue

            # Build a mock librarian that returns canned abstracts
            class _MockLibrarian:
                """Injects canned abstracts instead of making real PubMed calls."""

                def __init__(self, abstracts: list[dict[str, Any]]) -> None:
                    self._abstracts = abstracts

                def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
                    return self._abstracts[:max_results]

            # Build canned LiteratureHit objects for relevance labelling
            canned_hits = []
            relevance_labels: dict[str, bool] = {}
            for ab in canned_abstracts:
                pmid = ab.get("pmid", "")
                relevance_labels[pmid] = ab.get("relevant", False)
                canned_hits.append(LiteratureHit(
                    title=ab.get("title", ""),
                    abstract=ab.get("abstract", ""),
                    source="pubmed",
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                    year=ab.get("year"),
                    citation_count=ab.get("citation_count", 0),
                    relevance_score=ab.get("relevance_score", 0.5),
                ))

            librarian = _MockLibrarian(canned_abstracts) if (offline or canned_abstracts) else None
            agent = LiteratureAgent(librarian=librarian, biollm=None)

            query_obj = ResearchQuery(
                question=question,
                analysis_type=analysis_type,
                max_results=len(canned_abstracts) if canned_abstracts else 10,
            )
            report = agent.research(query_obj)

            checks_total = 0
            checks_passed = 0
            details: dict[str, Any] = {}

            # nDCG ranking quality
            if canned_abstracts and report.hits:
                # Build ranked relevance list from returned order
                ranked_rels: list[bool] = []
                for hit in report.hits:
                    pmid_match = re.search(r"/(\d+)/?$", hit.url or "")
                    pmid = pmid_match.group(1) if pmid_match else ""
                    ranked_rels.append(relevance_labels.get(pmid, False))
                ndcg = _compute_ndcg(ranked_rels, k=len(ranked_rels))
            else:
                ndcg = 0.0 if canned_abstracts else 1.0  # no abstracts = N/A
            checks_total += 1
            skip_rank_gate = not expected_recommendations and not expected_tool_preference
            ndcg_ok = skip_rank_gate or ndcg >= 0.70
            if ndcg_ok:
                checks_passed += 1
            details["ndcg"] = round(ndcg, 4)

            # Deduplication: no duplicate URLs
            checks_total += 1
            urls = [h.url for h in report.hits if h.url]
            dedup_ok = len(urls) == len(set(urls))
            if dedup_ok:
                checks_passed += 1
            details["dedup_ok"] = dedup_ok

            # Recommendation coverage
            synthesis_text = report.synthesis + " " + " ".join(report.recommendations)
            rec_matched, rec_total = _check_regex_facts(synthesis_text, expected_recommendations)
            checks_total += rec_total
            checks_passed += rec_matched
            details["rec_matched"] = rec_matched
            details["rec_total"] = rec_total

            # Tool preference
            if expected_tool_preference:
                checks_total += 1
                pref_found = expected_tool_preference.lower() in synthesis_text.lower()
                if pref_found:
                    checks_passed += 1
                details["tool_preference_match"] = pref_found

            # Forbidden claims
            forbidden_found = False
            for claim in forbidden_claims:
                checks_total += 1
                if claim.lower() in synthesis_text.lower():
                    forbidden_found = True
                else:
                    checks_passed += 1
            details["forbidden_found"] = forbidden_found

            if expected_confidence:
                checks_total += 1
                if expected_confidence == "low":
                    confidence_ok = report.confidence < 0.5
                elif expected_confidence == "high":
                    confidence_ok = report.confidence >= 0.5
                else:
                    confidence_ok = True
                if confidence_ok:
                    checks_passed += 1
                details["confidence"] = report.confidence
                details["confidence_calibrated"] = confidence_ok

            rec_coverage = 1.0 if rec_total == 0 else _safe_ratio(rec_matched, rec_total)
            effective_ndcg = 1.0 if skip_rank_gate else ndcg
            score_parts = [
                effective_ndcg * 0.3,
                rec_coverage * 0.3,
                (1.0 if details.get("tool_preference_match", True) else 0.0) * 0.2,
                (0.0 if forbidden_found else 1.0) * 0.1,
                (1.0 if dedup_ok else 0.0) * 0.1,
            ]
            score = sum(score_parts)

            # Hard gates: nDCG >= 0.70, rec coverage >= 75%, no forbidden claims
            if skip_rank_gate:
                passed = not forbidden_found and details.get("confidence_calibrated", True)
            else:
                passed = ndcg_ok and rec_coverage >= 0.75 and not forbidden_found

            results.append(ScenarioResult(
                feature="literature-agent",
                scenario_id=sid,
                description=desc,
                passed=passed,
                score=round(score, 4),
                checks_total=checks_total,
                checks_passed=checks_passed,
                checks_failed=checks_total - checks_passed,
                details=details,
                elapsed_seconds=time.monotonic() - t0,
            ))
        except Exception as exc:
            results.append(ScenarioResult(
                feature="literature-agent",
                scenario_id=sid,
                description=desc,
                passed=False,
                score=0.0,
                checks_total=1,
                checks_passed=0,
                checks_failed=1,
                details={},
                elapsed_seconds=time.monotonic() - t0,
                error=str(exc),
            ))
    return results


# ---------------------------------------------------------------------------
# Feature runner: integration
# ---------------------------------------------------------------------------

def run_integration(
    task_dir: Path, verbose: bool, offline: bool, quick: bool
) -> list[ScenarioResult]:
    """Cross-feature integration benchmark chaining Features 1-7."""
    # Attempt all feature imports up front
    import_errors: list[str] = []
    scan_plan_inputs = None
    assess_output_quality_fn = None
    build_output_catalog_fn = None
    interpret_run_results_fn = None
    diagnose_step_failure_fn = None
    compare_run_quality_fn = None
    LiteratureAgentCls = None
    ResearchQueryCls = None

    try:
        from bio_harness.core.input_quality import scan_plan_inputs as _spf
        scan_plan_inputs = _spf
    except ImportError as exc:
        import_errors.append(f"input_quality: {exc}")

    try:
        from bio_harness.core.output_quality import assess_output_quality as _aoq
        assess_output_quality_fn = _aoq
    except ImportError as exc:
        import_errors.append(f"output_quality: {exc}")

    try:
        from bio_harness.core.output_catalog import build_output_catalog as _boc
        build_output_catalog_fn = _boc
    except ImportError as exc:
        import_errors.append(f"output_catalog: {exc}")

    try:
        from bio_harness.core.result_interpreter import interpret_run_results as _irr
        interpret_run_results_fn = _irr
    except ImportError as exc:
        import_errors.append(f"result_interpreter: {exc}")

    try:
        from bio_harness.core.error_diagnosis import diagnose_step_failure as _dsf
        diagnose_step_failure_fn = _dsf
    except ImportError as exc:
        import_errors.append(f"error_diagnosis: {exc}")

    try:
        from bio_harness.reporting.quality_compare import compare_run_quality as _crq
        compare_run_quality_fn = _crq
    except ImportError as exc:
        import_errors.append(f"quality_compare: {exc}")

    try:
        from bio_harness.core.literature_agent import (
            LiteratureAgent as _LA,
            ResearchQuery as _RQ,
        )
        LiteratureAgentCls = _LA
        ResearchQueryCls = _RQ
    except ImportError as exc:
        import_errors.append(f"literature_agent: {exc}")

    scenarios = _load_scenarios(task_dir)
    if not scenarios:
        # If no scenarios.json, create a single synthetic integration check
        scenarios = [{"scenario_id": "full_pipeline", "description": "Full feature chain integration"}]
    if quick:
        scenarios = scenarios[:1]

    results: list[ScenarioResult] = []
    for sc in scenarios:
        t0 = time.monotonic()
        sid = sc.get("scenario_id", "integration")
        desc = sc.get("description", "Cross-feature integration benchmark")

        checks_total = 8  # one per feature step + chain integrity
        checks_passed = 0
        details: dict[str, Any] = {"import_errors": import_errors}
        step_errors: list[str] = []

        # Step 1: Preflight
        if scan_plan_inputs is not None:
            try:
                plan = sc.get("plan", {"plan": []})
                data_root = task_dir / sc.get("data_subdir", "data")
                analysis_type = sc.get("analysis_type", "")
                scan_result = scan_plan_inputs(
                    plan=plan, data_root=data_root, analysis_type=analysis_type,
                )
                preflight_clean = not scan_result.has_blocking
                expected_clean = sc.get("preflight_expected_clean", True)
                if preflight_clean == expected_clean:
                    checks_passed += 1
                    details["step1_preflight"] = "pass"
                else:
                    details["step1_preflight"] = f"fail: expected_clean={expected_clean}, got={preflight_clean}"
            except Exception as exc:
                details["step1_preflight"] = f"error: {exc}"
                step_errors.append(f"preflight: {exc}")
        else:
            details["step1_preflight"] = "skipped (import error)"

        # Step 2: Quality gate
        if assess_output_quality_fn is not None:
            try:
                quality_files = sc.get("quality_files", [])
                quality_ok = True
                quality_details: list[dict[str, str]] = []
                for qf in quality_files:
                    fpath = task_dir / qf.get("file", "")
                    expected = qf.get("expected_level", "").upper()
                    if fpath.exists():
                        report = assess_output_quality_fn(fpath)
                        level_map = {"WARNING": "WARN", "PASS": "PASS", "FAIL": "FAIL"}
                        actual = level_map.get(report.overall_level.value.upper(), report.overall_level.value.upper())
                        norm_exp = level_map.get(expected, expected)
                        match = actual == norm_exp
                        quality_details.append({"file": str(fpath.name), "expected": norm_exp, "actual": actual, "match": str(match)})
                        if not match:
                            quality_ok = False
                    else:
                        quality_details.append({"file": qf.get("file", ""), "error": "not found"})
                        quality_ok = False
                if quality_ok:
                    checks_passed += 1
                details["step2_quality"] = "pass" if quality_ok else "fail"
                details["step2_quality_details"] = quality_details
            except Exception as exc:
                details["step2_quality"] = f"error: {exc}"
                step_errors.append(f"quality: {exc}")
        else:
            details["step2_quality"] = "skipped (import error)"

        # Step 3: Catalog
        if build_output_catalog_fn is not None:
            try:
                selected_subdir = sc.get("selected_subdir", "outputs")
                selected_dir = task_dir / selected_subdir
                plan = sc.get("plan", {"plan": []})
                catalog = build_output_catalog_fn(selected_dir=selected_dir, plan=plan)
                expected_count = sc.get("expected_catalog_count")
                if expected_count is not None:
                    catalog_ok = len(catalog.entries) >= expected_count
                else:
                    catalog_ok = True  # just verify no crash
                if catalog_ok:
                    checks_passed += 1
                details["step3_catalog"] = "pass" if catalog_ok else f"fail: expected>={expected_count}, got={len(catalog.entries)}"
                details["step3_catalog_entries"] = len(catalog.entries)
            except Exception as exc:
                details["step3_catalog"] = f"error: {exc}"
                step_errors.append(f"catalog: {exc}")
        else:
            details["step3_catalog"] = "skipped (import error)"

        # Step 4: Interpretation
        if interpret_run_results_fn is not None:
            try:
                selected_subdir = sc.get("selected_subdir", "outputs")
                selected_dir = task_dir / selected_subdir
                analysis_type = sc.get("analysis_type", "")
                plan = sc.get("plan", {"plan": []})
                interp = interpret_run_results_fn(
                    selected_dir=selected_dir,
                    analysis_type=analysis_type,
                    plan=plan,
                    llm=None,
                )
                interp_facts: list[dict[str, str]] = sc.get("interp_required_facts", [])
                if interp_facts:
                    matched, total = _check_regex_facts(interp.interpretation, interp_facts)
                    interp_ok = _safe_ratio(matched, total) >= 0.80
                else:
                    interp_ok = len(interp.interpretation) > 0
                if interp_ok:
                    checks_passed += 1
                details["step4_interpretation"] = "pass" if interp_ok else "fail"
            except Exception as exc:
                details["step4_interpretation"] = f"error: {exc}"
                step_errors.append(f"interpretation: {exc}")
        else:
            details["step4_interpretation"] = "skipped (import error)"

        # Step 5: Diagnosis
        if diagnose_step_failure_fn is not None:
            try:
                diag_stderr = sc.get("diag_stderr", "")
                diag_tool = sc.get("diag_tool", "unknown_tool")
                diag_expected_cause = sc.get("diag_expected_root_cause", "")
                if diag_stderr:
                    diagnosis = diagnose_step_failure_fn(
                        tool_name=diag_tool,
                        failure_class="tool_error",
                        exit_code=1,
                        stderr=diag_stderr,
                        llm=None,
                    )
                    cause_text = f"{diagnosis.root_cause} {diagnosis.failure_class}".lower()
                    diag_ok = diag_expected_cause.lower() in cause_text or _keyword_overlap(diag_expected_cause, cause_text) >= 0.3
                else:
                    diag_ok = True  # no diagnosis scenario planted
                if diag_ok:
                    checks_passed += 1
                details["step5_diagnosis"] = "pass" if diag_ok else "fail"
            except Exception as exc:
                details["step5_diagnosis"] = f"error: {exc}"
                step_errors.append(f"diagnosis: {exc}")
        else:
            details["step5_diagnosis"] = "skipped (import error)"

        # Step 6: Comparison
        if compare_run_quality_fn is not None:
            try:
                run_a_subdir = sc.get("compare_run_a_subdir", "run_a")
                run_b_subdir = sc.get("compare_run_b_subdir", "run_b")
                run_a_dir = task_dir / run_a_subdir
                run_b_dir = task_dir / run_b_subdir
                if run_a_dir.exists() and run_b_dir.exists():
                    comparison = compare_run_quality_fn(run_a_dir=run_a_dir, run_b_dir=run_b_dir)
                    expected_winner = sc.get("compare_expected_verdict", "")
                    if expected_winner:
                        actual = _benchmark_direction_for_token(comparison.overall_winner)
                        compare_ok = actual == _benchmark_direction_for_token(expected_winner)
                    else:
                        compare_ok = True
                else:
                    compare_ok = True  # no comparison dirs, auto-pass
                if compare_ok:
                    checks_passed += 1
                details["step6_comparison"] = "pass" if compare_ok else "fail"
            except Exception as exc:
                details["step6_comparison"] = f"error: {exc}"
                step_errors.append(f"comparison: {exc}")
        else:
            details["step6_comparison"] = "skipped (import error)"

        # Step 7: Literature
        if LiteratureAgentCls is not None and ResearchQueryCls is not None:
            try:
                lit_question = sc.get("lit_question", "")
                if lit_question:
                    agent = LiteratureAgentCls(librarian=None, biollm=None)
                    query_obj = ResearchQueryCls(
                        question=lit_question,
                        analysis_type=sc.get("analysis_type", ""),
                        max_results=5,
                    )
                    report = agent.research(query_obj)
                    lit_ok = True  # just verify no crash; live results vary
                else:
                    lit_ok = True
                if lit_ok:
                    checks_passed += 1
                details["step7_literature"] = "pass" if lit_ok else "fail"
            except Exception as exc:
                details["step7_literature"] = f"error: {exc}"
                step_errors.append(f"literature: {exc}")
        else:
            details["step7_literature"] = "skipped (import error)"

        # Step 8: Chain integrity (all steps ran without crash)
        chain_ok = len(step_errors) == 0 and len(import_errors) == 0
        if chain_ok:
            checks_passed += 1
        details["step8_chain_integrity"] = "pass" if chain_ok else f"fail: {len(step_errors)} step errors, {len(import_errors)} import errors"

        score = _safe_ratio(checks_passed, checks_total)
        passed = checks_passed >= 7  # hard gate: >=7 of 8

        results.append(ScenarioResult(
            feature="integration",
            scenario_id=sid,
            description=desc,
            passed=passed,
            score=round(score, 4),
            checks_total=checks_total,
            checks_passed=checks_passed,
            checks_failed=checks_total - checks_passed,
            details=details,
            elapsed_seconds=time.monotonic() - t0,
        ))
    return results


# ---------------------------------------------------------------------------
# Feature registry
# ---------------------------------------------------------------------------

FEATURE_REGISTRY: dict[str, Callable[..., list[ScenarioResult]]] = {
    "output-quality-gate": run_output_quality_gate,
    "preflight-scanner": run_preflight_scanner,
    "output-catalog": run_output_catalog,
    "result-interpreter": run_result_interpreter,
    "error-diagnosis": run_error_diagnosis,
    "quality-compare": run_quality_compare,
    "literature-agent": run_literature_agent,
    "integration": run_integration,
}

FEATURE_LABELS: dict[str, str] = {
    "output-quality-gate": "Output Quality Gate",
    "preflight-scanner": "Preflight Scanner",
    "output-catalog": "Output Catalog",
    "result-interpreter": "Result Interpreter",
    "error-diagnosis": "Error Diagnosis",
    "quality-compare": "Quality Compare",
    "literature-agent": "Literature Agent",
    "integration": "Integration",
}


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def _print_feature_summary(
    feature: str, feature_results: list[ScenarioResult], index: int
) -> None:
    """Print a single line feature summary to the console."""
    total = len(feature_results)
    passed = sum(1 for r in feature_results if r.passed)
    skipped = sum(1 for r in feature_results if r.details.get("skipped"))
    scores = [r.score for r in feature_results if not r.details.get("skipped")]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    label = FEATURE_LABELS.get(feature, feature)
    marker = "+" if passed == total else "-"
    # Pad to align columns
    prefix = f"  Feature {index} ({label}):"
    counts = f"{passed}/{total} scenarios"
    if skipped > 0:
        counts += f" ({skipped} skipped)"
    score_str = f"score={avg_score:.2f}"
    line = f"{prefix:<45} {counts:<28} {score_str:<14} {marker}"
    print(line)


def _print_scenario_detail(r: ScenarioResult) -> None:
    """Print verbose detail for one scenario result."""
    status = "PASS" if r.passed else "FAIL"
    if r.details.get("skipped"):
        status = "SKIP"
    print(f"    [{status}] {r.scenario_id}: {r.description}")
    if r.error:
        print(f"           error: {r.error}")
    if r.details and not r.details.get("skipped"):
        for k, v in r.details.items():
            if k == "skipped":
                continue
            print(f"           {k}: {v}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the bio_harness feature benchmark suite.",
    )
    parser.add_argument(
        "--feature",
        choices=list(FEATURE_REGISTRY.keys()),
        help="Run only this feature's benchmarks",
    )
    parser.add_argument(
        "--scenario",
        help="Run only this scenario (requires --feature)",
    )
    parser.add_argument(
        "--report",
        default="workspace/benchmarks/feature-bench/report.json",
        help="Output JSON report path",
    )
    parser.add_argument(
        "--data-root",
        default="workspace/benchmarks/feature-bench/tasks",
        help="Data root containing per-feature task directories",
    )
    parser.add_argument("--verbose", action="store_true", help="Print detailed output")
    parser.add_argument("--offline", action="store_true", help="Skip network-dependent tests")
    parser.add_argument("--quick", action="store_true", help="One scenario per feature")
    args = parser.parse_args()

    if args.scenario and not args.feature:
        parser.error("--scenario requires --feature")

    data_root = Path(args.data_root)
    if not data_root.is_absolute():
        data_root = Path.cwd() / data_root

    # Determine which features to run
    if args.feature:
        features_to_run = [args.feature]
    else:
        features_to_run = list(FEATURE_REGISTRY.keys())

    print("=" * 78)
    print("  Bio-Harness Feature Benchmark Suite")
    print("=" * 78)
    print()

    all_results: list[ScenarioResult] = []
    feature_summaries: dict[str, dict[str, Any]] = {}

    for idx, feature in enumerate(features_to_run, start=1):
        task_dir = data_root / feature
        runner = FEATURE_REGISTRY[feature]

        if not task_dir.exists():
            print(f"  WARNING: task directory {task_dir} does not exist, skipping {feature}")
            feature_summaries[feature] = {
                "status": "skipped",
                "reason": "task directory not found",
                "scenarios_total": 0,
                "scenarios_passed": 0,
                "score": 0.0,
            }
            continue

        feature_results = runner(task_dir, args.verbose, args.offline, args.quick)

        # Filter to a single scenario if requested
        if args.scenario:
            feature_results = [
                r for r in feature_results if r.scenario_id == args.scenario
            ]
            if not feature_results:
                print(f"  WARNING: scenario '{args.scenario}' not found in {feature}")

        all_results.extend(feature_results)

        # Per-feature summary
        total = len(feature_results)
        passed = sum(1 for r in feature_results if r.passed)
        skipped = sum(1 for r in feature_results if r.details.get("skipped"))
        scores = [r.score for r in feature_results if not r.details.get("skipped")]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        feature_summaries[feature] = {
            "status": "passed" if passed == total else "failed",
            "scenarios_total": total,
            "scenarios_passed": passed,
            "scenarios_skipped": skipped,
            "score": round(avg_score, 4),
        }

        _print_feature_summary(feature, feature_results, idx)
        if args.verbose:
            for r in feature_results:
                _print_scenario_detail(r)

    # Aggregate
    total_scenarios = len(all_results)
    total_passed = sum(1 for r in all_results if r.passed)
    total_skipped = sum(1 for r in all_results if r.details.get("skipped"))
    total_failed = total_scenarios - total_passed
    non_skipped_scores = [r.score for r in all_results if not r.details.get("skipped")]
    overall_score = sum(non_skipped_scores) / len(non_skipped_scores) if non_skipped_scores else 0.0

    print()
    print("-" * 78)
    all_pass = total_passed == total_scenarios
    verdict = "BENCHMARK PASSED" if all_pass else "BENCHMARK FAILED"
    print(
        f"  TOTAL: {total_passed}/{total_scenarios} scenarios"
        f" | Overall score: {overall_score:.2f}"
        f" | {verdict}"
    )
    if total_skipped > 0:
        print(f"  ({total_skipped} scenarios skipped)")
    print("-" * 78)

    # Build report
    timestamp = datetime.now(timezone.utc).isoformat()
    report = FeatureBenchReport(
        timestamp=timestamp,
        total_scenarios=total_scenarios,
        total_passed=total_passed,
        total_failed=total_failed,
        total_skipped=total_skipped,
        overall_score=round(overall_score, 4),
        features=feature_summaries,
        scenarios=[asdict(r) for r in all_results],
    )

    # Write report
    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = Path.cwd() / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(asdict(report), indent=2, default=str))
    print(f"\n  Report written to {report_path}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
