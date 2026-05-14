---
name: deseq2_run
description: Run DESeq2 differential expression from count matrix + sample metadata.
when_to_use: Use for differential gene expression analysis with DESeq2 from a counts matrix
when_not_to_use: Not for transcript-level DE (use Sleuth) or when sample size < 3 per group
risk_level: medium
tools_required:
- rscript
- deseq2
capabilities:
- differential_analysis
- group_comparison
input_types:
- tsv
- csv
output_types:
- tsv
- csv
analysis_categories:
- rna_seq_differential_expression
- multi_model_dge_pathway
parameters:
  script_path:
    type: path
    description: Path to DESeq2 wrapper R script.
    required: false
    ownership: harness_managed
  counts_matrix:
    type: path
    description: Gene/sample counts TSV matrix.
    required: true
    file_role: counts_matrix
    ownership: user_input
  metadata_table:
    type: path
    description: Sample metadata table.
    required: true
    file_role: sample_metadata
    ownership: user_input
  design_formula:
    type: string
    description: Design formula (e.g. ~ condition).
    required: true
    ownership: tuning
  contrast:
    type: string
    description: Contrast tuple/list label.
    required: true
    ownership: tuning
  output_dir:
    type: path
    description: Output directory for DE tables.
    required: true
    file_role: output_dir
    ownership: execution_output
  engine:
    type: string
    description: Optional implementation engine. Use `pydeseq2` to run the bundled Python backend instead of the default R/DESeq2 wrapper.
    required: false
    ownership: tuning
system_requirements:
  min_ram_gb: 8
  min_cores: 2
command_template: Rscript {script_path} --counts {counts_matrix} --metadata {metadata_table} --design {design_formula} --contrast
  {contrast} --outdir {output_dir}
---
Use for count-based RNA-seq differential expression with explicit group contrasts.

## Onboarding Metadata
- Source: https://bioconductor.org/packages/DESeq2/
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:expression_core
