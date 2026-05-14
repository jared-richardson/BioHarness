---
name: metagenomics_kraken2_bracken_style
description: Run Kraken2/Bracken-style metagenomics profiling with deterministic fallback output when tools are missing.
when_to_use: Use for taxonomic classification of metagenomic reads with Kraken2+Bracken
when_not_to_use: Not for functional annotation or viral metagenomics assembly
risk_level: medium
tools_required:
- kraken2
- bracken
capabilities:
- metagenomics_profiling
input_types:
- fastq
output_types:
- tsv
analysis_categories:
- metagenomics_classification
system_requirements:
  min_ram_gb: 16
  min_cores: 4
parameters:
  database:
    type: path
    description: Kraken2 database path.
    required: true
    file_role: buildable_database
  reference_fasta:
    type: path
    description: Optional reference FASTA used to build a tiny Kraken2 database or regenerate Bracken support files.
    required: false
  taxonomy_names:
    type: path
    description: Optional taxonomy names.dmp file used when building a database.
    required: false
  taxonomy_nodes:
    type: path
    description: Optional taxonomy nodes.dmp file used when building a database.
    required: false
  reads_1:
    type: path
    description: Read 1 FASTQ path.
    required: true
  reads_2:
    type: path
    description: Read 2 FASTQ path.
    required: true
  output_dir:
    type: path
    description: Output directory.
    required: true
    file_role: output_dir
  output_report:
    type: path
    description: Bracken abundance report TSV output.
    required: true
  threads:
    type: integer
    description: Thread count.
    required: false
  read_len:
    type: integer
    description: Estimated read length.
    required: false
  threshold:
    type: integer
    description: Minimum read threshold passed to Bracken abundance estimation.
    required: false
  taxonomy_level:
    type: string
    description: Taxonomic level passed to Bracken abundance estimation, for example S or G.
    required: false
command_template: kraken2 --db {database} --paired {reads_1} {reads_2} --threads {threads} --report {output_dir}/kraken.report
  --output {output_dir}/kraken.out && est_abundance.py -i {output_dir}/kraken.report -k {database}/database{read_len}mers.kmer_distrib
  -o {output_report} -l {taxonomy_level} -t {threshold}
---
Use for uncommon metagenomics taxonomic profiling requests.

Fallback mode writes a deterministic unclassified abundance report if Kraken2 or the Bracken helper scripts are unavailable.
If the database is not already built, the wrapper can also build a tiny custom Kraken2 database when `reference_fasta`,
`taxonomy_names`, and `taxonomy_nodes` are provided.
