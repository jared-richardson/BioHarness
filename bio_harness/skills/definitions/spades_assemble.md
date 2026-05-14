---
name: spades_assemble
description: Assemble short-read genomes/transcriptomes with SPAdes.
when_to_use: Use for de novo short-read genome assembly of bacterial/small genomes
when_not_to_use: Not for long reads (use Flye) or large eukaryotic genomes
risk_level: high
tools_required:
- spades.py
capabilities:
- genome_assembly
input_types:
- fastq
output_types:
- fasta
analysis_categories:
- comparative_genomics
- evolution
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
  memory_gb:
    type: integer
    description: Memory cap in GB.
    required: true
  careful:
    type: boolean
    description: Enable SPAdes careful mode.
    required: false
  meta_mode:
    type: boolean
    description: Enable metaSPAdes mode for metagenomic assemblies.
    required: false
  isolate_mode:
    type: boolean
    description: Enable SPAdes isolate mode for microbial isolate genomes.
    required: false
  phred_offset:
    type: integer
    description: FASTQ PHRED quality offset. Defaults to 33; set to 64 only for legacy PHRED+64 reads, or auto to use SPAdes auto-detection.
    required: false
  output_dir:
    type: path
    description: Assembly output directory.
    required: true
    file_role: output_dir
system_requirements:
  min_ram_gb: 32
  min_cores: 8
command_template: spades.py -1 {reads_1} -2 {reads_2} -t {threads} -m {memory_gb} [--phred-offset {phred_offset}] [--meta] [--careful] [--isolate] -o {output_dir}
---
Use for short-read assembly with explicit memory/thread controls.

## Onboarding Metadata
- Source: https://github.com/ablab/spades
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:annotation_assembly_core
