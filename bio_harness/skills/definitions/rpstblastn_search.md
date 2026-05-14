---
name: rpstblastn_search
description: Run RPSTBLASTN translated nucleotide-to-domain search and emit tabular hits.
when_to_use: Use when nucleotide queries need to be translated and searched against a conserved-domain or profile BLAST database
when_not_to_use: Not for ordinary nucleotide or protein databases built with makeblastdb
risk_level: medium
tools_required:
- rpstblastn
capabilities:
- annotation
- protein_analysis
input_types:
- fasta_nucleotide
output_types:
- tsv
- xml
analysis_categories:
- comparative_genomics
- annotation
parameters:
  query_fasta:
    type: path
    description: Nucleotide FASTA query file.
    required: true
  database:
    type: string
    description: Conserved-domain/profile BLAST database path.
    required: true
  output_tsv:
    type: path
    description: Output hit file.
    required: true
  strand:
    type: string
    description: Optional query strand setting.
    required: false
  outfmt:
    type: string
    description: BLAST outfmt string.
    required: false
  evalue:
    type: string
    description: E-value threshold.
    required: false
  threads:
    type: integer
    description: Thread count.
    required: false
system_requirements:
  min_ram_gb: 4
  min_cores: 2
command_template: rpstblastn -query {query_fasta} -db {database} -out {output_tsv} -outfmt {outfmt} -num_threads {threads} -evalue {evalue}
---
Use for translated nucleotide-to-domain searches against profile BLAST databases.
