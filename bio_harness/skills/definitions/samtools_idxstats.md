---
name: samtools_idxstats
description: Report per-reference mapped and unmapped read counts with samtools idxstats.
when_to_use: Use for deterministic per-contig alignment count summaries from indexed BAM or CRAM files
when_not_to_use: Do not use for aggregate mapping summaries (use samtools_flagstat) or full statistics (use samtools_stats)
risk_level: low
tools_required:
- samtools
capabilities:
- alignment_qc
input_types:
- bam
output_types:
- tsv
analysis_categories:
- general
parameters:
  input_bam:
    type: path
    description: Input BAM or CRAM path.
    required: true
    file_role: input_bam
  output_tsv:
    type: path
    description: Output path for the idxstats table.
    required: true
    file_role: output_dir
system_requirements:
  min_ram_gb: 2
  min_cores: 1
command_template: samtools idxstats {input_bam} > {output_tsv}
---
Use for deterministic per-reference alignment counts when the harness should
own the exact `samtools idxstats` command and output location.
