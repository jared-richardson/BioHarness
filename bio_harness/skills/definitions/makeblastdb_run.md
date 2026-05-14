---
name: makeblastdb_run
description: Build a nucleotide or protein BLAST database from a FASTA file.
when_to_use: Use to build a reusable BLAST database from a FASTA file before running BLAST searches
when_not_to_use: Not for executing a search itself
risk_level: low
tools_required:
- makeblastdb
capabilities:
- annotation
input_types:
- fasta_nucleotide
- fasta_protein
output_types:
- directory
analysis_categories:
- comparative_genomics
- annotation
parameters:
  input_fasta:
    type: path
    description: FASTA input to convert into a BLAST database.
    required: true
    file_role: input_fasta
  output_prefix:
    type: path
    description: Output database prefix path.
    required: true
    file_role: output_dir
  dbtype:
    type: string
    description: BLAST database type, either prot or nucl.
    required: true
  title:
    type: string
    description: Optional BLAST database title.
    required: false
  parse_seqids:
    type: boolean
    description: Enable sequence identifier parsing in the generated database.
    required: false
  input_type:
    type: string
    description: Input type passed to makeblastdb.
    required: false
system_requirements:
  min_ram_gb: 2
  min_cores: 1
command_template: makeblastdb -in {input_fasta} -input_type {input_type} -dbtype {dbtype} -out {output_prefix}
---
Use for deterministic BLAST database construction.