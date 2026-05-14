---
name: prokka_annotate
description: Annotate prokaryotic assemblies with Prokka.
when_to_use: Use for rapid prokaryotic genome annotation (gene prediction + functional annotation)
when_not_to_use: Not for eukaryotic genomes or metagenome annotation
risk_level: medium
tools_required:
- prokka
capabilities:
- annotation
- genome_assembly
input_types:
- fasta
output_types:
- gff
- fasta_protein
- gbk
analysis_categories:
- annotation
- comparative_genomics
- evolution
parameters:
  output_dir:
    type: path
    description: Output directory.
    required: true
    file_role: output_dir
  sample_prefix:
    type: string
    description: Output file prefix.
    required: true
  input_fasta:
    type: path
    description: Assembly FASTA input.
    required: true
    file_role: input_fasta
  cpus:
    type: integer
    description: Optional thread count passed to Prokka.
    required: false
  kingdom:
    type: string
    description: Optional Prokka kingdom label, such as Bacteria or Archaea.
    required: false
  genus:
    type: string
    description: Optional genus name used for annotation metadata.
    required: false
  species:
    type: string
    description: Optional species name used for annotation metadata.
    required: false
  strain:
    type: string
    description: Optional strain name used for annotation metadata.
    required: false
  locustag:
    type: string
    description: Optional locus tag prefix passed to Prokka.
    required: false
system_requirements:
  min_ram_gb: 8
  min_cores: 4
command_template: prokka --outdir {output_dir} --prefix {sample_prefix} {input_fasta}
---
Use for bacterial/archaeal structural + functional annotation.

## Onboarding Metadata
- Source: https://github.com/tseemann/prokka
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:annotation_assembly_core
