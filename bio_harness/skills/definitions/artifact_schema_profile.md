---
name: artifact_schema_profile
description: Build a compact schema and column-level data dictionary for a completed artifact such as CSV, TSV, VCF, GTF,
  or JSONL.
when_to_use: Use after a run finishes to inspect the exact columns, inferred types, and example values of a data artifact
when_not_to_use: Do not use to execute or modify the scientific workflow itself
risk_level: low
tools_required:
- python3
capabilities:
- artifact_schema_profiling
input_types:
- csv
- tsv
- vcf
- gtf
- gff
- jsonl
output_types:
- json
analysis_categories:
- general
parameters:
  input_path:
    type: path
    description: Path to the completed artifact that should be profiled.
    required: true
    file_role: input_fasta
  output_json:
    type: path
    description: Optional path for the emitted schema JSON report.
    required: false
    file_role: output_dir
  sample_rows:
    type: integer
    description: Number of data rows to sample for type inference.
    required: false
system_requirements:
  min_ram_gb: 2
  min_cores: 1
---
# Usage Guide

- Case 1: Profile a final benchmark CSV before sharing it with a collaborator.
- Case 2: Inspect a VCF header and sample columns before writing a downstream export step.
- Case 3: Build a compact data dictionary for a run report.

# Common Pitfalls

- Large files are sampled rather than read in full, so rare tail-only values may not appear in examples.
- This skill reports structure; it does not validate biological correctness.