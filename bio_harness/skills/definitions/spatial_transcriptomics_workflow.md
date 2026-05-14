---
name: spatial_transcriptomics_workflow
description: Run a deterministic processed-input spatial transcriptomics workflow on an AnnData spatial dataset.
when_to_use: Use for processed Visium-style or AnnData spatial transcriptomics inputs with spot coordinates already present.
when_not_to_use: Not for raw FASTQ/image preprocessing or Space Ranger-style reconstruction from raw Visium data.
risk_level: medium
tools_required:
- python
capabilities:
- spatial_transcriptomics
- single_cell_analysis
input_types:
- h5ad
output_types:
- csv
- h5ad
- json
analysis_categories:
- spatial_transcriptomics
parameters:
  input_path:
    type: path
    description: Input spatial AnnData `.h5ad` path.
    required: true
    file_role: input_h5ad
    ownership: user_input
  script_path:
    type: path
    description: Optional path to a custom spatial workflow script. Defaults to the bundled deterministic workflow.
    required: false
    ownership: harness_managed
  output_dir:
    type: path
    description: Output directory for canonical spatial result artifacts.
    required: true
    file_role: output_dir
    ownership: execution_output
  domain_assignments_csv:
    type: path
    description: Optional explicit path for the domain-assignment CSV.
    required: false
    file_role: output_csv
    ownership: execution_output
  marker_genes_csv:
    type: path
    description: Optional explicit path for the marker-gene CSV.
    required: false
    file_role: output_csv
    ownership: execution_output
  results_h5ad:
    type: path
    description: Optional explicit path for the processed output AnnData file.
    required: false
    file_role: output_h5ad
    ownership: execution_output
  min_genes:
    type: integer
    description: Minimum detected genes per retained spot.
    required: false
    ownership: tuning
  min_cells:
    type: integer
    description: Minimum expressing spots per retained gene.
    required: false
    ownership: tuning
  n_hvgs:
    type: integer
    description: Maximum number of variable genes retained for clustering.
    required: false
    ownership: tuning
  n_pcs:
    type: integer
    description: Maximum number of expression PCs retained before spatial fusion.
    required: false
    ownership: tuning
system_requirements:
  min_ram_gb: 8
  min_cores: 4
command_template: python {script_path} --input-path {input_path} --output-dir {output_dir}
---
Use for deterministic processed-input spatial transcriptomics analysis. The
workflow expects `obsm["spatial"]` coordinates in the AnnData object and writes
canonical domain and marker CSV artifacts.
