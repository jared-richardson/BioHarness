---
name: sniffles_sv_call
description: Call structural variants from aligned long-read BAM or CRAM data with Sniffles.
when_to_use: Use for long-read structural-variant calling from an aligned BAM or CRAM against a matching reference
when_not_to_use: Not for SNP or small-indel calling, or for raw reads that have not already been aligned
risk_level: medium
tools_required:
- sniffles
- samtools
capabilities:
- structural_variant_calling
- reference_inputs
input_types:
- bam
- fasta_reference
output_types:
- vcf
analysis_categories:
- structural_variant_calling
parameters:
  reference_fasta:
    type: path
    description: Reference FASTA path.
    required: true
    file_role: reference_genome
  input_bam:
    type: path
    description: Coordinate-sorted long-read BAM or CRAM input.
    required: true
    file_role: input_bam
  output_vcf:
    type: path
    description: Output structural-variant VCF path.
    required: true
    file_role: output_dir
  threads:
    type: integer
    description: Thread count.
    required: false
  sample_id:
    type: string
    description: Optional sample identifier to embed in the output VCF.
    required: false
  min_support:
    type: integer
    description: Minimum supporting-read count required per variant call.
    required: false
  min_sv_length:
    type: integer
    description: Minimum structural-variant length to emit.
    required: false
system_requirements:
  min_ram_gb: 16
  min_cores: 4
command_template: sniffles --input {input_bam} --vcf {output_vcf} --reference {reference_fasta} --threads {threads}
---
Use for long-read structural-variant detection from an aligned BAM or CRAM
against a matching reference FASTA.