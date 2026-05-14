---
name: trinity_assemble
description: Assemble transcriptomes de novo with Trinity.
when_to_use: Use for de novo transcriptome assembly from RNA-seq reads without a reference genome
when_not_to_use: Not for genome assembly (use SPAdes/Flye) or when a reference is available
risk_level: high
tools_required:
- trinity
capabilities:
- genome_assembly
input_types:
- fastq
output_types:
- fasta
analysis_categories:
- transcript_quantification
parameters:
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
  threads:
    type: integer
    description: Thread count.
    required: true
  max_memory_gb:
    type: integer
    description: Max memory allocation in GB.
    required: true
  output_dir:
    type: path
    description: Output directory.
    required: true
    file_role: output_dir
system_requirements:
  min_ram_gb: 32
  min_cores: 8
command_template: Trinity --seqType fq --left {reads_1} --right {reads_2} --CPU {threads} --max_memory {max_memory_gb}G --output
  {output_dir}
---
Use for RNA-seq transcriptome reconstruction when no trusted reference exists.

## Onboarding Metadata
- Source: https://github.com/trinityrnaseq/trinityrnaseq/wiki
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:annotation_assembly_core
