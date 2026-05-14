"""Helper CLI for one atomic ``bcftools filter`` invocation.

This helper keeps one user-visible filter transformation atomic while still
handling deterministic output-directory creation, argument validation, and
VCF-header-driven tag validation inside the harness.

The header-driven tag validator runs before ``bcftools filter`` is invoked.
If the filter expression references INFO/FORMAT tags that are not declared
in the input VCF header, the helper first applies conservative, header-backed
repairs for known producer-specific aliases. Remaining missing tags fail fast
with a structured, machine-readable diagnostic and exit code 64 (EX_USAGE).
This prevents opaque ``filter.c:3463 filters_init1 Error: the tag "X" is not
defined in the VCF header`` failures from bcftools and gives the repair loop
the list of tags that ARE available plus hints for known aliases (e.g.
FreeBayes uses ``MQM``/``MQMR``, not ``MQ``).
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from contextlib import suppress
from pathlib import Path

# Exit code for filter-expression validation failures. 64 == sysexits EX_USAGE.
EXIT_FILTER_EXPRESSION_INVALID = 64

_VALID_OUTPUT_TYPES = {
    "v": "-Ov",
    "z": "-Oz",
    "b": "-Ob",
}

# VCF columns that are always present and never need to be declared in the
# header. Filter expressions can reference these without a preceding INFO/
# FORMAT prefix.
_VCF_CORE_COLUMNS = frozenset(
    {
        "CHROM",
        "POS",
        "ID",
        "REF",
        "ALT",
        "QUAL",
        "FILTER",
        "INFO",
        "FORMAT",
        "TYPE",
        "N_ALT",
        "N_SAMPLES",
        "N_PASS",
        "N_MISSING",
    }
)

# bcftools expression operators, literals, and keywords to skip when extracting
# tag names.
_BCFTOOLS_EXPRESSION_RESERVED = frozenset(
    {
        "AND",
        "OR",
        "NOT",
        "ALT",
        "REF",
        "SUM",
        "MIN",
        "MAX",
        "AVG",
        "MEAN",
        "MEDIAN",
        "COUNT",
        "LENGTH",
        "STRLEN",
        "MISSING",
        "PASS",
        "TRUE",
        "FALSE",
    }
)

# Known hints for common tag-naming mismatches. Keys are tags that might be
# referenced; values are human-readable suggestions that include common
# producer tools.
_KNOWN_TAG_HINTS: dict[str, str] = {
    "MQ": "FreeBayes emits MQM (mean mapping quality of observed alleles) "
    "and MQMR (for the reference). GATK/samtools emit MQ. If the caller is "
    "freebayes, rewrite 'MQ' to 'MQM' (or 'MQM>X || MQMR>X').",
    "MQRankSum": "MQRankSum is a GATK-specific INFO tag. FreeBayes does not "
    "emit it. Drop this clause for freebayes-produced VCFs.",
    "ReadPosRankSum": "ReadPosRankSum is a GATK-specific INFO tag. FreeBayes "
    "does not emit it. Drop this clause for freebayes-produced VCFs.",
    "FS": "FS (FisherStrand) is a GATK-specific INFO tag. FreeBayes emits "
    "SAP (strand-allele-probability) instead.",
    "SOR": "SOR (StrandOddsRatio) is a GATK-specific INFO tag. FreeBayes emits SAP/SRP instead.",
    "QD": "QD (QualByDepth) is a GATK-specific INFO tag. For freebayes, use "
    "QUAL/DP as an equivalent ratio.",
    "VAF": "VAF (variant allele frequency) is not always declared. FreeBayes "
    "emits AF; GATK emits AF in some modes. Check header for AF.",
    "AF": "AF (allele frequency) should be declared by most callers. If "
    "missing, the VCF may be a per-sample callset — try AO/(AO+RO) instead.",
}
_SAFE_SINGLE_SAMPLE_INFO_TAGS = frozenset(
    {
        "AC",
        "AF",
        "AN",
        "AO",
        "DP",
        "MIN_DP",
        "NS",
        "QA",
        "QR",
        "RO",
    }
)


def _read_vcf_header_lines(vcf_path: Path, *, max_lines: int = 20000) -> list[str]:
    """Read header (``##``/``#CHROM``) lines from a VCF or VCF.gz file.

    Args:
        vcf_path: VCF or VCF.gz path.
        max_lines: Safety cap on header-parsing iterations.

    Returns:
        List of raw header lines (without trailing newlines).

    Raises:
        FileNotFoundError: If the VCF path does not exist.
    """
    if not vcf_path.exists():
        raise FileNotFoundError(f"VCF not found: {vcf_path}")

    is_gzip = vcf_path.suffix in {".gz", ".bgz"} or vcf_path.name.endswith(".vcf.gz")
    if is_gzip:
        with gzip.open(vcf_path, "rt", errors="replace") as handle:
            return _collect_vcf_header_lines(handle, max_lines=max_lines)
    with vcf_path.open(errors="replace") as handle:
        return _collect_vcf_header_lines(handle, max_lines=max_lines)


def _collect_vcf_header_lines(handle: Iterable[str], *, max_lines: int) -> list[str]:
    header_lines: list[str] = []
    for idx, line in enumerate(handle):
        if idx >= max_lines:
            break
        stripped = line.rstrip("\n")
        if not stripped:
            continue
        if stripped.startswith("#"):
            header_lines.append(stripped)
            if stripped.startswith("#CHROM"):
                break
        else:
            # Once we hit a data line, the header is over.
            break
    return header_lines


def _parse_header_tags(header_lines: Iterable[str]) -> dict[str, set[str]]:
    """Extract declared tag names grouped by ``INFO`` / ``FORMAT``.

    Args:
        header_lines: Raw VCF header lines.

    Returns:
        Mapping with keys ``"INFO"`` and ``"FORMAT"`` to set of tag IDs.
    """
    info_tags: set[str] = set()
    format_tags: set[str] = set()
    info_pat = re.compile(r"^##INFO=<ID=([A-Za-z_][A-Za-z0-9_.]*)")
    format_pat = re.compile(r"^##FORMAT=<ID=([A-Za-z_][A-Za-z0-9_.]*)")
    for line in header_lines:
        m = info_pat.match(line)
        if m:
            info_tags.add(m.group(1))
            continue
        m = format_pat.match(line)
        if m:
            format_tags.add(m.group(1))
    return {"INFO": info_tags, "FORMAT": format_tags}


def _parse_sample_names(header_lines: Iterable[str]) -> list[str]:
    """Extract sample names from the ``#CHROM`` header line."""
    for line in header_lines:
        if not str(line or "").startswith("#CHROM"):
            continue
        fields = str(line).split("\t")
        if len(fields) <= 9:
            return []
        return [field.strip() for field in fields[9:] if field.strip()]
    return []


# Identifier pattern used to extract candidate tag references. We capture
# bare identifiers; prefixed forms (``INFO/X``, ``FORMAT/X``, ``FMT/X``) are
# detected separately.
_IDENTIFIER_PAT = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_PREFIXED_TAG_PAT = re.compile(r"\b(INFO|FORMAT|FMT)/([A-Za-z_][A-Za-z0-9_.]*)")


def extract_referenced_tags(filter_expression: str) -> list[tuple[str, str]]:
    """Extract candidate INFO/FORMAT tag references from a filter expression.

    Args:
        filter_expression: Raw ``bcftools`` include expression.

    Returns:
        List of ``(scope, tag)`` tuples where ``scope`` is one of ``"INFO"``,
        ``"FORMAT"``, or ``"ANY"`` (scope not explicit in the expression, so
        either INFO or FORMAT may satisfy it).
    """
    if not filter_expression:
        return []

    prefixed: list[tuple[str, str]] = []
    consumed_spans: list[tuple[int, int]] = []
    for m in _PREFIXED_TAG_PAT.finditer(filter_expression):
        scope_raw, tag = m.group(1), m.group(2)
        scope = "FORMAT" if scope_raw in {"FORMAT", "FMT"} else "INFO"
        prefixed.append((scope, tag))
        consumed_spans.append(m.span())

    # Mask out already-captured spans so we don't double-count the bare tag.
    masked = list(filter_expression)
    for start, end in consumed_spans:
        for i in range(start, end):
            masked[i] = " "
    masked_expression = "".join(masked)

    bare: list[tuple[str, str]] = []
    for m in _IDENTIFIER_PAT.finditer(masked_expression):
        ident = m.group(0)
        if ident in _VCF_CORE_COLUMNS:
            continue
        if ident.upper() in _BCFTOOLS_EXPRESSION_RESERVED:
            continue
        if ident.isdigit():
            continue
        bare.append(("ANY", ident))

    # De-duplicate while preserving order.
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for entry in prefixed + bare:
        if entry in seen:
            continue
        seen.add(entry)
        ordered.append(entry)
    return ordered


def validate_filter_expression_against_header(
    filter_expression: str,
    header_tags: dict[str, set[str]],
) -> list[dict[str, object]]:
    """Return a list of missing-tag diagnostic records.

    Args:
        filter_expression: Raw ``bcftools`` include expression.
        header_tags: Mapping from :func:`_parse_header_tags`.

    Returns:
        Empty list when every referenced tag is declared; otherwise one
        record per missing tag with fields ``tag``, ``scope``, ``hint``.
    """
    info_tags = header_tags.get("INFO", set())
    format_tags = header_tags.get("FORMAT", set())
    missing: list[dict[str, object]] = []
    for scope, tag in extract_referenced_tags(filter_expression):
        if scope == "INFO":
            if tag in info_tags:
                continue
        elif scope == "FORMAT":
            if tag in format_tags:
                continue
        else:  # ANY
            if tag in info_tags or tag in format_tags:
                continue
        record: dict[str, object] = {"tag": tag, "scope": scope}
        hint = _KNOWN_TAG_HINTS.get(tag)
        if hint:
            record["hint"] = hint
        missing.append(record)
    return missing


def repair_known_filter_expression_aliases(
    filter_expression: str,
    header_tags: dict[str, set[str]],
) -> tuple[str, list[dict[str, object]]]:
    """Repair conservative producer-specific filter tag aliases.

    Args:
        filter_expression: Raw ``bcftools`` filter expression.
        header_tags: Mapping from :func:`_parse_header_tags`.

    Returns:
        A tuple of ``(expression, repairs)``. Currently this only rewrites
        missing GATK-style ``QD``/``INFO/QD`` references to a FreeBayes-safe
        ``QUAL / INFO/DP`` ratio when the input VCF declares ``INFO/DP``.
    """
    expression = str(filter_expression or "").strip()
    if not expression:
        return expression, []

    missing = validate_filter_expression_against_header(expression, header_tags)
    should_repair_qd = any(
        record.get("tag") == "QD" and record.get("scope") in {"ANY", "INFO"} for record in missing
    )
    if not should_repair_qd or "DP" not in header_tags.get("INFO", set()):
        return expression, []

    repaired = _replace_qd_with_qual_depth_ratio(expression)
    if repaired == expression:
        return expression, []
    return repaired, [
        {
            "tag": "QD",
            "from": "QD",
            "to": "QUAL / INFO/DP",
            "reason": "freebayes_missing_gatk_qual_by_depth",
        }
    ]


def qualify_ambiguous_filter_expression(
    filter_expression: str,
    header_tags: dict[str, set[str]],
    sample_names: Sequence[str],
) -> tuple[str, list[dict[str, object]]]:
    """Qualify low-risk ambiguous bare tags before invoking ``bcftools``.

    Args:
        filter_expression: Raw ``bcftools`` filter expression.
        header_tags: Mapping from :func:`_parse_header_tags`.
        sample_names: Sample names parsed from the VCF header.

    Returns:
        A tuple of ``(expression, repairs)``. Repairs are emitted only for
        single-sample VCFs where a bare tag is declared in both INFO and FORMAT
        and the tag is in the conservative single-sample INFO allowlist.
    """
    expression = str(filter_expression or "").strip()
    if not expression or len(tuple(sample_names or ())) != 1:
        return expression, []
    info_tags = header_tags.get("INFO", set())
    format_tags = header_tags.get("FORMAT", set())
    repair_tags: list[str] = []
    seen: set[str] = set()
    for scope, tag in extract_referenced_tags(expression):
        if scope != "ANY" or tag in seen:
            continue
        seen.add(tag)
        if tag in info_tags and tag in format_tags and tag in _SAFE_SINGLE_SAMPLE_INFO_TAGS:
            repair_tags.append(tag)
    if not repair_tags:
        return expression, []
    repaired = _qualify_bare_info_tags(expression, set(repair_tags))
    repairs = [
        {
            "tag": tag,
            "from": tag,
            "to": f"INFO/{tag}",
            "reason": "single_sample_safe_info_namespace",
        }
        for tag in repair_tags
    ]
    return repaired, repairs


def build_bcftools_filter_command(
    *,
    input_vcf: Path,
    output_vcf: Path,
    filter_expression: str,
    output_type: str = "z",
    soft_filter_name: str = "",
) -> list[str]:
    """Build one ``bcftools filter`` command.

    Args:
        input_vcf: Input VCF or VCF.GZ path.
        output_vcf: Output filtered VCF path.
        filter_expression: Include expression passed to ``bcftools filter -i``.
        output_type: ``bcftools`` output type token (``v``, ``z``, or ``b``).
        soft_filter_name: Optional filter label forwarded to ``bcftools``.

    Returns:
        Command parts suitable for ``subprocess.run``.

    Raises:
        ValueError: If the output type is unsupported or the expression is empty.
    """
    normalized_expression = str(filter_expression or "").strip()
    if not normalized_expression:
        raise ValueError("filter_expression must be a non-empty string")

    normalized_output_type = str(output_type or "").strip().lower() or "z"
    output_flag = _VALID_OUTPUT_TYPES.get(normalized_output_type)
    if output_flag is None:
        allowed = ", ".join(sorted(_VALID_OUTPUT_TYPES))
        raise ValueError(f"output_type must be one of: {allowed}")

    command = [
        "bcftools",
        "filter",
        "-i",
        normalized_expression,
        output_flag,
        "-o",
        str(output_vcf),
    ]
    normalized_soft_filter = str(soft_filter_name or "").strip()
    if normalized_soft_filter:
        command.extend(["-s", normalized_soft_filter])
    command.append(str(input_vcf))
    return command


def _emit_tag_validation_failure(
    *,
    input_vcf: Path,
    filter_expression: str,
    missing: list[dict[str, object]],
    header_tags: dict[str, set[str]],
) -> None:
    """Print a human + machine readable diagnostic to stderr."""
    info_available = sorted(header_tags.get("INFO", set()))
    format_available = sorted(header_tags.get("FORMAT", set()))
    print(
        "ERROR: bcftools_filter_run: filter expression references tag(s) "
        "not declared in the VCF header.",
        file=sys.stderr,
    )
    print(f"  input_vcf: {input_vcf}", file=sys.stderr)
    print(f"  filter_expression: {filter_expression}", file=sys.stderr)
    for record in missing:
        line = f"  missing: {record['tag']} (scope={record['scope']})"
        hint = record.get("hint")
        if hint:
            line += f" — hint: {hint}"
        print(line, file=sys.stderr)
    if info_available:
        print(
            f"  available INFO tags: {info_available}",
            file=sys.stderr,
        )
    if format_available:
        print(
            f"  available FORMAT tags: {format_available}",
            file=sys.stderr,
        )
    diagnostic = {
        "failure_class": "filter_expression_tag_not_in_header",
        "tool": "bcftools_filter_run",
        "input_vcf": str(input_vcf),
        "filter_expression": filter_expression,
        "missing_tags": missing,
        "available_info_tags": info_available,
        "available_format_tags": format_available,
    }
    print(
        "BCFTOOLS_FILTER_DIAGNOSTIC_JSON=" + json.dumps(diagnostic),
        file=sys.stderr,
    )


def run_bcftools_filter(
    *,
    input_vcf: Path,
    output_vcf: Path,
    filter_expression: str,
    output_type: str = "z",
    soft_filter_name: str = "",
    skip_header_validation: bool = False,
) -> int:
    """Run one atomic ``bcftools filter`` operation.

    Args:
        input_vcf: Input VCF or VCF.GZ path.
        output_vcf: Output filtered VCF path.
        filter_expression: Include expression passed to ``bcftools filter -i``.
        output_type: ``bcftools`` output type token (``v``, ``z``, or ``b``).
        soft_filter_name: Optional filter label forwarded to ``bcftools``.
        skip_header_validation: When ``True``, do not inspect the input VCF
            header before invoking ``bcftools`` (useful in test fixtures).

    Returns:
        Process exit code. ``EXIT_FILTER_EXPRESSION_INVALID`` (64) when the
        filter expression references tags not declared in the header.
    """
    if not skip_header_validation:
        try:
            header_lines = _read_vcf_header_lines(input_vcf)
        except FileNotFoundError as exc:
            print(f"ERROR: bcftools_filter_run: {exc}", file=sys.stderr)
            return 66  # EX_NOINPUT
        header_tags = _parse_header_tags(header_lines)
        sample_names = _parse_sample_names(header_lines)
        filter_expression, alias_repairs = repair_known_filter_expression_aliases(
            filter_expression,
            header_tags,
        )
        for repair in alias_repairs:
            print(
                "INFO: bcftools_filter_run: rewrote filter tag "
                f"{repair['from']} as {repair['to']} "
                f"({repair['reason']})",
                file=sys.stderr,
            )
        missing = validate_filter_expression_against_header(filter_expression, header_tags)
        if missing:
            _emit_tag_validation_failure(
                input_vcf=input_vcf,
                filter_expression=filter_expression,
                missing=missing,
                header_tags=header_tags,
            )
            return EXIT_FILTER_EXPRESSION_INVALID
        filter_expression, namespace_repairs = qualify_ambiguous_filter_expression(
            filter_expression,
            header_tags,
            sample_names,
        )
        for repair in namespace_repairs:
            print(
                "INFO: bcftools_filter_run: qualified ambiguous filter tag "
                f"{repair['from']} as {repair['to']}",
                file=sys.stderr,
            )

    output_vcf.parent.mkdir(parents=True, exist_ok=True)
    command = build_bcftools_filter_command(
        input_vcf=input_vcf,
        output_vcf=output_vcf,
        filter_expression=filter_expression,
        output_type=output_type,
        soft_filter_name=soft_filter_name,
    )
    completed = subprocess.run(command, check=False)
    returncode = int(completed.returncode)

    # Fix #27 (producer-side auto-index): when the filter output is bgzipped
    # (``output_type=z``) we also emit a tabix index so any downstream
    # ``bcftools`` consumer (``isec``, ``merge``, ``concat``, ``view -r ...``)
    # can stream regions from the output without a separately planned
    # ``tabix_index_run`` step. exp43 failed at ``bcftools_isec_run`` with
    # "Could not retrieve index file for evol1.filtered.vcf.gz" — the filter
    # step succeeded but produced no ``.tbi``, so every subsequent isec
    # retry also failed, the planner cascaded through alternate shapes, and
    # the whole evolution run stalled before any shared-variant export.
    #
    # The patch is tool-agnostic: bcftools convention pairs ``.vcf.gz`` with
    # ``.tbi``/``.csi``, so auto-indexing here fixes the general class of
    # "producer makes .vcf.gz, consumer can't read it without an index"
    # failures for ANY downstream bcftools call, not just evolution isec.
    # Indexing a non-bgzipped output is not meaningful, so we guard on
    # ``output_type == "z"``. ``tabix -f`` overwrites any stale index and
    # returns non-zero only on malformed input — we don't fail the filter
    # step on indexing errors because the primary filter output is still
    # valid, and the consumer will emit its own structured error if the
    # index is truly required and absent.
    normalized_output_type_for_index = str(output_type or "").strip().lower() or "z"
    if returncode == 0 and normalized_output_type_for_index == "z":
        with suppress(FileNotFoundError):
            subprocess.run(
                ["tabix", "-p", "vcf", "-f", str(output_vcf)],
                check=False,
            )

    return returncode


def _qualify_bare_info_tags(expression: str, tags: set[str]) -> str:
    consumed_spans = [match.span() for match in _PREFIXED_TAG_PAT.finditer(expression)]
    rebuilt: list[str] = []
    last = 0
    for match in _IDENTIFIER_PAT.finditer(expression):
        tag = match.group(0)
        if tag not in tags:
            continue
        if _span_inside(match.span(), consumed_spans):
            continue
        rebuilt.append(expression[last : match.start()])
        rebuilt.append(f"INFO/{tag}")
        last = match.end()
    if last == 0:
        return expression
    rebuilt.append(expression[last:])
    return "".join(rebuilt)


def _replace_qd_with_qual_depth_ratio(expression: str) -> str:
    repaired = re.sub(r"\bINFO/QD\b", "(QUAL / INFO/DP)", expression)
    return re.sub(r"(?<!/)\bQD\b", "(QUAL / INFO/DP)", repaired)


def _span_inside(span: tuple[int, int], containers: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(
        container_start <= start and end <= container_end
        for container_start, container_end in containers
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI entrypoint for one ``bcftools filter`` helper call.

    Args:
        argv: Optional argv override for tests.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description="Run one atomic bcftools filter command.")
    parser.add_argument("--input-vcf", required=True)
    parser.add_argument("--output-vcf", required=True)
    parser.add_argument("--filter-expression", required=True)
    parser.add_argument("--output-type", default="z")
    parser.add_argument("--soft-filter-name", default="")
    parser.add_argument(
        "--skip-header-validation",
        action="store_true",
        help="Skip VCF-header-driven filter-expression validation.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    return run_bcftools_filter(
        input_vcf=Path(args.input_vcf),
        output_vcf=Path(args.output_vcf),
        filter_expression=args.filter_expression,
        output_type=args.output_type,
        soft_filter_name=args.soft_filter_name,
        skip_header_validation=args.skip_header_validation,
    )


if __name__ == "__main__":
    raise SystemExit(main())
