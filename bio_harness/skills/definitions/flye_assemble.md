---
name: flye_assemble
description: Assemble long reads with Flye.
when_to_use: Use for de novo assembly of long reads (PacBio/Nanopore)
when_not_to_use: Not for short-read assembly (use SPAdes) or hybrid assembly
risk_level: high
tools_required:
- flye
capabilities:
- genome_assembly
input_types:
- fastq
output_types:
- fasta
analysis_categories:
- comparative_genomics
parameters:
  reads_fastq:
    type: path
    description: Long-read FASTQ input.
    required: true
  read_mode:
    type: string
    description: Flye read mode such as nano-raw, nano-hq, pacbio-raw, or pacbio-hifi.
    required: false
  threads:
    type: integer
    description: Thread count.
    required: true
  output_dir:
    type: path
    description: Output directory.
    required: true
  genome_size:
    type: string
    description: Estimated genome size (e.g. 5m, 3g).
    required: true
  meta_mode:
    type: boolean
    description: Enable Flye metagenome mode for mixed-community long-read assembly.
    required: false
system_requirements:
  min_ram_gb: 32
  min_cores: 8
command_template: flye --{read_mode} {reads_fastq} --threads {threads} --out-dir {output_dir} --genome-size {genome_size}
---
Use for long-read assembly with Flye. The wrapper can also enable `--meta` for metagenome-style prompts.

## Onboarding Metadata
- Source: https://github.com/mikolmogorov/Flye
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:annotation_assembly_core
