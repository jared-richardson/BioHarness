---
name: bcftools_filter_run
description: Filter one VCF with bcftools filter as a single atomic wrapper step.
when_to_use: Use for one deterministic VCF filtering step that keeps only variants matching a bcftools include expression
when_not_to_use: Do not use when the workflow still needs subtraction, normalization, indexing, or CSV export in the same step
risk_level: medium
tools_required:
- bcftools
capabilities:
- variant_calling
- filtering
input_types:
- vcf
output_types:
- vcf
analysis_categories:
- variant_calling
- comparative_genomics
parameters:
  input_vcf:
    type: path
    description: Input VCF or VCF.GZ path to filter.
    required: true
    file_role: input_vcf
  output_vcf:
    type: path
    description: Output filtered VCF or VCF.GZ path.
    required: true
    file_role: output_dir
  filter_expression:
    type: string
    description: Include expression passed to `bcftools filter -i`.
    required: true
  output_type:
    type: string
    description: Output encoding (`v`, `z`, or `b`). Defaults to bgzipped VCF (`z`).
    required: false
  soft_filter_name:
    type: string
    description: Optional bcftools filter label to attach to the emitted records.
    required: false
system_requirements:
  min_ram_gb: 2
  min_cores: 1
command_template: python3 bio_harness/pipeline_scripts/run_bcftools_filter.py --input-vcf {input_vcf} --output-vcf {output_vcf} --filter-expression {filter_expression}
---
Use for one deterministic VCF filtering step. This wrapper creates the output
directory internally and keeps the planner-visible operation to one helper
invocation. Keep tabix indexing as a separate `tabix_index_run` step so the
workflow remains fully transparent.

## Onboarding Metadata
- Source: https://samtools.github.io/bcftools/bcftools.html
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:alignment_variant_core
