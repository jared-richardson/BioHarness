---
name: prodigal_annotate
description: Predict bacterial genes from an assembled genome with Prodigal.
when_to_use: Use for prokaryotic gene prediction from assembled genomes
when_not_to_use: Not for eukaryotic gene prediction or full functional annotation (use Prokka)
risk_level: medium
tools_required:
- prodigal
capabilities:
- annotation
- assembly
input_types:
- fasta
output_types:
- gff
- fasta_protein
analysis_categories:
- annotation
- comparative_genomics
- evolution
parameters:
  input_fasta:
    type: path
    description: Input assembled genome FASTA.
    required: true
    file_role: input_fasta
  output_gff:
    type: path
    description: Output gene predictions in GFF format.
    required: true
    file_role: output_dir
  output_faa:
    type: path
    description: Output translated protein sequences in FASTA format.
    required: true
  mode:
    type: string
    description: "Prodigal mode: auto, single, or meta. Auto uses meta for short assemblies that cannot train single-genome models."
    required: false
    default: auto
  require_cds:
    type: boolean
    description: Fail the wrapper when Prodigal emits no CDS/protein predictions.
    required: false
    default: true
system_requirements:
  min_ram_gb: 4
  min_cores: 1
command_template: python3 bio_harness/pipeline_scripts/run_prodigal_annotate.py --input-fasta {input_fasta} --output-gff {output_gff} --output-faa {output_faa} --mode {mode}
---
Use for bacterial/prokaryotic gene prediction on assembled contigs before downstream
variant consequence annotation.

## Onboarding Metadata
- Source: https://github.com/hyattpd/Prodigal
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:annotation_assembly_core
