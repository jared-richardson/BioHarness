---
name: samtools_flagstat
description: Compute alignment summary counts from BAM or CRAM with samtools flagstat.
when_to_use: Use for deterministic alignment QC summaries such as mapped, paired, duplicate, and properly paired counts
when_not_to_use: Do not use for per-reference counts (use samtools_idxstats) or full detailed metrics (use samtools_stats)
risk_level: low
tools_required:
- samtools
capabilities:
- alignment_qc
input_types:
- bam
output_types:
- txt
analysis_categories:
- general
parameters:
  input_bam:
    type: path
    description: Input BAM or CRAM path.
    required: true
    file_role: input_bam
  output_txt:
    type: path
    description: Output path for the flagstat report.
    required: true
    file_role: output_dir
  threads:
    type: integer
    description: Thread count for samtools flagstat.
    required: false
system_requirements:
  min_ram_gb: 2
  min_cores: 1
command_template: samtools flagstat -@ {threads} {input_bam} > {output_txt}
---
Use for deterministic BAM or CRAM alignment-summary reporting without falling
back to generic shell execution.
