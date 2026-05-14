---
name: bcftools_call
description: Call variants via bcftools mpileup + call pipeline.
when_to_use: Use for variant calling via bcftools mpileup+call pipeline on aligned BAM data
when_not_to_use: Not for somatic calling or haplotype-aware germline calling (use GATK or FreeBayes)
risk_level: medium
tools_required:
- bcftools
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
    description: Input BAM/CRAM file.
    required: true
    file_role: input_bam
  output_vcf_gz:
    type: path
    description: Compressed output VCF path.
    required: true
    file_role: output_dir
system_requirements:
  min_ram_gb: 8
  min_cores: 4
command_template: python3 bio_harness/pipeline_scripts/run_bcftools_call.py --reference-fasta {reference_fasta} --input-bam {input_bam} --output-vcf-gz {output_vcf_gz}
---
Use for lightweight germline variant calling in WGS/WES pipelines.

## Onboarding Metadata
- Source: https://samtools.github.io/bcftools/bcftools.html
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:alignment_variant_core
