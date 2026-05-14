---
name: majiq_run
description: Build splice graphs and run MAJIQ deltapsi comparison for two sample groups.
when_to_use: Use for local splicing variation analysis in RNA-seq data
when_not_to_use: Not for differential gene expression (use DESeq2) or exon usage (use DEXSeq)
risk_level: medium
tools_required:
- majiq
capabilities:
- splicing_analysis
- group_comparison
input_types:
- bam
- gff
output_types:
- tsv
analysis_categories:
- rna_seq_differential_expression
parameters:
  config_file:
    type: path
    description: MAJIQ build config file.
    required: false
  group1_bams:
    type: string
    description: Group 1 BAM path(s) or list token.
    required: false
  group2_bams:
    type: string
    description: Group 2 BAM path(s) or list token.
    required: false
  output_dir:
    type: path
    description: Output directory.
    required: false
  analysis_name:
    type: string
    description: Comparison label.
    required: false
  threads:
    type: integer
    description: Thread count.
    required: false
system_requirements:
  min_ram_gb: 8
  min_cores: 2
command_template: majiq build -j {threads} -c {config_file} -o {output_dir} && majiq deltapsi -j {threads} -grp1 {group1_bams}
  -grp2 {group2_bams} -n {analysis_name} -o {output_dir}
---
Use when a MAJIQ-based splicing fallback is preferred and MAJIQ is available.
