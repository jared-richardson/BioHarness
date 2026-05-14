"""Helpers for preserving explicit one-tool requests in the Streamlit UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from bio_harness.ui.path_text import extract_paths_from_text

_DIRECT_REQUEST_PREFIXES: tuple[str, ...] = (
    "run ",
    "please run ",
    "execute ",
    "please execute ",
    "use ",
    "please use ",
    "perform ",
    "please perform ",
)

_MULTI_STEP_MARKERS: tuple[str, ...] = (
    " then ",
    " after ",
    " followed by ",
    " workflow",
    " pipeline",
    " multi-step",
    " full analysis",
    " complete analysis",
)

_DIRECT_SMOKE_MARKER = "direct one-step skill smoke test"
_DIRECT_TOOL_ALIAS_MAP: dict[str, str] = {
    "samtools flagstat": "samtools_flagstat",
    "flagstat": "samtools_flagstat",
    "samtools_flagstat": "samtools_flagstat",
    "samtools idxstats": "samtools_idxstats",
    "idxstats": "samtools_idxstats",
    "samtools_idxstats": "samtools_idxstats",
    "samtools stats": "samtools_stats",
    "samtools_stats": "samtools_stats",
    "multiqc report": "multiqc_report",
    "multiqc_report": "multiqc_report",
    "multiqc": "multiqc_report",
    "quarto report": "quarto_report",
    "quarto_report": "quarto_report",
    "quarto": "quarto_report",
}
_GENERIC_HINTS: frozenset[str] = frozenset({"samtools"})
_DIRECT_REPORT_TOOLS: frozenset[str] = frozenset({"multiqc_report", "quarto_report"})


def _requested_tool_from_text(user_text: str) -> str | None:
    """Infer an explicitly requested utility skill directly from user text."""
    text_l = str(user_text or "").strip().lower()
    if not text_l:
        return None
    specific_aliases = [
        ("samtools flagstat", "samtools_flagstat"),
        ("flagstat", "samtools_flagstat"),
        ("samtools idxstats", "samtools_idxstats"),
        ("idxstats", "samtools_idxstats"),
        ("samtools stats", "samtools_stats"),
        ("samtools_stats", "samtools_stats"),
        ("multiqc report", "multiqc_report"),
        ("multiqc_report", "multiqc_report"),
        ("multiqc", "multiqc_report"),
        ("quarto report", "quarto_report"),
        ("quarto_report", "quarto_report"),
        ("quarto", "quarto_report"),
    ]
    for phrase, tool_name in specific_aliases:
        if phrase in text_l:
            return tool_name
    return None


def _requested_tool_hints(contract: Mapping[str, Any]) -> list[str]:
    required = [
        str(x).strip().lower()
        for x in (contract.get("required_tool_hints", []) if isinstance(contract, Mapping) else [])
        if str(x).strip()
    ]
    explicit = [
        str(x).strip().lower()
        for x in (contract.get("explicit_tool_hints", []) if isinstance(contract, Mapping) else [])
        if str(x).strip()
    ]
    hints = required or explicit
    deduped: list[str] = []
    for hint in hints:
        normalized = _DIRECT_TOOL_ALIAS_MAP.get(hint, hint)
        if normalized not in deduped:
            deduped.append(normalized)
    specific = [hint for hint in deduped if hint not in _GENERIC_HINTS]
    if specific:
        return specific
    return deduped


def looks_like_direct_single_skill_request(
    user_text: str,
    contract: Mapping[str, Any],
) -> bool:
    """Return whether a user request should stay as one explicit skill.

    Args:
        user_text: Raw user request text from chat.
        contract: Inferred request contract for the same message.

    Returns:
        ``True`` when the request is an explicit one-tool imperative and should
        stay on the direct-skill path instead of expanding into a workflow.
    """
    text_l = str(user_text or "").strip().lower()
    if not text_l:
        return False
    if _DIRECT_SMOKE_MARKER in text_l:
        return True
    hinted_tool = _requested_tool_from_text(user_text)
    hints = _requested_tool_hints(contract)
    if _looks_like_direct_report_bundle_request(text_l, hinted_tool, hints):
        return True
    if hinted_tool is None and len(hints) != 1:
        return False
    if any(marker in text_l for marker in _MULTI_STEP_MARKERS):
        return False
    return any(text_l.startswith(prefix) or f" {prefix}" in text_l for prefix in _DIRECT_REQUEST_PREFIXES)


def decorate_direct_single_skill_request(
    request_text: str,
    contract: Mapping[str, Any],
) -> str:
    """Append a deterministic single-skill directive when appropriate.

    Args:
        request_text: Request text that will be sent to the planner.
        contract: Inferred request contract for the original user request.

    Returns:
        The original request text, optionally augmented with a deterministic
        single-skill directive that triggers the direct-skill execution path.
    """
    base_text = str(request_text or "").strip()
    if not base_text or _DIRECT_SMOKE_MARKER in base_text.lower():
        return base_text
    if not looks_like_direct_single_skill_request(base_text, contract):
        return base_text
    tool = _requested_tool_hints(contract)[0]
    directive = (
        f"Treat this as a {_DIRECT_SMOKE_MARKER}.\n"
        f"Use the explicitly requested Bio-Harness skill/tool hint `{tool}` as the primary execution step.\n"
        "Do not expand this into a broader workflow unless the user explicitly asked for additional steps."
    )
    return f"{base_text}\n\n{directive}"


def select_execution_contract(
    user_text: str,
    direct_contract: Mapping[str, Any],
    scoped_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Choose the contract the UI should use for execution validation.

    Args:
        user_text: Raw user request text from chat.
        direct_contract: Contract inferred only from the current user message.
        scoped_contract: Contract inferred from the current message plus the
            surrounding session scope.

    Returns:
        The per-message contract when the request is an explicit direct
        one-skill imperative, otherwise the broader scoped contract.
    """
    if looks_like_direct_single_skill_request(user_text, direct_contract):
        narrowed = dict(direct_contract)
        direct_hints = _requested_tool_hints(direct_contract)
        text_tool = _requested_tool_from_text(user_text)
        if text_tool is not None:
            direct_hints = [text_tool]
        if direct_hints:
            narrowed["explicit_tool_hints"] = list(direct_hints)
            narrowed["required_tool_hints"] = list(direct_hints)
        narrowed["must_include_capabilities"] = []
        return narrowed
    return dict(scoped_contract)


def build_direct_single_skill_plan(
    user_text: str,
    contract: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Build a deterministic one-step plan for simple explicit utility requests.

    Args:
        user_text: Raw user request text from chat.
        contract: Inferred request contract for the same message.

    Returns:
        Executable one-step plan when the request maps cleanly to a supported
        deterministic helper, otherwise ``None``.
    """
    if not looks_like_direct_single_skill_request(user_text, contract):
        return None
    text_tool = _requested_tool_from_text(user_text)
    hints = [text_tool] if text_tool is not None else _requested_tool_hints(contract)
    if len(hints) != 1:
        return None
    requested_tool = hints[0]
    path_candidates = [Path(p).expanduser() for p in extract_paths_from_text(user_text)]

    def _first_suffix(suffix: str) -> Path | None:
        for path in path_candidates:
            if path.suffix.lower() == suffix.lower():
                return path
        return None

    if requested_tool == "samtools_flagstat":
        input_bam = _first_suffix(".bam")
        if input_bam is None:
            return None
        output_txt = f"{str(input_bam)[:-4]}.flagstat.txt"
        return {
            "thought_process": "Run the explicitly requested samtools_flagstat helper as a single-step utility task.",
            "plan": [
                {
                    "step_id": 1,
                    "tool_name": "samtools_flagstat",
                    "arguments": {
                        "input_bam": str(input_bam),
                        "output_txt": output_txt,
                    },
                }
            ],
        }
    if requested_tool == "samtools_idxstats":
        input_bam = _first_suffix(".bam")
        if input_bam is None:
            return None
        output_txt = f"{str(input_bam)[:-4]}.idxstats.txt"
        return {
            "thought_process": "Run the explicitly requested samtools_idxstats helper as a single-step utility task.",
            "plan": [
                {
                    "step_id": 1,
                    "tool_name": "samtools_idxstats",
                    "arguments": {
                        "input_bam": str(input_bam),
                        "output_txt": output_txt,
                    },
                }
            ],
        }
    if requested_tool == "samtools_stats":
        input_bam = _first_suffix(".bam")
        if input_bam is None:
            return None
        output_txt = f"{str(input_bam)[:-4]}.stats.txt"
        return {
            "thought_process": "Run the explicitly requested samtools_stats helper as a single-step utility task.",
            "plan": [
                {
                    "step_id": 1,
                    "tool_name": "samtools_stats",
                    "arguments": {
                        "input_bam": str(input_bam),
                        "output_txt": output_txt,
                    },
                }
            ],
        }
    if requested_tool in _DIRECT_REPORT_TOOLS:
        run_input = _preferred_report_input_path(path_candidates)
        if run_input is None:
            return None
        default_output_dir = run_input.parent if run_input.name == "result.json" else run_input
        return {
            "thought_process": (
                "Build the explicitly requested report bundle as a single-step "
                "post-analysis reporting task."
            ),
            "plan": [
                {
                    "step_id": 1,
                    "tool_name": requested_tool,
                    "arguments": {
                        "run_input": str(run_input),
                        "output_dir": str(default_output_dir),
                    },
                }
            ],
        }
    return None


def _looks_like_direct_report_bundle_request(
    text_l: str,
    hinted_tool: str | None,
    hints: list[str],
) -> bool:
    """Return whether one request is a direct report-bundle task."""

    selected_tool = hinted_tool or (hints[0] if len(hints) == 1 else "")
    if selected_tool not in _DIRECT_REPORT_TOOLS:
        return False
    if any(marker in text_l for marker in _MULTI_STEP_MARKERS):
        return False
    if "report bundle" in text_l or "researcher-facing report" in text_l:
        return True
    if ("completed run" in text_l or "completed fastqc outputs" in text_l or "completed outputs" in text_l) and (
        "multiqc" in text_l or "quarto" in text_l
    ):
        return True
    return False


def _preferred_report_input_path(path_candidates: list[Path]) -> Path | None:
    """Return the best run-report input path from extracted user paths."""

    existing = [path for path in path_candidates if path.exists()]
    candidate_pool = existing or path_candidates
    if not candidate_pool:
        return None
    for path in candidate_pool:
        if path.is_dir():
            return path
    for path in candidate_pool:
        if path.name == "result.json":
            return path
    return candidate_pool[0]
