"""Render safe shell commands for the ``snpeff_annotate`` skill."""

from __future__ import annotations

import os
import shlex
import shutil
import string
import sys
from pathlib import Path

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.core.wrapper_contracts import normalize_snpeff_codon_table
from bio_harness.core.wrapper_staging import idempotent_stage_copy_command

DEFAULT_SNPEFF_JAVA_MEM_GB = 8
_REUSE_ANNOTATED_VCF_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "reuse_existing_annotated_vcf.py"


def _preferred_snpeff_executable() -> str:
    """Resolve the preferred ``snpEff`` executable for the active environment.

    Returns:
        The executable path or fallback command name to invoke.
    """

    env_bin = Path(sys.executable).resolve().parent
    for name in ("snpEff", "snpeff"):
        candidate = env_bin / name
        if candidate.exists():
            return str(candidate)
    return shutil.which("snpEff") or shutil.which("snpeff") or "snpEff"


def _preferred_java_home(snpeff_executable: str) -> str:
    """Resolve a compatible Java home for ``snpEff`` if one is bundled nearby.

    Args:
        snpeff_executable: Resolved ``snpEff`` executable path.

    Returns:
        The Java home path when a colocated runtime is available, else an
        empty string.
    """

    candidates: list[Path] = []
    exe_path = Path(str(snpeff_executable)).expanduser()
    if exe_path.is_absolute():
        candidates.append(exe_path.parent.parent / "lib" / "jvm")
    candidates.append(Path(sys.executable).resolve().parent.parent / "lib" / "jvm")
    for candidate in candidates:
        if (candidate / "bin" / "java").exists():
            return str(candidate)
    return ""


def _snpeff_prefix() -> str:
    """Build the environment-prefixed ``snpEff`` invocation prefix.

    Returns:
        A shell-safe command prefix with PATH and JAVA_HOME hints when
        available.
    """

    executable = _preferred_snpeff_executable()
    parts: list[str] = []
    java_home = _preferred_java_home(executable)
    if java_home:
        parts.append(f"JAVA_HOME={shlex.quote(java_home)}")
    exe_path = Path(str(executable)).expanduser()
    if exe_path.is_absolute():
        parts.append(f"PATH={shlex.quote(str(exe_path.parent))}:$PATH")
        parts.append(shlex.quote(str(exe_path)))
    else:
        parts.append(shlex.quote(executable))
    return " ".join(parts)


def _render_template(template: str, kwargs: dict) -> str:
    """Render a shell template using shell-quoted keyword arguments.

    Args:
        template: Command template containing named placeholders.
        kwargs: Raw keyword arguments for placeholder substitution.

    Returns:
        The rendered command string.

    Raises:
        ValueError: If required template fields are missing.
    """

    rendered: dict[str, str] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        rendered[key] = shlex.quote(str(value))
    formatter = string.Formatter()
    field_names = [field_name for _, field_name, _, _ in formatter.parse(template) if field_name]
    missing = [field for field in field_names if field not in rendered]
    if missing:
        missing_args = ", ".join(sorted(set(missing)))
        raise ValueError(f"Missing required parameter(s) for template: {missing_args}")
    return template.format(**rendered).strip()


def _resolve_java_mem_gb(kwargs: dict[str, object]) -> int:
    """Resolve the JVM heap size for ``snpEff`` invocations.

    Args:
        kwargs: Raw skill keyword arguments.

    Returns:
        The heap size in gigabytes.
    """

    raw_value = kwargs.get("java_mem_gb")
    if raw_value is None or str(raw_value).strip() == "":
        raw_value = os.getenv(
            "BIO_HARNESS_SNPEFF_JAVA_MEM_GB",
            DEFAULT_SNPEFF_JAVA_MEM_GB,
        )
    try:
        return max(1, int(str(raw_value).strip()))
    except ValueError:
        return DEFAULT_SNPEFF_JAVA_MEM_GB


def _snpeff_invocation(java_mem_gb: int) -> str:
    """Build a ``snpEff`` command with an explicit JVM heap limit.

    Args:
        java_mem_gb: Heap size in gigabytes.

    Returns:
        A shell-safe ``snpEff`` command prefix.
    """

    return f"{_snpeff_prefix()} -Xmx{java_mem_gb}g"


def _existing_annotation_passthrough_command(input_vcf: str, output_vcf: str) -> str:
    """Return a runtime guard that reuses already annotated VCF inputs.

    The non-custom ``snpEff`` path sometimes receives a VCF that already
    carries ``ANN`` annotations. Re-annotating those files is unnecessary and
    can fail when a local packaged ``snpEff`` database is unavailable. This
    guard copies the input to the requested output when ``ANN`` is already
    present, preserving the original annotations and bypassing redundant work.

    Args:
        input_vcf: Source VCF path.
        output_vcf: Requested destination VCF path.

    Returns:
        A shell-safe ``python3`` command that exits ``0`` when it reuses the
        annotated input and ``1`` otherwise.
    """

    helper_python = str(preferred_helper_python_executable())
    return (
        f"{shlex.quote(helper_python)} {shlex.quote(str(_REUSE_ANNOTATED_VCF_SCRIPT))} "
        f"{shlex.quote(input_vcf)} {shlex.quote(output_vcf)}"
    )


def snpeff_annotate(**kwargs) -> str:
    """Build a shell command for variant annotation with ``snpEff``.

    Args:
        **kwargs: Skill parameters including input and output VCF paths, genome
            database identifiers, and optional custom-database inputs.

    Returns:
        A shell command string that stages inputs, optionally builds a custom
        database, and annotates the input VCF.
    """

    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    normalized = dict(kwargs)
    reuse_existing_annotations = bool(normalized.get("reuse_existing_annotations", True))
    java_mem_gb = _resolve_java_mem_gb(normalized)
    snpeff_cmd = _snpeff_invocation(java_mem_gb)
    template = f"{snpeff_cmd} {{genome_db}} {{input_vcf}} > {{output_vcf}}"
    reference_fasta = str(normalized.get("reference_fasta", "")).strip()
    annotation_gff = str(normalized.get("annotation_gff", "")).strip()
    config_dir = str(normalized.get("config_dir", "")).strip()
    if not config_dir and reference_fasta and annotation_gff:
        genome_db = str(normalized.get("genome_db", "")).strip() or "custom"
        output_vcf = str(normalized.get("output_vcf", "")).strip()
        output_parent = Path(output_vcf).expanduser().parent if output_vcf else Path.cwd()
        config_dir = str(output_parent / "_snpeff" / genome_db)
        normalized["config_dir"] = config_dir
    if not (reference_fasta and annotation_gff and config_dir):
        rendered = _render_template(template, normalized)
        output_vcf = str(normalized.get("output_vcf", "")).strip()
        input_vcf = str(normalized.get("input_vcf", "")).strip()
        output_parent = Path(output_vcf).expanduser().parent if output_vcf else Path.cwd()
        if reuse_existing_annotations and input_vcf and output_vcf:
            passthrough = _existing_annotation_passthrough_command(input_vcf, output_vcf)
            return f"mkdir -p {shlex.quote(str(output_parent))} && ( {passthrough} || {rendered} )"
        return f"mkdir -p {shlex.quote(str(output_parent))} && {rendered}"

    genome_db = str(normalized.get("genome_db", "")).strip()
    output_vcf = str(normalized.get("output_vcf", "")).strip()
    config_root = Path(config_dir).expanduser()
    data_dir = config_root / "data" / genome_db
    config_path = config_root / "snpEff.config"
    seq_path = data_dir / "sequences.fa"
    gff_path = data_dir / "genes.gff"
    genome_label = str(normalized.get("genome_label", "")).strip() or genome_db
    codon_table = normalize_snpeff_codon_table(normalized.get("codon_table", ""))
    check_protein = bool(normalized.get("check_protein", False))
    check_cds = bool(normalized.get("check_cds", False))
    config_lines = [
        "data.dir = data/",
        f"{genome_db}.genome : {genome_label}",
    ]
    if codon_table:
        config_lines.append(f"{genome_db}.codonTable : {codon_table}")
    config_lines.append(f"{genome_db}.checkProtein : {'true' if check_protein else 'false'}")
    config_lines.append(f"{genome_db}.checkCds : {'true' if check_cds else 'false'}")
    config_printf = " ".join(shlex.quote(line) for line in config_lines)
    build_cmd = (
        f"mkdir -p {shlex.quote(str(data_dir))} "
        f"&& cp {shlex.quote(reference_fasta)} {shlex.quote(str(seq_path))} "
        f"&& cp {shlex.quote(annotation_gff)} {shlex.quote(str(gff_path))} "
        f"&& printf '%s\\n' {config_printf} > {shlex.quote(str(config_path))} "
        f"&& {snpeff_cmd} build -c {shlex.quote(str(config_path))} -gff3 -v "
        f"-noCheckCds -noCheckProtein {shlex.quote(genome_db)}"
    )
    input_vcf = str(normalized.get("input_vcf", "")).strip()
    output_parent = Path(output_vcf).expanduser().parent
    run_root = output_parent.parent if output_parent.name == "output" else output_parent
    stage_root = run_root / "_staging" / "snpeff"
    staged_input_vcf = stage_root / Path(input_vcf).name if input_vcf else stage_root / "input.vcf"
    stage_input_cmd = (
        idempotent_stage_copy_command(input_vcf, str(staged_input_vcf))
        if input_vcf
        else f"mkdir -p {shlex.quote(str(stage_root))}"
    )
    annotate_cmd = (
        f"{snpeff_cmd} -c {shlex.quote(str(config_path))} {shlex.quote(genome_db)} "
        f"{shlex.quote(str(staged_input_vcf))} > {shlex.quote(output_vcf)}"
    )
    return (
        f"mkdir -p {shlex.quote(str(output_parent))} && {build_cmd} "
        f"&& {stage_input_cmd} && {annotate_cmd}"
    )
