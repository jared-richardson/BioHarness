---
name: featurecounts_run
description: Quantify aligned reads to genes/exons using featureCounts.
when_to_use: Use for gene-level read counting from aligned BAM files using a GTF/GFF annotation
when_not_to_use: Not for transcript-level quantification (use salmon/kallisto)
risk_level: medium
tools_required:
- featurecounts
- subread
capabilities:
- quantification
input_types:
- bam
- gtf
output_types:
- tsv
analysis_categories:
- rna_seq_differential_expression
- multi_model_dge_pathway
parameters:
  threads:
    type: integer
    description: Number of worker threads.
    required: true
  annotation_gtf:
    type: path
    description: Reference GTF annotation.
    required: true
    file_role: annotation_gtf
  output_counts:
    type: path
    description: Output counts file path.
    required: true
    file_role: output_dir
  input_bams:
    type: string
    description: Space-separated BAM inputs.
    required: true
    file_role: input_bam
  annotation_format:
    type: string
    description: Optional annotation format override such as `GFF` when the annotation file is not GTF.
    required: false
  feature_type:
    type: string
    description: Optional feature type to count, for example `gene` when using GFF annotations.
    required: false
  attribute_type:
    type: string
    description: Optional GFF/GTF attribute to aggregate counts by, such as `ID` or `gene_id`.
    required: false
  is_paired_end:
    type: boolean
    description: Treat the BAM inputs as paired-end and pass `-p` to featureCounts.
    required: false
  count_read_pairs:
    type: boolean
    description: When paired-end counting is enabled, count read pairs instead of individual reads.
    required: false
  strand_specificity:
    type: integer
    description: featureCounts strandedness setting (`0`, `1`, or `2`).
    required: false
system_requirements:
  min_ram_gb: 8
  min_cores: 4
command_template: python3 bio_harness/pipeline_scripts/run_featurecounts.py --threads {threads} --annotation-gtf {annotation_gtf} --output-counts {output_counts}
---
Use for gene-level read counting from coordinate-sorted BAM files.

If `is_paired_end` is omitted and exactly one BAM is provided, the wrapper may
use `samtools` to auto-detect paired-end reads. Set `is_paired_end`
explicitly to avoid that optional helper path.

## Onboarding Metadata
- Source: https://subread.sourceforge.net/featureCounts.html
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:expression_core
