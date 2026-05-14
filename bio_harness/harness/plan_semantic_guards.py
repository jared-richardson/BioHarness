from __future__ import annotations

import re
from copy import deepcopy

# ruff: noqa: F403,F405
from bio_harness.core.bcftools_shell_semantics import (
    inspect_bcftools_expression_command,
    inspect_bcftools_isec_command,
    repair_bcftools_expression_command,
    repair_bcftools_isec_command,
)
from bio_harness.core.cystic_fibrosis_scaffold import is_cystic_fibrosis_scaffold_command
from bio_harness.core.shell_parse import is_shell_assignment
from bio_harness.core.semantic_plan_validation import semantic_plan_issues
from bio_harness.harness.plan_helpers_support import *

def _extract_csv_output_from_command(command: str) -> str:
    text = str(command or "").strip()
    if not text:
        return ""
    patterns = (
        r">\s*([A-Za-z0-9_./-]+\.csv)\b",
        r"(?:--out|--output|-o)\s+([A-Za-z0-9_./-]+\.csv)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return str(match.group(1) or "").strip()
    try:
        tokens = shlex.split(text, posix=True)
    except Exception:
        tokens = text.split()
    candidates = [str(token).strip() for token in tokens if str(token).strip().lower().endswith(".csv")]
    if not candidates:
        return ""
    preferred = sorted(
        candidates,
        key=lambda token: (
            0 if "variant" in Path(token).name.lower() else 1,
            len(token),
        ),
    )[0]
    return preferred


def _detect_placeholder_pathway_content(command: str) -> str:
    """Detect placeholder pathway content in inline scientific bash code."""

    command_l = str(command or "").lower()
    placeholder_patterns = (
        "mock kegg",
        "for demonstration",
        "in a real scenario",
    )
    for pattern in placeholder_patterns:
        if pattern in command_l:
            return pattern
    path_placeholder = re.search(r"\bpathway_[a-z]\b", command_l)
    if path_placeholder:
        return str(path_placeholder.group(0))
    return ""


def _detect_placeholder_scientific_content(command: str) -> str:
    """Detect mock or demonstration scientific content in inline commands."""

    command_l = str(command or "").lower()
    placeholder_patterns = (
        "for demonstration",
        "placeholder",
        "mock ",
        "toy ",
        "dummy ",
        "synthetic ",
        "fake ",
        "in a real scenario",
        "example result",
        "example output",
    )
    scientific_tokens = (
        "pathway",
        "csv",
        "tsv",
        "report",
        "classification",
        "abundance",
        "variant",
        "vcf",
        "tree",
        "newick",
        "phylogen",
        "kegg",
        "enrichment",
        "deseq",
        "counts",
    )
    for pattern in placeholder_patterns:
        if pattern in command_l and any(token in command_l for token in scientific_tokens):
            return pattern
    return ""


def _detect_speculative_step_handoff(command: str) -> str:
    """Detect speculative comments that assume missing upstream artifacts exist."""

    command_l = str(command or "").lower()
    speculative_patterns = (
        "if step 1 didn't save",
        "we assume step 1 succeeded",
        "the plan says step 1 already did it",
        "if they don't exist, we might need to re-run",
        "step 1 likely saved",
    )
    for pattern in speculative_patterns:
        if pattern in command_l:
            return pattern
    return ""


def _detect_guessed_group_split(command: str) -> str:
    """Detect ad hoc case/control splits in differential-expression commands."""

    command_l = str(command or "").lower()
    group_guess_patterns = (
        "assume first half control, second half treated",
        "fallback: assume first half control, second half treated",
        "first half are controls and second half are treated",
        "split by index",
        "fallback: split by index",
        "if no clear labels",
        "unless sample names indicate otherwise",
    )
    for pattern in group_guess_patterns:
        if pattern in command_l:
            return pattern
    return ""


_BCFTOOLS_VIEW_VALUE_OPTIONS: dict[str, str] = {
    "-m": "integer",
    "--min-alleles": "integer",
    "-M": "integer",
    "--max-alleles": "integer",
    "-v": "types",
    "--types": "types",
    "-V": "types",
    "--exclude-types": "types",
    "-i": "expression",
    "--include": "expression",
    "-e": "expression",
    "--exclude": "expression",
    "-o": "path",
    "--output": "path",
    "-r": "regions",
    "--regions": "regions",
    "-R": "regions_file",
    "--regions-file": "regions_file",
    "-t": "targets",
    "--targets": "targets",
    "-T": "targets_file",
    "--targets-file": "targets_file",
    "-s": "samples",
    "--samples": "samples",
    "-S": "samples_file",
    "--samples-file": "samples_file",
}
_BCFTOOLS_VIEW_TYPE_OPTIONS = {"-v", "--types", "-V", "--exclude-types"}
_BCFTOOLS_VIEW_ALLELE_COUNT_OPTIONS = {"-m", "--min-alleles", "-M", "--max-alleles"}


def _bcftools_view_command_start(tokens: list[str]) -> int | None:
    """Return the token index of ``bcftools view`` or ``None`` if absent."""

    idx = 0
    while idx < len(tokens) and is_shell_assignment(tokens[idx]):
        idx += 1
    if idx >= len(tokens):
        return None
    if Path(str(tokens[idx]).strip()).name.lower() == "env":
        idx += 1
        while idx < len(tokens) and is_shell_assignment(tokens[idx]):
            idx += 1
    if idx >= len(tokens):
        return None
    if Path(str(tokens[idx]).strip()).name.lower() == "command":
        idx += 1
    if idx + 1 >= len(tokens):
        return None
    if Path(str(tokens[idx]).strip()).name.lower() != "bcftools":
        return None
    if str(tokens[idx + 1]).strip().lower() != "view":
        return None
    return idx + 2


def _analyze_bcftools_view_segment(
    segment: str,
    *,
    auto_repair: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Inspect one shell segment for malformed ``bcftools view`` arguments."""

    text = str(segment or "").strip()
    if not text:
        return [], text
    try:
        tokens = shlex.split(text, posix=True)
    except Exception:
        return [], text

    arg_idx = _bcftools_view_command_start(tokens)
    if arg_idx is None:
        return [], text

    repaired_tokens = list(tokens)
    issues: list[dict[str, Any]] = []
    changed = False
    idx = arg_idx
    while idx < len(repaired_tokens):
        token = str(repaired_tokens[idx]).strip()
        if token == "--":
            break
        if token not in _BCFTOOLS_VIEW_VALUE_OPTIONS:
            idx += 1
            continue

        next_token = str(repaired_tokens[idx + 1]).strip() if idx + 1 < len(repaired_tokens) else ""
        missing_value = not next_token or (next_token != "-" and next_token.startswith("-"))
        if not missing_value:
            idx += 2
            continue

        repairable = token in _BCFTOOLS_VIEW_ALLELE_COUNT_OPTIONS and next_token in _BCFTOOLS_VIEW_TYPE_OPTIONS
        issues.append(
            {
                "issue": "invalid_bcftools_view_cli",
                "option": token,
                "expected_value": _BCFTOOLS_VIEW_VALUE_OPTIONS[token],
                "repairable": repairable,
                "segment": text,
            }
        )
        if auto_repair and repairable:
            del repaired_tokens[idx]
            changed = True
            continue
        idx += 1

    repaired_text = " ".join(shlex.quote(token) for token in repaired_tokens) if changed else text
    return issues, repaired_text


def inspect_invalid_bcftools_view_command(command: str) -> list[dict[str, Any]]:
    """Return malformed ``bcftools view`` CLI issues found in a shell command.

    Args:
        command: Shell command string that may contain one or more ``bcftools
            view`` segments joined by shell separators.

    Returns:
        List of semantic issue rows describing malformed ``bcftools view``
        option/value pairs.
    """

    issues: list[dict[str, Any]] = []
    for segment in split_shell_segments(command):
        segment_issues, _ = _analyze_bcftools_view_segment(segment, auto_repair=False)
        issues.extend(segment_issues)
    return issues


def repair_invalid_bcftools_view_bash_run_commands(
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Drop stray malformed allele-count flags from ``bcftools view`` commands."""

    if not isinstance(plan, dict):
        return plan, {"changed": False, "why": "plan_not_dict", "repairs": []}

    repaired_plan = deepcopy(plan)
    repairs: list[dict[str, Any]] = []
    changed = False
    for idx, step in enumerate(_normalize_steps(repaired_plan), start=1):
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "")).strip()
        if not command:
            continue

        updated_command = command
        step_repairs: list[dict[str, Any]] = []
        for segment in split_shell_segments(command):
            segment_issues, repaired_segment = _analyze_bcftools_view_segment(segment, auto_repair=True)
            if repaired_segment == segment:
                continue
            updated_command = updated_command.replace(segment, repaired_segment, 1)
            changed = True
            step_repairs.extend(
                {
                    "option": issue.get("option"),
                    "expected_value": issue.get("expected_value"),
                    "segment": issue.get("segment"),
                }
                for issue in segment_issues
                if issue.get("repairable")
            )

        if updated_command != command:
            args["command"] = updated_command
            step["arguments"] = args
            repairs.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "repairs": step_repairs,
                }
            )

    if not changed:
        return plan, {"changed": False, "why": "no_invalid_bcftools_view_cli", "repairs": []}
    return repaired_plan, {
        "changed": True,
        "why": "repaired_invalid_bcftools_view_cli",
        "repairs": repairs,
    }


def repair_ambiguous_bcftools_expression_bash_run_commands(
    plan: dict[str, Any],
    *,
    cwd: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Qualify safe ambiguous ``bcftools`` expression tags in ``bash_run`` steps.

    Args:
        plan: Candidate execution plan.
        cwd: Working directory used to resolve relative VCF paths.

    Returns:
        Tuple of ``(repaired_plan, meta)`` describing the deterministic repair.
    """
    if not isinstance(plan, dict):
        return plan, {"changed": False, "why": "plan_not_dict", "repairs": []}

    repaired_plan = deepcopy(plan)
    repairs: list[dict[str, Any]] = []
    changed = False
    for idx, step in enumerate(_normalize_steps(repaired_plan), start=1):
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "")).strip()
        if not command:
            continue

        repaired_command, step_repairs = repair_bcftools_expression_command(command, cwd=cwd)
        if repaired_command == command:
            continue
        args["command"] = repaired_command
        step["arguments"] = args
        repairs.append(
            {
                "step_id": int(step.get("step_id", idx)),
                "tool_name": "bash_run",
                "repairs": step_repairs,
            }
        )
        changed = True

    if not changed:
        return plan, {"changed": False, "why": "no_ambiguous_bcftools_expression_namespace", "repairs": []}
    return repaired_plan, {
        "changed": True,
        "why": "repaired_ambiguous_bcftools_expression_namespace",
        "repairs": repairs,
    }


def repair_invalid_bcftools_isec_bash_run_commands(
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Repair deterministic ``bcftools isec`` export misuse in ``bash_run`` steps."""

    if not isinstance(plan, dict):
        return plan, {"changed": False, "why": "plan_not_dict", "repairs": []}

    repaired_plan = deepcopy(plan)
    repairs: list[dict[str, Any]] = []
    changed = False
    for idx, step in enumerate(_normalize_steps(repaired_plan), start=1):
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "")).strip()
        if not command:
            continue

        repaired_command, step_repairs = repair_bcftools_isec_command(command)
        if repaired_command == command:
            continue
        args["command"] = repaired_command
        step["arguments"] = args
        repairs.append(
            {
                "step_id": int(step.get("step_id", idx)),
                "tool_name": "bash_run",
                "repairs": step_repairs,
            }
        )
        changed = True

    if not changed:
        return plan, {"changed": False, "why": "no_invalid_bcftools_isec_output_mode", "repairs": []}
    return repaired_plan, {
        "changed": True,
        "why": "repaired_invalid_bcftools_isec_output_mode",
        "repairs": repairs,
    }


def _helper_scripts_from_analysis_spec(analysis_spec: dict[str, Any] | None) -> set[str]:
    """Collect helper-script basenames declared in the analysis spec scaffold."""

    spec = analysis_spec if isinstance(analysis_spec, dict) else {}
    scripts: set[str] = set()

    skeleton = spec.get("plan_skeleton", [])
    if isinstance(skeleton, list):
        for step in skeleton:
            if not isinstance(step, (list, tuple)) or len(step) < 3:
                continue
            step_meta = step[2] if isinstance(step[2], dict) else {}
            helper_script = str(step_meta.get("helper_script", "") or "").strip()
            if helper_script:
                scripts.add(Path(helper_script).name.lower())

    params = spec.get("parameter_profile", [])
    if isinstance(params, list):
        for item in params:
            if not isinstance(item, dict):
                continue
            settings = item.get("settings", {}) if isinstance(item.get("settings", {}), dict) else {}
            helper_script = str(settings.get("helper_script", "") or "").strip()
            if helper_script:
                scripts.add(Path(helper_script).name.lower())

    return scripts


def _is_lightweight_validation_command(command: str) -> bool:
    """Return True for output checks that verify artifacts without fabricating them."""

    command_l = str(command or "").lower()
    if not command_l:
        return False
    write_tokens = (
        ".to_csv(",
        "writer.writerow(",
        "writer.writerows(",
        "csv.writer(",
        "csv.dictwriter(",
        "write_text(",
        "cat <<",
        "printf ",
        "echo ",
    )
    if any(token in command_l for token in write_tokens):
        return False
    verification_tokens = (
        "validated ",
        "csv.dictreader(",
        "fieldnames",
        "row_count",
        "os.path.exists",
        "missing pathway comparison output",
        "unexpected pathway comparison columns",
        "unexpected output columns",
    )
    return any(token in command_l for token in verification_tokens)


_HELPER_GUARD_TOKENS: dict[str, tuple[str, ...]] = {
    "multi_model_dge_pathway": (
        "import pandas",
        "import numpy",
        "from scipy",
        "ttest_ind",
        "fisher_exact",
        "cpm",
        "kegg",
        "dea_ps3o1s",
        "gse161904",
        "gse168137",
        "5xfad_pvalue",
        "3xtg_ad_pvalue",
        "ps3o1s_pvalue",
        "log2fc",
    ),
    "metagenomics_classification": (
        "kraken2",
        "bracken",
        "taxonomy",
        "unclassified",
        "community composition",
        "classify reads",
        "metagenomic",
    ),
    "viral_metagenomics": (
        "minimap2",
        "paf",
        "viral",
        "virus",
        "coverage",
        "abundance",
        "detected_viruses",
    ),
    "phylogenetics": (
        "iqtree",
        "newick",
        "phylogen",
        "distance matrix",
        "neighbor joining",
        "treefile",
        "align sequences",
    ),
}


def _looks_like_inline_scientific_script(command: str) -> str:
    """Detect inline scientific scripting that should usually be helper-backed."""

    command_l = str(command or "").lower()
    inline_markers = (
        "python3 - <<",
        "python - <<",
        "python3 -c ",
        "python -c ",
        "rscript -e ",
    )
    script_markers = (
        "import ",
        "from ",
        "read_csv(",
        "read_table(",
        "pd.dataframe(",
        ".to_csv(",
        "csv.dictreader(",
        "csv.writer(",
        "writer.writerow(",
        "writer.writerows(",
        "kegg",
        "taxonomy",
        "phylogen",
        "newick",
        "coverage",
        "abundance",
        "variant",
        "vcf",
    )
    for marker in inline_markers:
        if marker in command_l and any(token in command_l for token in script_markers):
            return marker
    return ""


def _detect_missing_helper_backed_command(
    command: str,
    *,
    analysis_spec: dict[str, Any] | None = None,
) -> str:
    """Detect scientific bash that bypasses a helper-backed strict scaffold."""

    helper_scripts = _helper_scripts_from_analysis_spec(analysis_spec)
    if not helper_scripts:
        return ""

    command_l = str(command or "").lower()
    if any(helper_script in command_l for helper_script in helper_scripts):
        return ""
    if _is_lightweight_validation_command(command):
        return ""

    analysis_type = str((analysis_spec or {}).get("analysis_type", "") or "").strip().lower()
    suspicious_tokens = _HELPER_GUARD_TOKENS.get(analysis_type, ())
    for token in suspicious_tokens:
        if token in command_l:
            return token
    inline_marker = _looks_like_inline_scientific_script(command)
    if inline_marker:
        return inline_marker
    return ""


def _detect_invented_scientific_output(
    command: str,
    *,
    analysis_spec: dict[str, Any] | None = None,
) -> str:
    """Detect commands that appear to fabricate benchmark outputs from scratch."""

    command_l = str(command or "").lower()
    if not command_l:
        return ""
    if _is_lightweight_validation_command(command):
        return ""
    if is_cystic_fibrosis_scaffold_command(command, analysis_spec=analysis_spec):
        return ""

    helper_scripts = _helper_scripts_from_analysis_spec(analysis_spec)
    if any(helper_script in command_l for helper_script in helper_scripts):
        return ""

    write_patterns = (
        ".to_csv(",
        "writer.writerow(",
        "writer.writerows(",
        "csv.writer(",
        "csv.dictwriter(",
        "write_text(",
        "cat <<",
    )
    redirect_match = re.search(
        r"(?:printf|echo)\b[\s\S]{0,200}>\s*[^ \n]+\.(?:csv|tsv|txt|vcf|treefile|nwk)\b",
        command_l,
    )
    matched_write = next((token for token in write_patterns if token in command_l), "")
    if not matched_write and not redirect_match:
        return ""

    read_patterns = (
        "read_csv(",
        "read_table(",
        "read_excel(",
        "csv.dictreader(",
        "csv.reader(",
        "--input",
        "--reads-",
        "--reference",
        "--taxonomy",
        "--count-table",
        "--precomputed-de-table",
        "--vcf",
        "--bam",
        "--gff",
        "--gtf",
        "--fasta",
        "glob(",
    )
    if any(pattern in command_l for pattern in read_patterns):
        return ""
    open_write_match = re.search(r"open\([^)]*,\s*['\"]w", command_l)
    if open_write_match:
        return str(open_write_match.group(0))
    if redirect_match:
        return "shell_redirect_output"
    return matched_write


def _detect_non_helper_multi_model_pathway_workflow(
    command: str,
    *,
    analysis_spec: dict[str, Any] | None = None,
) -> str:
    """Detect inline Alzheimer pathway workflows that bypass the repo helper."""

    analysis_type = str((analysis_spec or {}).get("analysis_type", "") or "").strip().lower()
    if analysis_type != "multi_model_dge_pathway":
        return ""

    command_l = str(command or "").lower()
    if "compare_pathways.py" in command_l:
        return ""
    if "validated pathway comparison csv" in command_l:
        return ""

    suspicious_tokens = (
        "scipy",
        "ttest_ind",
        "fisher_exact",
        "dea_ps3o1s",
        "gse161904",
        "gse168137",
        "merged_differential_expression",
        "5xfad_pvalue",
        "3xtg_ad_pvalue",
        "ps3o1s_pvalue",
    )
    for token in suspicious_tokens:
        if token in command_l:
            return token
    return ""


def _assess_plan_semantic_guards(
    plan: dict[str, Any],
    *,
    analysis_spec: dict[str, Any] | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    steps = _normalize_steps(plan)
    issues: list[dict[str, Any]] = semantic_plan_issues(
        plan,
        analysis_spec=analysis_spec,
    )
    if not steps:
        return {"passed": not issues, "issues": issues}

    annotation_available = False
    for idx, step in enumerate(steps, start=1):
        tool_name = str(step.get("tool_name", "")).strip().lower()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        if tool_name in {"snpeff_annotate", "vep_annotate"}:
            annotation_available = True
            continue
        if tool_name != "bash_run":
            continue
        command = str(args.get("command", "")).strip()
        command_l = command.lower()
        references_annotation_fields = any(token in command_l for token in ('impact=', '%info/ann', 'ann[', 'info/ann'))
        if references_annotation_fields and not annotation_available:
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": "annotation_filter_before_annotation",
                    "command": command,
                }
            )
        placeholder_pathway_pattern = _detect_placeholder_pathway_content(command)
        if placeholder_pathway_pattern:
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": "placeholder_pathway_content",
                    "pattern": placeholder_pathway_pattern,
                    "command": command,
                }
            )
        placeholder_scientific_pattern = _detect_placeholder_scientific_content(command)
        if placeholder_scientific_pattern:
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": "placeholder_scientific_content",
                    "pattern": placeholder_scientific_pattern,
                    "command": command,
                }
            )
        speculative_handoff_pattern = _detect_speculative_step_handoff(command)
        if speculative_handoff_pattern:
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": "speculative_step_handoff",
                    "pattern": speculative_handoff_pattern,
                    "command": command,
                }
            )
        guessed_group_pattern = _detect_guessed_group_split(command)
        if guessed_group_pattern:
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": "guessed_case_control_split",
                    "pattern": guessed_group_pattern,
                    "command": command,
                }
            )
        invented_output_pattern = _detect_invented_scientific_output(
            command,
            analysis_spec=analysis_spec,
        )
        if invented_output_pattern:
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": "invented_scientific_output",
                    "pattern": invented_output_pattern,
                    "command": command,
                }
            )
        missing_helper_pattern = _detect_missing_helper_backed_command(
            command,
            analysis_spec=analysis_spec,
        )
        if missing_helper_pattern:
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": "missing_helper_backed_command",
                    "pattern": missing_helper_pattern,
                    "command": command,
                }
            )
        non_helper_pattern = _detect_non_helper_multi_model_pathway_workflow(
            command,
            analysis_spec=analysis_spec,
        )
        if non_helper_pattern:
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": "non_helper_multi_model_pathway_workflow",
                    "pattern": non_helper_pattern,
                    "command": command,
                }
            )
        for bcftools_issue in _analyze_bcftools_view_segment(command)[0]:
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": bcftools_issue["issue"],
                    "option": bcftools_issue["option"],
                    "expected_value": bcftools_issue["expected_value"],
                    "repairable": bcftools_issue["repairable"],
                    "segment": bcftools_issue["segment"],
                    "command": command,
                }
            )
        for bcftools_issue in inspect_bcftools_expression_command(command, cwd=cwd):
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "tag": bcftools_issue.get("tag", ""),
                    "issue": bcftools_issue.get("issue", "ambiguous_bcftools_expression_namespace"),
                    "input_vcf": bcftools_issue.get("input_vcf", ""),
                    "option": bcftools_issue.get("option", ""),
                    "subcommand": bcftools_issue.get("subcommand", ""),
                    "expression": bcftools_issue.get("expression", ""),
                    "repairable": bool(bcftools_issue.get("repairable", False)),
                    "preferred_namespace": bcftools_issue.get("preferred_namespace", ""),
                    "reason": bcftools_issue.get("reason", ""),
                    "command": command,
                }
            )
        for bcftools_issue in inspect_bcftools_isec_command(command):
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": bcftools_issue.get("issue", "invalid_bcftools_isec_output_mode"),
                    "reason": bcftools_issue.get("reason", ""),
                    "repairable": bool(bcftools_issue.get("repairable", False)),
                    "prefix": bcftools_issue.get("prefix", ""),
                    "subcommand": bcftools_issue.get("subcommand", "isec"),
                    "write_index": bcftools_issue.get("write_index", 0),
                    "output_target": bcftools_issue.get("output_target", ""),
                    "command": command,
                }
            )

    return {"passed": not issues, "issues": issues}
