---
name: macs2_chipseq_callpeak
description: Call ChIP-seq peaks with MACS2/3 callpeak mode.
when_to_use: Use for ChIP-seq peak calling to identify protein-DNA binding sites
when_not_to_use: Not for ATAC-seq (use macs2_atacseq_callpeak) or RNA-seq analysis
risk_level: medium
tools_required:
- macs2
capabilities:
- chipseq_analysis
input_types:
- bam
output_types:
- bed
- tsv
analysis_categories:
- chipseq_analysis
parameters:
  treatment_bam:
    type: path
    description: Treatment BAM file.
    required: true
  control_bam:
    type: path
    description: Matched control/input BAM file.
    required: true
  genome_size:
    type: string
    description: Effective genome size (hs, mm, etc.).
    required: true
  name:
    type: string
    description: Output sample label.
    required: true
  output_dir:
    type: path
    description: Output directory.
    required: true
system_requirements:
  min_ram_gb: 8
  min_cores: 2
command_template: macs2 callpeak -t {treatment_bam} -c {control_bam} -f BAM -g {genome_size} -n {name} --outdir {output_dir}
---
Use for TF/histone ChIP-seq peak calling with treatment/control BAMs.

## Onboarding Metadata
- Source: https://macs3-project.github.io/MACS/docs/callpeak.html
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:chromatin_core