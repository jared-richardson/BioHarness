---
name: gatk_haplotypecaller
description: Call germline SNPs/indels with GATK HaplotypeCaller.
when_to_use: Use for germline SNP/indel calling with GATK best practices pipeline
when_not_to_use: Not for somatic calling (use Mutect2) or bacterial genomes (use FreeBayes/bcftools)
risk_level: high
tools_required:
- gatk
capabilities:
- variant_calling
- reference_inputs
input_types:
- bam
- fasta_reference
output_types:
- vcf
analysis_categories:
- germline_variant_calling
- variant_calling
parameters:
  reference_fasta:
    type: path
    description: Reference FASTA.
    required: true
    file_role: reference_genome
  input_bam:
    type: path
    description: Deduplicated BAM input.
    required: true
    file_role: input_bam
  output_vcf:
    type: path
    description: Output VCF or gVCF path.
    required: true
    file_role: output_dir
system_requirements:
  min_ram_gb: 16
  min_cores: 4
command_template: gatk HaplotypeCaller -R {reference_fasta} -I {input_bam} -O {output_vcf}
---
Use for Broad-style germline variant calling with proper preprocessing upstream.

## Onboarding Metadata
- Source: https://gatk.broadinstitute.org/hc/en-us/articles/360037225632-HaplotypeCaller
- Source Mode: official_docs
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:alignment_variant_core
