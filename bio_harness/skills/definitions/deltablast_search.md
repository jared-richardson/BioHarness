---
name: deltablast_search
description: Run DELTA-BLAST protein search and emit tabular hits.
when_to_use: Use for domain-seeded protein homology searches when a conserved-domain database is available
when_not_to_use: Not for direct nucleotide searches or when no conserved-domain database is available
risk_level: medium
tools_required:
- deltablast
- makeblastdb
capabilities:
- annotation
- protein_analysis
input_types:
- fasta_protein
output_types:
- tsv
- xml
analysis_categories:
- comparative_genomics
- annotation
parameters:
  query_fasta:
    type: path
    description: Protein FASTA query file.
    required: true
  database:
    type: string
    description: Protein BLAST database name/path or subject FASTA.
    required: true
  output_tsv:
    type: path
    description: Output hit file.
    required: true
  domain_database:
    type: string
    description: Optional RPS domain database path for conserved-domain seeding.
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
command_template: deltablast -query {query_fasta} -db {database} -out {output_tsv} -outfmt {outfmt} -num_threads {threads}
  -evalue {evalue}
---
Use for domain-seeded protein searches when DELTA-BLAST is specifically requested.