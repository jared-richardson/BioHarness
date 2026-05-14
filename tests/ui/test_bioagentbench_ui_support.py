from __future__ import annotations

from pathlib import Path

from bio_harness.ui.bioagentbench_ui_support import (
    apply_benchmark_prompt_contract_seed,
    benchmark_manifest_default_policy,
    benchmark_prompt_contract_seed,
    build_ui_benchmark_prompt,
    concretize_ui_benchmark_prompt,
    extract_ui_benchmark_data_root,
    extract_ui_benchmark_task_id,
    is_ui_benchmark_prompt,
    ui_benchmark_policy,
)


def test_ui_benchmark_policy_defaults_to_scientific_harness() -> None:
    assert ui_benchmark_policy({}) == "scientific_harness"


def test_ui_benchmark_policy_normalizes_env_value() -> None:
    assert ui_benchmark_policy({"BIO_HARNESS_BENCHMARK_POLICY": "official_bioagentbench"}) == "official_bioagentbench"
    assert ui_benchmark_policy({"BIO_HARNESS_BENCHMARK_POLICY": "bioagentbench_planning_strict"}) == (
        "bioagentbench_planning_strict"
    )


def test_benchmark_manifest_default_policy_is_normalized(monkeypatch) -> None:
    monkeypatch.setenv("BIO_HARNESS_BENCHMARK_POLICY", "OFFICIAL_BIOAGENTBENCH")
    assert benchmark_manifest_default_policy() == "official_bioagentbench"


def test_build_ui_benchmark_prompt_uses_relative_deliverables_and_blind_guardrails(tmp_path) -> None:
    data_root = tmp_path / "task_data"
    data_root.mkdir()
    (data_root / "reads_1.fq.gz").write_text("x", encoding="utf-8")
    (data_root / "reads_2.fq.gz").write_text("x", encoding="utf-8")

    entry = {
        "task_id": "transcript-quant",
        "task_name": "Transcript Quantification",
        "task_dir": str(tmp_path / "task_dir"),
        "data_root": str(data_root),
        "runs_root": str(tmp_path / "runs_root"),
        "task_prompt": "Perform transcript quantification on the paired reads under {data_root}.",
        "output_requirements": [
            "Write the quantification report inside {selected_dir}/output.",
        ],
        "deliverables": [
            {
                "path": "final/transcript_counts.tsv",
                "description": "Write the final transcript-count TSV.",
                "columns": ["transcript_id", "count"],
            }
        ],
    }

    prompt = build_ui_benchmark_prompt(entry)

    assert "Proceed with execution now." in prompt
    assert str(data_root) in prompt
    assert "reads_1.fq.gz" in prompt
    assert "reads_2.fq.gz" in prompt
    assert "`final/transcript_counts.tsv`" in prompt
    assert "transcript_id, count" in prompt
    assert "benchmark truth files" in prompt
    assert "benchmark results files" in prompt
    assert "{selected_dir}" not in prompt


def test_build_ui_benchmark_prompt_avoids_old_generic_reference_wording(tmp_path) -> None:
    data_root = tmp_path / "task_data"
    data_root.mkdir()
    (data_root / "counts.tsv").write_text("gene\tcount\nA\t1\n", encoding="utf-8")

    entry = {
        "task_id": "alzheimer-mouse",
        "task_name": "Shared Alzheimer Pathways",
        "task_dir": str(tmp_path / "task_dir"),
        "data_root": str(data_root),
        "runs_root": str(tmp_path / "runs_root"),
        "task_prompt": (
            "Perform a comparative differential expression analysis of the 5xFAD, 3xTG-AD, and PS3O1S "
            "Alzheimer's mouse models to identify shared molecular KEGG pathways. Use the provided count "
            "matrices and differential-expression table, and produce the shared-pathway comparison CSV in "
            "the requested schema."
        ),
        "deliverables": [
            {
                "path": "final/shared_pathways.csv",
                "description": "Write the final shared-pathway comparison CSV.",
                "columns": ["pathway_id", "pathway_name"],
            }
        ],
    }

    prompt = build_ui_benchmark_prompt(entry)

    assert "benchmark inputs and references" not in prompt
    assert "other local task files" in prompt


def test_build_ui_benchmark_prompt_surfaces_sample_metadata_guidance(tmp_path) -> None:
    data_root = tmp_path / "task_data"
    data_root.mkdir()
    (data_root / "SRR1_1.fastq").write_text("x", encoding="utf-8")
    (data_root / "SRR1_2.fastq").write_text("x", encoding="utf-8")
    (data_root / "sample_metadata.tsv").write_text("sample\tcondition\nSRR1\tbiofilm\n", encoding="utf-8")

    entry = {
        "task_id": "deseq",
        "task_name": "RNA-Seq Differential Expression",
        "task_dir": str(tmp_path / "task_dir"),
        "data_root": str(data_root),
        "runs_root": str(tmp_path / "runs_root"),
        "task_prompt": "Use DESeq2 on the provided Candida RNA-seq inputs.",
        "deliverables": [
            {
                "path": "final/deseq_results.csv",
                "description": "Write the differential-expression result CSV.",
                "columns": ["gene_id", "log2FoldChange", "pvalue", "padj"],
            }
        ],
    }

    prompt = build_ui_benchmark_prompt(entry)

    assert "sample_metadata.tsv" in prompt
    assert "instead of inferring control/treatment groups from FASTQ filenames" in prompt


def test_build_ui_benchmark_prompt_surfaces_viral_reference_guardrails(tmp_path) -> None:
    data_root = tmp_path / "task_data"
    task_dir = tmp_path / "viral_task"
    refs_dir = task_dir / "references"
    data_root.mkdir()
    refs_dir.mkdir(parents=True)
    (data_root / "sample_R1.fastq.gz").write_text("x", encoding="utf-8")
    (data_root / "sample_R2.fastq.gz").write_text("x", encoding="utf-8")
    (refs_dir / "virus_a.fna").write_text(">virus\nACGT\n", encoding="utf-8")

    entry = {
        "task_id": "viral-metagenomics",
        "task_name": "Viral Metagenomics",
        "task_dir": str(task_dir),
        "data_root": str(data_root),
        "runs_root": str(tmp_path / "runs_root"),
        "task_prompt": (
            "Identify viruses in the paired-end reads by mapping against the viral reference FASTAs "
            "staged in {task_dir}/references."
        ),
        "deliverables": [
            {
                "path": "output/classification_report.tsv",
                "description": "Write the viral classification report TSV.",
            },
            {
                "path": "output/detected_viruses.txt",
                "description": "Write the detected viruses list.",
            },
        ],
    }

    prompt = build_ui_benchmark_prompt(entry)

    assert "Treat this as the `viral_metagenomics` workflow class." in prompt
    assert str(refs_dir) in prompt
    assert "workspace/inputs_readonly" in prompt
    assert "Prefer the repo-local helper-backed viral classification path" in prompt


def test_extract_ui_benchmark_data_root_uses_explicit_input_files_line() -> None:
    prompt = (
        "Proceed with execution now. BioAgentBench task: viral-metagenomics. "
        "Input files are under /tmp/task/data and include sample_R1.fastq.gz, sample_R2.fastq.gz. "
        "Use only staged references under /tmp/task/references and do not use workspace/inputs_readonly."
    )

    assert extract_ui_benchmark_data_root(prompt) == str(Path("/tmp/task/data").resolve(strict=False))


def test_extract_ui_benchmark_task_id_prefers_task_path_over_display_label() -> None:
    prompt = (
        "Proceed with execution now. BioAgentBench task: Viral Metagenomics. "
        "Input files are under /tmp/tasks/viral-metagenomics/data and include sample_R1.fastq.gz, sample_R2.fastq.gz."
    )

    assert extract_ui_benchmark_task_id(prompt) == "viral-metagenomics"


def test_benchmark_prompt_contract_seed_guides_viral_helper_path() -> None:
    prompt = (
        "Proceed with execution now. BioAgentBench task: Viral Metagenomics. "
        "Input files are under /tmp/tasks/viral-metagenomics/data and include sample_R1.fastq.gz, sample_R2.fastq.gz."
    )

    assert benchmark_prompt_contract_seed(prompt) == {
        "must_include_capabilities": ["metagenomics_profiling"],
        "required_tool_hints": ["classify_viral_reads_kmer.py"],
        "explicit_tool_hints": ["classify_viral_reads_kmer.py"],
    }


def test_apply_benchmark_prompt_contract_seed_overrides_generic_viral_contract() -> None:
    prompt = (
        "Proceed with execution now. BioAgentBench task: Viral Metagenomics. "
        "Input files are under /tmp/tasks/viral-metagenomics/data and include sample_R1.fastq.gz, sample_R2.fastq.gz."
    )
    generic = {
        "must_include_capabilities": ["alignment", "reference_inputs", "metagenomics_profiling"],
        "explicit_tool_hints": ["minimap2"],
    }

    assert apply_benchmark_prompt_contract_seed(generic, prompt) == {
        "must_include_capabilities": ["metagenomics_profiling"],
        "required_tool_hints": ["classify_viral_reads_kmer.py"],
        "explicit_tool_hints": ["classify_viral_reads_kmer.py"],
    }


def test_is_ui_benchmark_prompt_detects_chat_placeholder_prompt() -> None:
    prompt = (
        "Proceed with execution now. BioAgentBench task: Example task. "
        "Write final.csv under the current run directory."
    )

    assert is_ui_benchmark_prompt(prompt) is True
    assert is_ui_benchmark_prompt("Analyze these files in workspace/inputs_readonly.") is False


def test_concretize_ui_benchmark_prompt_binds_run_dir_for_blind_mode() -> None:
    prompt = (
        "Proceed with execution now. BioAgentBench task: Example task. "
        "Write final.csv under the current run directory. "
        "Save all generated outputs inside the current run directory for this UI run. "
        "Do not write anywhere outside the current run directory except reading the provided local benchmark inputs and other local task files."
    )

    rewritten = concretize_ui_benchmark_prompt(
        prompt,
        selected_dir="/tmp/ui_benchmark_run",
        benchmark_policy="official_bioagentbench",
    )
    resolved_dir = str(Path("/tmp/ui_benchmark_run").resolve(strict=False))

    assert rewritten.startswith("BioAgentBench official-mode task: Example task.")
    assert "current run directory" not in rewritten
    assert resolved_dir in rewritten
    assert f"Write all generated outputs under {resolved_dir}." in rewritten
    assert "Do not write anywhere outside the selected directory except reading the provided input files." in rewritten


def test_concretize_ui_benchmark_prompt_leaves_nonblind_prompt_unchanged() -> None:
    prompt = (
        "Proceed with execution now. BioAgentBench task: Example task. "
        "Write final.csv under the current run directory."
    )

    assert (
        concretize_ui_benchmark_prompt(
            prompt,
            selected_dir="/tmp/ui_benchmark_run",
            benchmark_policy="scientific_harness",
        )
        == prompt
    )
