---
name: blastdb_aliastool_run
description: Create an alias BLAST database from one or more existing BLAST databases.
when_to_use: Use to aggregate or relabel one or more BLAST databases into a reusable alias database
when_not_to_use: Do not use to build a primary BLAST database from FASTA input
risk_level: low
tools_required:
- blastdb_aliastool
capabilities:
- annotation
- protein_analysis
input_types:
- directory
output_types:
- directory
analysis_categories:
- comparative_genomics
- annotation
parameters:
  dblist:
    type: list[path]
    description: One or more BLAST database prefixes to alias.
    required: true
  dbtype:
    type: string
    description: Database molecule type.
    required: true
  output_alias:
    type: path
    description: Alias database output prefix.
    required: true
    file_role: output_dir
  title:
    type: string
    description: Optional alias database title.
    required: false
  num_volumes:
    type: integer
    description: Optional alias volume count.
    required: false
system_requirements:
  min_ram_gb: 2
  min_cores: 1
command_template: blastdb_aliastool -dblist {dblist} -dbtype {dbtype} -out {output_alias}
---
Use for deterministic creation of alias databases for BLAST workflows.