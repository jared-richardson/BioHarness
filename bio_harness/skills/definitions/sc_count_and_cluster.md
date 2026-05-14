---
name: sc_count_and_cluster
description: Single-cell RNA-seq pipeline from 10x FASTQs to clusters via kmer counting + scanpy.
when_to_use: Use for single-cell counting and clustering with STARsolo or equivalent
when_not_to_use: Not for bulk RNA-seq or pre-processed h5ad files
risk_level: medium
tools_required:
- python
- scanpy
capabilities:
- single_cell_analysis
- differential_analysis
- demultiplexing
- clustering
input_types:
- fastq
- fasta_reference
output_types:
- h5ad
- csv
- tsv
analysis_categories:
- single_cell_rna_seq
parameters:
  r1:
    type: path
    description: R1 FASTQ (barcode + UMI).
    required: true
  r2:
    type: path
    description: R2 FASTQ (cDNA).
    required: true
  whitelist:
    type: path
    description: Optional barcode whitelist file. When omitted, the wrapper infers one from observed R1 barcodes.
    required: false
  reference:
    type: path
    description: Reference genome FASTA.
    required: true
  gtf:
    type: path
    description: Gene annotation GTF.
    required: true
  output_dir:
    type: path
    description: Output directory for results.
    required: true
  barcode_len:
    type: integer
    description: Barcode length in R1.
    required: false
  umi_len:
    type: integer
    description: UMI length in R1.
    required: false
  kmer_size:
    type: integer
    description: Kmer size for read-to-gene mapping.
    required: false
  min_genes:
    type: integer
    description: Minimum genes per cell for QC.
    required: false
  min_cells:
    type: integer
    description: Minimum cells per gene for QC.
    required: false
  leiden_resolution:
    type: float
    description: Leiden clustering resolution.
    required: false
system_requirements:
  min_ram_gb: 8
  min_cores: 2
command_template: python {script_path} --r1 {r1} --r2 {r2} --whitelist {whitelist} --reference {reference} --gtf {gtf} --output-dir
  {output_dir}
---
End-to-end single-cell RNA-seq pipeline for 10x Chromium data.

Handles demultiplexing, UMI counting, and clustering from raw FASTQs.

Steps:
1. Demultiplex reads by cell barcode (R1)
2. Map cDNA reads (R2) to genes via kmer matching
3. Deduplicate UMIs
4. Scanpy: QC, normalize, HVG, PCA, neighbors, Leiden clustering, marker genes

Outputs: AnnData h5ad, cluster assignments JSON, marker genes JSON.
