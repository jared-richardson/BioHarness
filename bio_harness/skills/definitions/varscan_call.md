---
name: varscan_call
description: Call germline variants from a BAM using VarScan2 mpileup2cns.
when_to_use: Use for variant calling with VarScan2, suitable for tumor/normal or pooled samples
when_not_to_use: Use GATK HaplotypeCaller for germline best practices or FreeBayes for haplotype-aware calling
risk_level: high
tools_required:
- samtools
- varscan
- java
capabilities:
- variant_calling
- reference_inputs
input_types:
- bam
- fasta_reference
output_types:
- vcf
analysis_categories:
- variant_calling
parameters:
  reference_fasta:
    type: path
    description: Reference FASTA.
    required: true
    file_role: reference_genome
  input_bam:
    type: path
    description: Input BAM path.
    required: true
    file_role: input_bam
  output_vcf:
    type: path
    description: Output VCF path.
    required: true
    file_role: output_dir
  min_var_freq:
    type: float
    description: Optional minimum variant allele frequency.
    required: false
  p_value:
    type: float
    description: Optional VarScan p-value threshold.
    required: false
system_requirements:
  min_ram_gb: 8
  min_cores: 2
command_template: samtools mpileup -f {reference_fasta} {input_bam} | varscan mpileup2cns --output-vcf 1 > {output_vcf}
---
Use for VarScan2-based germline calling when the user explicitly requests VarScan or when a complementary caller is desired alongside bcftools/GATK outputs.

## Onboarding Metadata
- Source: https://pmc.ncbi.nlm.nih.gov/articles/PMC4278659/
- Source Mode: canonical_publication
- Installed At: curated_seed_v1
- Install Workflow: controlled_curated_batch_onboarding:alignment_variant_core