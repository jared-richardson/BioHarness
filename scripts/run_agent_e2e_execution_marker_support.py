"""Helpers for parsing executor log lines into durable run-state markers.

These helpers centralize the stream-marker and status-line side effects used
by the execution loop so the runner stays focused on polling and control flow.
"""

from __future__ import annotations

from typing import Any, Callable

from scripts.run_agent_e2e_support import (
    STREAM_MARKER_RE,
    _all_steps_completed,
    _append_recent_marker,
    _extract_missing_tools_from_line,
    _mark_group_missing_signal,
    _mark_group_observed,
    _parse_log_channel,
    _signature_contains,
    detect_stream_failure_signatures,
    re,
)

RunDict = dict[str, Any]
NoteFailureSignatureFn = Callable[[str], None]


def process_execution_marker_line(
    run: RunDict,
    line: str,
    *,
    now_ts: float,
    note_failure_signature: NoteFailureSignatureFn,
) -> None:
    """Apply one executor output line to the mutable run state."""

    stripped = line.strip()
    stripped_lower = stripped.lower()
    run["last_queue_activity_ts"] = now_ts
    _apply_step_status_markers(run, stripped)
    _apply_status_failure_markers(
        run,
        stripped=stripped,
        stripped_lower=stripped_lower,
        note_failure_signature=note_failure_signature,
    )
    _apply_stream_channel_markers(
        run,
        line=line,
        note_failure_signature=note_failure_signature,
        now_ts=now_ts,
    )


def _apply_step_status_markers(run: RunDict, stripped: str) -> None:
    """Update step-status fields from orchestrator status lines."""

    if stripped.startswith("--- Executing Step "):
        match = re.match(r"--- Executing Step (\d+):", stripped)
        if match:
            step_id = int(match.group(1))
            if 0 < step_id <= len(run["step_statuses"]):
                run["step_statuses"][step_id - 1] = "running"
            run["next_step_idx"] = max(0, step_id - 1)
    if stripped.startswith("--- Step ") and " finished ---" in stripped:
        match = re.match(r"--- Step (\d+) \(([^)]+)\) finished ---", stripped)
        if match:
            step_id = int(match.group(1))
            if 0 < step_id <= len(run["step_statuses"]):
                run["step_statuses"][step_id - 1] = "completed"
            run["next_step_idx"] = step_id
    failed_step = re.match(
        r"(?:Error:\s*)?Step (\d+) \(([^)]+)\) failed with exit code (\d+)",
        stripped,
    )
    if failed_step:
        step_id = int(failed_step.group(1))
        if 0 < step_id <= len(run["step_statuses"]):
            run["step_statuses"][step_id - 1] = "failed"
        run["status"] = "failed"
        run["error"] = f"Step {step_id} failed with exit code {failed_step.group(3)}"
    exec_fail = re.match(r"Error executing step (\d+) \(([^)]+)\):\s*(.+)", stripped)
    if exec_fail:
        step_id = int(exec_fail.group(1))
        if 0 < step_id <= len(run["step_statuses"]):
            run["step_statuses"][step_id - 1] = "failed"
        run["status"] = "failed"
        run["error"] = stripped


def _apply_status_failure_markers(
    run: RunDict,
    *,
    stripped: str,
    stripped_lower: str,
    note_failure_signature: NoteFailureSignatureFn,
) -> None:
    """Update run-level status flags from non-stream status lines."""

    if "blocked by policy" in stripped_lower:
        run["policy_block_detected"] = True
        run["status"] = "failed"
        if not run.get("error"):
            run["error"] = stripped
    if "blocked by validation agent" in stripped_lower:
        run["validation_block_detected"] = True
        run["status"] = "failed"
        if not run.get("error"):
            run["error"] = stripped
    if "plannernode failed" in stripped_lower and "timed out" in stripped_lower:
        run["planner_timeout_detected"] = True
        note_failure_signature("planner_timeout")
        if not _all_steps_completed(run.get("step_statuses", [])):
            run["status"] = "failed"
            if not run.get("error"):
                run["error"] = stripped
    if "planner request timed out" in stripped_lower:
        run["planner_timeout_detected"] = True
        note_failure_signature("planner_timeout")
        if not _all_steps_completed(run.get("step_statuses", [])):
            run["status"] = "failed"
            if not run.get("error"):
                run["error"] = stripped
    if "local model loopback access is blocked" in stripped_lower:
        run["local_model_loopback_blocked_detected"] = True
        note_failure_signature("local_model_loopback_blocked")
        if not _all_steps_completed(run.get("step_statuses", [])):
            run["status"] = "failed"
            if not run.get("error"):
                run["error"] = stripped


def _apply_stream_channel_markers(
    run: RunDict,
    *,
    line: str,
    note_failure_signature: NoteFailureSignatureFn,
    now_ts: float,
) -> None:
    """Update run state from stdout/stderr/live marker content."""

    channel, body = _parse_log_channel(line)
    marker_text = body if channel in {"stdout", "stderr"} else ""
    marker_lower = marker_text.lower()
    stream_counters = dict(run.get("stream_counters", {}))
    counter_key = f"{channel}_lines"
    stream_counters[counter_key] = int(stream_counters.get(counter_key, 0)) + 1
    run["stream_counters"] = stream_counters

    for marker in STREAM_MARKER_RE.findall(marker_text):
        _append_recent_marker(run, f"__{marker}__")
    if channel == "stderr" and "warning" in marker_lower:
        _append_recent_marker(run, "STDERR_WARNING")
    if channel == "stderr" and "error" in marker_lower:
        _append_recent_marker(run, "STDERR_ERROR")

    missing_tools = _extract_missing_tools_from_line(marker_text) if marker_text else []
    if missing_tools:
        existing = set(run.get("missing_tools_detected", []))
        run["missing_tools_detected"] = sorted(existing.union(missing_tools))

    if "__POLICY_BLOCK__" in marker_text or "denied command" in marker_lower:
        run["policy_block_detected"] = True
        run["status"] = "failed"
        if not run.get("error"):
            run["error"] = "Execution blocked by policy guard."
    if "__VALIDATION_BLOCK__" in marker_text:
        run["validation_block_detected"] = True
        run["status"] = "failed"
        if not run.get("error"):
            run["error"] = "Execution blocked by validation guard."
    format_input_match = re.search(r"__FORMAT_INPUT_ERROR__:(.+)", marker_text)
    if format_input_match:
        run["format_input_error_detected"] = True
        run["status"] = "failed"
        if not run.get("error"):
            run["error"] = f"Input validation issue: {format_input_match.group(1).strip()}"
    if "__NO_FASTQ_FOUND__" in marker_text:
        run["no_fastq_found"] = True

    manifest_match = re.search(r"__FASTQ_MANIFEST_COUNT__:(\d+)", marker_text)
    if manifest_match:
        try:
            manifest_count = int(manifest_match.group(1))
        except Exception:
            manifest_count = 0
        if manifest_count <= 0:
            run["no_fastq_found"] = True

    if "__NO_CONTROL_FASTQ__" in marker_text:
        _mark_group_missing_signal(run, "control")
    if "__NO_TREATMENT_FASTQ__" in marker_text:
        _mark_group_missing_signal(run, "treatment")
    if "__TEST_SUBSET_SKIPPED__:missing_inputs" in marker_text:
        run["format_input_error_detected"] = True
    if "__FASTQC_SKIPPED__:missing_inputs" in marker_text or "__FASTQC_SKIPPED__:empty_lists" in marker_text:
        run["format_input_error_detected"] = True

    selected_match = re.search(r"__SELECTED_([A-Z0-9_]+)_R1__:", marker_text)
    if selected_match:
        _mark_group_observed(run, selected_match.group(1), source="stream_marker:selected_r1")

    _apply_group_count_markers(run, marker_text)

    if (
        "__MISSING_PAIR__" in marker_text
        or "__READ_DECOMPRESS_FAILED__" in marker_text
        or "__RMATS_INPUT_LIST_EMPTY__" in marker_text
    ):
        run["format_input_error_detected"] = True

    empty_bam_match = re.search(r"__EMPTY_BAM__:(.+)", marker_text)
    if empty_bam_match:
        empty_bams = list(run.get("empty_bams_detected", []))
        empty_bams.append(empty_bam_match.group(1).strip())
        run["empty_bams_detected"] = sorted(set(empty_bams))
        run["status"] = "failed"
        run["error"] = "STAR alignment produced empty BAM output; check STAR logs."

    ref_match = re.search(r"__MISSING_REFERENCE__:(fasta|gtf)", marker_text)
    if ref_match:
        run["missing_reference_detected"] = sorted(
            set(run.get("missing_reference_detected", []) + [ref_match.group(1)])
        )
    rmats_match = re.search(r"__RMATS_FAILED__:exit_code:(\d+)", marker_text)
    if rmats_match:
        run["stale_tmp_cache_detected"] = True

    if "no such file or directory" in marker_lower and ".gtf" in marker_lower:
        run["missing_reference_detected"] = sorted(
            set(run.get("missing_reference_detected", []) + ["gtf"])
        )
    if "no such file or directory" in marker_lower and (
        ".fa" in marker_lower or ".fasta" in marker_lower or ".fna" in marker_lower
    ):
        run["missing_reference_detected"] = sorted(
            set(run.get("missing_reference_detected", []) + ["fasta"])
        )

    _apply_failure_signature_markers(
        run,
        marker_text=marker_text,
        marker_lower=marker_lower,
        note_failure_signature=note_failure_signature,
    )

    if channel in {"stdout", "stderr", "live"}:
        run["last_executor_event_ts"] = now_ts


def _apply_group_count_markers(run: RunDict, marker_text: str) -> None:
    """Record observed sample groups from subset and BAM count markers."""

    subset_match = re.search(r"__TEST_SUBSET_GROUP_COUNT__:([^:]+):(\d+)", marker_text)
    if subset_match:
        group_label = subset_match.group(1)
        try:
            count_value = int(subset_match.group(2))
        except Exception:
            count_value = 0
        if count_value > 0 and not bool(run.get("no_fastq_found", False)):
            _mark_group_observed(
                run,
                group_label,
                source="stream_marker:subset_group_count",
            )

    bam_match = re.search(r"__BAM_LIST_COUNT__:([^:]+):(\d+)", marker_text)
    if bam_match:
        group_label = bam_match.group(1)
        try:
            count_value = int(bam_match.group(2))
        except Exception:
            count_value = 0
        if count_value > 0 and not bool(run.get("no_fastq_found", False)):
            _mark_group_observed(run, group_label, source="stream_marker:bam_list_count")


def _apply_failure_signature_markers(
    run: RunDict,
    *,
    marker_text: str,
    marker_lower: str,
    note_failure_signature: NoteFailureSignatureFn,
) -> None:
    """Record derived failure signatures and error flags from stream text."""

    if _signature_contains(marker_lower, "metadata does not contain all count-matrix sample names"):
        note_failure_signature("deseq2_metadata_mismatch")
    if _signature_contains(marker_lower, "more columns than column names"):
        note_failure_signature("deseq2_counts_parse_error")
    if _signature_contains(marker_lower, "all samples have 0 counts for all genes"):
        note_failure_signature("deseq2_all_zero_counts")
        run["format_input_error_detected"] = True
    if _signature_contains(marker_lower, "[mpileup] failed to read from input file") or _signature_contains(
        marker_lower, "the input is not sorted (chromosomes out of order)"
    ):
        note_failure_signature("bcftools_mpileup_input_error")
        run["status"] = "failed"
        if not run.get("error"):
            run["error"] = "bcftools mpileup failed to read aligned input; check BAM sort/order."
    if _signature_contains(marker_lower, "paired-end reads were detected in single-end read library"):
        note_failure_signature("featurecounts_paired_mode_required")
    if _signature_contains(marker_lower, "cannot open file") and _signature_contains(marker_lower, "deseq2_wrapper.r"):
        note_failure_signature("deseq2_wrapper_missing")
    if _signature_contains(marker_lower, "stdin_block:head"):
        note_failure_signature("stdin_block_head")
    for signature in detect_stream_failure_signatures(marker_text):
        note_failure_signature(signature)


__all__ = ["process_execution_marker_line"]
