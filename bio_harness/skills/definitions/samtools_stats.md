---
name: samtools_stats
description: Compute detailed alignment statistics from BAM or CRAM with samtools stats.
when_to_use: Use for deterministic detailed alignment QC metrics beyond flagstat or idxstats
when_not_to_use: Do not use for interval overlap operations or lightweight mapped-read counts alone
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
    description: Output path for the samtools stats report.
    required: true
    file_role: output_dir
  threads:
    type: integer
    description: Thread count for samtools stats.
    required: false
  reference_fasta:
    type: path
    description: Optional reference FASTA for CRAM-aware statistics.
    required: false
    file_role: reference_genome
system_requirements:
  min_ram_gb: 2
  min_cores: 1
command_template: samtools stats -@ {threads} {input_bam} > {output_txt}
---
Use for deterministic detailed alignment-statistics reporting from indexed BAM
or CRAM files.
