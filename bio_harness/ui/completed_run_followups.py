"""Deterministic completed-run follow-up helpers for the Streamlit UI.

These helpers answer artifact inspection and result-explanation prompts from a
finished run without routing back through the generic interactive orchestrator.
That keeps post-run chat follow-ups local to the completed run state and avoids
planner-style responses for simple explanation requests.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from bio_harness.core.preflight_summary import (
    build_preflight_summary,
    preflight_summary_from_json,
)
from bio_harness.core.result_review import review_run_results
from bio_harness.reporting.artifact_schema import profile_artifact_schema
from bio_harness.ui.artifact_preview import artifact_preview_metadata
from bio_harness.ui.chat_first_shell import (
    artifact_kind,
    collect_run_artifacts,
    select_primary_artifact,
    summarize_artifacts_for_chat,
)

_EXPLANATION_PHRASES = (
    "summarize",
    "summary",
    "inspect",
    "explain",
    "describe",
    "review",
    "what it contains",
    "what's in",
    "what is in",
    "tell me about",
)
_RESULT_TARGET_PHRASES = (
    "result",
    "results",
    "output",
    "outputs",
    "artifact",
    "artifacts",
    "table",
    "file",
    "report",
    "plot",
    "csv",
    "tsv",
    "gtf",
    "h5ad",
    "vcf",
    "cluster",
    "marker",
    "abundance",
)
_SUMMARY_ONLY_PHRASES = (
    "summarize the outputs",
    "summarize the results",
    "explain the result",
    "explain the results",
    "what changed",
    "what do the results show",
)
_PREFLIGHT_PHRASES = (
    "preflight",
    "input quality",
    "input issues",
    "inputs look okay",
    "resource warning",
    "resource warnings",
    "before running",
    "before execution",
)
_IN_RUN_QUALITY_PHRASES = (
    "during the run",
    "during execution",
    "while running",
    "heartbeat",
    "in-run quality",
    "zero-byte",
    "zero byte",
    "empty output",
    "suspicious output",
)
_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "from",
    "with",
    "for",
    "in",
    "on",
    "it",
    "this",
    "that",
    "what",
    "does",
    "contains",
    "contain",
    "latest",
    "completed",
    "run",
    "result",
    "results",
    "output",
    "outputs",
    "artifact",
    "artifacts",
    "file",
    "files",
}
_ARTIFACT_ALIASES = (
    (("gene abundance", "abundance table", "abundance tsv"), ("gene_abundance", "abundance")),
    (("assembled gtf", "transcript gtf", "assembled transcript"), ("assembled", ".gtf")),
    (("deseq results", "differential expression", "de results"), ("deseq", "results")),
    (("cluster assignments", "clusters"), ("cluster_assign", "cluster")),
    (("marker genes", "markers"), ("marker_genes", "marker")),
    (("processed h5ad", "processed file"), ("processed", ".h5ad")),
    (("multiqc", "fastqc report"), ("multiqc", "fastqc")),
)


def should_route_completed_run_followup(
    run: Mapping[str, Any] | None,
    user_text: str,
) -> bool:
    """Return whether a chat prompt should use completed-run follow-up handling.

    Args:
        run: Candidate run mapping.
        user_text: Raw chat input.

    Returns:
        ``True`` when the run is completed and the prompt asks for a result or
        artifact explanation instead of new execution work.
    """
    if not isinstance(run, Mapping):
        return False
    if str(run.get("status", "")).strip().lower() != "completed":
        return False
    text = _normalize_text(user_text)
    if not text:
        return False
    if any(phrase in text for phrase in _SUMMARY_ONLY_PHRASES):
        return True
    if any(phrase in text for phrase in _PREFLIGHT_PHRASES):
        return True
    if any(phrase in text for phrase in _IN_RUN_QUALITY_PHRASES):
        return True
    if _resolve_requested_artifact(run, text) is not None:
        return True
    has_explanation_phrase = any(phrase in text for phrase in _EXPLANATION_PHRASES)
    has_result_target = any(phrase in text for phrase in _RESULT_TARGET_PHRASES)
    return has_explanation_phrase and has_result_target


def build_completed_run_followup_response(
    run: Mapping[str, Any],
    user_text: str,
) -> str:
    """Build a deterministic response for a completed-run follow-up.

    Args:
        run: Completed run mapping.
        user_text: Raw follow-up prompt.

    Returns:
        One Markdown response, or an empty string when no deterministic
        follow-up answer should be produced.
    """
    if not should_route_completed_run_followup(run, user_text):
        return ""
    normalized_text = _normalize_text(user_text)
    if any(phrase in normalized_text for phrase in _PREFLIGHT_PHRASES):
        return _build_preflight_response(run)
    if any(phrase in normalized_text for phrase in _IN_RUN_QUALITY_PHRASES):
        return _build_in_run_quality_response(run)
    artifact = _resolve_requested_artifact(run, normalized_text)
    if artifact is not None:
        return _build_artifact_response(run, artifact)
    return _build_run_response(run)


def _build_artifact_response(run: Mapping[str, Any], artifact: Path) -> str:
    """Build a concise explanation of one completed-run artifact."""
    schema = profile_artifact_schema(artifact)
    label = _artifact_label(run, artifact)
    metadata = artifact_preview_metadata(artifact, artifact_kind(artifact))
    lines = [
        f"I inspected `{label}` from the completed run.",
        _artifact_semantics_sentence(artifact, schema),
        f"Format: `{schema.get('format', 'unknown')}`. Size: `{metadata.get('size', '')}`. Sampled rows: `{schema.get('sample_rows_analyzed', 0)}`.",
    ]
    columns = list(schema.get("columns", []) or [])
    if columns:
        lines.append(f"Columns: {_format_column_summary(columns[:6])}")
        quantitative_columns = [
            str(column.get('name', '')).strip()
            for column in columns
            if str(column.get("inferred_type", "")).strip() in {"integer", "number"}
            and str(column.get("name", "")).strip()
        ]
        if quantitative_columns:
            lines.append(
                "Numeric fields available for interpretation: "
                + ", ".join(f"`{name}`" for name in quantitative_columns[:4])
                + "."
            )
    return "\n\n".join(lines)


def _build_run_response(run: Mapping[str, Any]) -> str:
    """Build a concise overall summary for one completed run."""
    interpretation_dir = _interpretation_dir(run)
    analysis_type = _analysis_type_from_run(run)
    plan = dict(run.get("plan", {}) or {}) if isinstance(run.get("plan", {}), Mapping) else {}
    review = review_run_results(
        interpretation_dir,
        analysis_type=analysis_type,
        plan=plan,
        llm=None,
    )
    artifacts = collect_run_artifacts(run, limit=5)
    labels = summarize_artifacts_for_chat(
        artifacts,
        run_dir=str(run.get("run_dir", "")),
        limit=4,
    )
    lines = [
        f"I inspected the completed run `{str(run.get('run_uid', '') or 'run')}`.",
        review.interpretation.interpretation,
    ]
    preflight_summary = _preflight_summary_for_run(run)
    if preflight_summary is not None and preflight_summary.recommendation != "unavailable":
        lines.append(
            "Preflight: "
            f"`{preflight_summary.recommendation}`. {preflight_summary.rationale}"
        )
    in_run_quality_summary = _in_run_quality_summary_for_run(run)
    if in_run_quality_summary is not None:
        lines.append(_in_run_quality_one_line(in_run_quality_summary))
    if review.interpretation.concerns:
        lines.append("Concerns: " + "; ".join(review.interpretation.concerns[:3]) + ".")
    lines.append(
        "Recommended next step: "
        f"`{review.decision.decision.value}`. {review.decision.rationale}"
    )
    if labels:
        lines.append("Recent outputs: " + ", ".join(f"`{label}`" for label in labels) + ".")
    return "\n\n".join(lines)


def _build_in_run_quality_response(run: Mapping[str, Any]) -> str:
    """Build a concise deterministic explanation of in-run quality state."""

    summary = _in_run_quality_summary_for_run(run)
    if summary is None:
        return "I could not reconstruct a persisted in-run quality summary for this completed run."
    lines = [
        f"I inspected in-run quality state for the completed run `{str(run.get('run_uid', '') or 'run')}`.",
        "This summary is reporting-only and reflects the latest persisted execution heartbeat.",
        _in_run_quality_one_line(summary),
    ]
    zero_byte_outputs = list(summary.get("zero_byte_outputs", []) or [])
    if zero_byte_outputs:
        lines.append(
            "Suspicious zero-byte outputs: "
            + ", ".join(f"`{path}`" for path in zero_byte_outputs[:4])
            + "."
        )
    else:
        lines.append("Suspicious zero-byte outputs: none recorded in the persisted summary.")
    expected_missing = list(summary.get("expected_outputs_missing", []) or [])
    if expected_missing:
        lines.append(
            "Expected outputs still missing in the latest heartbeat: "
            + ", ".join(f"`{path}`" for path in expected_missing[:4])
            + "."
        )
    return "\n\n".join(lines)


def _build_preflight_response(run: Mapping[str, Any]) -> str:
    """Build a concise deterministic explanation of run preflight state."""

    summary = _preflight_summary_for_run(run)
    if summary is None or summary.recommendation == "unavailable":
        return "I could not reconstruct a reproducible preflight summary for this completed run."

    lines = [
        f"I inspected preflight state for the completed run `{str(run.get('run_uid', '') or 'run')}`.",
        f"Recommendation: `{summary.recommendation}`. {summary.rationale}",
    ]
    if summary.input_scan is not None:
        lines.append(
            "Input scan: "
            f"`{summary.input_scan_source}` with blocking="
            f"`{str(summary.input_scan.has_blocking).lower()}`. {summary.input_scan.summary}"
        )
        if summary.input_scan.issues:
            lines.append(
                "Input issues: "
                + "; ".join(
                    f"`{issue.category}` ({issue.severity})"
                    for issue in summary.input_scan.issues[:3]
                )
                + "."
            )
    if summary.resource_report is not None:
        warnings = summary.resource_report.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            lines.append("Resource warnings: " + "; ".join(str(item) for item in warnings[:2]) + ".")
        else:
            lines.append("Resource preflight reported no warnings.")
    return "\n\n".join(lines)


def _resolve_requested_artifact(
    run: Mapping[str, Any],
    normalized_text: str,
) -> Path | None:
    """Return the artifact that best matches one follow-up prompt."""
    artifacts = collect_run_artifacts(run, limit=24)
    if not artifacts:
        return None
    best: Path | None = None
    best_score = 0
    for artifact in artifacts:
        score = _artifact_match_score(artifact, normalized_text)
        if score > best_score:
            best_score = score
            best = artifact
    if best is not None and best_score > 0:
        return best
    if any(phrase in normalized_text for phrase in _SUMMARY_ONLY_PHRASES):
        return None
    if any(phrase in normalized_text for phrase in _RESULT_TARGET_PHRASES):
        return select_primary_artifact(artifacts)
    return None


def _artifact_match_score(artifact: Path, normalized_text: str) -> int:
    """Return a simple lexical match score for one artifact."""
    score = 0
    name = artifact.name.lower()
    stem_text = artifact.stem.lower().replace("_", " ").replace("-", " ")
    if name in normalized_text:
        score += 24
    if stem_text and stem_text in normalized_text:
        score += 16
    token_matches = _token_overlap_score(normalized_text, f"{name} {stem_text}")
    score += token_matches
    for phrases, required_tokens in _ARTIFACT_ALIASES:
        if not any(phrase in normalized_text for phrase in phrases):
            continue
        if all(token in name or token in stem_text for token in required_tokens):
            score += 20
    return score


def _token_overlap_score(user_text: str, artifact_text: str) -> int:
    """Return the count of overlapping non-trivial tokens."""
    user_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", user_text.lower())
        if token and token not in _STOPWORDS and len(token) > 2
    }
    artifact_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", artifact_text.lower())
        if token and token not in _STOPWORDS and len(token) > 2
    }
    return len(user_tokens & artifact_tokens)


def _artifact_label(run: Mapping[str, Any], artifact: Path) -> str:
    """Return a run-relative label when possible."""
    run_dir = str(run.get("run_dir", "")).strip()
    if run_dir:
        try:
            base = Path(run_dir).expanduser().resolve()
            return str(artifact.resolve().relative_to(base))
        except Exception:
            pass
    return artifact.name


def _interpretation_dir(run: Mapping[str, Any]) -> Path:
    """Return the best directory root for deterministic result interpretation."""
    run_dir = Path(str(run.get("run_dir", "") or "")).expanduser().resolve(strict=False)
    final_dir = (run_dir / "final").resolve(strict=False)
    if final_dir.exists():
        try:
            if any(path.is_file() for path in final_dir.iterdir()):
                return final_dir
        except Exception:
            pass
    return run_dir


def _analysis_type_from_run(run: Mapping[str, Any]) -> str:
    """Infer the run analysis type for result interpretation."""
    analysis_spec = run.get("analysis_spec", {}) if isinstance(run.get("analysis_spec", {}), Mapping) else {}
    analysis_type = str(analysis_spec.get("analysis_type", "")).strip()
    if not analysis_type:
        state = _run_state_payload(run)
        analysis_spec = state.get("analysis_spec", {}) if isinstance(state.get("analysis_spec", {}), Mapping) else {}
        analysis_type = str(analysis_spec.get("analysis_type", "")).strip()
    if analysis_type:
        return analysis_type
    execution_contract = (
        analysis_spec.get("execution_contract", {})
        if isinstance(analysis_spec.get("execution_contract", {}), Mapping)
        else {}
    )
    return str(execution_contract.get("analysis_family", "")).strip() or "analysis"


def _preflight_summary_for_run(run: Mapping[str, Any]):
    """Build a reporting-safe preflight summary for one completed run."""

    run_dir = _run_dir_path(run)
    if run_dir is None:
        return None
    persisted_summary_path = run_dir / "preflight_summary.json"
    if persisted_summary_path.exists():
        try:
            payload = json.loads(persisted_summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        persisted_summary = preflight_summary_from_json(payload if isinstance(payload, Mapping) else None)
        if persisted_summary is not None:
            return persisted_summary
    manifest = _run_manifest_payload(run)
    state = _run_state_payload(run)
    analysis_spec = run.get("analysis_spec", {}) if isinstance(run.get("analysis_spec", {}), Mapping) else {}
    if not analysis_spec and isinstance(state.get("analysis_spec", {}), Mapping):
        analysis_spec = state.get("analysis_spec", {})
    data_root_text = str(
        analysis_spec.get("data_root", "")
        or run.get("data_root", "")
        or manifest.get("data_root", "")
        or state.get("requested_data_root", "")
        or ""
    ).strip()
    run_input_quality = run.get("input_quality", {})
    if isinstance(run_input_quality, Mapping) and run_input_quality:
        persisted_input_quality = run_input_quality
    else:
        state_input_quality = state.get("input_quality", {})
        persisted_input_quality = state_input_quality if isinstance(state_input_quality, Mapping) else None
    selected_dir = _selected_dir_for_run(run, manifest=manifest, run_dir=run_dir)
    return build_preflight_summary(
        dict(run.get("plan", {}) or {}) if isinstance(run.get("plan", {}), Mapping) else {"plan": []},
        selected_dir=selected_dir,
        analysis_type=_analysis_type_from_run(run),
        data_root=Path(data_root_text).expanduser().resolve(strict=False) if data_root_text else None,
        persisted_input_quality=persisted_input_quality if isinstance(persisted_input_quality, Mapping) else None,
    )


def _in_run_quality_summary_for_run(run: Mapping[str, Any]) -> dict[str, Any] | None:
    """Load one persisted in-run quality summary for a completed run."""

    persisted = run.get("in_run_quality_summary", {})
    if isinstance(persisted, Mapping) and persisted:
        return dict(persisted)
    state = _run_state_payload(run)
    persisted = state.get("in_run_quality_summary", {})
    if isinstance(persisted, Mapping) and persisted:
        return dict(persisted)
    run_dir = _run_dir_path(run)
    if run_dir is None:
        return None
    summary_path = run_dir / "in_run_quality_summary.json"
    if not summary_path.exists():
        return None
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dict(payload) if isinstance(payload, Mapping) and payload else None


def _run_dir_path(run: Mapping[str, Any]) -> Path | None:
    """Return the resolved run directory path when available."""

    run_dir_text = str(run.get("run_dir", "") or "").strip()
    if not run_dir_text:
        return None
    return Path(run_dir_text).expanduser().resolve(strict=False)


def _run_state_payload(run: Mapping[str, Any]) -> dict[str, Any]:
    """Load persisted state for one completed run when available."""

    context_payload = _completed_run_context_payload(run)
    state = context_payload.get("state", {}) if isinstance(context_payload, Mapping) else {}
    if isinstance(state, Mapping) and state:
        return dict(state)
    run_dir = _run_dir_path(run)
    if run_dir is None:
        return {}
    state_path = run_dir / "state.json"
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _run_manifest_payload(run: Mapping[str, Any]) -> dict[str, Any]:
    """Load persisted manifest metadata for one completed run when available."""

    context_payload = _completed_run_context_payload(run)
    manifest = context_payload.get("manifest", {}) if isinstance(context_payload, Mapping) else {}
    if isinstance(manifest, Mapping) and manifest:
        return dict(manifest)
    run_dir = _run_dir_path(run)
    if run_dir is None:
        return {}
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _selected_dir_for_run(
    run: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    run_dir: Path,
) -> Path:
    """Infer the selected-dir path for one completed run."""

    context_payload = _completed_run_context_payload(run)
    selected_dir_text = str(run.get("selected_dir", "") or "").strip() or str(
        context_payload.get("selected_dir", "") if isinstance(context_payload, Mapping) else ""
    ).strip() or str(
        manifest.get("selected_dir", "") or ""
    ).strip()
    if selected_dir_text:
        return Path(selected_dir_text).expanduser().resolve(strict=False)
    return run_dir


def _completed_run_context_payload(run: Mapping[str, Any]) -> dict[str, Any]:
    """Load one persisted completed-run context payload when available."""

    run_dir = _run_dir_path(run)
    if run_dir is None:
        return {}
    context_path = run_dir / "completed_run_context.json"
    if not context_path.exists():
        return {}
    try:
        payload = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _in_run_quality_one_line(summary: Mapping[str, Any]) -> str:
    """Render one concise sentence for a persisted in-run quality summary."""

    zero_byte_outputs = list(summary.get("zero_byte_outputs", []) or [])
    suspicious_count = int(summary.get("suspicious_event_count", 0) or 0)
    if zero_byte_outputs:
        return (
            "In-run quality: "
            "`review_during_run`. Suspicious zero-byte outputs were observed for "
            + ", ".join(f"`{path}`" for path in zero_byte_outputs[:3])
            + "."
        )
    return (
        "In-run quality: "
        f"`proceed`. The latest persisted heartbeat recorded no suspicious output events "
        f"and `{suspicious_count}` new warnings."
    )


def _artifact_semantics_sentence(
    artifact: Path,
    schema: Mapping[str, Any],
) -> str:
    """Return one plain-English sentence about what an artifact contains."""
    name = artifact.name.lower()
    column_names = [
        str(column.get("name", "")).strip().lower()
        for column in list(schema.get("columns", []) or [])
        if isinstance(column, Mapping)
    ]
    if "gene_abundance" in name or {"coverage", "fpkm", "tpm"}.issubset(set(column_names)):
        return (
            "This table stores StringTie abundance estimates, with one row per reference transcript/gene region and "
            "quantitative columns such as coverage, FPKM, and TPM."
        )
    if name.endswith(".gtf"):
        return (
            "This GTF lists assembled transcript features with genomic coordinates and annotation attributes that "
            "describe the transcript models produced by the run."
        )
    if {"gene_id", "log2foldchange", "padj"}.issubset(set(column_names)):
        return (
            "This table contains differential-expression results, with one row per gene and statistics such as "
            "log2 fold change and adjusted p-value."
        )
    if {"cell_id", "cluster_id"}.issubset(set(column_names)):
        return (
            "This table maps cells to inferred clusters, so it captures the clustering assignment produced by the "
            "single-cell workflow."
        )
    if "marker" in name:
        return (
            "This table lists marker genes associated with discovered clusters, so it is useful for interpreting "
            "cluster identity."
        )
    return (
        f"This `{schema.get('format', 'artifact')}` artifact contains the structured output materialized by the "
        "completed run."
    )


def _format_column_summary(columns: Sequence[Mapping[str, Any]]) -> str:
    """Return a compact schema summary for the first few columns."""
    parts: list[str] = []
    for column in columns:
        name = str(column.get("name", "")).strip()
        inferred_type = str(column.get("inferred_type", "")).strip() or "unknown"
        examples = [str(item).strip() for item in list(column.get("examples", []) or []) if str(item).strip()]
        if examples:
            parts.append(f"`{name}` ({inferred_type}; e.g. {', '.join(examples[:2])})")
        else:
            parts.append(f"`{name}` ({inferred_type})")
    return ", ".join(parts)


def _normalize_text(text: str) -> str:
    """Normalize one user prompt for phrase matching."""
    return " ".join(str(text or "").strip().lower().split())


__all__ = [
    "build_completed_run_followup_response",
    "should_route_completed_run_followup",
]
