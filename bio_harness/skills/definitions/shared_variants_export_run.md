---
name: shared_variants_export_run
description: Export shared annotated variants from two VCF inputs to CSV as one atomic helper-backed step.
when_to_use: Use to build a shared-variant CSV from two annotated or normalized VCF inputs
when_not_to_use: Do not use when the workflow still needs annotation, normalization, or comparison setup first
risk_level: medium
tools_required: []
capabilities:
- variant_annotation
- reporting
- shared_variant_export
input_types:
- vcf
output_types:
- csv
analysis_categories:
- variant_annotation
- comparative_genomics
parameters:
  input_vcf_a:
    type: path
    description: First annotated or normalized VCF input.
    required: true
    file_role: input_vcf
  input_vcf_b:
    type: path
    description: Second annotated or normalized VCF input.
    required: true
    file_role: input_vcf
  output_csv:
    type: path
    description: Output CSV path.
    required: true
    file_role: output_dir
  min_impact:
    type: string
    description: Minimum impact tier to keep (`LOW`, `MODERATE`, or `HIGH`).
    required: false
  status:
    type: string
    description: Status label written into the export rows.
    required: false
  header_case:
    type: string
    description: Header case for the CSV (`upper` or `lower`).
    required: false
  dedupe_by_gene:
    type: boolean
    description: Whether to keep only one row per gene.
    required: false
system_requirements:
  min_ram_gb: 2
  min_cores: 1
command_template: python3 bio_harness/pipeline_scripts/export_shared_variants_csv.py --input-vcf-a {input_vcf_a} --input-vcf-b {input_vcf_b} --output-csv {output_csv}
---
Use for one deterministic shared-variant CSV export. The wrapper invokes the
checked-in helper directly and keeps the planner-visible step atomic.

## Onboarding Metadata
- Source: repo_local_helper
- Source Mode: curated_helper
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:alignment_variant_core
