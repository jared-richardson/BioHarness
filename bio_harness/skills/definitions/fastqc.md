---
name: fastqc_run
description: 'Performs quality control on raw sequencing data (FASTQ files) using FastQC.

  '
when_to_use: Use for quality control assessment of raw FASTQ sequencing data before processing
when_not_to_use: Not a processing step; does not filter or trim reads (use fastp/trimmomatic)
risk_level: medium
tools_required:
- fastqc
capabilities:
- fastqc
input_types:
- fastq
output_types:
- html
- zip
analysis_categories:
- general
- evolution
- rna_seq_differential_expression
- variant_calling
parameters:
  input_file:
    type: path
    description: Path to the input FASTQ file(s). Can be a single file or a space-separated list of files.
    required: true
    file_role: input_fastq_r1
  output_dir:
    type: path
    description: Directory to save FastQC reports.
    required: true
    file_role: output_dir
  threads:
    type: integer
    description: Number of threads to use. Defaults to 2.
    default: 2
  contaminants:
    type: path
    description: An optional file containing a list of contaminants to screen against.
    required: false
  casava:
    type: boolean
    description: Files come from raw Casava 1.8 or later output, so only the first in a pair is checked for Ns.
    required: false
system_requirements:
  min_ram_gb: 4
  min_cores: 2
---
# Usage Guide
FastQC is a quality control tool for high throughput sequencing data. It takes raw sequencing data (FASTQ files) and produces HTML reports summarizing various quality metrics.

- **Case 1: Basic QC for a single FASTQ file:**
  `fastqc_run(input_file="reads.fastq.gz", output_dir="workspace/outputs/qc_run_01")`

- **Case 2: QC for multiple FASTQ files with increased threads:**
  `fastqc_run(input_file="reads_R1.fastq.gz reads_R2.fastq.gz", output_dir="workspace/outputs/qc_run_01", threads=4)`

- **Case 3: QC with a custom contaminants file:**
  `fastqc_run(input_file="reads.fastq.gz", output_dir="workspace/outputs/qc_run_01", contaminants="my_adapters.fa")`

# Common Pitfalls
- **Error: "FastQC: Command not found"** -> Ensure FastQC is installed and available in the system's PATH.
- **Error: "Failed to process file..."** -> Check if the input FASTQ file exists and is readable. Also verify its integrity.
- **Error: "Out of memory"** -> Increase `min_ram_gb` or reduce the number of input files processed simultaneously if running multiple FastQC instances manually.