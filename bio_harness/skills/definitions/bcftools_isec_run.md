---
name: bcftools_isec_run
description: Perform one bcftools isec set operation as a single atomic wrapper step.
when_to_use: Use for one deterministic intersection, complement, or private-variant operation across multiple VCF inputs
when_not_to_use: Do not use when the workflow still needs additional normalization or CSV export in the same step
risk_level: medium
tools_required:
- bcftools
capabilities:
- variant_calling
- set_operations
input_types:
- vcf
output_types:
- directory
- vcf
analysis_categories:
- variant_calling
- comparative_genomics
parameters:
  input_vcfs:
    type: list[path]
    description: Two or more VCF inputs for bcftools isec.
    required: true
    file_role: input_vcf
  output_dir:
    type: path
    description: Output directory where bcftools isec writes numbered VCF results.
    required: true
    file_role: output_dir
  output_vcf:
    type: path
    description: Optional stable named VCF copied from the first numbered isec result.
    required: false
    file_role: output_vcf
  mode:
    type: string
    description: Set-operation mode (`intersection`, `complement`, or `private`).
    required: false
  min_matches:
    type: integer
    description: Minimum matching input count for intersection mode.
    required: false
system_requirements:
  min_ram_gb: 4
  min_cores: 1
command_template: python3 bio_harness/pipeline_scripts/run_bcftools_isec.py --input-vcf {input_vcfs} --output-dir {output_dir} --output-vcf {output_vcf}
---
Use for one explicit bcftools isec operation. The wrapper creates the output
directory internally and can also materialize one stable branch-named VCF for
downstream steps while keeping the visible planner step to a single helper
invocation.

## Onboarding Metadata
- Source: https://samtools.github.io/bcftools/bcftools.html
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:alignment_variant_core
