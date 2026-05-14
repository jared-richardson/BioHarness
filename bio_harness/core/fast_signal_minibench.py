"""Prepare and validate fast-signal mini-benchmarks.

Mini-benchmarks are tiny real-input harness runs used before long Qwen 3.6
sentinels. The preparation helpers write deterministic FASTA/FASTQ/metadata
fixtures; the validators stay contract-level and intentionally avoid exact
scientific assertions such as coordinates, p-values, or fold changes.
"""

from __future__ import annotations

import csv
import gzip
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MiniBenchmarkArtifactOption:
    """One acceptable artifact shape for a mini-benchmark contract.

    Attributes:
        artifact: Artifact path relative to the selected directory.
        required_columns: Required tabular columns, when applicable.
        required_sidecars: Sidecar paths relative to the selected directory.
    """

    artifact: str
    required_columns: tuple[str, ...] = ()
    required_sidecars: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        """Return a JSON-compatible artifact-option payload."""
        return asdict(self)


@dataclass(frozen=True)
class MiniBenchmarkContract:
    """Contract-level expectations for one mini-benchmark artifact.

    Attributes:
        case_id: Mini-benchmark case identifier.
        artifact: Legacy/default artifact path relative to the selected
            directory.
        required_columns: Legacy/default required tabular columns.
        require_non_empty: Whether the artifact must contain at least one data
            row or a non-empty payload.
        required_sidecars: Legacy/default sidecar paths relative to the
            selected directory.
        artifact_options: Alternate acceptable artifact shapes. When present,
            any one option may satisfy the contract.
    """

    case_id: str
    artifact: str
    required_columns: tuple[str, ...] = ()
    require_non_empty: bool = True
    required_sidecars: tuple[str, ...] = ()
    artifact_options: tuple[MiniBenchmarkArtifactOption, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        """Return a JSON-compatible contract payload."""
        payload = asdict(self)
        payload["artifact_options"] = [
            option.to_mapping() for option in self.resolved_artifact_options()
        ]
        return payload

    def resolved_artifact_options(self) -> tuple[MiniBenchmarkArtifactOption, ...]:
        """Return explicit artifact options, falling back to legacy fields."""
        if self.artifact_options:
            return self.artifact_options
        return (
            MiniBenchmarkArtifactOption(
                artifact=self.artifact,
                required_columns=self.required_columns,
                required_sidecars=self.required_sidecars,
            ),
        )


@dataclass(frozen=True)
class MiniBenchmarkCase:
    """One prepared fast-signal mini-benchmark case.

    Attributes:
        case_id: Stable mini-benchmark case identifier.
        task_name: Task directory name used under ``tasks/``.
        analysis_family: Release-gate analysis family.
        analysis_type: Analysis type surfaced to prompts and metadata.
        prompt: Harness prompt for the mini case.
    """

    case_id: str
    task_name: str
    analysis_family: str
    analysis_type: str
    prompt: str

    def to_manifest_case(self, root: Path) -> dict[str, Any]:
        """Return a domain-runner-compatible manifest case.

        Args:
            root: Prepared mini-benchmark suite root.

        Returns:
            JSON-compatible manifest case payload.
        """
        task_root = root / "tasks" / self.task_name
        return {
            "id": self.case_id,
            "band": 1,
            "data_root": str((task_root / "data").resolve(strict=False)),
            "prompt_file": str((task_root / "prompt.txt").resolve(strict=False)),
            "selected_dir": str(
                (root / "official_runs" / self.task_name / "attempt1").resolve(strict=False)
            ),
            "analysis_family": self.analysis_family,
            "analysis_type": self.analysis_type,
        }


MINI_BENCHMARK_CASES: dict[str, MiniBenchmarkCase] = {
    "control_evolution_mini": MiniBenchmarkCase(
        case_id="control_evolution_mini",
        task_name="evolution",
        analysis_family="evolution",
        analysis_type="bacterial_evolution_variant_calling",
        prompt=(
            "Identify and annotate variants shared by two evolved bacterial "
            "lines relative to the ancestor line. Write the shared variant "
            "table to the final deliverables directory."
        ),
    ),
    "germline_vc_mini": MiniBenchmarkCase(
        case_id="germline_vc_mini",
        task_name="germline-vc",
        analysis_family="germline_vc",
        analysis_type="germline_variant_calling",
        prompt=(
            "Use the provided paired-end sequencing reads and miniature "
            "reference genome to run germline variant calling. Write the "
            "called variants to the final deliverables directory."
        ),
    ),
    "de_mini": MiniBenchmarkCase(
        case_id="de_mini",
        task_name="deseq",
        analysis_family="de",
        analysis_type="rna_seq_differential_expression",
        prompt=(
            "Identify differentially expressed genes between planktonic and "
            "biofilm conditions using the provided tiny RNA-seq reads, "
            "reference genome, annotation, and sample metadata."
        ),
    ),
}


DEFAULT_MINI_BENCHMARK_CONTRACTS: dict[str, MiniBenchmarkContract] = {
    "control_evolution_mini": MiniBenchmarkContract(
        case_id="control_evolution_mini",
        artifact="final/variants_shared.csv",
        required_columns=("CHROM", "POS"),
    ),
    "germline_vc_mini": MiniBenchmarkContract(
        case_id="germline_vc_mini",
        artifact="final/variants.vcf",
        artifact_options=(
            MiniBenchmarkArtifactOption(artifact="final/variants.vcf"),
            MiniBenchmarkArtifactOption(
                artifact="final/variants.vcf.gz",
                required_sidecars=("final/variants.vcf.gz.tbi",),
            ),
        ),
    ),
    "de_mini": MiniBenchmarkContract(
        case_id="de_mini",
        artifact="final/deseq_results.csv",
        artifact_options=(
            MiniBenchmarkArtifactOption(
                artifact="final/deseq_results.csv",
                required_columns=("gene_id", "log2FoldChange", "pvalue"),
            ),
            MiniBenchmarkArtifactOption(
                artifact="final/differential_expression.csv",
                required_columns=("gene", "log2FoldChange", "pvalue"),
            ),
        ),
    ),
}


def validate_mini_benchmark_contract(
    selected_dir: Path | str,
    contract: MiniBenchmarkContract,
) -> dict[str, Any]:
    """Validate a mini-benchmark artifact at contract granularity.

    Args:
        selected_dir: Selected output directory for a completed mini run.
        contract: Contract to validate.

    Returns:
        Validation payload with ``passed`` and ``issues``.
    """
    root = Path(selected_dir).expanduser().resolve(strict=False)
    option_results = [
        _validate_artifact_option(
            root=root,
            option=option,
            require_non_empty=contract.require_non_empty,
        )
        for option in contract.resolved_artifact_options()
    ]
    passing = [result for result in option_results if result.get("passed", False)]
    issues: list[str] = []
    if not passing:
        for result in option_results:
            option_artifact = str(result.get("artifact", "") or "")
            for issue in result.get("issues", []) or []:
                issues.append(f"{option_artifact}: {issue}")
    return {
        "case_id": contract.case_id,
        "passed": bool(passing),
        "issues": issues,
        "matched_artifact": str(passing[0].get("artifact", "") or "") if passing else "",
        "option_results": option_results,
        "contract": contract.to_mapping(),
    }


def prepare_mini_benchmark_suite(
    root: Path | str,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Prepare deterministic tiny inputs for the mini-benchmark suite.

    Args:
        root: Suite root to create. The function writes ``tasks/`` inputs and
            ``manifest.json`` below this directory.
        overwrite: Whether to rewrite existing generated files.

    Returns:
        JSON-compatible preparation payload with manifest and case paths.
    """
    suite_root = Path(root).expanduser().resolve(strict=False)
    suite_root.mkdir(parents=True, exist_ok=True)
    _prepare_evolution_case(suite_root, overwrite=overwrite)
    _prepare_germline_case(suite_root, overwrite=overwrite)
    _prepare_de_case(suite_root, overwrite=overwrite)
    cases = [
        MINI_BENCHMARK_CASES[case_id].to_manifest_case(suite_root)
        for case_id in sorted(MINI_BENCHMARK_CASES)
    ]
    manifest = {
        "description": "Fast-signal mini-benchmark suite with tiny real inputs.",
        "cases": cases,
    }
    manifest_path = suite_root / "manifest.json"
    _write_json_if_needed(manifest_path, manifest, overwrite=overwrite)
    return {
        "root": str(suite_root),
        "manifest_file": str(manifest_path),
        "cases": cases,
    }


def selected_dir_for_mini_case(root: Path | str, case_id: str) -> Path:
    """Return the strict-mode selected directory for a mini-benchmark case.

    Args:
        root: Prepared suite root.
        case_id: Mini-benchmark case id.

    Returns:
        Selected directory path whose layout is compatible with strict
        benchmark binders.

    Raises:
        KeyError: If the case id is unknown.
    """
    suite_root = Path(root).expanduser().resolve(strict=False)
    case = MINI_BENCHMARK_CASES[case_id]
    return suite_root / "official_runs" / case.task_name / "attempt1"


def _validate_artifact_option(
    *,
    root: Path,
    option: MiniBenchmarkArtifactOption,
    require_non_empty: bool,
) -> dict[str, Any]:
    artifact_path = root / option.artifact
    issues: list[str] = []
    if not artifact_path.is_file():
        issues.append(f"missing artifact: {option.artifact}")
    elif require_non_empty and artifact_path.stat().st_size <= 0:
        issues.append(f"empty artifact: {option.artifact}")

    for sidecar in option.required_sidecars:
        sidecar_path = root / sidecar
        if not sidecar_path.is_file():
            issues.append(f"missing sidecar: {sidecar}")
        elif require_non_empty and sidecar_path.stat().st_size <= 0:
            issues.append(f"empty sidecar: {sidecar}")

    if artifact_path.is_file() and option.required_columns:
        observed_columns = _read_tabular_columns(artifact_path)
        missing = sorted(set(option.required_columns) - set(observed_columns))
        if missing:
            issues.append(f"missing columns in {option.artifact}: {missing}")
        elif require_non_empty and not _has_data_row(artifact_path):
            issues.append(f"no data rows in {option.artifact}")

    return {
        "artifact": option.artifact,
        "passed": not issues,
        "issues": issues,
    }


def _read_tabular_columns(path: Path) -> list[str]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            return [str(item).strip() for item in next(reader, [])]
    except OSError:
        return []


def _has_data_row(path: Path) -> bool:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            next(reader, None)
            return next(reader, None) is not None
    except OSError:
        return False


def _prepare_evolution_case(root: Path, *, overwrite: bool) -> None:
    case = MINI_BENCHMARK_CASES["control_evolution_mini"]
    task_root = root / "tasks" / case.task_name
    data_root = task_root / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    _write_text_if_needed(task_root / "prompt.txt", case.prompt + "\n", overwrite=overwrite)
    ancestor = _synthetic_coding_reference(2600)
    evolved = _mutate_sequence(ancestor, {520: "T", 1200: "G"})
    for sample, sequence in {
        "anc": ancestor,
        "evol1": evolved,
        "evol2": evolved,
    }.items():
        _write_paired_fastq(
            data_root / f"{sample}_R1.fastq.gz",
            data_root / f"{sample}_R2.fastq.gz",
            sample=sample,
            sequence=sequence,
            read_count=260,
            read_length=125,
            gzip_output=True,
            overwrite=overwrite,
        )


def _prepare_germline_case(root: Path, *, overwrite: bool) -> None:
    case = MINI_BENCHMARK_CASES["germline_vc_mini"]
    task_root = root / "tasks" / case.task_name
    data_root = task_root / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    _write_text_if_needed(task_root / "prompt.txt", case.prompt + "\n", overwrite=overwrite)
    reference = _synthetic_reference(1200)
    sample = _mutate_sequence(reference, {412: "A"})
    _write_text_if_needed(
        data_root / "ref_genome.fa",
        f">chrMini\n{_wrap_sequence(reference)}\n",
        overwrite=overwrite,
    )
    _write_paired_fastq(
        data_root / "sample_1.fastq",
        data_root / "sample_2.fastq",
        sample="sample",
        sequence=sample,
        read_count=80,
        read_length=100,
        gzip_output=False,
        overwrite=overwrite,
    )


def _prepare_de_case(root: Path, *, overwrite: bool) -> None:
    case = MINI_BENCHMARK_CASES["de_mini"]
    task_root = root / "tasks" / case.task_name
    data_root = task_root / "data"
    references_root = task_root / "references"
    data_root.mkdir(parents=True, exist_ok=True)
    references_root.mkdir(parents=True, exist_ok=True)
    _write_text_if_needed(task_root / "prompt.txt", case.prompt + "\n", overwrite=overwrite)
    reference = _synthetic_reference(1600)
    _write_text_if_needed(
        references_root / "C_parapsilosis_CDC317_current_chromosomes.fasta",
        f">chrMini\n{_wrap_sequence(reference)}\n",
        overwrite=overwrite,
    )
    _write_text_if_needed(
        references_root / "C_parapsilosis_CDC317_current_features.gff",
        "\n".join(
            [
                "##gff-version 3",
                "chrMini\tfast_signal\tgene\t101\t520\t.\t+\t.\tID=geneA",
                "chrMini\tfast_signal\tgene\t721\t1240\t.\t+\t.\tID=geneB",
                "",
            ]
        ),
        overwrite=overwrite,
    )
    metadata = [
        "sample\tcondition",
        "plankton1\tPlankton",
        "plankton2\tPlankton",
        "biofilm1\tBiofilm",
        "biofilm2\tBiofilm",
    ]
    _write_text_if_needed(
        data_root / "sample_metadata.tsv",
        "\n".join(metadata) + "\n",
        overwrite=overwrite,
    )
    sample_regions = {
        "plankton1": (100, 720),
        "plankton2": (120, 740),
        "biofilm1": (100, 100),
        "biofilm2": (120, 120),
    }
    for sample, (r1_start, r2_start) in sample_regions.items():
        _write_paired_fastq(
            data_root / f"{sample}_1.fastq",
            data_root / f"{sample}_2.fastq",
            sample=sample,
            sequence=reference,
            read_count=64,
            read_length=75,
            start_cycle=(r1_start, r2_start),
            gzip_output=False,
            overwrite=overwrite,
        )


def _synthetic_reference(length: int) -> str:
    motif = "ACGTGCAATGCCGTTAACCGGTTACGAT"
    repeats = (length // len(motif)) + 1
    return (motif * repeats)[:length]


def _synthetic_coding_reference(length: int) -> str:
    """Return a deterministic mini bacterial reference with a long ORF.

    The evolution mini-benchmark assembles ancestor reads before running
    Prodigal/SnpEff. A repetitive tiny reference can collapse to a very short
    contig and exercise only tool failure paths. This sequence stays tiny, but
    has enough non-repetitive coding content for real annotation tools.
    """
    if length < 900:
        raise ValueError("Coding mini reference must be at least 900 bp.")
    prefix_len = min(240, max(90, length // 10))
    suffix_len = min(500, max(120, length // 5))
    coding_len = max(600, length - prefix_len - suffix_len)
    coding_codons = max(3, coding_len // 3)
    sequence = (
        _pseudo_random_dna(prefix_len, seed=11)
        + _synthetic_open_reading_frame(coding_codons, seed=37)
        + _pseudo_random_dna(suffix_len, seed=23)
    )
    return sequence[:length]


def _pseudo_random_dna(length: int, *, seed: int) -> str:
    """Return deterministic non-repetitive DNA for tiny fixtures."""
    value = seed
    bases: list[str] = []
    for _ in range(length):
        value = (1103515245 * value + 12345) & 0x7FFFFFFF
        bases.append("ACGT"[(value >> 16) & 3])
    return "".join(bases)


def _synthetic_open_reading_frame(codons: int, *, seed: int) -> str:
    """Return a deterministic ORF with no internal stop codons."""
    safe_codons = [
        a + b + c
        for a in "ACGT"
        for b in "ACGT"
        for c in "ACGT"
        if a + b + c not in {"TAA", "TAG", "TGA"}
    ]
    value = seed
    body: list[str] = ["ATG"]
    for _ in range(max(0, codons - 2)):
        value = (1664525 * value + 1013904223) & 0xFFFFFFFF
        body.append(safe_codons[value % len(safe_codons)])
    body.append("TAA")
    return "".join(body)


def _mutate_sequence(sequence: str, replacements: dict[int, str]) -> str:
    chars = list(sequence)
    for offset, base in replacements.items():
        if 0 <= offset < len(chars):
            chars[offset] = base
    return "".join(chars)


def _wrap_sequence(sequence: str, width: int = 80) -> str:
    return "\n".join(sequence[index : index + width] for index in range(0, len(sequence), width))


def _write_paired_fastq(
    reads_1: Path,
    reads_2: Path,
    *,
    sample: str,
    sequence: str,
    read_count: int,
    read_length: int,
    gzip_output: bool,
    overwrite: bool,
    start_cycle: tuple[int, int] | None = None,
) -> None:
    if not overwrite and reads_1.exists() and reads_2.exists():
        return
    starts = start_cycle or (0, max(0, read_length // 2))
    records_1: list[str] = []
    records_2: list[str] = []
    max_start = max(1, len(sequence) - (read_length * 2) - 1)
    for index in range(read_count):
        start_1 = (starts[0] + index * 11) % max_start
        start_2 = (starts[1] + index * 11) % max_start
        seq_1 = sequence[start_1 : start_1 + read_length]
        seq_2 = _reverse_complement(sequence[start_2 : start_2 + read_length])
        records_1.append(_fastq_record(f"{sample}_{index}/1", seq_1))
        records_2.append(_fastq_record(f"{sample}_{index}/2", seq_2))
    _write_bytes_or_text(reads_1, "".join(records_1), gzip_output=gzip_output)
    _write_bytes_or_text(reads_2, "".join(records_2), gzip_output=gzip_output)


def _fastq_record(name: str, sequence: str) -> str:
    return f"@{name}\n{sequence}\n+\n{'I' * len(sequence)}\n"


def _reverse_complement(sequence: str) -> str:
    table = str.maketrans("ACGTacgt", "TGCAtgca")
    return sequence.translate(table)[::-1]


def _write_bytes_or_text(path: Path, text: str, *, gzip_output: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if gzip_output:
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(text)
        return
    path.write_text(text, encoding="utf-8")


def _write_text_if_needed(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json_if_needed(path: Path, payload: dict[str, Any], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
