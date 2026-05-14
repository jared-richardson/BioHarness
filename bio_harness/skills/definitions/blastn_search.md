---
name: blastn_search
description: Run BLASTN nucleotide homology search and emit tabular hits.
when_to_use: Use for nucleotide sequence similarity searches against a nucleotide database
when_not_to_use: Not for protein searches (use blastp) or translated searches (use blastx/tblastn)
risk_level: medium
tools_required:
- blastn
- makeblastdb
capabilities:
- annotation
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
    description: BLAST nucleotide database name/path or subject FASTA.
    required: true
  output_tsv:
    type: path
    description: Output hit file.
    required: true
  task:
    type: string
    description: Optional BLASTN task such as megablast or blastn-short.
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
command_template: blastn -query {query_fasta} -db {database} -out {output_tsv} -outfmt {outfmt} -num_threads {threads} -evalue
  {evalue}
---
Use for direct nucleotide sequence homology searches.