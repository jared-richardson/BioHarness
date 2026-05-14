---
name: methylation_bismark_style
description: Run Bismark-style bisulfite methylation analysis with deterministic degrade behavior when the binary is unavailable.
when_to_use: Use for bisulfite sequencing methylation analysis with Bismark
when_not_to_use: Not for general DNA-seq alignment or RNA-seq analysis
risk_level: medium
tools_required:
- bismark
capabilities:
- methylation_analysis
- alignment
- reference_inputs
input_types:
- fastq
- fasta_reference
output_types:
- bam
- tsv
analysis_categories:
- methylation_analysis
parameters:
  genome_folder:
    type: path
    description: Bismark genome folder.
    required: true
  reads_1:
    type: path
    description: Read 1 FASTQ path.
    required: true
  reads_2:
    type: path
    description: Read 2 FASTQ path.
    required: true
  output_dir:
    type: path
    description: Output directory.
    required: true
  output_report:
    type: path
    description: Methylation summary TSV output.
    required: true
  threads:
    type: integer
    description: Thread count.
    required: false
  sample_name:
    type: string
    description: Sample basename.
    required: false
system_requirements:
  min_ram_gb: 16
  min_cores: 4
command_template: bismark --genome_folder {genome_folder} -1 {reads_1} -2 {reads_2} --parallel {threads} --basename {sample_name}
  -o {output_dir}
---
Use for uncommon bisulfite-seq methylation requests where Bismark-style behavior is expected.

Fallback mode writes a deterministic placeholder report if the binary is unavailable.

When the full Bismark path is taken, the harness also checks for
`bismark_genome_preparation`, `bowtie2-build`, `samtools`, and `python3`
before running genome preparation or summary generation.
