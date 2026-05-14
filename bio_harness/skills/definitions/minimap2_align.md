---
name: minimap2_align
description: Align long-read (or mixed) sequencing reads with minimap2 and produce indexed BAM output.
when_to_use: Use for long-read alignment (PacBio/Nanopore) or fast approximate alignment
when_not_to_use: Not for short-read DNA (use BWA) or spliced RNA-seq alignment (use STAR)
risk_level: medium
tools_required:
- minimap2
- samtools
capabilities:
- alignment
- reference_inputs
input_types:
- fastq
- fasta
- fasta_reference
output_types:
- bam
analysis_categories:
- variant_calling
- comparative_genomics
parameters:
  reference_fasta:
    type: path
    description: Reference FASTA path.
    required: true
    file_role: reference_genome
  reads:
    type: path
    description: FASTQ path for single-file long-read input.
    required: false
  reads_1:
    type: path
    description: Optional read 1 FASTQ.
    required: false
  reads_2:
    type: path
    description: Optional read 2 FASTQ.
    required: false
  output_bam:
    type: path
    description: Sorted BAM output path.
    required: true
    file_role: output_dir
  preset:
    type: string
    description: Minimap2 preset (for example splice, map-ont, map-pb).
    required: false
  threads:
    type: integer
    description: Thread count.
    required: false
  cache_index_path:
    type: path
    description: Optional shared minimap2 .mmi index path.
    required: false
system_requirements:
  min_ram_gb: 8
  min_cores: 4
command_template: minimap2 -ax {preset} -t {threads} {reference_fasta} {reads} | samtools sort -@ {threads} -o {output_bam}
  -
---
Use for long-read DNA/RNA alignment fallback plans.
