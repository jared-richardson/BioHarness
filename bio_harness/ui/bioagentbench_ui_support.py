"""Support helpers for UI-driven BioAgentBench reliability runs.

These helpers keep benchmark prompt construction and benchmark-mode selection
deterministic for the Streamlit UI and any UI automation scripts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping

from bio_harness.core.benchmark_policy import (
    SCIENTIFIC_HARNESS_POLICY,
    is_blind_bioagentbench_policy,
    normalize_benchmark_policy,
)
from bio_harness.core.bioagentbench_official import entry_input_files


_UI_BENCHMARK_TASK_RE = re.compile(
    r"BioAgentBench(?:\s+official-mode)?\s+task:\s*([^.]+?)\.",
    re.IGNORECASE,
)
_UI_BENCHMARK_DATA_ROOT_RE = re.compile(
    r"Input files are under\s+(\S+)\s+and include\b",
    re.IGNORECASE,
)


def _normalize_ui_benchmark_task_id(token: str) -> str:
    """Normalize one UI benchmark task token to a manifest-style task id.

    Args:
        token: Free-form task label from a benchmark prompt.

    Returns:
        Lowercased hyphenated task identifier.
    """
    normalized = re.sub(r"[^a-z0-9]+", "-", str(token or "").strip().lower()).strip("-")
    return normalized


def ui_benchmark_policy(env: Mapping[str, str] | None = None) -> str:
    """Return the benchmark policy requested for the UI session.

    Args:
        env: Optional environment mapping. Defaults to ``os.environ``.

    Returns:
        One normalized benchmark-policy token. Invalid or missing values fall
        back to ``scientific_harness``.
    """
    payload = os.environ if env is None else env
    return normalize_benchmark_policy(payload.get("BIO_HARNESS_BENCHMARK_POLICY"))


def _render_ui_template(value: str, *, entry: Mapping[str, Any]) -> str:
    """Render one manifest template string for the chat UI.

    Args:
        value: Template string from the manifest.
        entry: One resolved benchmark manifest entry.

    Returns:
        A rendered string with ``selected_dir`` represented as the current run
        directory instead of one absolute path.
    """
    replacements = {
        "selected_dir": "the current run directory",
        "task_dir": str(entry.get("task_dir", "") or ""),
        "data_root": str(entry.get("data_root", "") or ""),
        "runs_root": str(entry.get("runs_root", "") or ""),
        "task_id": str(entry.get("task_id", "") or ""),
    }
    try:
        return str(value).format(**replacements)
    except Exception:
        return str(value)


def _summarize_input_files(entry: Mapping[str, Any], *, max_items: int = 8) -> str:
    """Build a compact file summary for one benchmark entry.

    Args:
        entry: One resolved benchmark manifest entry.
        max_items: Maximum number of filenames to show explicitly.

    Returns:
        One human-readable file summary.
    """
    files = entry_input_files(dict(entry))
    if not files:
        return "the provided input files"
    if len(files) <= max_items:
        return ", ".join(files)
    head = ", ".join(files[:max_items])
    return f"{head}, and {len(files) - max_items} more"


def _additional_ui_input_guidance(entry: Mapping[str, Any]) -> list[str]:
    """Return deterministic prompt guidance for special benchmark input files.

    Args:
        entry: One resolved benchmark manifest entry.

    Returns:
        Optional prompt lines that make hidden benchmark-side metadata explicit
        to the UI agent.
    """
    files = {name.lower(): name for name in entry_input_files(dict(entry))}
    guidance: list[str] = []
    sample_metadata_name = files.get("sample_metadata.tsv") or files.get("sample_metadata.csv")
    if sample_metadata_name:
        guidance.append(
            f"Use `{sample_metadata_name}` under the task data directory to map samples to conditions instead of inferring control/treatment groups from FASTQ filenames."
        )
    task_id = str(entry.get("task_id", "") or "").strip().lower()
    task_dir = Path(str(entry.get("task_dir", "") or "")).resolve(strict=False)
    if task_id == "viral-metagenomics":
        guidance.extend(
            [
                "Treat this as the `viral_metagenomics` workflow class.",
                (
                    "Use only the viral reference FASTAs already staged under "
                    f"{task_dir / 'references'} and do not use unrelated FASTA files from workspace/inputs_readonly or elsewhere."
                ),
                "Prefer the repo-local helper-backed viral classification path over ad hoc shell pipelines.",
            ]
        )
    return guidance


def build_ui_benchmark_prompt(entry: Mapping[str, Any]) -> str:
    """Build a chat-ready benchmark prompt for one UI-driven attempt.

    Args:
        entry: One resolved benchmark manifest entry.

    Returns:
        A deterministic user-facing prompt that points the UI agent at the
        local benchmark inputs, required outputs, and blind-eval restrictions.
    """
    task_name = str(entry.get("task_name", "") or entry.get("task_id", "task")).strip()
    task_prompt = _render_ui_template(str(entry.get("task_prompt", "") or "").strip(), entry=entry).strip()
    data_root = Path(str(entry.get("data_root", "") or "")).resolve(strict=False)
    input_summary = _summarize_input_files(entry)

    lines = [
        "Proceed with execution now.",
        f"BioAgentBench task: {task_name}.",
        f"Input files are under {data_root} and include {input_summary}.",
    ]
    if task_prompt:
        lines.append(task_prompt)
    lines.extend(_additional_ui_input_guidance(entry))

    for requirement in entry.get("output_requirements", []) or []:
        rendered = _render_ui_template(str(requirement).strip(), entry=entry).strip()
        if rendered:
            lines.append(rendered)

    deliverables = entry.get("deliverables", []) if isinstance(entry.get("deliverables", []), list) else []
    for deliverable in deliverables:
        if not isinstance(deliverable, Mapping):
            continue
        rel_path = str(deliverable.get("path", "") or "").strip()
        if not rel_path:
            continue
        description = str(deliverable.get("description", "") or "").strip()
        line = f"Write `{rel_path}` under the current run directory."
        if description:
            line = f"{description} Save it as `{rel_path}` under the current run directory."
        columns = [str(col).strip() for col in (deliverable.get("columns", []) or []) if str(col).strip()]
        if columns:
            line += f" The file must contain exactly these columns: {', '.join(columns)}."
        lines.append(line)

    lines.extend(
        [
            "Save all generated outputs inside the current run directory for this UI run.",
            "Do not read benchmark truth files, benchmark results files, or benchmark recipe files.",
            "Do not write anywhere outside the current run directory except reading the provided local benchmark inputs and other local task files.",
        ]
    )

    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = " ".join(str(line).split())
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return " ".join(deduped)


def is_ui_benchmark_prompt(request_text: str) -> bool:
    """Return whether text matches the chat-facing UI benchmark prompt form.

    Args:
        request_text: Raw chat request text.

    Returns:
        True when the request is one UI-generated benchmark prompt that still
        refers to the placeholder ``current run directory``.
    """
    text = " ".join(str(request_text or "").split())
    return "BioAgentBench task:" in text and "current run directory" in text


def extract_ui_benchmark_data_root(request_text: str) -> str | None:
    """Extract the explicit benchmark task data directory from one UI prompt.

    Args:
        request_text: Raw UI benchmark prompt text.

    Returns:
        Canonical absolute task data root when the prompt exposes it,
        otherwise ``None``.
    """
    text = str(request_text or "").strip()
    if not text:
        return None
    match = _UI_BENCHMARK_DATA_ROOT_RE.search(text)
    if not match:
        return None
    return str(Path(match.group(1)).expanduser().resolve(strict=False))


def extract_ui_benchmark_task_id(request_text: str) -> str | None:
    """Extract the benchmark task identifier from one UI benchmark prompt.

    Args:
        request_text: Raw UI benchmark prompt text.

    Returns:
        Normalized benchmark task id when it can be inferred, otherwise
        ``None``.
    """
    text = str(request_text or "").strip()
    if not text:
        return None
    data_root = extract_ui_benchmark_data_root(text)
    if data_root:
        match = re.search(r"/tasks/([^/]+)/data/?$", data_root)
        if match:
            return _normalize_ui_benchmark_task_id(match.group(1))
    label_match = _UI_BENCHMARK_TASK_RE.search(text)
    if not label_match:
        return None
    task_id = _normalize_ui_benchmark_task_id(label_match.group(1))
    return task_id or None


def benchmark_prompt_contract_seed(request_text: str) -> dict[str, Any]:
    """Return task-specific contract overrides for UI benchmark prompts.

    Args:
        request_text: Raw UI benchmark prompt text.

    Returns:
        Deterministic contract seed keyed by task id. Empty when no task-
        specific override is needed.
    """
    task_id = extract_ui_benchmark_task_id(request_text)
    if task_id == "viral-metagenomics":
        return {
            "must_include_capabilities": ["metagenomics_profiling"],
            "required_tool_hints": ["classify_viral_reads_kmer.py"],
            "explicit_tool_hints": ["classify_viral_reads_kmer.py"],
        }
    return {}


def apply_benchmark_prompt_contract_seed(
    contract: Mapping[str, Any] | None,
    request_text: str,
) -> dict[str, Any]:
    """Apply benchmark prompt contract overrides to one inferred contract.

    Args:
        contract: Inferred contract from generic prompt analysis.
        request_text: Raw UI benchmark prompt text.

    Returns:
        Contract with any task-specific benchmark overrides applied.
    """
    merged = dict(contract or {})
    seed = benchmark_prompt_contract_seed(request_text)
    if not seed:
        return merged
    for key in ("must_include_capabilities", "required_tool_hints", "explicit_tool_hints"):
        if key not in seed:
            continue
        values = [str(item).strip() for item in seed.get(key, []) if str(item).strip()]
        merged[key] = values
    return merged


def concretize_ui_benchmark_prompt(
    request_text: str,
    *,
    selected_dir: str,
    benchmark_policy: str,
) -> str:
    """Rewrite one UI benchmark prompt for deterministic blind planning.

    The browser-facing prompt intentionally hides the final run path until the
    UI has created a run directory. Before sending the request into the
    planner, the UI should concretize that placeholder and align the wording
    with the backend official runner so prompt structure stays consistent.

    Args:
        request_text: Raw UI benchmark prompt text.
        selected_dir: Concrete run directory for the current UI attempt.
        benchmark_policy: Active benchmark policy token.

    Returns:
        A concrete prompt string for planner consumption. Non-benchmark or
        non-blind prompts are returned unchanged.
    """
    text = str(request_text or "").strip()
    if not text or not is_blind_bioagentbench_policy(benchmark_policy) or not is_ui_benchmark_prompt(text):
        return text

    resolved_dir = str(Path(selected_dir).expanduser().resolve(strict=False))
    rewritten = re.sub(r"^\s*Proceed with execution now\.\s*", "", text, count=1)
    rewritten = rewritten.replace("BioAgentBench task:", "BioAgentBench official-mode task:", 1)
    rewritten = rewritten.replace("the current run directory", resolved_dir)
    rewritten = rewritten.replace("current run directory", resolved_dir)
    rewritten = rewritten.replace(
        f"Save all generated outputs inside {resolved_dir} for this UI run.",
        f"Write all generated outputs under {resolved_dir}.",
    )
    rewritten = rewritten.replace(
        f"Do not write anywhere outside {resolved_dir} except reading the provided local benchmark inputs and other local task files.",
        "Do not write anywhere outside the selected directory except reading the provided input files.",
    )
    return " ".join(rewritten.split())


def benchmark_manifest_default_policy() -> str:
    """Return the default benchmark policy for UI benchmark sweeps.

    Returns:
        The benchmark policy the UI benchmark runner should use when the caller
        does not override it explicitly.
    """
    return normalize_benchmark_policy(os.getenv("BIO_HARNESS_BENCHMARK_POLICY", SCIENTIFIC_HARNESS_POLICY))
