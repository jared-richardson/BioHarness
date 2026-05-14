from __future__ import annotations

from pathlib import Path

import pytest

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.skills.library.bcftools_call import bcftools_call
from bio_harness.skills.library.blastp_search import blastp_search
from bio_harness.skills.library.bowtie2_align import bowtie2_align
from bio_harness.skills.library.bwa_mem_align import bwa_mem_align
from bio_harness.skills.library.freebayes_call import freebayes_call
from bio_harness.skills.library.gatk_haplotypecaller import gatk_haplotypecaller
from bio_harness.skills.library.gatk_mutect2_call import gatk_mutect2_call
from bio_harness.skills.library.fallback_skill_builder import fallback_skill_builder
from bio_harness.skills.library.hisat2_align import hisat2_align
from bio_harness.skills.library.hmmscan_search import hmmscan_search
from bio_harness.skills.library.minimap2_align import minimap2_align
from bio_harness.skills.library.rmats_run import rmats_run
from bio_harness.skills.library.kallisto_quant import kallisto_quant
from bio_harness.skills.library.salmon_quant import salmon_quant
from bio_harness.skills.library.prodigal_annotate import prodigal_annotate
from bio_harness.skills.library.snpeff_annotate import snpeff_annotate
from bio_harness.skills.library.spades_assemble import spades_assemble
from bio_harness.skills.library.subread_align import subread_align
from bio_harness.skills.library.varscan_call import varscan_call
from bio_harness.skills.library.deseq2_run import deseq2_run
from bio_harness.skills.library.dexseq_run import dexseq_run
from bio_harness.skills.library.edger_run import edger_run
from bio_harness.skills.library.featurecounts_run import featurecounts_run


def test_bwa_mem_align_builds_index_and_indexes_bam():
    cmd = bwa_mem_align(
        reference_fasta="/refs/genome.fa",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        output_bam="/tmp/out/sample.bam",
        threads=4,
        cache_index_prefix="/tmp/cache/bwa/genome",
    )
    assert ("bwa mem" in cmd) or ("bwa-mem2 mem" in cmd)
    assert ("bwa index -p" in cmd) or ("bwa-mem2 index -p" in cmd)
    assert "samtools index" in cmd


def test_bwa_mem_align_can_use_bwa_mem2_when_only_mem2_is_available(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "bio_harness.skills.library.bwa_mem_align.which_with_pixi",
        lambda name: {
            "bwa": "/opt/bin/bwa-mem2",
            "samtools": "/opt/bin/samtools",
        }.get(name),
    )
    cmd = bwa_mem_align(
        reference_fasta="/refs/genome.fa",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        output_bam="/tmp/out/sample.bam",
        threads=4,
        cache_index_prefix="/tmp/cache/bwa/genome",
    )
    assert "bwa-mem2 mem" in cmd
    assert "bwa-mem2 index" in cmd


def test_bwa_mem_align_resolves_shared_tool_binaries(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "bio_harness.skills.library.bwa_mem_align.which_with_pixi",
        lambda name: {
            "bwa": "/opt/tools/bwa-mem2",
            "samtools": "/opt/tools/samtools",
        }.get(name),
    )
    cmd = bwa_mem_align(
        reference_fasta="/refs/genome.fa",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        output_bam="/tmp/out/sample.bam",
        threads=2,
        cache_index_prefix="/tmp/cache/bwa/genome",
    )

    assert "/opt/tools/bwa-mem2 index -p /tmp/cache/bwa/genome /refs/genome.fa" in cmd
    assert "/opt/tools/bwa-mem2 mem -t 2" in cmd
    assert "/opt/tools/samtools faidx /refs/genome.fa" in cmd
    assert "/opt/tools/samtools sort -@ 2 -o /tmp/out/sample.bam -" in cmd
    assert "/opt/tools/samtools index /tmp/out/sample.bam" in cmd


def test_bwa_mem_align_normalizes_malformed_read_group() -> None:
    cmd = bwa_mem_align(
        reference_fasta="/refs/genome.fa",
        reads_1="/data/evol2_R1.fastq.gz",
        reads_2="/data/evol2_R2.fastq.gz",
        output_bam="/tmp/out/evol2.bam",
        sample_name="evol2",
        read_group="SM:evol2",
    )

    assert r"@RG\tID:evol2\tSM:evol2\tPL:ILLUMINA\tLB:lib1" in cmd


def test_minimap2_align_builds_cached_mmi_when_requested():
    cmd = minimap2_align(
        reference_fasta="/refs/genome.fa",
        reads="/data/long.fastq",
        output_bam="/tmp/out/long.bam",
        preset="map-ont",
        cache_index_path="/tmp/cache/mm2/genome.mmi",
    )
    assert "minimap2 -d" in cmd
    assert "map-ont" in cmd
    assert "samtools index" in cmd


def test_gatk_mutect2_call_renders_tumor_normal_flags():
    cmd = gatk_mutect2_call(
        reference_fasta="/refs/genome.fa",
        tumor_bam="/tmp/tumor.bam",
        tumor_sample="tumor",
        normal_bam="/tmp/normal.bam",
        normal_sample="normal",
        output_vcf="/tmp/out/somatic.vcf.gz",
    )
    assert "gatk Mutect2" in cmd
    assert "-normal" in cmd
    assert "-tumor" in cmd
    assert "samtools faidx /refs/genome.fa" in cmd
    assert "CreateSequenceDictionary -R /refs/genome.fa -O /refs/genome.dict" in cmd
    assert "samtools index /tmp/tumor.bam" in cmd
    assert "samtools index /tmp/normal.bam" in cmd


def test_hisat2_align_can_build_index_from_reference():
    cmd = hisat2_align(
        index_base="/tmp/hisat2/genome",
        reference_fasta="/refs/genome.fa",
        cache_index_base="/tmp/cache/hisat2/genome",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        output_sam="/tmp/out/sample.sam",
        threads=2,
    )
    assert "hisat2-build" in cmd
    assert "hisat2 -x" in cmd
    assert "mkdir -p /tmp/out" in cmd


def test_protein_wrapper_commands_render():
    blast_cmd = blastp_search(
        query_fasta="/tmp/query.faa",
        database="swissprot",
        output_tsv="/tmp/out/blast.tsv",
    )
    hmmer_cmd = hmmscan_search(
        query_fasta="/tmp/query.faa",
        hmm_db="/db/Pfam-A.hmm",
        output_tbl="/tmp/out/hmmscan.tbl",
    )
    assert "blastp -query" in blast_cmd
    assert "makeblastdb" in blast_cmd
    assert "-subject" in blast_cmd
    assert "export PATH=" in blast_cmd
    assert "set -euo pipefail;" in hmmer_cmd
    assert "--tblout" in hmmer_cmd


def test_blastp_search_uses_shared_tool_resolution(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.which_with_pixi",
        lambda name: {
            "blastp": "/opt/tools/blastp",
            "makeblastdb": "/opt/tools/makeblastdb",
        }.get(name),
    )
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.shell_path_prefix",
        lambda *names: "/opt/tools",
    )
    cmd = blastp_search(
        query_fasta="/tmp/query.faa",
        database="/tmp/db.faa",
        output_tsv="/tmp/out/blast.tsv",
    )
    assert "export PATH=/opt/tools:$PATH" in cmd
    assert "/opt/tools/blastp -query" in cmd


def test_germline_variant_wrappers_create_parent_output_dirs():
    bcftools_cmd = bcftools_call(
        reference_fasta="/refs/genome.fa",
        input_bam="/tmp/in/sample.bam",
        output_vcf_gz="/tmp/out/germline.vcf.gz",
    )
    gatk_cmd = gatk_haplotypecaller(
        reference_fasta="/refs/genome.fa",
        input_bam="/tmp/in/sample.bam",
        output_vcf="/tmp/out/germline.vcf",
    )
    freebayes_cmd = freebayes_call(
        reference_fasta="/refs/genome.fa",
        input_bam="/tmp/in/sample.bam",
        output_vcf="/tmp/out/germline.vcf",
    )
    varscan_cmd = varscan_call(
        reference_fasta="/refs/genome.fa",
        input_bam="/tmp/in/sample.bam",
        output_vcf="/tmp/out/germline.vcf",
    )
    assert "run_bcftools_call.py" in bcftools_cmd
    assert "mkdir -p" in gatk_cmd
    assert "run_freebayes_call.py" in freebayes_cmd
    assert "samtools faidx /refs/genome.fa" in varscan_cmd
    assert "mpileup2cns" in varscan_cmd


def test_freebayes_wrapper_supports_gzipped_output_and_ploidy():
    cmd = freebayes_call(
        reference_fasta="/refs/genome.fa",
        input_bam="/tmp/in/sample.bam",
        output_vcf_gz="/tmp/out/germline.vcf.gz",
        ploidy=1,
    )
    assert "run_freebayes_call.py" in cmd
    assert "--ploidy 1" in cmd
    assert "--output-vcf-gz /tmp/out/germline.vcf.gz" in cmd


def test_subread_align_builds_index_and_indexes_bam():
    cmd = subread_align(
        index_base="/tmp/subread/genome",
        reference_fasta="/refs/genome.fa",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        output_bam="/tmp/out/sample.bam",
        threads=2,
        cache_index_base="/tmp/cache/subread/genome",
    )
    assert "subread-buildindex" in cmd
    assert "samtools sort" in cmd
    assert "samtools index" in cmd


def test_bowtie2_align_resolves_shared_tool_binaries(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "bio_harness.skills.library.bowtie2_align.which_with_pixi",
        lambda name: {
            "bowtie2-build": "/opt/tools/bowtie2-build",
            "bowtie2": "/opt/tools/bowtie2",
            "samtools": "/opt/tools/samtools",
        }.get(name),
    )
    cmd = bowtie2_align(
        reference_fasta="/refs/genome.fa",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        output_bam="/tmp/out/sample.bam",
    )
    assert "/opt/tools/bowtie2-build /refs/genome.fa" in cmd
    assert "/opt/tools/bowtie2 -x" in cmd
    assert "/opt/tools/samtools sort" in cmd
    assert "/opt/tools/samtools index" in cmd


def test_subread_align_resolves_shared_tool_binaries(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "bio_harness.skills.library.subread_align.which_with_pixi",
        lambda name: {
            "subread-buildindex": "/opt/tools/subread-buildindex",
            "subjunc": "/opt/tools/subjunc",
            "samtools": "/opt/tools/samtools",
        }.get(name),
    )
    cmd = subread_align(
        index_base="/tmp/subread/genome",
        reference_fasta="/refs/genome.fa",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        output_bam="/tmp/out/sample.bam",
        threads=2,
    )
    assert "/opt/tools/subread-buildindex -o /tmp/subread/genome /refs/genome.fa" in cmd
    assert "/opt/tools/subjunc -T 2 -i /tmp/subread/genome" in cmd
    assert "/opt/tools/samtools sort" in cmd
    assert "/opt/tools/samtools index" in cmd


def test_gatk_haplotypecaller_resolves_shared_tool_binary(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "bio_harness.skills.library._gatk_support.which_with_pixi",
        lambda name: {
            "gatk": "/opt/tools/gatk",
            "samtools": "/opt/tools/samtools",
        }.get(name),
    )
    cmd = gatk_haplotypecaller(
        reference_fasta="/refs/genome.fa",
        input_bam="/tmp/in/sample.bam",
        output_vcf="/tmp/out/germline.vcf",
    )
    assert cmd.startswith("mkdir -p /tmp/out && if [ ! -f /refs/genome.fa.fai ]; then /opt/tools/samtools faidx /refs/genome.fa; fi")
    assert "/opt/tools/gatk CreateSequenceDictionary -R /refs/genome.fa -O /refs/genome.dict" in cmd
    assert "/opt/tools/samtools index /tmp/in/sample.bam" in cmd
    assert cmd.endswith("/opt/tools/gatk HaplotypeCaller -R /refs/genome.fa -I /tmp/in/sample.bam -O /tmp/out/germline.vcf")


def test_featurecounts_run_supports_strandedness():
    cmd = featurecounts_run(
        input_bams=["/tmp/a.bam", "/tmp/b.bam"],
        annotation_gtf="/tmp/genes.gff",
        annotation_format="GFF",
        feature_type="gene",
        attribute_type="ID",
        output_counts="/tmp/counts.txt",
        count_read_pairs=True,
        is_paired_end=True,
        strand_specificity=2,
        threads=4,
    )
    assert "--strand-specificity 2" in cmd


def test_deseq2_run_can_render_python_wrapper():
    cmd = deseq2_run(
        counts_matrix="/tmp/counts.txt",
        metadata_table="/tmp/meta.tsv",
        design_formula="~ condition",
        contrast="condition_Biofilm_vs_Plankton",
        output_dir="/tmp/out",
        engine="pydeseq2",
    )
    assert "env PYTHONPATH=" in cmd
    assert str(preferred_helper_python_executable()) in cmd
    assert "pydeseq2_wrapper.py" in cmd


def test_dexseq_run_uses_bundled_wrapper_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "bio_harness.skills.library.dexseq_run.rscript_for_requirement",
        lambda name: "/opt/tools/Rscript" if name == "dexseq" else None,
    )
    cmd = dexseq_run(
        counts_matrix="/tmp/exon_counts.tsv",
        metadata_table="/tmp/meta.tsv",
        design_formula="~ sample + exon + condition:exon",
        contrast="condition_treated_vs_control",
        output_dir="/tmp/dexseq_out",
    )
    assert cmd.startswith("/opt/tools/Rscript ")
    assert "dexseq_wrapper.R" in cmd
    assert "--counts /tmp/exon_counts.tsv" in cmd
    assert "--metadata /tmp/meta.tsv" in cmd


def test_edger_run_uses_bundled_wrapper_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "bio_harness.skills.library.edger_run.rscript_for_requirement",
        lambda name: "/opt/tools/Rscript" if name == "edger" else None,
    )
    cmd = edger_run(
        counts_matrix="/tmp/gene_counts.tsv",
        metadata_table="/tmp/meta.tsv",
        design_formula="~ condition",
        contrast="condition_treated_vs_control",
        output_dir="/tmp/edger_out",
    )
    assert cmd.startswith("/opt/tools/Rscript ")
    assert "edger_wrapper.R" in cmd
    assert "--counts /tmp/gene_counts.tsv" in cmd
    assert "--metadata /tmp/meta.tsv" in cmd


def test_rmats_run_materializes_group_lists_and_calls_wrapper():
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "bio_harness.skills.library.rmats_run.which_with_pixi",
            lambda name: "/opt/tools/rmats.py" if name == "rmats" else None,
        )
        cmd = rmats_run(
            group1_bams=["/tmp/a.bam", "/tmp/b.bam"],
            group2_bams="/tmp/c.bam,/tmp/d.bam",
            annotation_gtf="/refs/genes.gtf",
            output_dir="/tmp/out/rmats",
            tmp_dir="/tmp/out/rmats_tmp",
            read_length=150,
            threads=4,
        )
    assert "group1_bams.txt" in cmd
    assert "group2_bams.txt" in cmd
    assert "run_rmats_if_needed.sh" in cmd
    assert "/refs/genes.gtf" in cmd
    assert "RMATS_BIN=/opt/tools/rmats.py bash" in cmd


def test_rmats_run_uses_packaged_rmats_layout_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_root = tmp_path / ".pixi" / "envs" / "default"
    packaged_dir = env_root / "rMATS"
    packaged_dir.mkdir(parents=True)
    packaged_script = packaged_dir / "rmats.py"
    packaged_script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    python_bin = env_root / "bin" / "python3"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.skills.library.rmats_run.which_with_pixi",
        lambda name: str(env_root / "bin" / "rmats.py") if name == "rmats" else None,
    )

    cmd = rmats_run(
        group1_bams=["/tmp/a.bam"],
        group2_bams=["/tmp/b.bam"],
        annotation_gtf="/refs/genes.gtf",
        output_dir="/tmp/out/rmats",
        tmp_dir="/tmp/out/rmats_tmp",
        read_length=100,
        threads=2,
    )

    assert f"RMATS_BIN={packaged_script}" in cmd
    assert f"RMATS_PYTHON_BIN={python_bin}" in cmd
    assert f"RMATS_PYTHONPATH={packaged_dir}" in cmd


def test_kallisto_quant_can_build_index_from_transcriptome():
    cmd = kallisto_quant(
        index_path="/tmp/cache/kallisto/transcriptome.idx",
        transcriptome_fasta="/refs/transcriptome.fa",
        output_dir="/tmp/out/kallisto",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        threads=4,
    )
    assert "kallisto index -i" in cmd
    assert "/refs/transcriptome.fa" in cmd
    assert "kallisto quant -i /tmp/cache/kallisto/transcriptome.idx" in cmd


def test_salmon_quant_defaults_library_type_and_can_build_index():
    cmd = salmon_quant(
        index_dir="/tmp/cache/salmon/transcriptome",
        transcriptome_fasta="/refs/transcriptome.fa",
        output_dir="/tmp/out/salmon",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        threads=4,
    )
    assert "salmon index -t /refs/transcriptome.fa -i /tmp/cache/salmon/transcriptome" in cmd
    assert "salmon quant -i /tmp/cache/salmon/transcriptome -l A" in cmd
    assert "--validateMappings" in cmd


def test_prodigal_annotate_creates_output_dir_and_runs_prodigal():
    cmd = prodigal_annotate(
        input_fasta="/tmp/assembly/scaffolds.fa",
        output_gff="/tmp/annot/predictions.gff",
        output_faa="/tmp/annot/proteins.faa",
    )
    assert "run_prodigal_annotate.py" in cmd
    assert "--input-fasta /tmp/assembly/scaffolds.fa" in cmd
    assert "--output-gff /tmp/annot/predictions.gff" in cmd
    assert "--output-faa /tmp/annot/proteins.faa" in cmd
    assert "--mode auto" in cmd


def test_prodigal_annotate_can_disable_empty_cds_guard():
    cmd = prodigal_annotate(
        input_fasta="/tmp/assembly/scaffolds.fa",
        output_gff="/tmp/annot/predictions.gff",
        output_faa="/tmp/annot/proteins.faa",
        mode="meta",
        require_cds=False,
    )

    assert "--mode meta" in cmd
    assert "--allow-empty-cds" in cmd


def test_spades_assemble_can_enable_careful_and_isolate_modes():
    # --careful and --isolate are mutually exclusive in SPAdes;
    # when both are requested, --careful takes precedence.
    cmd = spades_assemble(
        reads_1="/tmp/reads_R1.fastq.gz",
        reads_2="/tmp/reads_R2.fastq.gz",
        threads=8,
        memory_gb=32,
        output_dir="/tmp/spades_out",
        careful=True,
        isolate_mode=True,
    )
    assert "--careful" in cmd
    assert "--isolate" not in cmd
    assert "--phred-offset 33" in cmd
    assert "-o /tmp/spades_out" in cmd

    # When only isolate is requested, it should be used.
    cmd2 = spades_assemble(
        reads_1="/tmp/reads_R1.fastq.gz",
        reads_2="/tmp/reads_R2.fastq.gz",
        threads=8,
        memory_gb=32,
        output_dir="/tmp/spades_out",
        careful=False,
        isolate_mode=True,
    )
    assert "--isolate" in cmd2
    assert "--careful" not in cmd2


def test_spades_assemble_can_override_or_disable_phred_offset():
    cmd = spades_assemble(
        reads_1="/tmp/reads_R1.fastq.gz",
        reads_2="/tmp/reads_R2.fastq.gz",
        threads=8,
        memory_gb=32,
        output_dir="/tmp/spades_out",
        phred_offset=64,
    )
    assert "--phred-offset 64" in cmd

    auto_cmd = spades_assemble(
        reads_1="/tmp/reads_R1.fastq.gz",
        reads_2="/tmp/reads_R2.fastq.gz",
        threads=8,
        memory_gb=32,
        output_dir="/tmp/spades_out",
        phred_offset="auto",
    )
    assert "--phred-offset" not in auto_cmd


def test_spades_assemble_rejects_invalid_phred_offset():
    with pytest.raises(ValueError, match="Unsupported SPAdes phred_offset"):
        spades_assemble(
            reads_1="/tmp/reads_R1.fastq.gz",
            reads_2="/tmp/reads_R2.fastq.gz",
            threads=8,
            memory_gb=32,
            output_dir="/tmp/spades_out",
            phred_offset=42,
        )


def test_snpeff_annotate_can_build_custom_database():
    cmd = snpeff_annotate(
        genome_db="ecoli_custom",
        reference_fasta="/tmp/assembly/scaffolds.fa",
        annotation_gff="/tmp/annot/predictions.gff",
        config_dir="/tmp/snpeff_work",
        input_vcf="/tmp/vars/shared.vcf",
        output_vcf="/tmp/vars/shared.anno.vcf",
    )
    assert "snpEff -Xmx8g build -c /tmp/snpeff_work/snpEff.config" in cmd
    assert "snpEff.config -gff3 -v -noCheckCds -noCheckProtein ecoli_custom" in cmd
    assert "if [ ! -e /tmp/vars/_staging/snpeff/shared.vcf ]" in cmd
    assert "! [ /tmp/vars/shared.vcf -ef /tmp/vars/_staging/snpeff/shared.vcf ]" in cmd
    assert "cp -f /tmp/vars/shared.vcf /tmp/vars/_staging/snpeff/shared.vcf" in cmd
    assert (
        "-c /tmp/snpeff_work/snpEff.config ecoli_custom "
        "/tmp/vars/_staging/snpeff/shared.vcf > /tmp/vars/shared.anno.vcf"
    ) in cmd


def test_snpeff_annotate_derives_config_dir_from_output_when_build_inputs_present():
    cmd = snpeff_annotate(
        genome_db="ecoli_custom",
        reference_fasta="/tmp/assembly/scaffolds.fa",
        annotation_gff="/tmp/annot/predictions.gff",
        input_vcf="/tmp/vars/shared.vcf",
        output_vcf="/tmp/vars/shared.anno.vcf",
    )
    assert "/tmp/vars/_snpeff/ecoli_custom/snpEff.config" in cmd


def test_snpeff_annotate_clears_numeric_codon_table_for_custom_database_build() -> None:
    cmd = snpeff_annotate(
        genome_db="ancestor",
        reference_fasta="/tmp/assembly/scaffolds.fa",
        annotation_gff="/tmp/annot/predictions.gff",
        config_dir="/tmp/snpeff_work",
        input_vcf="/tmp/vars/shared.vcf.gz",
        output_vcf="/tmp/vars/shared.anno.vcf",
        codon_table="11",
    )

    assert "ancestor.codonTable" not in cmd


def test_snpeff_annotate_clears_bacterial_codon_table_for_custom_database_build() -> None:
    cmd = snpeff_annotate(
        genome_db="ancestor",
        reference_fasta="/tmp/assembly/scaffolds.fa",
        annotation_gff="/tmp/annot/predictions.gff",
        config_dir="/tmp/snpeff_work",
        input_vcf="/tmp/vars/shared.vcf.gz",
        output_vcf="/tmp/vars/shared.anno.vcf",
        codon_table="Bacterial",
    )

    assert "ancestor.codonTable" not in cmd


def test_snpeff_annotate_uses_inode_safe_stage_guard_for_relative_inputs(tmp_path: Path):
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    input_vcf = selected_dir / "evol1_subtracted_anc.vcf.gz"
    input_vcf.write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    cmd = snpeff_annotate(
        genome_db="ecoli_custom",
        reference_fasta=str(selected_dir / "anc.fa"),
        annotation_gff=str(selected_dir / "anc.gff"),
        config_dir=str(selected_dir / "_snpeff" / "ecoli_custom"),
        input_vcf=f"./{input_vcf.name}",
        output_vcf=str(selected_dir / "evol1_annotated.vcf"),
    )

    assert "if [ ! -e " in cmd
    assert " -ef " in cmd
    assert "_staging/snpeff/evol1_subtracted_anc.vcf.gz" in cmd


def test_snpeff_annotate_creates_output_parent_for_standard_annotation():
    cmd = snpeff_annotate(
        genome_db="GRCh37.75",
        input_vcf="/tmp/in.vcf",
        output_vcf="/tmp/nested/out/annotated.vcf",
    )

    assert cmd.startswith("mkdir -p /tmp/nested/out && ")
    assert "-Xmx8g GRCh37.75 /tmp/in.vcf > /tmp/nested/out/annotated.vcf" in cmd


def test_snpeff_annotate_reuses_existing_ann_for_standard_annotation():
    cmd = snpeff_annotate(
        genome_db="GRCh38",
        input_vcf="/tmp/in.eff.vcf",
        output_vcf="/tmp/nested/out/annotated.vcf",
    )

    assert str(preferred_helper_python_executable()) in cmd
    assert "reuse_existing_annotated_vcf.py" in cmd
    assert "python3 -c '" not in cmd
    assert "/tmp/in.eff.vcf /tmp/nested/out/annotated.vcf" in cmd
    assert "||" in cmd
    assert "-Xmx8g GRCh38 /tmp/in.eff.vcf > /tmp/nested/out/annotated.vcf" in cmd


def test_snpeff_annotate_can_disable_existing_ann_passthrough():
    cmd = snpeff_annotate(
        genome_db="GRCh38",
        input_vcf="/tmp/in.eff.vcf",
        output_vcf="/tmp/nested/out/annotated.vcf",
        reuse_existing_annotations=False,
    )

    assert "##INFO=<ID=ANN" not in cmd
    assert "||" not in cmd
    assert "-Xmx8g GRCh38 /tmp/in.eff.vcf > /tmp/nested/out/annotated.vcf" in cmd


def test_snpeff_annotate_honors_explicit_java_heap_size():
    cmd = snpeff_annotate(
        genome_db="GRCh37.75",
        input_vcf="/tmp/in.vcf",
        output_vcf="/tmp/nested/out/annotated.vcf",
        java_mem_gb=12,
    )

    assert "-Xmx12g GRCh37.75 /tmp/in.vcf > /tmp/nested/out/annotated.vcf" in cmd


def test_snpeff_annotate_uses_environment_java_heap_size(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIO_HARNESS_SNPEFF_JAVA_MEM_GB", "6")
    cmd = snpeff_annotate(
        genome_db="GRCh37.75",
        input_vcf="/tmp/in.vcf",
        output_vcf="/tmp/nested/out/annotated.vcf",
    )

    assert "-Xmx6g GRCh37.75 /tmp/in.vcf > /tmp/nested/out/annotated.vcf" in cmd


def test_minimap2_align_resolves_reads_from_inputs_readonly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    inputs = workspace / "inputs_readonly"
    inputs.mkdir(parents=True, exist_ok=True)
    ref = workspace / "ref.fa"
    query = inputs / "nanopore_reads.fastq"
    ref.write_text(">chr1\nACGT\n", encoding="utf-8")
    query.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    monkeypatch.chdir(workspace)
    cmd = minimap2_align(
        reference_fasta=str(ref),
        reads="nanopore_reads.fastq",
        output_bam="aligned.bam",
        preset="map-ont",
    )
    assert "inputs_readonly/nanopore_reads.fastq" in cmd


def test_fallback_skill_builder_wrapper_renders_required_flags():
    cmd = fallback_skill_builder(
        target_capability_set="alignment,variant_calling",
        allowed_tools="bcftools,bwa,samtools",
        data_reference_constraints='{"required_paths":["inputs_readonly"]}',
        strictness_mode="conservative",
        request_text="Call variants.",
        out_json="/tmp/fallback_builder_report.json",
    )
    assert "set -euo pipefail;" in cmd
    assert "python3 -m scripts.fallback_skill_builder" in cmd or " -m scripts.fallback_skill_builder" in cmd
    assert "/Users/" not in cmd
    assert "--target-capabilities" in cmd
    assert "--allowed-tools" in cmd
    assert 'if [ "$status" -eq 2 ] && [ -s /tmp/fallback_builder_report.json ]' in cmd


def test_fallback_skill_builder_wrapper_normalizes_iterables_and_json_objects():
    cmd = fallback_skill_builder(
        target_capability_set=["alignment", "variant_calling"],
        allowed_tools=["bash", "python"],
        data_reference_constraints={
            "required_paths": ["/data/input.txt"],
            "reference_fasta": "/refs/genome.fa",
        },
        strictness_mode="conservative",
        request_text="Call variants.",
        out_json="/tmp/fallback_builder_report.json",
    )
    assert "--target-capabilities alignment,variant_calling" in cmd
    assert "--allowed-tools bash,python" in cmd
    assert '"required_paths"' in cmd
    assert '"reference_fasta"' in cmd


def test_fallback_skill_builder_wrapper_normalizes_small_model_drift_tokens():
    cmd = fallback_skill_builder(
        target_capability_set="coverage_report,coverage_report_generation",
        allowed_tools=["fastp_run", "edger_run"],
        data_reference_constraints="input_files_must_be_present",
        strictness_mode="strict",
        request_text="",
        out_json="/tmp/fallback_builder_report.json",
    )
    assert "--target-capabilities run_reporting" in cmd
    assert "--strictness-mode conservative" in cmd
    assert "--data-constraints-json '{}'" in cmd


def test_fallback_skill_builder_wrapper_uses_repo_neutral_default_output_path():
    cmd = fallback_skill_builder(
        target_capability_set="alignment",
        allowed_tools="python",
        data_reference_constraints="{}",
        strictness_mode="conservative",
    )

    assert "workspace/outputs/fallback/fallback_skill_builder_report.json" in cmd
    assert "/Users/" not in cmd


def test_fallback_skill_builder_definition_has_no_author_local_paths() -> None:
    definition = (
        Path(__file__).resolve().parents[2]
        / "bio_harness"
        / "skills"
        / "definitions"
        / "fallback_skill_builder.md"
    ).read_text(encoding="utf-8")

    assert "/Users/" not in definition
    assert "tools_required:\n- python\n" in definition
    assert "- pytest" not in definition
    assert "python3 -m scripts.fallback_skill_builder" in definition
