---
name: macs2_atacseq_callpeak
description: Call ATAC-seq accessibility peaks with MACS2/3 tuned flags.
when_to_use: Use for ATAC-seq peak calling to identify open chromatin regions
when_not_to_use: Not for ChIP-seq (use macs2_chipseq_callpeak) or RNA-seq analysis
risk_level: medium
tools_required:
- macs2
capabilities:
- atacseq_analysis
input_types:
- bam
output_types:
- bed
- tsv
analysis_categories:
- atacseq_analysis
parameters:
  treatment_bam:
    type: path
    description: ATAC paired-end BAM file.
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
command_template: macs2 callpeak -t {treatment_bam} -f BAMPE -g {genome_size} -n {name} --outdir {output_dir} --nomodel --shift
  -100 --extsize 200
---
Use for ATAC-seq peak calling with paired-end fragment mode.

## Onboarding Metadata
- Source: https://macs3-project.github.io/MACS/docs/callpeak.html
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:chromatin_core