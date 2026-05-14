---
name: bedtools_intersect
description: Intersect two genomic interval sets with bedtools and write the overlap result.
when_to_use: Use for deterministic overlap operations between two BED or interval-like files
when_not_to_use: Do not use for BAM alignment QC summaries or for non-genomic tabular joins
risk_level: low
tools_required:
- bedtools
capabilities:
- interval_operations
input_types:
- bed
output_types:
- bed
- tsv
analysis_categories:
- general
parameters:
  a_intervals:
    type: path
    description: Primary BED or interval file supplied as -a.
    required: true
  b_intervals:
    type: path
    description: Secondary BED or interval file supplied as -b.
    required: true
  output_file:
    type: path
    description: Output path for the intersect result.
    required: true
    file_role: output_dir
  report_mode:
    type: string
    description: Optional bedtools intersect reporting mode (default, wa, wb, wawb, wao, loj, u, v, c).
    required: false
  sorted_input:
    type: boolean
    description: Whether the input interval files are already coordinate-sorted.
    required: false
  min_overlap_fraction:
    type: number
    description: Optional minimum fraction of A that must overlap B.
    required: false
  require_reciprocal_overlap:
    type: boolean
    description: Require reciprocal overlap when min_overlap_fraction is provided.
    required: false
system_requirements:
  min_ram_gb: 2
  min_cores: 1
command_template: bedtools intersect -a {a_intervals} -b {b_intervals} > {output_file}
---
Use for deterministic interval-overlap operations where the harness should own
the exact `bedtools intersect` invocation instead of leaving it to `bash_run`.
