---
name: bedtools_genomecov
description: Generate genome coverage profiles with bedtools genomecov from BAM or interval inputs.
when_to_use: Use for deterministic coverage track generation or depth profiling across a genome
when_not_to_use: Do not use for simple overlap counts between two BED files or for BAM QC text summaries
risk_level: low
tools_required:
- bedtools
capabilities:
- interval_operations
- coverage_profiling
input_types:
- bam
- bed
output_types:
- bedgraph
analysis_categories:
- general
parameters:
  input_bam:
    type: path
    description: Optional BAM or CRAM input for genomecov -ibam mode.
    required: false
    file_role: input_bam
  input_bed:
    type: path
    description: Optional BED or interval input for genomecov -i mode.
    required: false
  genome_file:
    type: path
    description: Genome sizes file required when input_bed is used.
    required: false
  output_file:
    type: path
    description: Output path for the coverage profile.
    required: true
    file_role: output_dir
  report_mode:
    type: string
    description: Coverage reporting mode (bedgraph, bedgraph_all, histogram, per_base).
    required: false
  split_intervals:
    type: boolean
    description: Treat split alignments or BED12 blocks as distinct intervals.
    required: false
  strand:
    type: string
    description: Optional strand restriction (+ or -).
    required: false
system_requirements:
  min_ram_gb: 2
  min_cores: 1
command_template: bedtools genomecov -ibam {input_bam} -bg > {output_file}
---
Use for deterministic coverage-profile generation when the harness should own
the exact `bedtools genomecov` mode and output path.
