---
name: vep_annotate
description: Annotate variants with Ensembl VEP using either offline cache/database mode or a deterministic local GFF/FASTA
  reference.
when_to_use: Use for clinical-grade variant annotation with Ensembl VEP
when_not_to_use: Not for bacterial/custom genomes (use SnpEff with custom database)
risk_level: medium
tools_required:
- vep
capabilities:
- annotation
- variant_calling
input_types:
- vcf
output_types:
- vcf
- tsv
analysis_categories:
- variant_annotation
parameters:
  assembly:
    type: string
    description: Genome assembly name (e.g. GRCh38) for cache/database mode.
    required: false
  species:
    type: string
    description: Species name for VEP. Defaults to homo_sapiens for cache/database mode and custom for local GFF mode.
    required: false
  input_vcf:
    type: path
    description: Input VCF file.
    required: true
    file_role: input_vcf
  output_vcf:
    type: path
    description: Output VCF path.
    required: true
    file_role: output_dir
  reference_fasta:
    type: path
    description: Reference FASTA for custom local annotation mode.
    required: false
    file_role: reference_genome
  annotation_gff:
    type: path
    description: GFF3 annotation file for deterministic custom-reference annotation mode.
    required: false
    file_role: annotation_gff
  annotation_gtf:
    type: path
    description: GTF annotation file for deterministic custom-reference annotation mode.
    required: false
    file_role: annotation_gtf
  cache_dir:
    type: path
    description: Optional VEP cache directory when using offline cache mode.
    required: false
    file_role: output_dir
  use_database:
    type: boolean
    description: Use Ensembl database mode instead of offline cache mode.
    required: false
system_requirements:
  min_ram_gb: 12
  min_cores: 4
command_template: vep --cache --offline --assembly {assembly} -i {input_vcf} -o {output_vcf} --vcf
---
Use for Ensembl consequence annotation. The wrapper supports:

- offline cache mode for standard Ensembl references
- database mode when explicitly requested
- deterministic custom-reference annotation using a local GFF/GTF plus FASTA

For custom-reference mode, the harness bgzips and tabix-indexes the annotation file automatically before invoking VEP.
That conditional path requires `bgzip` and `tabix`, and the wrapper fails early
with a clear message if either helper binary is unavailable.

## Onboarding Metadata
- Source: https://www.ensembl.org/info/docs/tools/vep/index.html
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:annotation_assembly_core
