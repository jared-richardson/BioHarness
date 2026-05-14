"""Run focused Qwen-through-harness smoke tests for selected skills."""

from __future__ import annotations

import json
import importlib.util
import shutil
import subprocess
import sys
import gzip
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import os

from bio_harness.core.tool_env import requirement_available, which_with_pixi

_BLAST_DNA_SEQ = (
    "ATGAAAACCGCTTACATTGCTAAACAACGTCAAATTTCTTTTGTTAAATCTCATTTTTCTCGTCAA"
    "GATATTCTGGATCTGTGGATTTACCATACTCAAGGTTACTTTCCTGATTGGCAAAATTAC"
)
_BLAST_PROT_SEQ = "MKTAYIAKQRQISFVKSHFSRQDILDLWIYHTQGYFPDWQNY"
_BLAST_DB_SENTINELS: dict[str, tuple[str, ...]] = {
    "nucl": (".ndb", ".nin", ".nsq"),
    "prot": (".pdb", ".pin", ".psq"),
}

@dataclass(frozen=True)
class QwenSkillSmokeCase:
    """A single live harness smoke case for a specific skill."""

    name: str
    description: str
    source_input: str
    prompt_template: str
    expected_tool: str
    expected_outputs: tuple[str, ...]
    prompt_context: dict[str, str] | None = None
    expected_tools: tuple[str, ...] | None = None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_result_payload(result_path: Path, stdout: str) -> dict[str, Any]:
    if result_path.exists() and result_path.is_file():
        return _load_json(result_path)
    rendered = str(stdout or "").strip()
    if not rendered:
        return {}
    decoder = json.JSONDecoder()
    for index in reversed([i for i, char in enumerate(rendered) if char == "{"]):
        try:
            payload, end = decoder.raw_decode(rendered[index:])
        except Exception:
            continue
        if index + end == len(rendered) and isinstance(payload, dict):
            return payload
    return {}


def _find_run_dir_for_selected_dir(project_root: Path, selected_dir: Path) -> Path | None:
    runs_root = project_root / "workspace" / "runs"
    if not runs_root.exists():
        return None
    selected_dir_text = str(selected_dir)
    run_dirs = sorted(
        (path for path in runs_root.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for run_dir in run_dirs[:200]:
        execution_log = run_dir / "execution.log"
        if not execution_log.exists():
            continue
        try:
            log_text = execution_log.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if selected_dir_text in log_text:
            return run_dir
    return None


def _resolve_result_payload(
    project_root: Path,
    selected_dir: Path,
    stdout: str,
) -> tuple[dict[str, Any], Path | None]:
    result_path = selected_dir / "result.json"
    result = _load_result_payload(result_path, stdout)
    run_id = str(result.get("run_id", "")).strip()
    run_dir = project_root / "workspace" / "runs" / run_id if run_id else None
    if run_dir is not None and not run_dir.exists():
        run_dir = None
    if run_dir is None:
        run_dir = _find_run_dir_for_selected_dir(project_root, selected_dir)
    if run_dir is not None:
        run_result_path = run_dir / "result.json"
        if run_result_path.exists():
            result = _load_json(run_result_path)
        else:
            run_state_path = run_dir / "state.json"
            if run_state_path.exists():
                result = _load_json(run_state_path)
        if "run_id" not in result:
            result["run_id"] = run_dir.name
    return result, run_dir


def _find_latest_passing_selected_dir(
    project_root: Path,
    task_name: str,
    *,
    require_validator_pass: bool = True,
) -> Path:
    candidates = _iter_clean_selected_dirs(
        project_root,
        task_name,
        require_validator_pass=require_validator_pass,
    )
    if not candidates:
        task_root = (
            project_root
            / "workspace"
            / "benchmarks"
            / "bioagent-bench"
            / "official_runs"
            / task_name
        )
        raise FileNotFoundError(
            f"No clean passing selected_dir found for task '{task_name}' under {task_root}"
        )
    return candidates[0]


def _iter_clean_selected_dirs(
    project_root: Path,
    task_name: str,
    *,
    require_validator_pass: bool = True,
) -> list[Path]:
    task_root = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / task_name
    )
    candidates: list[Path] = []
    for result_path in sorted(task_root.glob("*/result.json")):
        selected_dir = result_path.parent
        result = _load_json(result_path)
        if str(result.get("status", "")).strip() != "completed":
            continue
        if int(result.get("auto_repair_history_count", 1) or 0) != 0:
            continue
        if require_validator_pass:
            validator_log = selected_dir / "validator.log"
            if validator_log.exists():
                validator_text = validator_log.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                if "BENCHMARK PASSED: True" not in validator_text:
                    continue
        candidates.append(selected_dir)
    return sorted(
        candidates,
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )


def build_starter_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build the first non-benchmark Qwen smoke cases from stable run outputs."""
    root = Path(project_root).expanduser().resolve()
    source_selected_dir = _find_latest_passing_selected_dir(root, "alzheimer-mouse")
    source_result_json = source_selected_dir / "result.json"
    source_artifact = source_selected_dir / "final" / "pathway_comparison.csv"
    if not source_result_json.exists():
        raise FileNotFoundError(f"Missing completed run result.json: {source_result_json}")
    if not source_artifact.exists():
        raise FileNotFoundError(f"Missing completed artifact for schema smoke: {source_artifact}")

    return [
        QwenSkillSmokeCase(
            name="artifact_schema_profile",
            description="Profile a completed CSV artifact into a JSON schema/data dictionary.",
            source_input=str(source_artifact),
            prompt_template=(
                "Use the artifact_schema_profile tool to profile the schema for the completed "
                "artifact at {source_input} and write the JSON output to {selected_dir}/schema.json. "
                "Do not use bash_run."
            ),
            expected_tool="artifact_schema_profile",
            expected_outputs=("schema.json",),
        ),
        QwenSkillSmokeCase(
            name="multiqc_report",
            description="Build a report bundle from a completed run with MultiQC enabled if available.",
            source_input=str(source_result_json),
            prompt_template=(
                "Use the multiqc_report tool to build a researcher-facing report bundle from the "
                "completed run at {source_input} and write the report bundle to "
                "{selected_dir}/report_bundle. Do not use bash_run."
            ),
            expected_tool="multiqc_report",
            expected_outputs=(
                "report_bundle/summary.json",
                "report_bundle/summary.md",
                "report_bundle/tooling_status.json",
            ),
        ),
        QwenSkillSmokeCase(
            name="quarto_report",
            description="Build a report bundle from a completed run with Quarto rendering enabled if available.",
            source_input=str(source_result_json),
            prompt_template=(
                "Use the quarto_report tool to build a researcher-facing report bundle from the "
                "completed run at {source_input} and write the report bundle to "
                "{selected_dir}/report_bundle. Do not use bash_run."
            ),
            expected_tool="quarto_report",
            expected_outputs=(
                "report_bundle/summary.json",
                "report_bundle/summary.md",
                "report_bundle/tooling_status.json",
            ),
        ),
    ]


def _resolve_star_binary(project_root: Path) -> str:
    resolved_star = which_with_pixi("STAR") or which_with_pixi("star")
    if resolved_star:
        return resolved_star
    candidates = [
        str(project_root / ".pixi" / "envs" / "default" / "bin" / "STAR"),
        str(project_root / ".pixi" / "envs" / "default" / "bin" / "star"),
        shutil.which("STAR") or "",
        shutil.which("star") or "",
        "/usr/local/bin/STAR",
        "/usr/local/bin/star",
    ]
    for candidate in candidates:
        resolved = str(candidate or "").strip()
        if resolved and os.path.isfile(resolved) and os.access(resolved, os.X_OK):
            return resolved
    return ""


def _has_executable(command_name: str) -> bool:
    return requirement_available(command_name)


def _has_python_module(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _find_latest_directory_with_child(
    root: Path,
    *,
    child_name: str,
) -> Path:
    candidates = [path.parent for path in root.glob(f"*/{child_name}") if path.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"No directory under {root} contains child '{child_name}'")
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def _reverse_complement(sequence: str) -> str:
    table = str.maketrans("ACGTNacgtn", "TGCANtgcan")
    return str(sequence or "").translate(table)[::-1]


def _synthetic_flye_fastq() -> str:
    genome = "".join("ACGT"[(index * 7 + index // 11) % 4] for index in range(12000))
    records: list[str] = []
    read_length = 5000
    for index, start in enumerate(range(0, 8000, 800), start=1):
        circular = genome + genome
        read = circular[start : start + read_length]
        records.append(f"@flye_{index}\n{read}\n+\n{'I' * len(read)}\n")
    return "".join(records)


def _synthetic_trinity_fastq_pair() -> tuple[str, str]:
    transcripts = [
        "".join("ACGT"[(index * 5 + 1) % 4] for index in range(420)),
        "".join("TGCA"[(index * 3 + 2) % 4] for index in range(440)),
    ]
    reads_1: list[str] = []
    reads_2: list[str] = []
    record_index = 1
    for transcript_id, transcript in enumerate(transcripts, start=1):
        for start in range(0, len(transcript) - 220, 20):
            left = transcript[start : start + 100]
            right = _reverse_complement(transcript[start + 120 : start + 220])
            header = f"@trinity_{transcript_id}_{record_index}"
            reads_1.append(f"{header}/1\n{left}\n+\n{'I' * len(left)}\n")
            reads_2.append(f"{header}/2\n{right}\n+\n{'I' * len(right)}\n")
            record_index += 1
    return ("".join(reads_1), "".join(reads_2))


def _ensure_smoke_source_alias(
    project_root: Path,
    source_path: Path,
    *,
    alias_parts: tuple[str, ...],
) -> Path:
    alias_path = (
        project_root
        / "workspace"
        / "skill_smoke"
        / "_source_aliases"
        / Path(*alias_parts)
    )
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    if alias_path.exists() or alias_path.is_symlink():
        return alias_path
    try:
        alias_path.symlink_to(source_path, target_is_directory=source_path.is_dir())
        return alias_path
    except OSError:
        if source_path.is_dir():
            shutil.copytree(source_path, alias_path)
        else:
            shutil.copy2(source_path, alias_path)
        return alias_path


def _ensure_generated_text_alias(
    project_root: Path,
    *,
    alias_parts: tuple[str, ...],
    content: str,
) -> Path:
    alias_path = (
        project_root
        / "workspace"
        / "skill_smoke"
        / "_source_aliases"
        / Path(*alias_parts)
    )
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    if not alias_path.exists():
        alias_path.write_text(content, encoding="utf-8")
    return alias_path


def _ensure_fastq_subset_alias(
    project_root: Path,
    source_path: Path,
    *,
    alias_parts: tuple[str, ...],
    read_limit: int = 500,
) -> Path:
    alias_path = (
        project_root
        / "workspace"
        / "skill_smoke"
        / "_source_aliases"
        / Path(*alias_parts)
    )
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    if alias_path.exists():
        return alias_path

    line_limit = max(1, int(read_limit)) * 4
    source_is_gzip = source_path.name.endswith(".gz")
    use_gzip = False
    if source_is_gzip:
        try:
            with open(source_path, "rb") as probe:
                use_gzip = probe.read(2) == b"\x1f\x8b"
        except OSError:
            use_gzip = False
    reader = gzip.open if use_gzip else open
    writer = gzip.open if source_is_gzip else open
    with reader(source_path, "rt", encoding="utf-8", errors="replace") as src:
        with writer(alias_path, "wt", encoding="utf-8") as dst:
            for index, line in enumerate(src):
                if index >= line_limit:
                    break
                dst.write(line)
    return alias_path


def _ensure_gff_to_gtf_alias(
    project_root: Path,
    source_path: Path,
    *,
    alias_parts: tuple[str, ...],
) -> Path:
    alias_path = (
        project_root
        / "workspace"
        / "skill_smoke"
        / "_source_aliases"
        / Path(*alias_parts)
    )
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    if alias_path.exists():
        return alias_path

    transcript_to_gene: dict[str, str] = {}
    rows: list[tuple[str, str, str, str, str, str, str, str, dict[str, str]]] = []

    def _parse_attrs(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for part in text.split(";"):
            token = part.strip()
            if (not token) or ("=" not in token):
                continue
            key, value = token.split("=", 1)
            out[key.strip()] = value.strip()
        return out

    for line in source_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if (not line) or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 9:
            continue
        chrom, source, feature, start, end, score, strand, phase, attrs = parts
        meta = _parse_attrs(attrs)
        rows.append((chrom, source, feature, start, end, score, strand, phase, meta))
        if feature == "gene":
            gene_id = str(meta.get("ID") or meta.get("Name") or "").strip()
            if gene_id:
                transcript_to_gene.setdefault(gene_id, gene_id)
        elif feature in {"mRNA", "transcript"}:
            transcript_id = str(meta.get("ID") or meta.get("Name") or "").strip()
            gene_id = str(meta.get("Parent") or transcript_id).strip()
            if transcript_id and gene_id:
                transcript_to_gene[transcript_id] = gene_id

    with alias_path.open("w", encoding="utf-8") as dst:
        for chrom, source, feature, start, end, score, strand, phase, meta in rows:
            if feature not in {"gene", "mRNA", "transcript", "exon"}:
                continue
            if feature == "gene":
                gene_id = str(meta.get("ID") or meta.get("Name") or "").strip()
                transcript_id = gene_id
                gtf_feature = "gene"
            elif feature in {"mRNA", "transcript"}:
                transcript_id = str(meta.get("ID") or meta.get("Name") or "").strip()
                gene_id = str(meta.get("Parent") or transcript_to_gene.get(transcript_id, transcript_id)).strip()
                gtf_feature = "transcript"
            else:
                transcript_id = str(meta.get("Parent") or meta.get("ID") or meta.get("Name") or "").strip()
                transcript_id = transcript_id.split(",")[0].strip()
                gene_id = str(transcript_to_gene.get(transcript_id, transcript_id)).strip()
                gtf_feature = "exon"
            if not gene_id or not transcript_id:
                continue
            rendered_attrs = f'gene_id "{gene_id}"; transcript_id "{transcript_id}";'
            dst.write(
                "\t".join(
                    [
                        chrom,
                        source,
                        gtf_feature,
                        start,
                        end,
                        score,
                        strand if strand in {"+", "-"} else ".",
                        phase if phase in {"0", "1", "2"} else ".",
                        rendered_attrs,
                    ]
            )
                + "\n"
            )
    return alias_path


def _ensure_blast_query_aliases(project_root: Path) -> tuple[Path, Path]:
    nucleotide_fasta = _ensure_generated_text_alias(
        project_root,
        alias_parts=("blast_family", "query.fa"),
        content=f">n1\n{_BLAST_DNA_SEQ}\n",
    )
    protein_fasta = _ensure_generated_text_alias(
        project_root,
        alias_parts=("blast_family", "query.faa"),
        content=f">p1\n{_BLAST_PROT_SEQ}\n",
    )
    return nucleotide_fasta, protein_fasta


def _ensure_blast_database(
    project_root: Path,
    input_fasta: Path,
    *,
    alias_parts: tuple[str, ...],
    dbtype: str,
    parse_seqids: bool = False,
) -> Path:
    db_prefix = (
        project_root
        / "workspace"
        / "skill_smoke"
        / "_source_aliases"
        / Path(*alias_parts)
    )
    db_prefix.parent.mkdir(parents=True, exist_ok=True)
    if not any(db_prefix.with_suffix(ext).exists() for ext in _BLAST_DB_SENTINELS[dbtype]):
        subprocess.run(
            [
                which_with_pixi("makeblastdb") or "makeblastdb",
                "-in",
                str(input_fasta),
                "-dbtype",
                dbtype,
                "-out",
                str(db_prefix),
                *(["-parse_seqids"] if parse_seqids else []),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return db_prefix


def _ensure_blast_archive(
    project_root: Path,
    *,
    query_fasta: Path,
    database_prefix: Path,
    alias_parts: tuple[str, ...],
) -> Path:
    archive_path = (
        project_root
        / "workspace"
        / "skill_smoke"
        / "_source_aliases"
        / Path(*alias_parts)
    )
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if not archive_path.exists():
        subprocess.run(
            [
                which_with_pixi("blastp") or "blastp",
                "-query",
                str(query_fasta),
                "-db",
                str(database_prefix),
                "-out",
                str(archive_path),
                "-outfmt",
                "11",
                "-num_threads",
                "1",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return archive_path


def _ensure_blast_alias_database(
    project_root: Path,
    *,
    database_prefix: Path,
    alias_parts: tuple[str, ...],
) -> Path:
    alias_prefix = (
        project_root
        / "workspace"
        / "skill_smoke"
        / "_source_aliases"
        / Path(*alias_parts)
    )
    alias_prefix.parent.mkdir(parents=True, exist_ok=True)
    if not alias_prefix.with_suffix(".pal").exists():
        subprocess.run(
            [
                which_with_pixi("blastdb_aliastool") or "blastdb_aliastool",
                "-dblist",
                str(database_prefix),
                "-dbtype",
                "prot",
                "-out",
                str(alias_prefix),
                "-title",
                "alias_db",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return alias_prefix


def _ensure_blast_profile_checkpoint(
    project_root: Path,
    *,
    query_fasta: Path,
    database_prefix: Path,
    alias_parts: tuple[str, ...],
) -> Path:
    checkpoint_path = (
        project_root
        / "workspace"
        / "skill_smoke"
        / "_source_aliases"
        / Path(*alias_parts)
    )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    if not checkpoint_path.exists():
        subprocess.run(
            [
                which_with_pixi("psiblast") or "psiblast",
                "-query",
                str(query_fasta),
                "-db",
                str(database_prefix),
                "-num_iterations",
                "2",
                "-out_pssm",
                str(checkpoint_path),
                "-out",
                os.devnull,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return checkpoint_path


def _ensure_blast_profile_list(
    project_root: Path,
    *,
    checkpoint_path: Path,
    alias_parts: tuple[str, ...],
) -> Path:
    input_list = (
        project_root
        / "workspace"
        / "skill_smoke"
        / "_source_aliases"
        / Path(*alias_parts)
    )
    input_list.parent.mkdir(parents=True, exist_ok=True)
    if not input_list.exists():
        input_list.write_text(f"{checkpoint_path}\n", encoding="utf-8")
    return input_list


def _ensure_blast_profile_database(
    project_root: Path,
    *,
    input_list: Path,
    alias_parts: tuple[str, ...],
) -> Path:
    db_prefix = (
        project_root
        / "workspace"
        / "skill_smoke"
        / "_source_aliases"
        / Path(*alias_parts)
    )
    db_prefix.parent.mkdir(parents=True, exist_ok=True)
    if not db_prefix.with_suffix(".rps").exists():
        subprocess.run(
            [
                which_with_pixi("makeprofiledb") or "makeprofiledb",
                "-in",
                str(input_list),
                "-out",
                str(db_prefix),
                "-dbtype",
                "rps",
                "-index",
                "true",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return db_prefix


def _ensure_bam_subset_alias(
    project_root: Path,
    source_path: Path,
    *,
    alias_parts: tuple[str, ...],
    alignment_limit: int = 40_000,
) -> Path:
    alias_path = (
        project_root
        / "workspace"
        / "skill_smoke"
        / "_source_aliases"
        / Path(*alias_parts)
    )
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    if alias_path.exists():
        return alias_path

    header = subprocess.check_output(
        ["samtools", "view", "-H", str(source_path)],
        text=True,
    )
    body_lines: list[str] = []
    with subprocess.Popen(
        ["samtools", "view", str(source_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ) as proc:
        assert proc.stdout is not None
        for index, line in enumerate(proc.stdout):
            if index >= max(1, int(alignment_limit)):
                break
            body_lines.append(line)
        proc.terminate()
        proc.wait(timeout=20)
    sam_path = alias_path.with_suffix(alias_path.suffix + ".sam")
    sam_path.write_text(header + "".join(body_lines), encoding="utf-8")
    subprocess.run(
        ["samtools", "view", "-b", "-o", str(alias_path), str(sam_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    sam_path.unlink(missing_ok=True)
    return alias_path


def _smoke_alias_name(source_path: Path) -> str:
    name = source_path.name
    if name.endswith(".fastq.gz"):
        return name.removesuffix(".fastq.gz") + ".smoke.fastq.gz"
    if name.endswith(".fq.gz"):
        return name.removesuffix(".fq.gz") + ".smoke.fq.gz"
    if name.endswith(".fastq"):
        return name.removesuffix(".fastq") + ".smoke.fastq"
    if name.endswith(".fq"):
        return name.removesuffix(".fq") + ".smoke.fq"
    return name + ".smoke"


def build_second_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a second tranche of cases supported by the current machine."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    star_bin = _resolve_star_binary(root)
    if star_bin:
        deseq_runs_root = (
            root
            / "workspace"
            / "benchmarks"
            / "bioagent-bench"
            / "official_runs"
            / "deseq"
        )
        try:
            star_index_parent = _find_latest_directory_with_child(
                deseq_runs_root,
                child_name="star_index",
            )
        except FileNotFoundError:
            star_index_parent = None
        if star_index_parent is not None:
            star_index = star_index_parent / "star_index"
            reads_1 = (
                root
                / "workspace"
                / "benchmarks"
                / "bioagent-bench"
                / "tasks"
                / "deseq"
                / "data"
                / "SRR1278968_1.fastq"
            )
            reads_2 = reads_1.with_name("SRR1278968_2.fastq")
            if star_index.exists() and reads_1.exists() and reads_2.exists():
                star_index_alias = _ensure_smoke_source_alias(
                    root,
                    star_index,
                    alias_parts=("star_align", "genome_dir"),
                )
                reads_1_alias = _ensure_smoke_source_alias(
                    root,
                    reads_1,
                    alias_parts=("star_align", reads_1.name),
                )
                reads_2_alias = _ensure_smoke_source_alias(
                    root,
                    reads_2,
                    alias_parts=("star_align", reads_2.name),
                )
                cases.append(
                    QwenSkillSmokeCase(
                        name="star_align",
                        description="Align a paired-end short-read sample with a prebuilt STAR index.",
                        source_input=str(reads_1_alias),
                        prompt_template=(
                            "This is a direct one-step skill smoke test. Use the star_align tool to align the "
                            "paired-end reads at {source_input} and {reads_2} against the existing STAR genome index "
                            "at {genome_dir}. Write outputs under {selected_dir}/star_output/ using the output prefix "
                            "{selected_dir}/star_output/sample_. Use exactly one step and do not add any other tools "
                            "or workflow stages. Do not use bash_run."
                        ),
                        expected_tool="star_align",
                        expected_outputs=(
                            "star_output/sample_Aligned.out.bam",
                            "star_output/sample_Log.final.out",
                        ),
                        prompt_context={
                            "reads_2": str(reads_2_alias),
                            "genome_dir": str(star_index_alias),
                        },
                    )
                )

    if _has_executable("freebayes") and _has_executable("samtools"):
        try:
            evolution_selected_dir = _find_latest_passing_selected_dir(root, "evolution")
        except FileNotFoundError:
            try:
                evolution_selected_dir = _find_latest_passing_selected_dir(
                    root,
                    "evolution",
                    require_validator_pass=False,
                )
            except FileNotFoundError:
                evolution_selected_dir = None
        if evolution_selected_dir is not None:
            reference_fasta = evolution_selected_dir / "assembly" / "scaffolds.fasta"
            input_bam = evolution_selected_dir / "alignments" / "anc_aligned.bam"
            if reference_fasta.exists() and input_bam.exists():
                reference_alias = _ensure_smoke_source_alias(
                    root,
                    reference_fasta,
                    alias_parts=("freebayes_call", reference_fasta.name),
                )
                bam_alias = _ensure_smoke_source_alias(
                    root,
                    input_bam,
                    alias_parts=("freebayes_call", input_bam.name),
                )
                cases.append(
                    QwenSkillSmokeCase(
                        name="freebayes_call",
                        description="Call variants from a completed alignment/reference pair with FreeBayes.",
                        source_input=str(bam_alias),
                        prompt_template=(
                            "This is a direct one-step skill smoke test. Use only the freebayes_call tool to call "
                            "variants from the aligned BAM at {source_input} against the reference FASTA at "
                            "{reference_fasta}. Write the output VCF to {selected_dir}/variants/anc_raw.vcf. "
                            "Do not add alignment, assembly, or any other steps. Do not use bash_run."
                        ),
                        expected_tool="freebayes_call",
                        expected_outputs=("variants/anc_raw.vcf",),
                        prompt_context={"reference_fasta": str(reference_alias)},
                    )
                )

    return cases


def build_third_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a third tranche of cases supported by the current machine."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if _has_python_module("pydeseq2") and _has_executable("Rscript"):
        try:
            deseq_selected_dir = _find_latest_passing_selected_dir(root, "deseq")
        except FileNotFoundError:
            deseq_selected_dir = None
        metadata_table = (
            root
            / "workspace"
            / "benchmarks"
            / "bioagent-bench"
            / "tasks"
            / "deseq"
            / "data"
            / "sample_metadata.tsv"
        )
        counts_matrix = (
            deseq_selected_dir / "counts" / "gene_counts.txt"
            if deseq_selected_dir is not None
            else None
        )
        script_path = (
            root
            / "bio_harness"
            / "pipeline_scripts"
            / "pydeseq2_wrapper.py"
        )
        if (
            counts_matrix is not None
            and counts_matrix.exists()
            and metadata_table.exists()
            and script_path.exists()
        ):
            counts_alias = _ensure_smoke_source_alias(
                root,
                counts_matrix,
                alias_parts=("deseq2_run", counts_matrix.name),
            )
            metadata_alias = _ensure_smoke_source_alias(
                root,
                metadata_table,
                alias_parts=("deseq2_run", metadata_table.name),
            )
            script_alias = _ensure_smoke_source_alias(
                root,
                script_path,
                alias_parts=("deseq2_run", script_path.name),
            )
            cases.append(
                QwenSkillSmokeCase(
                    name="deseq2_run",
                    description="Run PyDESeq2 differential expression from an existing counts matrix and sample metadata table.",
                    source_input=str(counts_alias),
                    prompt_template=(
                        "This is a direct one-step skill smoke test. Use only the deseq2_run tool with the wrapper "
                        "script at {script_path} to analyze the count matrix at {source_input} together with the "
                        "metadata table at {metadata_table}. Use design formula ~ condition and contrast "
                        "condition_Biofilm_vs_Plankton. Write outputs under {selected_dir}/deseq_results. Do not add "
                        "alignment, counting, or any other steps. Do not use bash_run."
                    ),
                    expected_tool="deseq2_run",
                    expected_outputs=("deseq_results/deseq2_results.tsv",),
                    prompt_context={
                        "metadata_table": str(metadata_alias),
                        "script_path": str(script_alias),
                    },
                )
            )

    cystic_fibrosis_selected_dir = None
    try:
        cystic_fibrosis_selected_dir = _find_latest_passing_selected_dir(
            root,
            "cystic-fibrosis",
        )
    except FileNotFoundError:
        cystic_fibrosis_selected_dir = None
    annotated_vcf = (
        cystic_fibrosis_selected_dir / "step1" / "snpeff_annotated.vcf"
        if cystic_fibrosis_selected_dir is not None
        else None
    )
    if annotated_vcf is not None and annotated_vcf.exists():
        annotated_alias = _ensure_smoke_source_alias(
            root,
            annotated_vcf,
            alias_parts=("snpeff_annotate", annotated_vcf.name),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="snpeff_annotate",
                description="Reuse an already ANN-annotated VCF through the snpEff annotation wrapper.",
                source_input=str(annotated_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the snpeff_annotate tool on the VCF at "
                    "{source_input} with genome database GRCh37.75 and write the annotated output VCF to "
                    "{selected_dir}/annotated/annotated.vcf. Do not add variant calling, filtering, or any other "
                    "steps. Do not use bash_run."
                ),
                expected_tool="snpeff_annotate",
                expected_outputs=("annotated/annotated.vcf",),
            )
        )

    return cases


def build_fourth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a fourth tranche of common non-benchmark cases supported by this machine."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    deseq_task_root = root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "deseq"
    deseq_task_data = deseq_task_root / "data"
    deseq_reference_root = deseq_task_root / "references"
    deseq_reads_1 = deseq_task_data / "SRR1278968_1.fastq"
    deseq_reads_2 = deseq_task_data / "SRR1278968_2.fastq"
    deseq_reference_fasta = (
        deseq_reference_root / "C_parapsilosis_CDC317_current_chromosomes.fasta"
    )
    deseq_reference_gff = (
        deseq_reference_root / "C_parapsilosis_CDC317_current_features.gff"
    )
    viral_task_data = (
        root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "viral-metagenomics"
        / "data"
    )
    transcript_quant_data = (
        root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "transcript-quant"
        / "data"
    )
    phylogenetics_data = (
        root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "phylogenetics"
        / "data"
    )
    phylogenetics_sequences = phylogenetics_data / "sequences.fasta"

    if _has_executable("cutadapt") and deseq_reads_1.exists() and deseq_reads_2.exists():
        reads_1_alias = _ensure_fastq_subset_alias(
            root,
            deseq_reads_1,
            alias_parts=("cutadapt_run", _smoke_alias_name(deseq_reads_1)),
        )
        reads_2_alias = _ensure_fastq_subset_alias(
            root,
            deseq_reads_2,
            alias_parts=("cutadapt_run", _smoke_alias_name(deseq_reads_2)),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="cutadapt_run",
                description="Trim paired-end RNA-seq reads with Cutadapt using explicit Illumina adapters.",
                source_input=str(reads_1_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the cutadapt_run tool to trim the paired-end "
                    "RNA-seq reads at {source_input} and {reads_2}. Use Illumina adapters "
                    "AGATCGGAAGAGCACACGTCTGAACTCCAGTCAC for read 1 and "
                    "AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT for read 2. Write trimmed reads to "
                    "{selected_dir}/trimmed/trimmed_R1.fastq.gz and {selected_dir}/trimmed/trimmed_R2.fastq.gz, and "
                    "write the JSON report to {selected_dir}/trimmed/cutadapt.json. Do not add any other tools or "
                    "workflow stages. Do not use bash_run."
                ),
                expected_tool="cutadapt_run",
                expected_outputs=(
                    "trimmed/trimmed_R1.fastq.gz",
                    "trimmed/trimmed_R2.fastq.gz",
                    "trimmed/cutadapt.json",
                ),
                prompt_context={"reads_2": str(reads_2_alias)},
            )
        )

    if _has_executable("fastp"):
        viral_reads_1 = viral_task_data / "sample_R1.fastq.gz"
        viral_reads_2 = viral_task_data / "sample_R2.fastq.gz"
        if viral_reads_1.exists() and viral_reads_2.exists():
            reads_1_alias = _ensure_fastq_subset_alias(
                root,
                viral_reads_1,
                alias_parts=("fastp_run", _smoke_alias_name(viral_reads_1)),
            )
            reads_2_alias = _ensure_fastq_subset_alias(
                root,
                viral_reads_2,
                alias_parts=("fastp_run", _smoke_alias_name(viral_reads_2)),
            )
            cases.append(
                QwenSkillSmokeCase(
                    name="fastp_run",
                    description="Trim paired-end metagenomic reads with fastp.",
                    source_input=str(reads_1_alias),
                    prompt_template=(
                        "This is a direct one-step skill smoke test. Use only the fastp_run tool to trim the "
                        "paired-end reads at {source_input} and {reads_2}. Write outputs to "
                        "{selected_dir}/trimmed/trimmed_R1.fastq.gz and {selected_dir}/trimmed/trimmed_R2.fastq.gz, "
                        "set minimum length to 30, and write the JSON report to {selected_dir}/trimmed/fastp.json. "
                        "Do not add any other tools or workflow stages. Do not use bash_run."
                    ),
                    expected_tool="fastp_run",
                    expected_outputs=(
                        "trimmed/trimmed_R1.fastq.gz",
                        "trimmed/trimmed_R2.fastq.gz",
                        "trimmed/fastp.json",
                    ),
                    prompt_context={"reads_2": str(reads_2_alias)},
                )
            )

    if (
        _has_executable("hisat2")
        and deseq_reads_1.exists()
        and deseq_reads_2.exists()
        and deseq_reference_fasta.exists()
    ):
        reads_1_alias = _ensure_fastq_subset_alias(
            root,
            deseq_reads_1,
            alias_parts=("hisat2_align", _smoke_alias_name(deseq_reads_1)),
        )
        reads_2_alias = _ensure_fastq_subset_alias(
            root,
            deseq_reads_2,
            alias_parts=("hisat2_align", _smoke_alias_name(deseq_reads_2)),
        )
        reference_alias = _ensure_smoke_source_alias(
            root,
            deseq_reference_fasta,
            alias_parts=("hisat2_align", deseq_reference_fasta.name),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="hisat2_align",
                description="Align one paired-end RNA-seq sample with HISAT2 using a local reference FASTA.",
                source_input=str(reads_1_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the hisat2_align tool to align the paired-end "
                    "reads at {source_input} and {reads_2} against the reference FASTA at {reference_fasta}. Build the "
                    "HISAT2 index under {selected_dir}/hisat2_index/genome if it does not already exist, and write the "
                    "alignment SAM to {selected_dir}/alignments/sample.sam. Do not add trimming, counting, or any other "
                    "workflow stages. Do not use bash_run."
                ),
                expected_tool="hisat2_align",
                expected_outputs=("alignments/sample.sam",),
                prompt_context={
                    "reads_2": str(reads_2_alias),
                    "reference_fasta": str(reference_alias),
                },
            )
        )

    if _has_executable("featurecounts") and _has_executable("subread"):
        sorted_bam = None
        for deseq_selected_dir in _iter_clean_selected_dirs(root, "deseq"):
            bam_candidates = sorted(
                path
                for path in (deseq_selected_dir / "alignments").glob("*.bam")
                if ".unsorted." not in path.name
            )
            if bam_candidates:
                sorted_bam = bam_candidates[0]
                break
        if sorted_bam is not None and deseq_reference_gff.exists():
            bam_alias = _ensure_smoke_source_alias(
                root,
                sorted_bam,
                alias_parts=("featurecounts_run", sorted_bam.name),
            )
            annotation_alias = _ensure_smoke_source_alias(
                root,
                deseq_reference_gff,
                alias_parts=("featurecounts_run", deseq_reference_gff.name),
            )
            cases.append(
                QwenSkillSmokeCase(
                    name="featurecounts_run",
                    description="Count aligned RNA-seq reads to genes with featureCounts.",
                    source_input=str(bam_alias),
                    prompt_template=(
                        "This is a direct one-step skill smoke test. Use only the featurecounts_run tool to count reads "
                        "from the coordinate-sorted paired-end BAM at {source_input} using the GFF annotation at "
                        "{annotation_gtf}. Treat this as paired-end data and count read pairs. Write the counts table "
                        "to {selected_dir}/counts/gene_counts.txt. Do not add alignment, normalization, or any other "
                        "steps. Do not use bash_run."
                    ),
                    expected_tool="featurecounts_run",
                    expected_outputs=("counts/gene_counts.txt",),
                    prompt_context={"annotation_gtf": str(annotation_alias)},
                )
            )

    transcriptome_fasta = transcript_quant_data / "transcriptome.fa"
    transcript_reads_1 = transcript_quant_data / "reads_1.fq.gz"
    transcript_reads_2 = transcript_quant_data / "reads_2.fq.gz"
    if (
        _has_executable("salmon")
        and transcriptome_fasta.exists()
        and transcript_reads_1.exists()
        and transcript_reads_2.exists()
    ):
        transcriptome_alias = _ensure_smoke_source_alias(
            root,
            transcriptome_fasta,
            alias_parts=("salmon_quant", transcriptome_fasta.name),
        )
        reads_1_alias = _ensure_fastq_subset_alias(
            root,
            transcript_reads_1,
            alias_parts=("salmon_quant", _smoke_alias_name(transcript_reads_1)),
        )
        reads_2_alias = _ensure_fastq_subset_alias(
            root,
            transcript_reads_2,
            alias_parts=("salmon_quant", _smoke_alias_name(transcript_reads_2)),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="salmon_quant",
                description="Quantify transcript abundance with Salmon from a local transcriptome FASTA.",
                source_input=str(reads_1_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the salmon_quant tool to quantify the paired-end "
                    "reads at {source_input} and {reads_2} against the transcriptome FASTA at {transcriptome_fasta}. "
                    "Build the Salmon index under {selected_dir}/salmon_index if it does not already exist, and write "
                    "quantification outputs under {selected_dir}/salmon_quant. Do not add trimming, gene counting, or any "
                    "other stages. Do not use bash_run."
                ),
                expected_tool="salmon_quant",
                expected_outputs=("salmon_quant/quant.sf",),
                prompt_context={
                    "reads_2": str(reads_2_alias),
                    "transcriptome_fasta": str(transcriptome_alias),
                },
            )
        )

    if (
        _has_executable("kallisto")
        and transcriptome_fasta.exists()
        and transcript_reads_1.exists()
        and transcript_reads_2.exists()
    ):
        transcriptome_alias = _ensure_smoke_source_alias(
            root,
            transcriptome_fasta,
            alias_parts=("kallisto_quant", transcriptome_fasta.name),
        )
        reads_1_alias = _ensure_fastq_subset_alias(
            root,
            transcript_reads_1,
            alias_parts=("kallisto_quant", _smoke_alias_name(transcript_reads_1)),
        )
        reads_2_alias = _ensure_fastq_subset_alias(
            root,
            transcript_reads_2,
            alias_parts=("kallisto_quant", _smoke_alias_name(transcript_reads_2)),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="kallisto_quant",
                description="Quantify transcript abundance with kallisto from a local transcriptome FASTA.",
                source_input=str(reads_1_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the kallisto_quant tool to quantify the "
                    "paired-end reads at {source_input} and {reads_2} against the transcriptome FASTA at "
                    "{transcriptome_fasta}. Build the kallisto index at {selected_dir}/kallisto_index/transcripts.idx if "
                    "it does not already exist, and write quantification outputs under {selected_dir}/kallisto_quant. Do "
                    "not add trimming, gene counting, or any other stages. Do not use bash_run."
                ),
                expected_tool="kallisto_quant",
                expected_outputs=("kallisto_quant/abundance.tsv",),
                prompt_context={
                    "reads_2": str(reads_2_alias),
                    "transcriptome_fasta": str(transcriptome_alias),
                },
            )
        )

    if _has_executable("bcftools") and _has_executable("samtools"):
        try:
            evolution_selected_dir = _find_latest_passing_selected_dir(root, "evolution")
        except FileNotFoundError:
            try:
                evolution_selected_dir = _find_latest_passing_selected_dir(
                    root,
                    "evolution",
                    require_validator_pass=False,
                )
            except FileNotFoundError:
                evolution_selected_dir = None
        reference_fasta = (
            evolution_selected_dir / "assembly" / "scaffolds.fasta"
            if evolution_selected_dir is not None
            else None
        )
        input_bam = (
            evolution_selected_dir / "alignments" / "anc_aligned.bam"
            if evolution_selected_dir is not None
            else None
        )
        if (
            reference_fasta is not None
            and input_bam is not None
            and reference_fasta.exists()
            and input_bam.exists()
        ):
            reference_alias = _ensure_smoke_source_alias(
                root,
                reference_fasta,
                alias_parts=("bcftools_call", reference_fasta.name),
            )
            bam_alias = _ensure_smoke_source_alias(
                root,
                input_bam,
                alias_parts=("bcftools_call", input_bam.name),
            )
            cases.append(
                QwenSkillSmokeCase(
                    name="bcftools_call",
                    description="Call variants from an existing BAM/reference pair with bcftools.",
                    source_input=str(bam_alias),
                    prompt_template=(
                        "This is a direct one-step skill smoke test. Use only the bcftools_call tool to call variants "
                        "from the aligned BAM at {source_input} against the reference FASTA at {reference_fasta}. Write "
                        "the compressed VCF to {selected_dir}/variants/anc_raw.vcf.gz. Do not add alignment, assembly, or "
                        "any other steps. Do not use bash_run."
                    ),
                    expected_tool="bcftools_call",
                    expected_outputs=(
                        "variants/anc_raw.vcf.gz",
                        "variants/anc_raw.vcf.gz.tbi",
                    ),
                    prompt_context={"reference_fasta": str(reference_alias)},
                )
            )

    if _has_executable("minimap2") and _has_executable("samtools"):
        viral_reference = (
            root
            / "workspace"
            / "benchmarks"
            / "bioagent-bench"
            / "official_runs"
            / "viral-metagenomics"
            / "replicate-1"
            / "viral_ref.fasta"
        )
        viral_reference_mmi = viral_reference.with_suffix(".mmi")
        viral_reads_1 = viral_task_data / "sample_R1.fastq.gz"
        viral_reads_2 = viral_task_data / "sample_R2.fastq.gz"
        if (
            viral_reference.exists()
            and viral_reference_mmi.exists()
            and viral_reads_1.exists()
            and viral_reads_2.exists()
        ):
            reference_alias = _ensure_smoke_source_alias(
                root,
                viral_reference,
                alias_parts=("minimap2_align", viral_reference.name),
            )
            cache_alias = _ensure_smoke_source_alias(
                root,
                viral_reference_mmi,
                alias_parts=("minimap2_align", viral_reference_mmi.name),
            )
            reads_1_alias = _ensure_fastq_subset_alias(
                root,
                viral_reads_1,
                alias_parts=("minimap2_align", _smoke_alias_name(viral_reads_1)),
            )
            reads_2_alias = _ensure_fastq_subset_alias(
                root,
                viral_reads_2,
                alias_parts=("minimap2_align", _smoke_alias_name(viral_reads_2)),
            )
            cases.append(
                QwenSkillSmokeCase(
                    name="minimap2_align",
                    description="Align paired-end viral reads with minimap2 using an existing cached index.",
                    source_input=str(reads_1_alias),
                    prompt_template=(
                        "This is a direct one-step skill smoke test. Use only the minimap2_align tool with preset sr to "
                        "align the paired-end reads at {source_input} and {reads_2} against the viral reference FASTA at "
                        "{reference_fasta}. Reuse the existing minimap2 index at {cache_index_path}. Write the sorted BAM "
                        "to {selected_dir}/alignments/viral.bam. Do not add trimming, taxonomic classification, or any "
                        "other steps. Do not use bash_run."
                    ),
                    expected_tool="minimap2_align",
                    expected_outputs=(
                        "alignments/viral.bam",
                        "alignments/viral.bam.bai",
                    ),
                    prompt_context={
                        "reads_2": str(reads_2_alias),
                        "reference_fasta": str(reference_alias),
                        "cache_index_path": str(cache_alias),
                    },
                )
            )

    if _has_executable("mafft") and _has_executable("iqtree") and phylogenetics_sequences.exists():
        phylogenetics_alias = _ensure_smoke_source_alias(
            root,
            phylogenetics_sequences,
            alias_parts=("phylogenetics_workflow", phylogenetics_sequences.name),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="phylogenetics_workflow",
                description="Align an unaligned FASTA with MAFFT, then infer a tree with IQ-TREE.",
                source_input=str(phylogenetics_alias),
                prompt_template=(
                    "Infer a phylogenetic tree from the unaligned FASTA at {source_input}. Align the sequences with "
                    "MAFFT into {selected_dir}/aligned_sequences.fasta, then infer the tree with IQ-TREE and write the "
                    "final Newick tree to {selected_dir}/final/phylogeny.treefile. Use model MFP, seed 42, and 2 "
                    "threads, keep working outputs under {selected_dir}, and do not add any other workflow stages. Do "
                    "not use bash_run."
                ),
                expected_tool="phylogenetics_iqtree_style",
                expected_tools=("mafft_align", "phylogenetics_iqtree_style"),
                expected_outputs=(
                    "aligned_sequences.fasta",
                    "final/phylogeny.treefile",
                ),
            )
        )

    if _has_executable("iqtree"):
        alignment_fasta = None
        for phylo_selected_dir in _iter_clean_selected_dirs(
            root,
            "phylogenetics",
            require_validator_pass=False,
        ):
            for candidate_name in ("aligned_sequences.fasta", "aligned.fasta"):
                candidate = phylo_selected_dir / candidate_name
                if candidate.exists():
                    alignment_fasta = candidate
                    break
            if alignment_fasta is not None:
                break
        if alignment_fasta is None:
            alignment_fasta = phylogenetics_data / "sequences.fasta"
        if alignment_fasta.exists():
            alignment_alias = _ensure_smoke_source_alias(
                root,
                alignment_fasta,
                alias_parts=("phylogenetics_iqtree_style", alignment_fasta.name),
            )
            cases.append(
                QwenSkillSmokeCase(
                    name="phylogenetics_iqtree_style",
                    description="Infer a phylogenetic tree from an aligned FASTA with IQ-TREE.",
                    source_input=str(alignment_alias),
                    prompt_template=(
                        "This is a direct one-step skill smoke test. Use only the phylogenetics_iqtree_style tool to "
                        "infer a tree from the aligned FASTA at {source_input}. Write working outputs under "
                        "{selected_dir}/phylo, use output prefix {selected_dir}/phylo/tree, and copy the final Newick tree "
                        "to {selected_dir}/phylo/final/tree.nwk. Use model MFP, seed 42, and 2 threads. Do not add any "
                        "other tools or workflow stages. Do not use bash_run."
                    ),
                    expected_tool="phylogenetics_iqtree_style",
                    expected_outputs=("phylo/final/tree.nwk",),
                )
            )

    return cases


def build_fifth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a fifth tranche of high-value machine-supported cases."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    deseq_task_root = root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "deseq"
    deseq_task_data = deseq_task_root / "data"
    deseq_reference_root = deseq_task_root / "references"
    deseq_reads_1 = deseq_task_data / "SRR1278968_1.fastq"
    deseq_reads_2 = deseq_task_data / "SRR1278968_2.fastq"
    deseq_reference_fasta = (
        deseq_reference_root / "C_parapsilosis_CDC317_current_chromosomes.fasta"
    )

    if (
        _has_executable("bwa")
        and _has_executable("samtools")
        and deseq_reads_1.exists()
        and deseq_reads_2.exists()
        and deseq_reference_fasta.exists()
    ):
        reference_alias = _ensure_smoke_source_alias(
            root,
            deseq_reference_fasta,
            alias_parts=("bwa_mem_align", deseq_reference_fasta.name),
        )
        reads_1_alias = _ensure_fastq_subset_alias(
            root,
            deseq_reads_1,
            alias_parts=("bwa_mem_align", _smoke_alias_name(deseq_reads_1)),
        )
        reads_2_alias = _ensure_fastq_subset_alias(
            root,
            deseq_reads_2,
            alias_parts=("bwa_mem_align", _smoke_alias_name(deseq_reads_2)),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="bwa_mem_align",
                description="Align one paired-end short-read sample with BWA MEM and build a cached index if needed.",
                source_input=str(reads_1_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the bwa_mem_align tool to align the paired-end "
                    "reads at {source_input} and {reads_2} against the reference FASTA at {reference_fasta}. Build the "
                    "cached BWA index under {selected_dir}/bwa_index/genome if it does not already exist, and write the "
                    "sorted BAM to {selected_dir}/alignments/sample.bam. Do not add counting or any other workflow "
                    "stages. Do not use bash_run."
                ),
                expected_tool="bwa_mem_align",
                expected_outputs=("alignments/sample.bam", "alignments/sample.bam.bai"),
                prompt_context={
                    "reads_2": str(reads_2_alias),
                    "reference_fasta": str(reference_alias),
                },
            )
        )

    if (
        _has_executable("bowtie2")
        and _has_executable("bowtie2-build")
        and _has_executable("samtools")
        and deseq_reads_1.exists()
        and deseq_reads_2.exists()
        and deseq_reference_fasta.exists()
    ):
        reference_alias = _ensure_smoke_source_alias(
            root,
            deseq_reference_fasta,
            alias_parts=("bowtie2_align", deseq_reference_fasta.name),
        )
        reads_1_alias = _ensure_fastq_subset_alias(
            root,
            deseq_reads_1,
            alias_parts=("bowtie2_align", _smoke_alias_name(deseq_reads_1)),
        )
        reads_2_alias = _ensure_fastq_subset_alias(
            root,
            deseq_reads_2,
            alias_parts=("bowtie2_align", _smoke_alias_name(deseq_reads_2)),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="bowtie2_align",
                description="Align one paired-end short-read sample with Bowtie2 and build an index if needed.",
                source_input=str(reads_1_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the bowtie2_align tool to align the paired-end "
                    "reads at {source_input} and {reads_2} against the reference FASTA at {reference_fasta}. Build the "
                    "Bowtie2 index under {selected_dir}/bowtie2_index/genome if it does not already exist, and write the "
                    "sorted BAM to {selected_dir}/alignments/sample.bam. Do not add counting or any other workflow "
                    "stages. Do not use bash_run."
                ),
                expected_tool="bowtie2_align",
                expected_outputs=("alignments/sample.bam", "alignments/sample.bam.bai"),
                prompt_context={
                    "reads_2": str(reads_2_alias),
                    "reference_fasta": str(reference_alias),
                },
            )
        )

    if (
        _has_executable("subread")
        and _has_executable("featurecounts")
        and _has_executable("samtools")
        and deseq_reads_1.exists()
        and deseq_reads_2.exists()
        and deseq_reference_fasta.exists()
    ):
        reference_alias = _ensure_smoke_source_alias(
            root,
            deseq_reference_fasta,
            alias_parts=("subread_align", deseq_reference_fasta.name),
        )
        reads_1_alias = _ensure_fastq_subset_alias(
            root,
            deseq_reads_1,
            alias_parts=("subread_align", _smoke_alias_name(deseq_reads_1)),
        )
        reads_2_alias = _ensure_fastq_subset_alias(
            root,
            deseq_reads_2,
            alias_parts=("subread_align", _smoke_alias_name(deseq_reads_2)),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="subread_align",
                description="Align one paired-end RNA-seq sample with Subread/Subjunc and build an index if needed.",
                source_input=str(reads_1_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the subread_align tool to align the paired-end "
                    "reads at {source_input} and {reads_2} against the reference FASTA at {reference_fasta}. Build the "
                    "Subread index under {selected_dir}/subread_index/genome if it does not already exist, and write the "
                    "sorted BAM to {selected_dir}/alignments/sample.bam. Do not add counting or any other workflow "
                    "stages. Do not use bash_run."
                ),
                expected_tool="subread_align",
                expected_outputs=("alignments/sample.bam", "alignments/sample.bam.bai"),
                prompt_context={
                    "reads_2": str(reads_2_alias),
                    "reference_fasta": str(reference_alias),
                },
            )
        )

    if _has_executable("gatk") and _has_executable("samtools"):
        try:
            evolution_selected_dir = _find_latest_passing_selected_dir(root, "evolution")
        except FileNotFoundError:
            try:
                evolution_selected_dir = _find_latest_passing_selected_dir(
                    root,
                    "evolution",
                    require_validator_pass=False,
                )
            except FileNotFoundError:
                evolution_selected_dir = None
        reference_fasta = (
            evolution_selected_dir / "assembly" / "scaffolds.fasta"
            if evolution_selected_dir is not None
            else None
        )
        input_bam = (
            evolution_selected_dir / "alignments" / "anc_aligned.bam"
            if evolution_selected_dir is not None
            else None
        )
        if (
            reference_fasta is not None
            and input_bam is not None
            and reference_fasta.exists()
            and input_bam.exists()
        ):
            reference_alias = _ensure_smoke_source_alias(
                root,
                reference_fasta,
                alias_parts=("gatk_haplotypecaller", reference_fasta.name),
            )
            bam_alias = _ensure_smoke_source_alias(
                root,
                input_bam,
                alias_parts=("gatk_haplotypecaller", input_bam.name),
            )
            cases.append(
                QwenSkillSmokeCase(
                    name="gatk_haplotypecaller",
                    description="Call variants from an existing BAM/reference pair with GATK HaplotypeCaller.",
                    source_input=str(bam_alias),
                    prompt_template=(
                        "This is a direct one-step skill smoke test. Use only the gatk_haplotypecaller tool to call "
                        "variants from the aligned BAM at {source_input} against the reference FASTA at {reference_fasta}. "
                        "Write the VCF to {selected_dir}/variants/anc_raw.vcf. Do not add alignment, assembly, or any "
                        "other steps. Do not use bash_run."
                    ),
                    expected_tool="gatk_haplotypecaller",
                    expected_outputs=("variants/anc_raw.vcf",),
                    prompt_context={"reference_fasta": str(reference_alias)},
                )
            )

    star_bin = _resolve_star_binary(root)
    if star_bin and deseq_reads_1.exists() and deseq_reads_2.exists():
        deseq_runs_root = (
            root / "workspace" / "benchmarks" / "bioagent-bench" / "official_runs" / "deseq"
        )
        try:
            star_index_parent = _find_latest_directory_with_child(
                deseq_runs_root,
                child_name="star_index",
            )
        except FileNotFoundError:
            star_index_parent = None
        if star_index_parent is not None:
            star_index = star_index_parent / "star_index"
            if star_index.exists():
                star_index_alias = _ensure_smoke_source_alias(
                    root,
                    star_index,
                    alias_parts=("star_2pass_align", "genome_dir"),
                )
                reads_1_alias = _ensure_fastq_subset_alias(
                    root,
                    deseq_reads_1,
                    alias_parts=("star_2pass_align", _smoke_alias_name(deseq_reads_1)),
                )
                reads_2_alias = _ensure_fastq_subset_alias(
                    root,
                    deseq_reads_2,
                    alias_parts=("star_2pass_align", _smoke_alias_name(deseq_reads_2)),
                )
                cases.append(
                    QwenSkillSmokeCase(
                        name="star_2pass_align",
                        description="Align one paired-end RNA-seq sample with an existing STAR genome dir in 2-pass mode.",
                        source_input=str(reads_1_alias),
                        prompt_template=(
                            "This is a direct one-step skill smoke test. Use only the star_2pass_align tool to align the "
                            "paired-end reads at {source_input} and {reads_2} against the existing STAR genome index at "
                            "{genome_dir}. Write outputs under {selected_dir}/star2_output/ using output prefix "
                            "{selected_dir}/star2_output/sample_. Do not add counting or any other workflow stages. Do "
                            "not use bash_run."
                        ),
                        expected_tool="star_2pass_align",
                        expected_outputs=(
                            "star2_output/sample_Aligned.out.bam",
                            "star2_output/sample_Log.final.out",
                        ),
                        prompt_context={
                            "reads_2": str(reads_2_alias),
                            "genome_dir": str(star_index_alias),
                        },
                    )
                )

    return cases


def build_sixth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a sixth tranche focused on a real single-cell harness lane."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    single_cell_data = (
        root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "single-cell"
        / "data"
    )
    reads_1 = single_cell_data / "sample_R1.fastq.gz"
    reads_2 = single_cell_data / "sample_R2.fastq.gz"
    whitelist = single_cell_data / "barcodes_whitelist.txt"
    reference_fasta = single_cell_data / "reference.fa"
    annotation_gtf = single_cell_data / "annotation.gtf"

    if (
        requirement_available("scanpy")
        and reads_1.exists()
        and reads_2.exists()
        and whitelist.exists()
        and reference_fasta.exists()
        and annotation_gtf.exists()
    ):
        reads_1_alias = _ensure_fastq_subset_alias(
            root,
            reads_1,
            alias_parts=("sc_count_and_cluster", _smoke_alias_name(reads_1)),
        )
        reads_2_alias = _ensure_fastq_subset_alias(
            root,
            reads_2,
            alias_parts=("sc_count_and_cluster", _smoke_alias_name(reads_2)),
        )
        whitelist_alias = _ensure_smoke_source_alias(
            root,
            whitelist,
            alias_parts=("sc_count_and_cluster", whitelist.name),
        )
        reference_alias = _ensure_smoke_source_alias(
            root,
            reference_fasta,
            alias_parts=("sc_count_and_cluster", reference_fasta.name),
        )
        gtf_alias = _ensure_smoke_source_alias(
            root,
            annotation_gtf,
            alias_parts=("sc_count_and_cluster", annotation_gtf.name),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="sc_count_and_cluster",
                description="Run the integrated single-cell counting and clustering pipeline on bundled 10x-style reads.",
                source_input=str(reads_1_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the sc_count_and_cluster tool to process the "
                    "single-cell reads at {source_input} and {reads_2} together with the whitelist at {whitelist}, "
                    "reference FASTA at {reference_fasta}, and GTF at {annotation_gtf}. Write outputs under "
                    "{selected_dir}/sc_output. Use barcode_len 16, umi_len 12, kmer_size 25, min_genes 3, min_cells 1, "
                    "leiden_resolution 0.3, and n_hvgs 100. Do not add any other tools or workflow stages. Do not use "
                    "bash_run."
                ),
                expected_tool="sc_count_and_cluster",
                expected_outputs=(
                    "sc_output/adata.h5ad",
                    "sc_output/cluster_assignments.json",
                    "sc_output/marker_genes.json",
                    "sc_output/raw_counts.json",
                ),
                prompt_context={
                    "reads_2": str(reads_2_alias),
                    "whitelist": str(whitelist_alias),
                    "reference_fasta": str(reference_alias),
                    "annotation_gtf": str(gtf_alias),
                },
            )
        )

    return cases


def build_seventh_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a seventh tranche for a real tumor-normal Mutect2 lane."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if _has_executable("gatk") and _has_executable("samtools"):
        try:
            evolution_selected_dir = _find_latest_passing_selected_dir(root, "evolution")
        except FileNotFoundError:
            try:
                evolution_selected_dir = _find_latest_passing_selected_dir(
                    root,
                    "evolution",
                    require_validator_pass=False,
                )
            except FileNotFoundError:
                evolution_selected_dir = None
        reference_fasta = (
            evolution_selected_dir / "assembly" / "scaffolds.fasta"
            if evolution_selected_dir is not None
            else None
        )
        normal_bam = (
            evolution_selected_dir / "alignments" / "anc_aligned.bam"
            if evolution_selected_dir is not None
            else None
        )
        tumor_bam = (
            evolution_selected_dir / "alignments" / "evol1_aligned.bam"
            if evolution_selected_dir is not None
            else None
        )
        if (
            reference_fasta is not None
            and normal_bam is not None
            and tumor_bam is not None
            and reference_fasta.exists()
            and normal_bam.exists()
            and tumor_bam.exists()
        ):
            reference_alias = _ensure_smoke_source_alias(
                root,
                reference_fasta,
                alias_parts=("gatk_mutect2_call", reference_fasta.name),
            )
            normal_alias = _ensure_smoke_source_alias(
                root,
                normal_bam,
                alias_parts=("gatk_mutect2_call", normal_bam.name),
            )
            tumor_alias = _ensure_smoke_source_alias(
                root,
                tumor_bam,
                alias_parts=("gatk_mutect2_call", tumor_bam.name),
            )
            cases.append(
                QwenSkillSmokeCase(
                    name="gatk_mutect2_call",
                    description="Call somatic variants from a real tumor-normal BAM pair with GATK Mutect2.",
                    source_input=str(tumor_alias),
                    prompt_template=(
                        "This is a direct one-step skill smoke test. Use only the gatk_mutect2_call tool to call "
                        "somatic variants from the tumor BAM at {source_input} with tumor sample name evol1 and the "
                        "matched normal BAM at {normal_bam} with normal sample name anc, using the reference FASTA at "
                        "{reference_fasta}. Write the output VCF to {selected_dir}/somatic/evol1_vs_anc.mutect2.vcf.gz. "
                        "Do not add alignment, filtering, or any other workflow stages. Do not use bash_run."
                    ),
                    expected_tool="gatk_mutect2_call",
                    expected_outputs=("somatic/evol1_vs_anc.mutect2.vcf.gz",),
                    prompt_context={
                        "normal_bam": str(normal_alias),
                        "reference_fasta": str(reference_alias),
                    },
                )
            )

    return cases


def build_eighth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build an eighth tranche for STARsolo with deterministic STAR index setup."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    single_cell_data = (
        root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "single-cell"
        / "data"
    )
    reads_1 = single_cell_data / "sample_R1.fastq.gz"
    reads_2 = single_cell_data / "sample_R2.fastq.gz"
    whitelist = single_cell_data / "barcodes_whitelist.txt"
    reference_fasta = single_cell_data / "reference.fa"
    annotation_gtf = single_cell_data / "annotation.gtf"

    if (
        _has_executable("star")
        and reads_1.exists()
        and reads_2.exists()
        and whitelist.exists()
        and reference_fasta.exists()
        and annotation_gtf.exists()
    ):
        reads_1_alias = _ensure_fastq_subset_alias(
            root,
            reads_1,
            alias_parts=("star_solo_count", _smoke_alias_name(reads_1)),
        )
        reads_2_alias = _ensure_fastq_subset_alias(
            root,
            reads_2,
            alias_parts=("star_solo_count", _smoke_alias_name(reads_2)),
        )
        whitelist_alias = _ensure_smoke_source_alias(
            root,
            whitelist,
            alias_parts=("star_solo_count", whitelist.name),
        )
        reference_alias = _ensure_smoke_source_alias(
            root,
            reference_fasta,
            alias_parts=("star_solo_count", reference_fasta.name),
        )
        gtf_alias = _ensure_smoke_source_alias(
            root,
            annotation_gtf,
            alias_parts=("star_solo_count", annotation_gtf.name),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="star_solo_count",
                description="Run STARsolo on bundled single-cell reads and build the STAR genome dir deterministically if needed.",
                source_input=str(reads_1_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the star_solo_count tool to process the "
                    "single-cell reads at {source_input} and {reads_2} with the whitelist at {whitelist}. Build the STAR "
                    "genome dir under {selected_dir}/star_index from the reference FASTA at {reference_fasta} and GTF at "
                    "{annotation_gtf} if it does not already exist, using cache root {selected_dir}/_cache/star_indexes. "
                    "Write STARsolo outputs with prefix {selected_dir}/solo/sample_. Use 2 threads. Do not add any other "
                    "tools or workflow stages. Do not use bash_run."
                ),
                expected_tool="star_solo_count",
                expected_outputs=(
                    "solo/sample_Aligned.out.bam",
                    "solo/sample_Solo.out/Gene/raw/matrix.mtx",
                    "solo/sample_Solo.out/Gene/raw/barcodes.tsv",
                    "solo/sample_Solo.out/Gene/raw/features.tsv",
                ),
                prompt_context={
                    "reads_2": str(reads_2_alias),
                    "whitelist": str(whitelist_alias),
                    "reference_fasta": str(reference_alias),
                    "annotation_gtf": str(gtf_alias),
                },
            )
        )

    return cases


def build_ninth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a ninth tranche for deterministic Scanpy processing of pre-counted single-cell input."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if not requirement_available("scanpy"):
        return cases

    source_h5ad = (
        root
        / "workspace"
        / "skill_smoke"
        / "qwen_skill_smoke_sixth_live_r2"
        / "sc_count_and_cluster"
        / "sc_output"
        / "adata.h5ad"
    )
    if not source_h5ad.exists():
        return cases

    input_alias = _ensure_smoke_source_alias(
        root,
        source_h5ad,
        alias_parts=("scanpy_workflow", source_h5ad.name),
    )
    cases.append(
        QwenSkillSmokeCase(
            name="scanpy_workflow",
            description="Run the deterministic Scanpy workflow on an existing AnnData artifact.",
            source_input=str(input_alias),
            prompt_template=(
                "This is a direct one-step skill smoke test. Use only the scanpy_workflow tool to process the AnnData "
                "file at {source_input}. Write outputs under {selected_dir}/scanpy_output, use min_genes 3, min_cells 1, "
                "max_mito_pct 100, n_hvgs 48, and leiden_resolution 0.3. Do not add any other tools or workflow stages. "
                "Do not use bash_run."
            ),
            expected_tool="scanpy_workflow",
            expected_outputs=(
                "scanpy_output/processed.h5ad",
                "scanpy_output/cluster_assignments.csv",
                "scanpy_output/marker_genes.csv",
                "scanpy_output/summary.json",
            ),
        )
    )
    return cases


def build_tenth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a tenth tranche for deterministic VEP annotation with local GFF/FASTA support."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    variant_annotation_data = (
        root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "variant-annotation"
        / "data"
    )
    input_vcf = variant_annotation_data / "input_variants.vcf"
    reference_fasta = variant_annotation_data / "reference.fa"
    annotation_gff = variant_annotation_data / "genes.gff"

    if (
        requirement_available("vep")
        and input_vcf.exists()
        and reference_fasta.exists()
        and annotation_gff.exists()
    ):
        input_alias = _ensure_smoke_source_alias(
            root,
            input_vcf,
            alias_parts=("vep_annotate", input_vcf.name),
        )
        reference_alias = _ensure_smoke_source_alias(
            root,
            reference_fasta,
            alias_parts=("vep_annotate", reference_fasta.name),
        )
        annotation_alias = _ensure_smoke_source_alias(
            root,
            annotation_gff,
            alias_parts=("vep_annotate", annotation_gff.name),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="vep_annotate",
                description="Annotate a small synthetic VCF with Ensembl VEP using a local GFF3 and FASTA reference bundle.",
                source_input=str(input_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the vep_annotate tool to annotate the VCF at "
                    "{source_input} with the local GFF annotation at {annotation_gff} and reference FASTA at "
                    "{reference_fasta}. Treat this as a custom species annotation run and write the annotated VCF to "
                    "{selected_dir}/annotated/annotated.vcf. Do not add filtering or any other workflow stages. Do not "
                    "use bash_run."
                ),
                expected_tool="vep_annotate",
                expected_outputs=("annotated/annotated.vcf",),
                prompt_context={
                    "annotation_gff": str(annotation_alias),
                    "reference_fasta": str(reference_alias),
                },
            )
        )
    return cases


def build_eleventh_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build an eleventh tranche for Prokka annotation on a small bacterial FASTA."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    input_fasta = (
        root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "variant-annotation"
        / "data"
        / "reference.fa"
    )

    if requirement_available("prokka") and input_fasta.exists():
        input_alias = _ensure_smoke_source_alias(
            root,
            input_fasta,
            alias_parts=("prokka_smoke", input_fasta.name),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="prokka_annotate",
                description="Annotate a small bacterial FASTA with Prokka through the isolated launcher path.",
                source_input=str(input_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the prokka_annotate tool to annotate the "
                    "FASTA at {source_input} as a bacterial genome. Write outputs under {selected_dir}/annot using "
                    "sample prefix sample1 and use 1 CPU. Do not add any other tools or workflow stages. Do not use "
                    "bash_run."
                ),
                expected_tool="prokka_annotate",
                expected_outputs=(
                    "annot/sample1.gff",
                    "annot/sample1.faa",
                    "annot/sample1.gbk",
                ),
            )
        )
    return cases


def build_twelfth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a twelfth tranche for deterministic rMATS splicing analysis."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if not (_has_executable("rmats") and _has_executable("samtools")):
        return cases

    try:
        deseq_selected_dir = _find_latest_passing_selected_dir(root, "deseq")
    except FileNotFoundError:
        return cases

    alignments_dir = deseq_selected_dir / "alignments"
    group1_bam = alignments_dir / "SRR1278968.bam"
    group2_bam = alignments_dir / "SRR1278971.bam"
    gff_path = (
        root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "deseq"
        / "references"
        / "C_parapsilosis_CDC317_current_features.gff"
    )
    if not (group1_bam.exists() and group2_bam.exists() and gff_path.exists()):
        return cases

    group1_alias = _ensure_bam_subset_alias(
        root,
        group1_bam,
        alias_parts=("rmats_run", "SRR1278968.smoke.bam"),
    )
    group2_alias = _ensure_bam_subset_alias(
        root,
        group2_bam,
        alias_parts=("rmats_run", "SRR1278971.smoke.bam"),
    )
    gtf_alias = _ensure_gff_to_gtf_alias(
        root,
        gff_path,
        alias_parts=("rmats_run", "C_parapsilosis_CDC317_current_features.smoke.gtf"),
    )
    cases.append(
        QwenSkillSmokeCase(
            name="rmats_run",
            description="Run rMATS alternative splicing analysis on small grouped BAM subsets with a converted GTF annotation.",
            source_input=str(group1_alias),
            prompt_template=(
                "This is a direct one-step skill smoke test. Use only the rmats_run tool to compare the grouped BAMs "
                "at {source_input} and {group2_bams} using the annotation GTF at {annotation_gtf}. Write outputs to "
                "{selected_dir}/rmats_out with temporary files under {selected_dir}/rmats_tmp. Use read length 100 and "
                "2 threads. Do not add alignment, counting, or any other workflow stages. Do not use bash_run."
            ),
            expected_tool="rmats_run",
            expected_outputs=(
                "rmats_out/SE.MATS.JC.txt",
                "rmats_out/SE.MATS.JCEC.txt",
                "rmats_out/summary.txt",
            ),
            prompt_context={
                "group2_bams": str(group2_alias),
                "annotation_gtf": str(gtf_alias),
            },
        )
    )
    return cases


def build_thirteenth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a thirteenth tranche for deterministic DEXSeq exon-usage analysis."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if not requirement_available("dexseq"):
        return cases

    counts_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("dexseq_run", "dexseq_counts.tsv"),
        content=(
            "gene_id\texon_id\tcontrol_rep1\tcontrol_rep2\ttreated_rep1\ttreated_rep2\n"
            "geneA\tE001\t100\t102\t101\t99\n"
            "geneA\tE002\t60\t58\t12\t10\n"
            "geneB\tE001\t80\t82\t79\t81\n"
            "geneB\tE002\t40\t41\t42\t39\n"
        ),
    )
    metadata_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("dexseq_run", "dexseq_metadata.tsv"),
        content=(
            "sample\tcondition\n"
            "control_rep1\tcontrol\n"
            "control_rep2\tcontrol\n"
            "treated_rep1\ttreated\n"
            "treated_rep2\ttreated\n"
        ),
    )
    cases.append(
        QwenSkillSmokeCase(
            name="dexseq_run",
            description="Run DEXSeq exon-usage analysis on a small synthetic exon count matrix with matched metadata.",
            source_input=str(counts_alias),
            prompt_template=(
                "This is a direct one-step skill smoke test. Use only the dexseq_run tool on the exon count matrix "
                "at {source_input} with metadata at {metadata_table}. Use design formula "
                "\"~ sample + exon + condition:exon\" and contrast "
                "\"condition_treated_vs_control\". Write outputs under {selected_dir}/dexseq_out. "
                "Do not add any other tools or workflow stages. Do not use bash_run."
            ),
            expected_tool="dexseq_run",
            expected_outputs=("dexseq_out/dexseq_results.tsv",),
            prompt_context={"metadata_table": str(metadata_alias)},
        )
    )
    return cases


def build_fourteenth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a fourteenth tranche for deterministic edgeR differential expression."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if not requirement_available("edger"):
        return cases

    counts_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("edger_run", "edger_counts.tsv"),
        content=(
            "gene_id\tcontrol_rep1\tcontrol_rep2\ttreated_rep1\ttreated_rep2\n"
            "geneA\t100\t105\t210\t220\n"
            "geneB\t85\t87\t83\t84\n"
            "geneC\t40\t39\t8\t10\n"
            "geneD\t15\t17\t16\t15\n"
        ),
    )
    metadata_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("edger_run", "edger_metadata.tsv"),
        content=(
            "sample\tcondition\n"
            "control_rep1\tcontrol\n"
            "control_rep2\tcontrol\n"
            "treated_rep1\ttreated\n"
            "treated_rep2\ttreated\n"
        ),
    )
    cases.append(
        QwenSkillSmokeCase(
            name="edger_run",
            description="Run edgeR differential expression on a small synthetic gene count matrix with matched metadata.",
            source_input=str(counts_alias),
            prompt_template=(
                "This is a direct one-step skill smoke test. Use only the edger_run tool on the gene count matrix "
                "at {source_input} with metadata at {metadata_table}. Use design formula \"~ condition\" and contrast "
                "\"condition_treated_vs_control\". Write outputs under {selected_dir}/edger_out. "
                "Do not add any other tools or workflow stages. Do not use bash_run."
            ),
            expected_tool="edger_run",
            expected_outputs=("edger_out/edger_results.tsv",),
            prompt_context={"metadata_table": str(metadata_alias)},
        )
    )
    return cases


def build_fifteenth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a fifteenth tranche for deterministic limma-voom differential expression."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if not requirement_available("limma"):
        return cases

    counts_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("limma_voom_run", "limma_counts.tsv"),
        content=(
            "gene_id\tcontrol_rep1\tcontrol_rep2\ttreated_rep1\ttreated_rep2\n"
            "geneA\t120\t118\t260\t255\n"
            "geneB\t90\t92\t88\t87\n"
            "geneC\t35\t33\t6\t7\n"
            "geneD\t14\t13\t15\t16\n"
        ),
    )
    metadata_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("limma_voom_run", "limma_metadata.tsv"),
        content=(
            "sample\tcondition\n"
            "control_rep1\tcontrol\n"
            "control_rep2\tcontrol\n"
            "treated_rep1\ttreated\n"
            "treated_rep2\ttreated\n"
        ),
    )
    cases.append(
        QwenSkillSmokeCase(
            name="limma_voom_run",
            description="Run limma-voom differential expression on a small synthetic gene count matrix with matched metadata.",
            source_input=str(counts_alias),
            prompt_template=(
                "This is a direct one-step skill smoke test. Use only the limma_voom_run tool on the gene count matrix "
                "at {source_input} with metadata at {metadata_table}. Use design formula \"~ condition\" and contrast "
                "\"condition_treated_vs_control\". Write outputs under {selected_dir}/limma_out. "
                "Do not add any other tools or workflow stages. Do not use bash_run."
            ),
            expected_tool="limma_voom_run",
            expected_outputs=("limma_out/limma_voom_results.tsv",),
            prompt_context={"metadata_table": str(metadata_alias)},
        )
    )
    return cases


def build_sixteenth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a sixteenth tranche for deterministic Seurat single-cell analysis."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if not requirement_available("seurat"):
        return cases

    matrix_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("seurat_rscript_workflow", "seurat_counts.tsv"),
        content=(
            "gene_id\tcell1\tcell2\tcell3\tcell4\tcell5\tcell6\tcell7\tcell8\n"
            "GeneA\t10\t11\t12\t13\t40\t41\t42\t43\n"
            "GeneB\t8\t7\t9\t8\t32\t31\t33\t34\n"
            "GeneC\t2\t1\t3\t2\t20\t21\t18\t19\n"
            "GeneD\t15\t14\t16\t15\t5\t6\t4\t5\n"
            "GeneE\t13\t12\t14\t13\t6\t7\t5\t6\n"
            "GeneF\t1\t2\t1\t2\t15\t14\t16\t15\n"
            "GeneG\t9\t10\t8\t9\t10\t11\t9\t10\n"
            "GeneH\t7\t8\t7\t8\t7\t8\t7\t8\n"
            "GeneI\t5\t4\t6\t5\t11\t12\t10\t11\n"
            "GeneJ\t3\t3\t4\t3\t13\t14\t12\t13\n"
        ),
    )
    metadata_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("seurat_rscript_workflow", "seurat_metadata.tsv"),
        content=(
            "cell\tcondition\tbatch\n"
            "cell1\tcontrol\tbatch1\n"
            "cell2\tcontrol\tbatch1\n"
            "cell3\tcontrol\tbatch1\n"
            "cell4\tcontrol\tbatch1\n"
            "cell5\ttreated\tbatch2\n"
            "cell6\ttreated\tbatch2\n"
            "cell7\ttreated\tbatch2\n"
            "cell8\ttreated\tbatch2\n"
        ),
    )
    cases.append(
        QwenSkillSmokeCase(
            name="seurat_rscript_workflow",
            description="Run a deterministic Seurat workflow on a small synthetic single-cell count matrix with metadata.",
            source_input=str(matrix_alias),
            prompt_template=(
                "This is a direct one-step skill smoke test. Use only the seurat_rscript_workflow tool on the "
                "single-cell count matrix at {source_input} with metadata at {metadata_table}. Write outputs under "
                "{selected_dir}/seurat_output. Do not add any other tools or workflow stages. Do not use bash_run."
            ),
            expected_tool="seurat_rscript_workflow",
            expected_outputs=(
                "seurat_output/seurat_object.rds",
                "seurat_output/pca_embeddings.csv",
                "seurat_output/cell_metadata.csv",
                "seurat_output/summary.json",
            ),
            prompt_context={"metadata_table": str(metadata_alias)},
        )
    )
    return cases


def build_seventeenth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a seventeenth tranche for MACS2 ChIP-seq peak calling."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if not (_has_executable("macs2") and _has_executable("samtools")):
        return cases

    evolution_runs_root = (
        root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "evolution"
    )
    treatment_bam: Path | None = None
    control_bam: Path | None = None
    for alignments_dir in sorted(evolution_runs_root.glob("*/alignments"), key=lambda p: (p.stat().st_mtime, str(p)), reverse=True):
        candidates = (
            alignments_dir / "evol1_aligned.bam",
            alignments_dir / "evol1.bam",
        )
        controls = (
            alignments_dir / "anc_aligned.bam",
            alignments_dir / "anc.bam",
        )
        hit_t = next((path for path in candidates if path.exists()), None)
        hit_c = next((path for path in controls if path.exists()), None)
        if hit_t is not None and hit_c is not None:
            treatment_bam = hit_t
            control_bam = hit_c
            break
    if treatment_bam is None or control_bam is None:
        return cases

    treatment_alias = _ensure_bam_subset_alias(
        root,
        treatment_bam,
        alias_parts=("macs2_chipseq_callpeak", "evol1.smoke.bam"),
    )
    control_alias = _ensure_bam_subset_alias(
        root,
        control_bam,
        alias_parts=("macs2_chipseq_callpeak", "anc.smoke.bam"),
    )
    cases.append(
        QwenSkillSmokeCase(
            name="macs2_chipseq_callpeak",
            description="Run MACS2 ChIP-seq peak calling on treatment and control BAM subsets.",
            source_input=str(treatment_alias),
            prompt_template=(
                "This is a direct one-step skill smoke test. Use only the macs2_chipseq_callpeak tool to call peaks "
                "from the treatment BAM at {source_input} against the control BAM at {control_bam}. Use genome size "
                "1.2e7, name chipseq_smoke, and write outputs under {selected_dir}/macs2_chipseq_out. "
                "Do not add any other tools or workflow stages. Do not use bash_run."
            ),
            expected_tool="macs2_chipseq_callpeak",
            expected_outputs=(
                "macs2_chipseq_out/chipseq_smoke_peaks.narrowPeak",
                "macs2_chipseq_out/chipseq_smoke_peaks.xls",
            ),
            prompt_context={"control_bam": str(control_alias)},
        )
    )
    return cases


def build_eighteenth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build an eighteenth tranche for MACS2 ATAC-seq peak calling."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if not (_has_executable("macs2") and _has_executable("samtools")):
        return cases

    try:
        deseq_selected_dir = _find_latest_passing_selected_dir(root, "deseq")
    except FileNotFoundError:
        return cases

    alignments_dir = deseq_selected_dir / "alignments"
    treatment_bam = alignments_dir / "SRR1278968.bam"
    if not treatment_bam.exists():
        return cases

    treatment_alias = _ensure_bam_subset_alias(
        root,
        treatment_bam,
        alias_parts=("macs2_atacseq_callpeak", "SRR1278968.smoke.bam"),
    )
    cases.append(
        QwenSkillSmokeCase(
            name="macs2_atacseq_callpeak",
            description="Run MACS2 ATAC-seq peak calling on a paired-end BAM subset.",
            source_input=str(treatment_alias),
            prompt_template=(
                "This is a direct one-step skill smoke test. Use only the macs2_atacseq_callpeak tool to call peaks "
                "from the paired-end BAM at {source_input}. Use genome size 1.2e7, name atac_smoke, and write outputs "
                "under {selected_dir}/macs2_atac_out. Do not add any other tools or workflow stages. Do not use bash_run."
            ),
            expected_tool="macs2_atacseq_callpeak",
            expected_outputs=(
                "macs2_atac_out/atac_smoke_peaks.narrowPeak",
                "macs2_atac_out/atac_smoke_peaks.xls",
            ),
        )
    )
    return cases


def build_nineteenth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a nineteenth tranche for deterministic CNVkit copy-number analysis."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if not requirement_available("cnvkit.py"):
        return cases

    input_bam: Path | None = None
    reference_fasta: Path | None = None
    evolution_dirs = _iter_clean_selected_dirs(
        root,
        "evolution",
        require_validator_pass=False,
    )
    for selected_dir in evolution_dirs:
        bam_hit = next(
            (
                path
                for path in (
                    selected_dir / "alignments" / "evol1_aligned.bam",
                    selected_dir / "alignments" / "evol1.bam",
                )
                if path.exists()
            ),
            None,
        )
        fasta_hit = next(
            (
                path
                for path in (
                    selected_dir / "assembly" / "scaffolds.fasta",
                    selected_dir / "assembly_anc" / "scaffolds.fasta",
                )
                if path.exists()
            ),
            None,
        )
        if bam_hit is not None and fasta_hit is not None:
            input_bam = bam_hit
            reference_fasta = fasta_hit
            break
    if input_bam is None or reference_fasta is None:
        return cases

    bam_alias = _ensure_smoke_source_alias(
        root,
        input_bam,
        alias_parts=("cnv_cnvkit_style", input_bam.name),
    )
    fasta_alias = _ensure_smoke_source_alias(
        root,
        reference_fasta,
        alias_parts=("cnv_cnvkit_style", reference_fasta.name),
    )
    cases.append(
        QwenSkillSmokeCase(
            name="cnv_cnvkit_style",
            description="Run CNVkit copy-number analysis on a single aligned BAM with a local scaffolds reference.",
            source_input=str(bam_alias),
            prompt_template=(
                "This is a direct one-step skill smoke test. Use only the cnv_cnvkit_style tool on the BAM at "
                "{source_input} with the reference FASTA at {reference_fasta}. Write outputs under {selected_dir}/cnv "
                "and write the TSV summary to {selected_dir}/cnv/cnv_summary.tsv. Use 2 processes. Do not add any "
                "other tools or workflow stages. Do not use bash_run."
            ),
            expected_tool="cnv_cnvkit_style",
            expected_outputs=(
                "cnv/cnv_summary.tsv",
                "cnv/reference.cnn",
            ),
            prompt_context={"reference_fasta": str(fasta_alias)},
        )
    )
    return cases


def build_twentieth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a twentieth tranche for deterministic Bismark methylation analysis."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if not requirement_available("bismark"):
        return cases

    reference_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("methylation_bismark_style", "genome_seed", "reference.fa"),
        content=(
            ">chr1\n"
            "ATGTTTGTTTGTTTGTTTGTTTGTTTGTTTGTTTGTTTGTTTGTTTGTTTGTTTGTTTGTTTG\n"
        ),
    )
    genome_folder_seed = str(reference_alias.parent)
    reads_1_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("methylation_bismark_style", "reads_R1.fastq"),
        content=(
            "@r1/1\nATGTTTGTTTGTTTGTTTGTTTGTTTGTTT\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
            "@r2/1\nATGTTTGTTTGTTTGTTTGTTTGTTTGTTT\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
            "@r3/1\nATGTTTGTTTGTTTGTTTGTTTGTTTGTTT\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
            "@r4/1\nATGTTTGTTTGTTTGTTTGTTTGTTTGTTT\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
        ),
    )
    reads_2_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("methylation_bismark_style", "reads_R2.fastq"),
        content=(
            "@r1/2\nATGTTTGTTTGTTTGTTTGTTTGTTTGTTT\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
            "@r2/2\nATGTTTGTTTGTTTGTTTGTTTGTTTGTTT\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
            "@r3/2\nATGTTTGTTTGTTTGTTTGTTTGTTTGTTT\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
            "@r4/2\nATGTTTGTTTGTTTGTTTGTTTGTTTGTTT\n+\nIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
        ),
    )
    cases.append(
        QwenSkillSmokeCase(
            name="methylation_bismark_style",
            description="Run deterministic Bismark methylation analysis on a tiny synthetic paired-end bisulfite input.",
            source_input=str(reads_1_alias),
            prompt_template=(
                "This is a direct one-step skill smoke test. Use only the methylation_bismark_style tool on the paired "
                "FASTQs at {source_input} and {reads_2} with genome folder {genome_folder}. The genome folder already "
                "contains the staged reference FASTA, so prepare the Bismark index there if it is missing. Write outputs "
                "under {selected_dir}/methylation with sample name methylation_smoke, use 1 thread, and write the TSV "
                "summary to {selected_dir}/methylation/methylation.tsv. Do not add any other tools or workflow stages. "
                "Do not use bash_run."
            ),
            expected_tool="methylation_bismark_style",
            expected_outputs=(
                "methylation/methylation.tsv",
                "methylation/methylation_smoke_pe.bam",
                "methylation/methylation_smoke_PE_report.txt",
            ),
            prompt_context={
                "genome_folder": genome_folder_seed,
                "reads_2": str(reads_2_alias),
            },
        )
    )
    return cases


def build_twentyfirst_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a twenty-first tranche for deterministic Kraken2/Bracken metagenomics profiling."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if not (
        requirement_available("kraken2")
        and requirement_available("bracken")
        and which_with_pixi("kraken2-build")
        and which_with_pixi("count-kmer-abundances.pl")
        and which_with_pixi("generate_kmer_distribution.py")
    ):
        return cases

    reference_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("metagenomics_kraken2_bracken_style", "reference.fa"),
        content=">ecoli|kraken:taxid|562\nACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT\n",
    )
    names_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("metagenomics_kraken2_bracken_style", "names.dmp"),
        content=(
            "1\t|\troot\t|\t\t|\tscientific name\t|\n"
            "2\t|\tBacteria\t|\t\t|\tscientific name\t|\n"
            "562\t|\tEscherichia coli\t|\t\t|\tscientific name\t|\n"
        ),
    )
    nodes_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("metagenomics_kraken2_bracken_style", "nodes.dmp"),
        content=(
            "1\t|\t1\t|\tno rank\t|\n"
            "2\t|\t1\t|\tsuperkingdom\t|\n"
            "562\t|\t2\t|\tspecies\t|\n"
        ),
    )
    reads_1_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("metagenomics_kraken2_bracken_style", "reads_R1.fastq"),
        content=(
            "@meta1/1\nACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT\n+\n"
            "IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
        ),
    )
    reads_2_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("metagenomics_kraken2_bracken_style", "reads_R2.fastq"),
        content=(
            "@meta1/2\nACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT\n+\n"
            "IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n"
        ),
    )
    cases.append(
        QwenSkillSmokeCase(
            name="metagenomics_kraken2_bracken_style",
            description="Run deterministic Kraken2/Bracken metagenomics profiling on a tiny synthetic paired-end input.",
            source_input=str(reads_1_alias),
            prompt_template=(
                "This is a direct one-step skill smoke test. Use only the metagenomics_kraken2_bracken_style tool on "
                "the paired FASTQs at {source_input} and {reads_2}. Use database path {selected_dir}/kraken_db, and if the database "
                "is missing, build it from the reference FASTA at {reference_fasta} with taxonomy files {taxonomy_names} "
                "and {taxonomy_nodes}. Write outputs under {selected_dir}/metagenomics, write the Bracken TSV to "
                "{selected_dir}/metagenomics/bracken.tsv, use 1 thread, read length 40, taxonomy level S, and threshold 1. "
                "Do not add any other tools or workflow stages. Do not use bash_run."
            ),
            expected_tool="metagenomics_kraken2_bracken_style",
            expected_outputs=(
                "metagenomics/bracken.tsv",
                "metagenomics/kraken.report",
                "kraken_db/hash.k2d",
                "kraken_db/database40mers.kmer_distrib",
            ),
            prompt_context={
                "reads_2": str(reads_2_alias),
                "reference_fasta": str(reference_alias),
                "taxonomy_names": str(names_alias),
                "taxonomy_nodes": str(nodes_alias),
            },
        )
    )
    return cases


def build_twentysecond_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a twenty-second tranche for the remaining locally-installable skills."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    source_alias = _ensure_generated_text_alias(
        root,
        alias_parts=("fallback_skill_builder", "request.json"),
        content=json.dumps(
            {
                "target_capability_set": [
                    "splicing_analysis",
                    "alignment",
                    "reference_inputs",
                    "group_comparison",
                ],
                "allowed_tools": ["star", "rmats", "samtools", "fastqc"],
                "strictness_mode": "conservative",
                "data_reference_constraints": {},
                "request_text": "direct fallback builder smoke for grouped alternative splicing",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    cases.append(
        QwenSkillSmokeCase(
            name="fallback_skill_builder",
            description="Generate a deterministic fallback coverage report without executing any downstream workflow.",
            source_input=str(source_alias),
            prompt_template=(
                "This is a direct one-step skill smoke test. Use only the fallback_skill_builder tool to generate a "
                "deterministic fallback coverage report. Read the JSON request spec at {source_input} and use its "
                "target_capability_set, allowed_tools, strictness_mode, data_reference_constraints, and request_text "
                "fields. Write the report JSON to "
                "{selected_dir}/fallback/fallback_skill_builder_report.json. Do not run end-to-end prompts. "
                "Do not use bash_run."
            ),
            expected_tool="fallback_skill_builder",
            expected_outputs=("fallback/fallback_skill_builder_report.json",),
        )
    )

    if requirement_available("flye") and which_with_pixi("minimap2"):
        flye_reads = _ensure_generated_text_alias(
            root,
            alias_parts=("flye_assemble", "reads.fastq"),
            content=_synthetic_flye_fastq(),
        )
        cases.append(
            QwenSkillSmokeCase(
                name="flye_assemble",
                description="Assemble a tiny synthetic long-read dataset with Flye.",
                source_input=str(flye_reads),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the flye_assemble tool on the long-read "
                    "FASTQ at {source_input}. Set read mode to nano-raw, use 1 thread, genome size 12k, and write "
                    "outputs under {selected_dir}/flye_out. Do not add any other tools or workflow stages. "
                    "Do not use bash_run."
                ),
                expected_tool="flye_assemble",
                expected_outputs=("flye_out/assembly.fasta",),
            )
        )

    if requirement_available("hmmscan") and which_with_pixi("hmmbuild") and which_with_pixi("hmmpress"):
        query_fasta = _ensure_generated_text_alias(
            root,
            alias_parts=("hmmscan_search", "query.faa"),
            content=">q1\nMKTIIALSYIFCLVFADYKDDDDK\n",
        )
        seed_path = _ensure_generated_text_alias(
            root,
            alias_parts=("hmmscan_search", "seed.sto"),
            content=(
                "# STOCKHOLM 1.0\n"
                "seq1 MKTIIALSYIFCLVFADYKDDDDK\n"
                "seq2 MKTIIALSYIFCLVFADYKDDDDK\n"
                "//\n"
            ),
        )
        hmm_db = seed_path.with_name("tiny.hmm")
        if not hmm_db.exists():
            subprocess.run(
                [which_with_pixi("hmmbuild") or "hmmbuild", str(hmm_db), str(seed_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        if not all(hmm_db.with_suffix(hmm_db.suffix + suffix).exists() for suffix in (".h3f", ".h3i", ".h3m", ".h3p")):
            subprocess.run(
                [which_with_pixi("hmmpress") or "hmmpress", str(hmm_db)],
                check=True,
                capture_output=True,
                text=True,
            )
        cases.append(
            QwenSkillSmokeCase(
                name="hmmscan_search",
                description="Search a tiny synthetic protein query against a tiny pressed HMM database.",
                source_input=str(query_fasta),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the hmmscan_search tool to search the "
                    "protein FASTA at {source_input} against the HMM database at {hmm_db}. Write the tabular output "
                    "to {selected_dir}/hmmscan/hmmscan.tbl and the text report to {selected_dir}/hmmscan/hmmscan.txt. "
                    "Use 1 CPU and do not add any other tools or workflow stages. Do not use bash_run."
                ),
                expected_tool="hmmscan_search",
                expected_outputs=("hmmscan/hmmscan.tbl", "hmmscan/hmmscan.txt"),
                prompt_context={"hmm_db": str(hmm_db)},
            )
        )

    if requirement_available("trinity") and which_with_pixi("jellyfish"):
        reads_1_content, reads_2_content = _synthetic_trinity_fastq_pair()
        reads_1_alias = _ensure_generated_text_alias(
            root,
            alias_parts=("trinity_assemble", "reads_R1.fastq"),
            content=reads_1_content,
        )
        reads_2_alias = _ensure_generated_text_alias(
            root,
            alias_parts=("trinity_assemble", "reads_R2.fastq"),
            content=reads_2_content,
        )
        cases.append(
            QwenSkillSmokeCase(
                name="trinity_assemble",
                description="Assemble a tiny synthetic paired-end RNA-seq dataset with Trinity.",
                source_input=str(reads_1_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the trinity_assemble tool on the paired-end "
                    "FASTQs at {source_input} and {reads_2}. Use 1 CPU, max memory 2G, set no_normalize_reads true, "
                    "and write outputs under {selected_dir}/trinity_out. Do not add any other tools or workflow "
                    "stages. Do not use bash_run."
                ),
                expected_tool="trinity_assemble",
                expected_outputs=("trinity_out/Trinity.fasta",),
                prompt_context={"reads_2": str(reads_2_alias)},
            )
        )

    if requirement_available("varscan") and requirement_available("samtools"):
        evolution_dirs = _iter_clean_selected_dirs(
            root,
            "evolution",
            require_validator_pass=False,
        )
        reference_fasta: Path | None = None
        input_bam: Path | None = None
        for selected_dir in evolution_dirs:
            fasta_hit = next(
                (
                    path
                    for path in (
                        selected_dir / "assembly" / "scaffolds.fasta",
                        selected_dir / "assembly_anc" / "scaffolds.fasta",
                    )
                    if path.exists()
                ),
                None,
            )
            bam_hit = next(
                (
                    path
                    for path in (
                        selected_dir / "alignments" / "anc_aligned.bam",
                        selected_dir / "alignments" / "evol1_aligned.bam",
                    )
                    if path.exists()
                ),
                None,
            )
            if fasta_hit is not None and bam_hit is not None:
                reference_fasta = fasta_hit
                input_bam = bam_hit
                break
        if reference_fasta is not None and input_bam is not None:
            reference_alias = _ensure_smoke_source_alias(
                root,
                reference_fasta,
                alias_parts=("varscan_call", reference_fasta.name),
            )
            bam_alias = _ensure_bam_subset_alias(
                root,
                input_bam,
                alias_parts=("varscan_call", input_bam.name),
                alignment_limit=8000,
            )
            cases.append(
                QwenSkillSmokeCase(
                    name="varscan_call",
                    description="Call variants from a smoke BAM/reference pair with VarScan2.",
                    source_input=str(bam_alias),
                    prompt_template=(
                        "This is a direct one-step skill smoke test. Use only the varscan_call tool to call variants "
                        "from the BAM at {source_input} against the reference FASTA at {reference_fasta}. Write the "
                        "output VCF to {selected_dir}/variants/varscan.vcf. Do not add any other tools or workflow "
                        "stages. Do not use bash_run."
                    ),
                    expected_tool="varscan_call",
                    expected_outputs=("variants/varscan.vcf",),
                    prompt_context={"reference_fasta": str(reference_alias)},
                )
            )

    return cases


def build_twentythird_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a twenty-third tranche for the final practical uncovered skills."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    if _has_executable("stringtie") and _has_executable("samtools"):
        try:
            deseq_selected_dir = _find_latest_passing_selected_dir(root, "deseq")
        except FileNotFoundError:
            deseq_selected_dir = Path()
        if deseq_selected_dir:
            alignments_dir = deseq_selected_dir / "alignments"
            bam_candidates = sorted(
                path
                for path in alignments_dir.glob("*.bam")
                if ".unsorted." not in path.name
            )
            gff_path = (
                root
                / "workspace"
                / "benchmarks"
                / "bioagent-bench"
                / "tasks"
                / "deseq"
                / "references"
                / "C_parapsilosis_CDC317_current_features.gff"
            )
            if bam_candidates and gff_path.exists():
                bam_alias = _ensure_bam_subset_alias(
                    root,
                    bam_candidates[0],
                    alias_parts=("stringtie_quant", bam_candidates[0].name),
                    alignment_limit=12_000,
                )
                gtf_alias = _ensure_gff_to_gtf_alias(
                    root,
                    gff_path,
                    alias_parts=("stringtie_quant", "C_parapsilosis_CDC317_current_features.smoke.gtf"),
                )
                cases.append(
                    QwenSkillSmokeCase(
                        name="stringtie_quant",
                        description="Estimate transcript abundance from a small RNA-seq BAM subset with StringTie.",
                        source_input=str(bam_alias),
                        prompt_template=(
                            "This is a direct one-step skill smoke test. Use only the stringtie_quant tool on the "
                            "coordinate-sorted BAM at {source_input} with the annotation GTF at {annotation_gtf}. "
                            "Write the assembled transcript GTF to {selected_dir}/stringtie/assembled.gtf and the "
                            "gene abundance table to {selected_dir}/stringtie/gene_abundances.tsv. Use 1 thread and "
                            "keep this reference-guided only. Do not add alignment, counting, or any other workflow "
                            "stages. Do not use bash_run."
                        ),
                        expected_tool="stringtie_quant",
                        expected_outputs=(
                            "stringtie/assembled.gtf",
                            "stringtie/gene_abundances.tsv",
                        ),
                        prompt_context={"annotation_gtf": str(gtf_alias)},
                    )
                )

    if requirement_available("STAR-Fusion"):
        reads_1_content, reads_2_content = _synthetic_trinity_fastq_pair()
        reads_1_alias = _ensure_generated_text_alias(
            root,
            alias_parts=("fusion_star_fusion_style", "reads_R1.fastq"),
            content=reads_1_content,
        )
        reads_2_alias = _ensure_generated_text_alias(
            root,
            alias_parts=("fusion_star_fusion_style", "reads_R2.fastq"),
            content=reads_2_content,
        )
        ctat_dir = (
            root
            / "workspace"
            / "skill_smoke"
            / "_source_aliases"
            / "fusion_star_fusion_style"
            / "ctat_genome_lib_smoke"
        )
        ctat_dir.mkdir(parents=True, exist_ok=True)
        cases.append(
            QwenSkillSmokeCase(
                name="fusion_star_fusion_style",
                description="Exercise the STAR-Fusion wrapper with deterministic degraded output when no CTAT genome library is available.",
                source_input=str(reads_1_alias),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the fusion_star_fusion_style tool on the "
                    "paired-end FASTQs at {source_input} and {reads_2} with the CTAT genome library directory at "
                    "{genome_lib_dir}. Write outputs under {selected_dir}/fusion and the final fusion report to "
                    "{selected_dir}/fusion/fusions.tsv. Do not add alignment or any other workflow stages. "
                    "Do not use bash_run."
                ),
                expected_tool="fusion_star_fusion_style",
                expected_outputs=("fusion/fusions.tsv",),
                prompt_context={
                    "reads_2": str(reads_2_alias),
                    "genome_lib_dir": str(ctat_dir),
                },
            )
        )

    return cases


def build_twentyfourth_tranche_qwen_skill_smoke_cases(project_root: str | Path) -> list[QwenSkillSmokeCase]:
    """Build a twenty-fourth tranche for the remaining practical BLAST+ family."""
    root = Path(project_root).expanduser().resolve()
    cases: list[QwenSkillSmokeCase] = []

    blast_tools = (
        "makeblastdb",
        "blastn",
        "blastx",
        "tblastn",
        "tblastx",
        "psiblast",
        "blast_formatter",
        "blastdbcmd",
        "blastdb_aliastool",
        "makeprofiledb",
        "rpsblast",
        "rpstblastn",
    )
    if not all(_has_executable(tool_name) for tool_name in blast_tools):
        return cases

    nucleotide_fasta, protein_fasta = _ensure_blast_query_aliases(root)
    nucleotide_db = _ensure_blast_database(
        root,
        nucleotide_fasta,
        alias_parts=("blast_family", "nucl_db", "query_db"),
        dbtype="nucl",
        parse_seqids=True,
    )
    protein_db = _ensure_blast_database(
        root,
        protein_fasta,
        alias_parts=("blast_family", "prot_db", "query_db"),
        dbtype="prot",
        parse_seqids=True,
    )
    archive_file = _ensure_blast_archive(
        root,
        query_fasta=protein_fasta,
        database_prefix=protein_db,
        alias_parts=("blast_family", "archive", "archive.asn"),
    )
    _ensure_blast_alias_database(
        root,
        database_prefix=protein_db,
        alias_parts=("blast_family", "alias_db", "alias_db"),
    )
    checkpoint_path = _ensure_blast_profile_checkpoint(
        root,
        query_fasta=protein_fasta,
        database_prefix=protein_db,
        alias_parts=("blast_family", "profiles", "p1.chk"),
    )
    profile_list = _ensure_blast_profile_list(
        root,
        checkpoint_path=checkpoint_path,
        alias_parts=("blast_family", "profiles", "pssm_list.txt"),
    )
    profile_db = _ensure_blast_profile_database(
        root,
        input_list=profile_list,
        alias_parts=("blast_family", "profiles", "domain_db"),
    )

    cases.extend(
        [
            QwenSkillSmokeCase(
                name="makeblastdb_run",
                description="Build a tiny nucleotide BLAST database from a synthetic FASTA.",
                source_input=str(nucleotide_fasta),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the makeblastdb_run tool on the FASTA at "
                    "{source_input}. Build a nucleotide database with parse_seqids enabled and write the output prefix "
                    "to {selected_dir}/db/query_db. Do not add any search steps or any other tools. Do not use bash_run."
                ),
                expected_tool="makeblastdb_run",
                expected_outputs=("db/query_db.nsq",),
            ),
            QwenSkillSmokeCase(
                name="blastn_search",
                description="Run a tiny nucleotide BLASTN self-search against a local BLAST database.",
                source_input=str(nucleotide_fasta),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the blastn_search tool to search the query "
                    "FASTA at {source_input} against the nucleotide BLAST database at {database}. Use task blastn, "
                    "1 thread, and write the tabular output to {selected_dir}/blastn/blastn.tsv. Do not add any other "
                    "tools or workflow stages. Do not use bash_run."
                ),
                expected_tool="blastn_search",
                expected_outputs=("blastn/blastn.tsv",),
                prompt_context={"database": str(nucleotide_db)},
            ),
            QwenSkillSmokeCase(
                name="blastx_search",
                description="Run a tiny BLASTX search from nucleotide query to protein database.",
                source_input=str(nucleotide_fasta),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the blastx_search tool to search the "
                    "nucleotide FASTA at {source_input} against the protein BLAST database at {database}. Use 1 "
                    "thread and write the tabular output to {selected_dir}/blastx/blastx.tsv. Do not add any other "
                    "tools or workflow stages. Do not use bash_run."
                ),
                expected_tool="blastx_search",
                expected_outputs=("blastx/blastx.tsv",),
                prompt_context={"database": str(protein_db)},
            ),
            QwenSkillSmokeCase(
                name="tblastn_search",
                description="Run a tiny TBLASTN search from protein query to nucleotide database.",
                source_input=str(protein_fasta),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the tblastn_search tool to search the "
                    "protein FASTA at {source_input} against the nucleotide BLAST database at {database}. Use 1 "
                    "thread and write the tabular output to {selected_dir}/tblastn/tblastn.tsv. Do not add any "
                    "other tools or workflow stages. Do not use bash_run."
                ),
                expected_tool="tblastn_search",
                expected_outputs=("tblastn/tblastn.tsv",),
                prompt_context={"database": str(nucleotide_db)},
            ),
            QwenSkillSmokeCase(
                name="tblastx_search",
                description="Run a tiny TBLASTX self-search against a nucleotide database.",
                source_input=str(nucleotide_fasta),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the tblastx_search tool to search the "
                    "nucleotide FASTA at {source_input} against the nucleotide BLAST database at {database}. Use 1 "
                    "thread and write the tabular output to {selected_dir}/tblastx/tblastx.tsv. Do not add any "
                    "other tools or workflow stages. Do not use bash_run."
                ),
                expected_tool="tblastx_search",
                expected_outputs=("tblastx/tblastx.tsv",),
                prompt_context={"database": str(nucleotide_db)},
            ),
            QwenSkillSmokeCase(
                name="psiblast_search",
                description="Run a tiny PSI-BLAST self-search against a protein database.",
                source_input=str(protein_fasta),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the psiblast_search tool to search the "
                    "protein FASTA at {source_input} against the protein BLAST database at {database}. Use 2 "
                    "iterations, 1 thread, and write the tabular output to {selected_dir}/psiblast/psiblast.tsv. "
                    "Do not add any other tools or workflow stages. Do not use bash_run."
                ),
                expected_tool="psiblast_search",
                expected_outputs=("psiblast/psiblast.tsv",),
                prompt_context={"database": str(protein_db)},
            ),
            QwenSkillSmokeCase(
                name="blast_formatter_run",
                description="Format a prebuilt BLAST archive into tabular output.",
                source_input=str(archive_file),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the blast_formatter_run tool on the BLAST "
                    "archive at {source_input}. Format it as '6 qseqid sseqid' and write the output to "
                    "{selected_dir}/blast_formatter/formatted.tsv. Do not add any other tools or workflow stages. "
                    "Do not use bash_run."
                ),
                expected_tool="blast_formatter_run",
                expected_outputs=("blast_formatter/formatted.tsv",),
            ),
            QwenSkillSmokeCase(
                name="blastdbcmd_run",
                description="Retrieve a known sequence entry from a tiny protein BLAST database.",
                source_input=str(protein_fasta),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the blastdbcmd_run tool to retrieve entry "
                    "p1 from the protein BLAST database at {database}. Write the FASTA output to "
                    "{selected_dir}/blastdbcmd/entry.faa using outfmt %f. Do not add any other tools or workflow "
                    "stages. Do not use bash_run."
                ),
                expected_tool="blastdbcmd_run",
                expected_outputs=("blastdbcmd/entry.faa",),
                prompt_context={"database": str(protein_db)},
            ),
            QwenSkillSmokeCase(
                name="blastdb_aliastool_run",
                description="Create a tiny protein BLAST alias database from an existing database.",
                source_input=str(protein_fasta),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the blastdb_aliastool_run tool to create a "
                    "protein alias database from the existing BLAST database at {database}. Set dblist to that single "
                    "database, set dbtype to prot, and write the alias output prefix to {selected_dir}/alias_db/alias_db. "
                    "Set title to alias_db. Do not add any other tools or workflow stages. Do not use bash_run."
                ),
                expected_tool="blastdb_aliastool_run",
                expected_outputs=("alias_db/alias_db.pal",),
                prompt_context={"database": str(protein_db)},
            ),
            QwenSkillSmokeCase(
                name="makeprofiledb_run",
                description="Build a tiny reverse-position-specific BLAST profile database from a checkpoint list.",
                source_input=str(profile_list),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the makeprofiledb_run tool on the checkpoint "
                    "list at {source_input}. Build an rps profile database with index true and write the output prefix "
                    "to {selected_dir}/profile_db/domain_db. Do not add any search steps or any other tools. "
                    "Do not use bash_run."
                ),
                expected_tool="makeprofiledb_run",
                expected_outputs=("profile_db/domain_db.rps",),
            ),
            QwenSkillSmokeCase(
                name="rpsblast_search",
                description="Run a tiny RPS-BLAST search against a locally built profile database.",
                source_input=str(protein_fasta),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the rpsblast_search tool to search the "
                    "protein FASTA at {source_input} against the profile database at {database}. Use 1 thread and "
                    "write the tabular output to {selected_dir}/rpsblast/rpsblast.tsv. Do not add any other tools "
                    "or workflow stages. Do not use bash_run."
                ),
                expected_tool="rpsblast_search",
                expected_outputs=("rpsblast/rpsblast.tsv",),
                prompt_context={"database": str(profile_db)},
            ),
            QwenSkillSmokeCase(
                name="rpstblastn_search",
                description="Run a tiny RPSTBLASTN search against a locally built profile database.",
                source_input=str(nucleotide_fasta),
                prompt_template=(
                    "This is a direct one-step skill smoke test. Use only the rpstblastn_search tool to search the "
                    "nucleotide FASTA at {source_input} against the profile database at {database}. Use strand plus, "
                    "1 thread, and write the tabular output to {selected_dir}/rpstblastn/rpstblastn.tsv. Do not add "
                    "any other tools or workflow stages. Do not use bash_run."
                ),
                expected_tool="rpstblastn_search",
                expected_outputs=("rpstblastn/rpstblastn.tsv",),
                prompt_context={"database": str(profile_db)},
            ),
        ]
    )

    return cases


def _iter_tool_names(events_path: Path) -> list[str]:
    tool_names: list[str] = []
    seen: set[str] = set()
    if (not events_path.exists()) or (not events_path.is_file()):
        return tool_names
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        inner = payload.get("payload", {})
        if not isinstance(inner, dict):
            continue
        tool_name = str(inner.get("tool_name", "")).strip()
        if tool_name and tool_name not in seen:
            seen.add(tool_name)
            tool_names.append(tool_name)
    return tool_names


def _planner_elapsed_seconds(events_path: Path) -> float | None:
    if (not events_path.exists()) or (not events_path.is_file()):
        return None
    last_elapsed: float | None = None
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("event_type", "")).strip() != "PLANNER_ATTEMPT_SUCCEEDED":
            continue
        inner = payload.get("payload", {})
        if not isinstance(inner, dict):
            continue
        elapsed = inner.get("elapsed_seconds")
        if isinstance(elapsed, (int, float)):
            last_elapsed = float(elapsed)
    return last_elapsed


def _required_tools_for_case(case: QwenSkillSmokeCase) -> tuple[str, ...]:
    """Return the ordered tool sequence required for a smoke case."""
    if case.expected_tools:
        return tuple(tool for tool in case.expected_tools if str(tool).strip())
    tool_name = str(case.expected_tool).strip()
    return (tool_name,) if tool_name else ()


def _expected_tools_satisfied(
    executed_tools: list[str],
    required_tools: tuple[str, ...],
) -> tuple[bool, list[str], bool]:
    """Check whether executed tools contain the required ordered subsequence."""
    missing_tools = [tool for tool in required_tools if tool not in executed_tools]
    if missing_tools:
        return False, missing_tools, False
    if not required_tools:
        return True, [], True
    positions = [executed_tools.index(tool) for tool in required_tools]
    in_order = positions == sorted(positions)
    return in_order, [], in_order


def _summarize_case_result(
    project_root: Path,
    selected_dir: Path,
    case: QwenSkillSmokeCase,
    *,
    process_returncode: int,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    result, run_dir = _resolve_result_payload(project_root, selected_dir, stdout)
    run_id = str(result.get("run_id", "")).strip()
    events_path = run_dir / "events.jsonl" if run_dir is not None else Path()
    executed_tools = _iter_tool_names(events_path)
    missing_outputs = [
        rel_path
        for rel_path in case.expected_outputs
        if not (selected_dir / rel_path).exists()
    ]
    auto_repair_history_count = int(result.get("auto_repair_history_count", 0) or 0)
    status = str(result.get("status", "")).strip()
    error = str(result.get("error", "")).strip()
    required_tools = _required_tools_for_case(case)
    expected_tool_executed = case.expected_tool in executed_tools
    expected_tools_satisfied, missing_expected_tools, expected_tools_in_order = _expected_tools_satisfied(
        executed_tools,
        required_tools,
    )
    passed = (
        process_returncode == 0
        and status == "completed"
        and error == ""
        and auto_repair_history_count == 0
        and expected_tool_executed
        and expected_tools_satisfied
        and not missing_outputs
    )
    return {
        "name": case.name,
        "description": case.description,
        "source_input": case.source_input,
        "selected_dir": str(selected_dir),
        "run_id": run_id,
        "run_dir": str(run_dir) if run_dir is not None else "",
        "expected_tool": case.expected_tool,
        "expected_tools": list(required_tools),
        "executed_tools": executed_tools,
        "expected_tool_executed": expected_tool_executed,
        "expected_tools_satisfied": expected_tools_satisfied,
        "expected_tools_in_order": expected_tools_in_order,
        "missing_expected_tools": missing_expected_tools,
        "expected_outputs": list(case.expected_outputs),
        "missing_outputs": missing_outputs,
        "status": status,
        "error": error,
        "auto_repair_history_count": auto_repair_history_count,
        "process_returncode": process_returncode,
        "planner_elapsed_seconds": _planner_elapsed_seconds(events_path),
        "stdout_tail": "\n".join(stdout.strip().splitlines()[-10:]),
        "stderr_tail": "\n".join(stderr.strip().splitlines()[-10:]),
        "passed": passed,
    }


def run_qwen_skill_smoke_matrix(
    project_root: str | Path,
    *,
    case_names: list[str] | None = None,
    label: str | None = None,
    model_name: str = "qwen3-coder-next:latest",
    llm_backend: str = "ollama",
    host: str = "",
    timeout_seconds: int = 1200,
    tranche: str = "starter",
) -> dict[str, Any]:
    """Run the starter Qwen-through-harness smoke matrix."""
    root = Path(project_root).expanduser().resolve()
    if tranche == "starter":
        all_cases = build_starter_qwen_skill_smoke_cases(root)
    elif tranche == "second":
        all_cases = build_second_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "third":
        all_cases = build_third_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "fourth":
        all_cases = build_fourth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "fifth":
        all_cases = build_fifth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "sixth":
        all_cases = build_sixth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "seventh":
        all_cases = build_seventh_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "eighth":
        all_cases = build_eighth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "ninth":
        all_cases = build_ninth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "tenth":
        all_cases = build_tenth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "eleventh":
        all_cases = build_eleventh_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "twelfth":
        all_cases = build_twelfth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "thirteenth":
        all_cases = build_thirteenth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "fourteenth":
        all_cases = build_fourteenth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "fifteenth":
        all_cases = build_fifteenth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "sixteenth":
        all_cases = build_sixteenth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "seventeenth":
        all_cases = build_seventeenth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "eighteenth":
        all_cases = build_eighteenth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "nineteenth":
        all_cases = build_nineteenth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "twentieth":
        all_cases = build_twentieth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "twentyfirst":
        all_cases = build_twentyfirst_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "twentysecond":
        all_cases = build_twentysecond_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "twentythird":
        all_cases = build_twentythird_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "twentyfourth":
        all_cases = build_twentyfourth_tranche_qwen_skill_smoke_cases(root)
    elif tranche == "all_supported":
        starter_cases = build_starter_qwen_skill_smoke_cases(root)
        second_tranche_cases = build_second_tranche_qwen_skill_smoke_cases(root)
        third_tranche_cases = build_third_tranche_qwen_skill_smoke_cases(root)
        fourth_tranche_cases = build_fourth_tranche_qwen_skill_smoke_cases(root)
        fifth_tranche_cases = build_fifth_tranche_qwen_skill_smoke_cases(root)
        sixth_tranche_cases = build_sixth_tranche_qwen_skill_smoke_cases(root)
        seventh_tranche_cases = build_seventh_tranche_qwen_skill_smoke_cases(root)
        eighth_tranche_cases = build_eighth_tranche_qwen_skill_smoke_cases(root)
        ninth_tranche_cases = build_ninth_tranche_qwen_skill_smoke_cases(root)
        tenth_tranche_cases = build_tenth_tranche_qwen_skill_smoke_cases(root)
        eleventh_tranche_cases = build_eleventh_tranche_qwen_skill_smoke_cases(root)
        twelfth_tranche_cases = build_twelfth_tranche_qwen_skill_smoke_cases(root)
        thirteenth_tranche_cases = build_thirteenth_tranche_qwen_skill_smoke_cases(root)
        fourteenth_tranche_cases = build_fourteenth_tranche_qwen_skill_smoke_cases(root)
        fifteenth_tranche_cases = build_fifteenth_tranche_qwen_skill_smoke_cases(root)
        sixteenth_tranche_cases = build_sixteenth_tranche_qwen_skill_smoke_cases(root)
        seventeenth_tranche_cases = build_seventeenth_tranche_qwen_skill_smoke_cases(root)
        eighteenth_tranche_cases = build_eighteenth_tranche_qwen_skill_smoke_cases(root)
        nineteenth_tranche_cases = build_nineteenth_tranche_qwen_skill_smoke_cases(root)
        twentieth_tranche_cases = build_twentieth_tranche_qwen_skill_smoke_cases(root)
        twentyfirst_tranche_cases = build_twentyfirst_tranche_qwen_skill_smoke_cases(root)
        twentysecond_tranche_cases = build_twentysecond_tranche_qwen_skill_smoke_cases(root)
        twentythird_tranche_cases = build_twentythird_tranche_qwen_skill_smoke_cases(root)
        twentyfourth_tranche_cases = build_twentyfourth_tranche_qwen_skill_smoke_cases(root)
        all_cases = starter_cases + second_tranche_cases + third_tranche_cases + fourth_tranche_cases + fifth_tranche_cases + sixth_tranche_cases + seventh_tranche_cases + eighth_tranche_cases + ninth_tranche_cases + tenth_tranche_cases + eleventh_tranche_cases + twelfth_tranche_cases + thirteenth_tranche_cases + fourteenth_tranche_cases + fifteenth_tranche_cases + sixteenth_tranche_cases + seventeenth_tranche_cases + eighteenth_tranche_cases + nineteenth_tranche_cases + twentieth_tranche_cases + twentyfirst_tranche_cases + twentysecond_tranche_cases + twentythird_tranche_cases + twentyfourth_tranche_cases
    else:
        raise ValueError(f"Unsupported smoke tranche: {tranche}")
    case_lookup = {case.name: case for case in all_cases}
    selected_cases = (
        [case_lookup[name] for name in case_names]
        if case_names
        else all_cases
    )
    label_value = label or datetime.now(timezone.utc).strftime("starter_%Y%m%d_%H%M%S")
    smoke_root = root / "workspace" / "skill_smoke" / label_value
    smoke_root.mkdir(parents=True, exist_ok=True)
    run_script = root / "scripts" / "run_agent_e2e.py"
    case_summaries: list[dict[str, Any]] = []

    for case in selected_cases:
        selected_dir = smoke_root / case.name
        if selected_dir.exists():
            shutil.rmtree(selected_dir)
        selected_dir.mkdir(parents=True, exist_ok=True)
        prompt = case.prompt_template.format(
            source_input=case.source_input,
            selected_dir=str(selected_dir),
            **dict(case.prompt_context or {}),
        )
        command = [
            sys.executable,
            str(run_script),
            "--prompt",
            prompt,
            "--selected-dir",
            str(selected_dir),
            "--model-name",
            model_name,
            "--llm-backend",
            llm_backend,
            "--max-repairs",
            "0",
            "--no-replan",
        ]
        if host:
            command.extend(["--host", host])
        proc = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        case_summaries.append(
            _summarize_case_result(
                root,
                selected_dir,
                case,
                process_returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        )

    passed_case_count = sum(1 for case in case_summaries if case["passed"])
    return {
        "project_root": str(root),
        "label": label_value,
        "model_name": model_name,
        "llm_backend": llm_backend,
        "host": host,
        "tranche": tranche,
        "case_count": len(case_summaries),
        "passed_case_count": passed_case_count,
        "all_passed": passed_case_count == len(case_summaries),
        "cases": case_summaries,
    }
