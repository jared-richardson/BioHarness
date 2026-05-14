---
name: dexseq_run
description: Run exon-level differential usage analysis via a DEXSeq-compatible Rscript interface.
when_to_use: Use for differential exon usage analysis in RNA-seq data
when_not_to_use: Not for gene-level differential expression (use DESeq2/edgeR/limma)
risk_level: medium
tools_required:
- rscript
capabilities:
- splicing_analysis
- differential_analysis
- group_comparison
input_types:
- tsv
- gtf
output_types:
- tsv
analysis_categories:
- rna_seq_differential_expression
parameters:
  script_path:
    type: path
    description: Path to DEXSeq-compatible R wrapper.
    required: false
  counts_matrix:
    type: path
    description: Exon-level count matrix.
    required: true
  metadata_table:
    type: path
    description: Sample metadata table.
    required: true
  design_formula:
    type: string
    description: Design formula (for example ~ condition).
    required: true
  contrast:
    type: string
    description: Contrast string (for example condition_treatment_vs_control).
    required: true
  output_dir:
    type: path
    description: Output directory.
    required: true
system_requirements:
  min_ram_gb: 8
  min_cores: 2
command_template: Rscript {script_path} --counts {counts_matrix} --metadata {metadata_table} --design {design_formula} --contrast
  {contrast} --outdir {output_dir}
---
Use for deterministic alternative-splicing fallback plans when exon-level tests are requested.
