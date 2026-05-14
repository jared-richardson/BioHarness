---
name: star_2pass_align
description: Run STAR two-pass alignment mode for improved novel junction detection.
when_to_use: Use for STAR 2-pass alignment for improved novel junction detection in RNA-seq
when_not_to_use: Not needed for routine RNA-seq; use single-pass STAR for speed
risk_level: medium
tools_required:
- star
capabilities:
- alignment
- reference_inputs
input_types:
- fastq
- fasta_reference
output_types:
- bam
analysis_categories:
- rna_seq_differential_expression
parameters:
  threads:
    type: integer
    description: Thread count.
    required: true
  genome_dir:
    type: path
    description: STAR genome index directory.
    required: true
    file_role: buildable_genome_index
  reference_fasta:
    type: path
    description: Reference FASTA used to build the STAR genome directory if it does not already exist.
    required: false
    file_role: reference_genome
  annotation_gtf:
    type: path
    description: Gene annotation GTF used to build the STAR genome directory if it does not already exist.
    required: false
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
  output_prefix:
    type: path
    description: Output prefix path.
    required: true
    file_role: output_dir
  star_index_cache_root:
    type: path
    description: Optional STAR index cache root used when materializing a genome dir.
    required: false
  sjdb_overhang:
    type: integer
    description: Optional sjdbOverhang used for STAR genomeGenerate.
    required: false
system_requirements:
  min_ram_gb: 32
  min_cores: 8
command_template: STAR --runMode alignReads --twopassMode Basic --runThreadN {threads} --genomeDir {genome_dir} --readFilesIn
  {reads_1} {reads_2} --readFilesCommand zcat --outFileNamePrefix {output_prefix}
---
Use when two-pass junction discovery is required before quantification/splicing analysis.

## Onboarding Metadata
- Source: https://github.com/alexdobin/STAR
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:alignment_variant_core
