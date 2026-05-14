---
name: salmon_quant
description: Quantify transcript abundance with Salmon quasi-mapping.
when_to_use: Use for fast transcript-level quantification via quasi-mapping in RNA-seq
when_not_to_use: Not for genome-level alignment or gene counting (use featureCounts/STAR)
risk_level: medium
tools_required:
- salmon
capabilities:
- quantification
input_types:
- fastq
- fasta_transcriptome
output_types:
- tsv
analysis_categories:
- transcript_quantification
- rna_seq_differential_expression
parameters:
  index_dir:
    type: path
    description: Salmon transcriptome index directory.
    required: true
    file_role: buildable_index
  transcriptome_fasta:
    type: path
    description: Optional transcriptome FASTA used to build the index when it does not already exist.
    required: false
    file_role: fasta_transcriptome
  library_type:
    type: string
    description: Library type string (e.g. A, ISR).
    required: false
  reads_1:
    type: path
    description: Read 1 FASTQ(.gz).
    required: true
    file_role: input_fastq_r1
  reads_2:
    type: path
    description: Read 2 FASTQ(.gz).
    required: true
    file_role: input_fastq_r2
  threads:
    type: integer
    description: Thread count.
    required: true
  output_dir:
    type: path
    description: Output directory.
    required: true
    file_role: output_dir
system_requirements:
  min_ram_gb: 8
  min_cores: 4
command_template: salmon quant -i {index_dir} -l {library_type} -1 {reads_1} -2 {reads_2} --validateMappings -p {threads}
  -o {output_dir}
---
Use for fast transcript-level quantification in bulk RNA-seq pipelines. Provide `transcriptome_fasta`
when the index has not been built yet. If `library_type` is omitted, the wrapper defaults to `A`.
The wrapper enables `--validateMappings` by default for higher-fidelity transcript quantification.

## Onboarding Metadata
- Source: https://salmon.readthedocs.io/en/latest/salmon.html
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:expression_core
