---
name: fastp_run
description: Trim adapters and low-quality bases from FASTQ reads with fastp.
when_to_use: Use for general-purpose read preprocessing when adapter trimming or quality filtering is needed before downstream analysis
when_not_to_use: Not for read quality assessment alone (use FastQC) or when RNA-specific adapter trimming should be handled with Cutadapt
risk_level: medium
tools_required:
  - fastp
capabilities:
  - preprocessing
  - read_trimming
input_types:
  - fastq
output_types:
  - fastq
  - html
  - json
analysis_categories:
  - general
  - metagenomics_classification
  - viral_metagenomics
  - bacterial_evolution_variant_calling
  - transcript_quantification
  - variant_calling
parameters:
  reads_1:
    type: path
    description: Read 1 FASTQ(.gz) input.
    required: true
    file_role: input_fastq_r1
  reads_2:
    type: path
    description: Optional Read 2 FASTQ(.gz) input for paired-end trimming.
    required: false
    file_role: input_fastq_r2
  output_reads_1:
    type: path
    description: Trimmed Read 1 FASTQ(.gz) output path.
    required: true
    file_role: output_dir
  output_reads_2:
    type: path
    description: Optional trimmed Read 2 FASTQ(.gz) output path for paired-end trimming.
    required: false
    file_role: output_dir
  detect_adapter_for_pe:
    type: boolean
    description: Enable fastp paired-end adapter auto-detection.
    required: false
  adapter_sequence:
    type: string
    description: Optional explicit adapter sequence for Read 1.
    required: false
  adapter_sequence_r2:
    type: string
    description: Optional explicit adapter sequence for Read 2.
    required: false
  cut_front:
    type: boolean
    description: Trim low-quality bases from the front of reads.
    required: false
  cut_tail:
    type: boolean
    description: Trim low-quality bases from the tail of reads.
    required: false
  cut_right:
    type: boolean
    description: Trim low-quality tails using the sliding-window right-cut mode.
    required: false
  correction:
    type: boolean
    description: Enable overlap-based base correction for paired-end reads.
    required: false
  cut_mean_quality:
    type: integer
    description: Mean quality threshold for quality trimming.
    required: false
  length_required:
    type: integer
    description: Minimum retained read length after trimming.
    required: false
  threads:
    type: integer
    description: Number of CPU threads for fastp.
    required: false
  json_report:
    type: path
    description: Optional JSON report output path.
    required: false
    file_role: output_dir
  html_report:
    type: path
    description: Optional HTML report output path.
    required: false
    file_role: output_dir
system_requirements:
  min_ram_gb: 4
  min_cores: 2
command_template: fastp -i {reads_1} -o {output_reads_1}
---
# Usage Guide

- Case 1: General paired-end trimming before metagenomics assembly or viral classification.
- Case 2: Quality trimming before variant calling when RNA-specific Cutadapt handling is not required.
- Case 3: Generate HTML/JSON QC reports alongside the trimmed FASTQ outputs.

# Common Pitfalls

- `adapter_sequence_r2` requires paired-end inputs.
- RNA-related adapter trimming should prefer `cutadapt_run` when adapter handling is the main objective.
- `fastp` uses `--thread` rather than `--threads`; the wrapper emits the correct flag.
