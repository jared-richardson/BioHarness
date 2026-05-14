---
name: seurat_rscript_workflow
description: Run Seurat workflow from an Rscript wrapper for CLI orchestration.
when_to_use: Use for single-cell RNA-seq analysis with Seurat in R
when_not_to_use: Not for Python-based analysis (use scanpy) or bulk RNA-seq
risk_level: medium
tools_required:
- rscript
- seurat
capabilities:
- single_cell_analysis
input_types:
- h5ad
- csv
- mtx
output_types:
- h5ad
- csv
- png
analysis_categories:
- single_cell_rna_seq
parameters:
  script_path:
    type: path
    description: Path to Seurat wrapper script.
    required: false
    ownership: harness_managed
  input_matrix:
    type: path
    description: Input matrix path (e.g. MTX/H5).
    required: true
    file_role: input_matrix
    ownership: user_input
  metadata_table:
    type: path
    description: Cell metadata table.
    required: true
    file_role: sample_metadata
    ownership: user_input
  output_dir:
    type: path
    description: Output directory.
    required: true
    file_role: output_dir
    ownership: execution_output
system_requirements:
  min_ram_gb: 16
  min_cores: 4
command_template: Rscript {script_path} --matrix {input_matrix} --metadata {metadata_table} --output-dir {output_dir}
---
Use for Seurat analyses integrated into non-interactive pipeline runs.

## Onboarding Metadata
- Source: https://satijalab.org/seurat/
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:single_cell_core
