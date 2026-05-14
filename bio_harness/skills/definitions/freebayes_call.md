---
name: freebayes_call
description: Call variants with haplotype-based FreeBayes.
when_to_use: Use for haplotype-based variant calling from aligned BAM data
when_not_to_use: Not for somatic variant calling (use Mutect2) or large cohorts (use GATK)
risk_level: medium
tools_required:
- freebayes
- samtools
capabilities:
- variant_calling
- reference_inputs
input_types:
- bam
- fasta_reference
output_types:
- vcf
analysis_categories:
- variant_calling
- evolution
- germline
parameters:
  reference_fasta:
    type: path
    description: Reference FASTA path.
    required: true
    file_role: reference_genome
  input_bam:
    type: path
    description: Input BAM file.
    required: true
    file_role: input_bam
  output_vcf:
    type: path
    description: Output VCF path.
    required: false
    file_role: output_dir
  output_vcf_gz:
    type: path
    description: Optional bgzip-compressed VCF output path.
    required: false
    file_role: output_dir
  ploidy:
    type: integer
    description: Optional organism ploidy passed to FreeBayes with -p.
    required: false
system_requirements:
  min_ram_gb: 8
  min_cores: 4
command_template: python3 bio_harness/pipeline_scripts/run_freebayes_call.py --reference-fasta {reference_fasta} --input-bam {input_bam}
---
Use for haplotype-based variant calling, including pooled or complex calling
settings through the documented wrapper parameters and deterministic harness
scaffolding.

If `output_vcf_gz` is requested, the harness also requires `bgzip` and `tabix`
for deterministic compression and indexing. That helper path fails early with a
clear message when those binaries are unavailable.

## Onboarding Metadata
- Source: https://github.com/freebayes/freebayes
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:alignment_variant_core
