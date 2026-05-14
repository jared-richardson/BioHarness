---
name: blastdbcheck_run
description: Verify integrity of a BLAST database or BLAST database directory.
when_to_use: Use after BLAST database creation or before downstream search execution to verify integrity
when_not_to_use: Do not use to execute searches or retrieve entries
risk_level: low
tools_required:
- blastdbcheck
capabilities:
- annotation
- protein_analysis
input_types:
- directory
output_types:
- txt
analysis_categories:
- comparative_genomics
- annotation
parameters:
  database:
    type: string
    description: BLAST database name or path prefix.
    required: false
  directory:
    type: path
    description: Directory containing BLAST databases.
    required: false
  dbtype:
    type: string
    description: Database molecule type.
    required: false
  verbosity:
    type: integer
    description: Output verbosity level.
    required: false
  full:
    type: boolean
    description: Check all sequences.
    required: false
  recursive:
    type: boolean
    description: Recurse through a directory tree.
    required: false
system_requirements:
  min_ram_gb: 2
  min_cores: 1
command_template: blastdbcheck -db {database} -dbtype {dbtype}
---
Use for deterministic BLAST database validation before search execution.