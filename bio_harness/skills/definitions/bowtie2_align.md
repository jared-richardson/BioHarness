---
name: bowtie2_align
description: Align short-read DNA sequencing reads with Bowtie2 and produce indexed BAM output.
when_to_use: Use for short-read alignment when BWA is unavailable or for gapped alignment to a reference
when_not_to_use: Not for spliced RNA-seq alignment (use STAR/HISAT2) or long reads (use minimap2)
risk_level: medium
tools_required:
- bowtie2
- bowtie2-build
- samtools
capabilities:
- alignment
- reference_inputs
input_types:
- fastq
- fasta_reference
output_types:
- bam
analysis_categories:
- variant_calling
- evolution
- germline
- metagenomics
parameters:
  reference_fasta:
    type: path
    description: Reference FASTA used to build Bowtie2 index when needed.
    required: true
    file_role: reference_genome
  index_base:
    type: path
    description: Bowtie2 index base path.
    required: true
    file_role: buildable_index
  reads_1:
    type: path
    description: Read 1 FASTQ(.gz).
    required: true
    file_role: input_fastq_r1
  reads_2:
    type: path
    description: Read 2 FASTQ(.gz).
    required: true
    file_role: input_fastq_r2
  output_bam:
    type: path
    description: Sorted BAM output path.
    required: true
    file_role: output_dir
  threads:
    type: integer
    description: Thread count.
    required: false
  cache_index_base:
    type: path
    description: Optional shared index base for reusable Bowtie2 index artifacts.
    required: false
system_requirements:
  min_ram_gb: 8
  min_cores: 4
command_template: bowtie2 -x {index_base} -1 {reads_1} -2 {reads_2} -p {threads} | samtools sort -@ {threads} -o {output_bam}
  -
---
Use for deterministic Bowtie2 alignment fallback plans.
