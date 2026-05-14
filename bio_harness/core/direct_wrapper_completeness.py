"""Deterministic binding and completeness checks for direct-wrapper plans."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from bio_harness.core.direct_wrapper_argument_utils import (
    _argument_missing,
    _bind_execution_output_argument,
    _explicit_path_preservation_flags,
    _locked_argument_values_for_tool,
    _missing_required_arguments,
    _path_exists,
    _required_output_paths,
    _same_path_value,
    _selected_dir_output_should_win,
    _should_preserve_path_value,
)
from bio_harness.core.direct_wrapper_input_bindings import (
    _DE_WRAPPERS,
    _DIRECT_WRAPPER_INPUT_HINTS,
    _contextual_request_bindings,
    _deterministic_input_candidate,
    _discovered_input_paths,
    _parameter_accepts_request_input_binding,
    _request_paths_from_text,
    _should_drop_missing_sc_whitelist,
    _unique_de_input_path,
    _unique_whitelist_input_path,
)
from bio_harness.core.tool_registry import ToolRegistry, default_tool_registry
from bio_harness.core.wrapper_contracts import normalize_wrapper_arguments


def repair_direct_wrapper_plan_bindings(
    plan: Mapping[str, Any],
    *,
    analysis_spec: Mapping[str, Any] | None,
    contract: Mapping[str, Any] | None,
    request_text: str,
    selected_dir: str,
    data_root: str | None = None,
    registry: ToolRegistry | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind deterministically recoverable arguments for direct-wrapper steps.

    Args:
        plan: Candidate executable plan.
        analysis_spec: Normalized analysis specification for the run.
        contract: Request contract for the run.
        request_text: Raw user request text.
        selected_dir: Current run output directory.
        data_root: Optional task data root used for deterministic input discovery.
        registry: Optional tool registry override.

    Returns:
        Tuple of `(repaired_plan, meta)` describing any applied changes.
    """

    registry = registry or default_tool_registry()
    compatible_tools = _compatible_direct_wrapper_tools(analysis_spec)
    if not compatible_tools:
        return dict(plan or {}), {"changed": False, "why": "not_direct_wrapper"}

    raw_steps = (plan or {}).get("plan", [])
    if not isinstance(raw_steps, list):
        return dict(plan or {}), {"changed": False, "why": "no_steps"}

    request_paths = _request_paths_from_text(request_text)
    discovered_input_paths = _discovered_input_paths(
        analysis_spec,
        data_root=data_root,
    )
    output_paths = _required_output_paths(contract)
    preserve_input_paths, preserve_output_paths = _explicit_path_preservation_flags(analysis_spec)
    repaired_steps: list[dict[str, Any]] = []
    changes: list[str] = []

    for raw_step in raw_steps:
        if not isinstance(raw_step, Mapping):
            repaired_steps.append(raw_step)
            continue
        tool_name = str(raw_step.get("tool_name", "") or "").strip().lower()
        if tool_name not in compatible_tools:
            repaired_steps.append(dict(raw_step))
            continue
        repaired_step = dict(raw_step)
        arguments = raw_step.get("arguments", {})
        args = dict(arguments) if isinstance(arguments, Mapping) else {}
        repaired_args, arg_changes = _repair_step_arguments(
            tool_name=tool_name,
            arguments=args,
            analysis_spec=analysis_spec,
            output_paths=output_paths,
            request_paths=request_paths,
            discovered_input_paths=discovered_input_paths,
            request_text=request_text,
            selected_dir=selected_dir,
            registry=registry,
            preserve_input_paths=preserve_input_paths,
            preserve_output_paths=preserve_output_paths,
        )
        if arg_changes:
            repaired_step["arguments"] = repaired_args
            changes.extend(f"{tool_name}.{change}" for change in arg_changes)
        repaired_steps.append(repaired_step)

    if not changes:
        return dict(plan or {}), {"changed": False, "why": "no_direct_wrapper_repairs"}

    repaired_plan = dict(plan or {})
    repaired_plan["plan"] = repaired_steps
    return repaired_plan, {
        "changed": True,
        "why": "direct_wrapper_binding",
        "changes": changes,
    }


def assess_direct_wrapper_plan_completeness(
    plan: Mapping[str, Any],
    *,
    analysis_spec: Mapping[str, Any] | None,
    registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    """Return whether a direct-wrapper plan is executable after deterministic binding.

    Args:
        plan: Candidate executable plan.
        analysis_spec: Normalized analysis specification for the run.
        registry: Optional tool registry override.

    Returns:
        Validation payload with `passed` and `issues` keys.
    """

    registry = registry or default_tool_registry()
    compatible_tools = _compatible_direct_wrapper_tools(analysis_spec)
    if not compatible_tools:
        return {"passed": True, "issues": [], "compatible_tools": []}

    raw_steps = (plan or {}).get("plan", [])
    if not isinstance(raw_steps, list):
        return {"passed": False, "issues": ["invalid_plan_steps"], "compatible_tools": sorted(compatible_tools)}

    issues: list[str] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, Mapping):
            continue
        tool_name = str(raw_step.get("tool_name", "") or "").strip().lower()
        if tool_name not in compatible_tools:
            continue
        arguments = raw_step.get("arguments", {})
        args = dict(arguments) if isinstance(arguments, Mapping) else {}
        missing = _missing_required_arguments(tool_name, args, registry)
        if not missing:
            continue
        issues.append(
            f"incomplete_direct_wrapper:{tool_name}:{','.join(sorted(missing))}"
        )

    return {
        "passed": not issues,
        "issues": issues,
        "compatible_tools": sorted(compatible_tools),
    }


def _compatible_direct_wrapper_tools(analysis_spec: Mapping[str, Any] | None) -> set[str]:
    """Return direct-wrapper tools that should be treated as authoritative."""

    spec = analysis_spec if isinstance(analysis_spec, Mapping) else {}
    execution_contract = (
        spec.get("execution_contract", {})
        if isinstance(spec.get("execution_contract", {}), Mapping)
        else {}
    )
    execution_mode = str(execution_contract.get("execution_mode", "") or "").strip()
    if execution_mode != "direct_wrapper":
        return set()

    compatible = {
        str(tool).strip().lower()
        for tool in (execution_contract.get("compatible_tools", []) or [])
        if str(tool).strip()
    }
    explicit_intent = (
        spec.get("explicit_execution_intent", {})
        if isinstance(spec.get("explicit_execution_intent", {}), Mapping)
        else {}
    )
    compatible.update(
        str(tool).strip().lower()
        for tool in (explicit_intent.get("locked_tools", []) or [])
        if str(tool).strip()
    )
    return compatible


def _repair_step_arguments(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    analysis_spec: Mapping[str, Any] | None,
    output_paths: list[str],
    request_paths: list[str],
    discovered_input_paths: list[str],
    request_text: str,
    selected_dir: str,
    registry: ToolRegistry,
    preserve_input_paths: bool,
    preserve_output_paths: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Repair one step's arguments using only deterministic bindings."""

    repaired = normalize_wrapper_arguments(tool_name, arguments, cwd=selected_dir)
    changes: list[str] = []
    locked_args = normalize_wrapper_arguments(
        tool_name,
        _locked_argument_values_for_tool(analysis_spec, tool_name),
        cwd=selected_dir,
    )
    profile_args = normalize_wrapper_arguments(
        tool_name,
        _parameter_profile_values_for_tool(analysis_spec, tool_name, registry),
        cwd=selected_dir,
    )
    contextual_bindings = _contextual_request_bindings(
        tool_name=tool_name,
        request_text=request_text,
    )

    alias_changes = _repair_input_alias_arguments(
        tool_name=tool_name,
        arguments=repaired,
        selected_dir=selected_dir,
    )
    changes.extend(alias_changes)

    for param_name, value in locked_args.items():
        if _argument_missing(repaired.get(param_name)) and not _argument_missing(value):
            repaired[param_name] = value
            changes.append(f"{param_name}=locked")

    for param_name, value in profile_args.items():
        if _argument_missing(repaired.get(param_name)) and not _argument_missing(value):
            repaired[param_name] = value
            changes.append(f"{param_name}=parameter_profile")

    if preserve_output_paths:
        output_keys = set(registry.execution_output_parameters_for(tool_name))
        output_keys.update(registry.output_argument_keys_for(tool_name))
        for param_name in sorted(output_keys):
            locked_value = locked_args.get(param_name)
            if _argument_missing(locked_value):
                continue
            if _same_path_value(repaired.get(param_name), locked_value):
                continue
            repaired[param_name] = locked_value
            changes.append(f"{param_name}=locked_path")
        for param_name in sorted(output_keys):
            candidate = _bind_execution_output_argument(
                tool_name=tool_name,
                param_name=param_name,
                output_paths=output_paths,
                selected_dir=selected_dir,
                current_arguments=repaired,
                registry=registry,
            )
            if _selected_dir_output_should_win(
                repaired.get(param_name),
                candidate,
                selected_dir=selected_dir,
                tool_name=tool_name,
                param_name=param_name,
                current_arguments=repaired,
                registry=registry,
            ):
                continue
            if not _should_preserve_path_value(
                repaired.get(param_name),
                candidate,
                preserve_paths=True,
            ):
                continue
            repaired[param_name] = candidate
            changes.append(f"{param_name}=explicit_requested_output")

    if tool_name in _DIRECT_WRAPPER_INPUT_HINTS:
        hint_map = _DIRECT_WRAPPER_INPUT_HINTS[tool_name]
        for param_name, suffixes in hint_map.items():
            if not _parameter_accepts_request_input_binding(
                tool_name=tool_name,
                param_name=param_name,
                registry=registry,
            ):
                continue
            candidate = _deterministic_input_candidate(
                tool_name=tool_name,
                param_name=param_name,
                suffixes=suffixes,
                request_paths=request_paths,
                discovered_input_paths=discovered_input_paths,
                contextual_bindings=contextual_bindings,
                analysis_spec=analysis_spec,
                registry=registry,
            )
            if candidate and _should_preserve_path_value(
                repaired.get(param_name),
                candidate,
                preserve_paths=preserve_input_paths,
            ):
                repaired[param_name] = candidate
                changes.append(f"{param_name}=deterministic_input_path")

    if tool_name in _DE_WRAPPERS:
        counts_candidate = _unique_de_input_path(request_paths, kind="counts")
        if not counts_candidate:
            counts_candidate = _unique_de_input_path(discovered_input_paths, kind="counts")
        if counts_candidate and _should_preserve_path_value(
            repaired.get("counts_matrix"),
            counts_candidate,
            preserve_paths=preserve_input_paths,
        ):
            repaired["counts_matrix"] = counts_candidate
            changes.append("counts_matrix=deterministic_input_path")
        metadata_candidate = _unique_de_input_path(request_paths, kind="metadata")
        if not metadata_candidate:
            metadata_candidate = _unique_de_input_path(discovered_input_paths, kind="metadata")
        if metadata_candidate and _should_preserve_path_value(
            repaired.get("metadata_table"),
            metadata_candidate,
            preserve_paths=preserve_input_paths,
        ):
            repaired["metadata_table"] = metadata_candidate
            changes.append("metadata_table=deterministic_input_path")

    if tool_name == "sc_count_and_cluster":
        whitelist_candidate = _unique_whitelist_input_path(request_paths)
        if not whitelist_candidate:
            whitelist_candidate = _unique_whitelist_input_path(discovered_input_paths)
        if whitelist_candidate and _should_preserve_path_value(
            repaired.get("whitelist"),
            whitelist_candidate,
            preserve_paths=preserve_input_paths,
        ):
            repaired["whitelist"] = whitelist_candidate
            changes.append("whitelist=deterministic_input_path")
        elif _should_drop_missing_sc_whitelist(
            repaired.get("whitelist"),
            request_text=request_text,
        ):
            repaired.pop("whitelist", None)
            changes.append("whitelist=infer_from_r1")

    if tool_name == "metagenomics_kraken2_bracken_style":
        metagenomics_changes = _repair_metagenomics_kraken2_arguments(
            repaired,
            analysis_spec=analysis_spec,
            selected_dir=selected_dir,
            preserve_input_paths=preserve_input_paths,
        )
        changes.extend(metagenomics_changes)

    if tool_name == "fastp_run":
        fastp_changes = _repair_fastp_pair_outputs(
            repaired,
            selected_dir=selected_dir,
        )
        changes.extend(fastp_changes)

    bindable_outputs = set(registry.execution_output_parameters_for(tool_name))
    bindable_outputs.update(registry.output_argument_keys_for(tool_name))
    if tool_name == "minimap2_align" and len(output_paths) == 1:
        requested_output = str(output_paths[0]).strip()
        if requested_output.lower().endswith((".sam", ".bam")):
            current_output = str(repaired.get("output_bam", "") or "").strip()
            if current_output != requested_output:
                repaired["output_bam"] = requested_output
                changes.append("output_bam=explicit_requested_output")
    for param_name in registry.required_parameters_for(tool_name):
        if param_name not in bindable_outputs or not _argument_missing(repaired.get(param_name)):
            continue
        candidate = _direct_wrapper_output_default(
            tool_name=tool_name,
            param_name=param_name,
            output_paths=output_paths,
            selected_dir=selected_dir,
        )
        if not candidate:
            candidate = _bind_execution_output_argument(
                tool_name=tool_name,
                param_name=param_name,
                output_paths=output_paths,
                selected_dir=selected_dir,
                current_arguments=repaired,
                registry=registry,
            )
        if candidate:
            repaired[param_name] = candidate
            changes.append(f"{param_name}=deterministic_output")

    return normalize_wrapper_arguments(tool_name, repaired, cwd=selected_dir), changes


def _parameter_profile_values_for_tool(
    analysis_spec: Mapping[str, Any] | None,
    tool_name: str,
    registry: ToolRegistry,
) -> dict[str, Any]:
    """Return declared parameter-profile settings for one wrapper tool."""

    spec = analysis_spec if isinstance(analysis_spec, Mapping) else {}
    profile = spec.get("parameter_profile", [])
    if not isinstance(profile, list):
        return {}
    declared = set(registry.parameter_schema_for(tool_name))
    values: dict[str, Any] = {}
    for entry in profile:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("tool_name", "") or "").strip().lower() != tool_name:
            continue
        settings = entry.get("settings", {})
        if not isinstance(settings, Mapping):
            continue
        for raw_name, value in settings.items():
            param_name = str(raw_name or "").strip()
            if param_name and param_name in declared:
                values[param_name] = value
    return values


def _direct_wrapper_output_default(
    *,
    tool_name: str,
    param_name: str,
    output_paths: list[str],
    selected_dir: str,
) -> str:
    """Return a selected-dir output default for wrappers with canonical roots."""

    if output_paths:
        return ""
    selected_root = Path(selected_dir).expanduser()
    if tool_name == "minimap2_align" and param_name == "output_bam":
        return str(selected_root / "aligned.bam")
    if tool_name == "sniffles_sv_call" and param_name == "output_vcf":
        return str(selected_root / "variants.vcf")
    if tool_name == "fastp_run" and param_name == "output_reads_1":
        return str(selected_root / "trimmed" / "sample_trimmed_1.fastq.gz")
    if tool_name == "metagenomics_kraken2_bracken_style":
        if param_name == "output_dir":
            return str(selected_root)
        if param_name == "output_report":
            return str(selected_root / "bracken_abundance.tsv")
    if param_name == "output_dir" and tool_name in {
        "flye_assemble",
        "metabolomics_diff_abundance",
        "proteomics_diff_abundance",
        "spatial_transcriptomics_workflow",
    }:
        return str(selected_root)
    return ""


def _repair_fastp_pair_outputs(
    arguments: dict[str, Any],
    *,
    selected_dir: str,
) -> list[str]:
    """Fill paired-end fastp output paths when paired inputs are present."""

    if _argument_missing(arguments.get("reads_2")):
        return []
    changes: list[str] = []
    selected_root = Path(selected_dir).expanduser()
    if _argument_missing(arguments.get("output_reads_1")):
        arguments["output_reads_1"] = str(
            selected_root / "trimmed" / "sample_trimmed_1.fastq.gz"
        )
        changes.append("output_reads_1=deterministic_output")
    if _argument_missing(arguments.get("output_reads_2")):
        arguments["output_reads_2"] = str(
            selected_root / "trimmed" / "sample_trimmed_2.fastq.gz"
        )
        changes.append("output_reads_2=paired_output")
    return changes


def _repair_metagenomics_kraken2_arguments(
    arguments: dict[str, Any],
    *,
    analysis_spec: Mapping[str, Any] | None,
    selected_dir: str,
    preserve_input_paths: bool,
) -> list[str]:
    """Bind deterministic paired-read and Kraken2 database arguments."""

    changes: list[str] = []
    reads_1, reads_2 = _metagenomics_fastq_pair(analysis_spec)
    if reads_1 and _should_preserve_path_value(
        arguments.get("reads_1"),
        reads_1,
        preserve_paths=preserve_input_paths,
    ):
        arguments["reads_1"] = reads_1
        changes.append("reads_1=paired_fastq_input")
    if reads_2 and _should_preserve_path_value(
        arguments.get("reads_2"),
        reads_2,
        preserve_paths=preserve_input_paths,
    ):
        arguments["reads_2"] = reads_2
        changes.append("reads_2=paired_fastq_input")

    database = _metagenomics_kraken2_database(
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
    )
    if database and _should_preserve_path_value(
        arguments.get("database"),
        database,
        preserve_paths=preserve_input_paths,
    ):
        arguments["database"] = database
        changes.append("database=kraken2_db")
    return changes


def _metagenomics_fastq_pair(
    analysis_spec: Mapping[str, Any] | None,
) -> tuple[str, str]:
    """Return the unique paired FASTQ inputs declared for metagenomics."""

    manifest = analysis_spec.get("file_manifest") if isinstance(analysis_spec, Mapping) else None
    reads_1 = _unique_manifest_role_path(manifest, "input_fastq_r1")
    reads_2 = _unique_manifest_role_path(manifest, "input_fastq_r2")
    if reads_1 and reads_2:
        return reads_1, reads_2

    discovered = []
    spec = analysis_spec if isinstance(analysis_spec, Mapping) else {}
    for entry in spec.get("discovered_data_files", []) or []:
        if isinstance(entry, Mapping):
            path_text = str(entry.get("path", "") or "").strip()
            if path_text:
                discovered.append(path_text)
    return _unique_discovered_fastq_pair(discovered)


def _unique_manifest_role_path(manifest: Any, role: str) -> str:
    """Return the unique existing path for a file-manifest role."""

    if not isinstance(manifest, Mapping):
        return ""
    matches: list[str] = []
    for entry in manifest.get("entries", []) or []:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("role", "") or "").strip() != role:
            continue
        path_text = str(entry.get("resolved_path", "") or entry.get("path", "")).strip()
        if path_text and _path_exists(path_text):
            matches.append(str(Path(path_text).expanduser().resolve(strict=False)))
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else ""


def _unique_discovered_fastq_pair(paths: list[str]) -> tuple[str, str]:
    """Return one paired FASTQ candidate from discovered paths."""

    r1_candidates = [
        path for path in paths if _fastq_pair_member(path, read_number=1)
    ]
    r2_candidates = [
        path for path in paths if _fastq_pair_member(path, read_number=2)
    ]
    r1_unique = list(dict.fromkeys(r1_candidates))
    r2_unique = list(dict.fromkeys(r2_candidates))
    if len(r1_unique) == 1 and len(r2_unique) == 1:
        return (
            str(Path(r1_unique[0]).expanduser().resolve(strict=False)),
            str(Path(r2_unique[0]).expanduser().resolve(strict=False)),
        )
    return "", ""


def _fastq_pair_member(path_text: str, *, read_number: int) -> bool:
    """Return whether a FASTQ path names the requested pair member."""

    name = Path(str(path_text or "")).name.lower()
    if not name.endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz")):
        return False
    tokens = (f"_r{read_number}", f"-r{read_number}", f"_{read_number}", f"-{read_number}")
    return any(token in name for token in tokens)


def _metagenomics_kraken2_database(
    *,
    analysis_spec: Mapping[str, Any] | None,
    selected_dir: str,
) -> str:
    """Return a valid Kraken2 database path from metagenomics context."""

    spec = analysis_spec if isinstance(analysis_spec, Mapping) else {}
    data_root_text = str(spec.get("requested_data_root", "") or "").strip()
    if not data_root_text:
        return ""
    try:
        from bio_harness.core.protocol_grounding import _resolve_metagenomics_kraken2_db

        database, _meta = _resolve_metagenomics_kraken2_db(
            selected_dir=Path(selected_dir).expanduser(),
            data_root=Path(data_root_text).expanduser(),
            analysis_spec=dict(spec),
        )
    except Exception:  # pragma: no cover - optional protocol grounding helper
        database = ""
    return database


def _repair_input_alias_arguments(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    selected_dir: str,
) -> list[str]:
    """Repair deterministic input/output role aliases emitted for wrappers."""

    if tool_name != "sniffles_sv_call":
        return []
    changes: list[str] = []
    alias_value = str(arguments.get("output_bam", "") or "").strip()
    if alias_value and _looks_like_alignment_input(alias_value):
        normalized_alias = _selected_dir_relative_path(alias_value, selected_dir)
        if _argument_missing(arguments.get("input_bam")):
            arguments["input_bam"] = normalized_alias
            changes.append("input_bam=output_bam_alias")
    if "output_bam" in arguments:
        arguments.pop("output_bam", None)
        changes.append("output_bam=removed_input_alias")
    return changes


def _looks_like_alignment_input(value: str) -> bool:
    """Return whether a path-like value names an aligned long-read file."""

    suffixes = Path(str(value or "").strip()).suffixes
    suffix_text = "".join(suffixes[-2:]).lower() if suffixes else ""
    return suffix_text.endswith((".bam", ".cram"))


def _selected_dir_relative_path(value: str, selected_dir: str) -> str:
    """Resolve relative wrapper alias paths against the selected directory."""

    path = Path(str(value or "").strip()).expanduser()
    if path.is_absolute():
        return str(path)
    return str(Path(selected_dir).expanduser() / path)
