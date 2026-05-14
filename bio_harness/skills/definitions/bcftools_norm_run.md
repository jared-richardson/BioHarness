---
name: bcftools_norm_run
description: Normalize or atomize one VCF with bcftools norm as a single atomic wrapper step.
when_to_use: Use for one VCF normalization step that needs a reference FASTA and a deterministic output path
when_not_to_use: Do not use for multi-operation shell chains that also index, intersect, or export downstream summaries
risk_level: medium
tools_required:
- bcftools
capabilities:
- variant_calling
- normalization
input_types:
- vcf
- fasta_reference
output_types:
- vcf
analysis_categories:
- variant_calling
- variant_annotation
parameters:
  input_vcf:
    type: path
    description: Input VCF or VCF.GZ path to normalize.
    required: true
    file_role: input_vcf
  reference_fasta:
    type: path
    description: Reference FASTA used by bcftools norm.
    required: true
    file_role: reference_genome
  output_vcf:
    type: path
    description: Output normalized VCF or VCF.GZ path.
    required: true
    file_role: output_dir
  multiallelic_mode:
    type: string
    description: Optional bcftools norm multiallelic mode (`+any`, `-any`, or `none` to omit `-m`).
    required: false
  atomize:
    type: boolean
    description: Whether to pass `--atomize`.
    required: false
system_requirements:
  min_ram_gb: 4
  min_cores: 1
command_template: python3 bio_harness/pipeline_scripts/run_bcftools_norm.py --input-vcf {input_vcf} --reference-fasta {reference_fasta} --output-vcf {output_vcf}
---
Use for one benchmark-blind VCF normalization step. The wrapper creates the
output directory internally and renders exactly one helper-backed command so it
stays compatible with the atomic-step shell policy.

## Onboarding Metadata
- Source: https://samtools.github.io/bcftools/bcftools.html
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:alignment_variant_core
