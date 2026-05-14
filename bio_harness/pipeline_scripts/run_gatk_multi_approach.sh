#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 6 ] || [ "$#" -gt 7 ]; then
  echo "usage: run_gatk_multi_approach.sh <reference_fasta> <normal_bam> <normal_sample> <tumor_bam> <tumor_sample> <out_dir> [threads]" >&2
  exit 2
fi

reference_fasta="$1"
normal_bam="$2"
normal_sample="$3"
tumor_bam="$4"
tumor_sample="$5"
out_dir="$6"
threads="${7:-4}"

for tool_name in gatk samtools; do
  if ! command -v "$tool_name" >/dev/null 2>&1; then
    echo "__MISSING_TOOL__:${tool_name}" >&2
    exit 1
  fi
done

mkdir -p "$out_dir"
ref_dir="${out_dir}/reference_bundle"
mkdir -p "$ref_dir"

ref_name="$(basename "$reference_fasta")"
case "$ref_name" in
  *.fa|*.fasta|*.fna|*.fa.gz|*.fasta.gz|*.fna.gz)
    ref_bundle="${ref_dir}/${ref_name}"
    ;;
  *)
    ref_bundle="${ref_dir}/${ref_name}.fasta"
    ;;
esac
ref_fai="${ref_bundle}.fai"
ref_stem="$(basename "${ref_bundle}")"
ref_dict="${ref_dir}/$(basename "${ref_stem%.*}").dict"

if [ ! -e "$ref_bundle" ]; then
  ln -s "$reference_fasta" "$ref_bundle"
fi

if [ -f "${reference_fasta}.fai" ] && [ ! -e "$ref_fai" ]; then
  ln -s "${reference_fasta}.fai" "$ref_fai"
fi

if [ ! -e "$ref_fai" ]; then
  samtools faidx "$ref_bundle"
fi

if [ ! -s "$ref_dict" ]; then
  gatk CreateSequenceDictionary -R "$ref_bundle" -O "$ref_dict"
fi

prepare_bam() {
  local input_bam="$1"
  local sample_name="$2"
  local sample_dir="${out_dir}/${sample_name}"
  local sorted_bam="${sample_dir}/${sample_name}.coord.bam"
  local rg_bam="${sample_dir}/${sample_name}.rg.bam"
  local md_bam="${sample_dir}/${sample_name}.md.bam"
  local split_bam="${sample_dir}/${sample_name}.splitncigar.bam"
  local metrics_file="${sample_dir}/${sample_name}.markdup.metrics.txt"

  mkdir -p "$sample_dir"

  if [ ! -s "$sorted_bam" ]; then
    samtools sort -@ "$threads" -o "$sorted_bam" "$input_bam"
  fi
  if [ ! -s "${sorted_bam}.bai" ]; then
    samtools index "$sorted_bam"
  fi

  if [ ! -s "$rg_bam" ]; then
    gatk AddOrReplaceReadGroups \
      -I "$sorted_bam" \
      -O "$rg_bam" \
      -RGID "$sample_name" \
      -RGLB "${sample_name}_lib1" \
      -RGPL ILLUMINA \
      -RGPU "${sample_name}_unit1" \
      -RGSM "$sample_name"
  fi
  if [ ! -s "${rg_bam}.bai" ]; then
    samtools index "$rg_bam"
  fi

  if [ ! -s "$md_bam" ]; then
    gatk MarkDuplicates \
      -I "$rg_bam" \
      -O "$md_bam" \
      -M "$metrics_file"
  fi
  if [ ! -s "${md_bam}.bai" ]; then
    samtools index "$md_bam"
  fi

  if [ ! -s "$split_bam" ]; then
    gatk SplitNCigarReads \
      -R "$ref_bundle" \
      -I "$md_bam" \
      -O "$split_bam"
  fi
  if [ ! -s "${split_bam}.bai" ]; then
    samtools index "$split_bam"
  fi

  printf '%s\n' "$split_bam"
}

normal_ready_bam="$(prepare_bam "$normal_bam" "$normal_sample")"
tumor_ready_bam="$(prepare_bam "$tumor_bam" "$tumor_sample")"

normal_hc_vcf="${out_dir}/${normal_sample}/${normal_sample}.haplotypecaller.vcf.gz"
tumor_hc_vcf="${out_dir}/${tumor_sample}/${tumor_sample}.haplotypecaller.vcf.gz"
normal_gvcf="${out_dir}/${normal_sample}/${normal_sample}.haplotypecaller.g.vcf.gz"
tumor_gvcf="${out_dir}/${tumor_sample}/${tumor_sample}.haplotypecaller.g.vcf.gz"
joint_gvcf="${out_dir}/${tumor_sample}_vs_${normal_sample}.cohort.g.vcf.gz"
joint_genotyped_vcf="${out_dir}/${tumor_sample}_vs_${normal_sample}.cohort.genotyped.vcf.gz"
mutect_unfiltered_vcf="${out_dir}/${tumor_sample}_vs_${normal_sample}.mutect2.unfiltered.vcf.gz"
mutect_filtered_vcf="${out_dir}/${tumor_sample}_vs_${normal_sample}.mutect2.filtered.vcf.gz"
mutect_tumor_only_unfiltered_vcf="${out_dir}/${tumor_sample}.tumor_only.mutect2.unfiltered.vcf.gz"
mutect_tumor_only_filtered_vcf="${out_dir}/${tumor_sample}.tumor_only.mutect2.filtered.vcf.gz"

if [ ! -s "$normal_hc_vcf" ]; then
  gatk HaplotypeCaller \
    -R "$ref_bundle" \
    -I "$normal_ready_bam" \
    -O "$normal_hc_vcf" \
    --dont-use-soft-clipped-bases true \
    --standard-min-confidence-threshold-for-calling 20.0 \
    --native-pair-hmm-threads "$threads"
fi

if [ ! -s "$tumor_hc_vcf" ]; then
  gatk HaplotypeCaller \
    -R "$ref_bundle" \
    -I "$tumor_ready_bam" \
    -O "$tumor_hc_vcf" \
    --dont-use-soft-clipped-bases true \
    --standard-min-confidence-threshold-for-calling 20.0 \
    --native-pair-hmm-threads "$threads"
fi

if [ ! -s "$normal_gvcf" ]; then
  gatk HaplotypeCaller \
    -R "$ref_bundle" \
    -I "$normal_ready_bam" \
    -O "$normal_gvcf" \
    -ERC GVCF \
    --dont-use-soft-clipped-bases true \
    --standard-min-confidence-threshold-for-calling 20.0 \
    --native-pair-hmm-threads "$threads"
fi

if [ ! -s "$tumor_gvcf" ]; then
  gatk HaplotypeCaller \
    -R "$ref_bundle" \
    -I "$tumor_ready_bam" \
    -O "$tumor_gvcf" \
    -ERC GVCF \
    --dont-use-soft-clipped-bases true \
    --standard-min-confidence-threshold-for-calling 20.0 \
    --native-pair-hmm-threads "$threads"
fi

if [ ! -s "$joint_gvcf" ]; then
  gatk CombineGVCFs \
    -R "$ref_bundle" \
    -V "$normal_gvcf" \
    -V "$tumor_gvcf" \
    -O "$joint_gvcf"
fi

if [ ! -s "$joint_genotyped_vcf" ]; then
  gatk GenotypeGVCFs \
    -R "$ref_bundle" \
    -V "$joint_gvcf" \
    -O "$joint_genotyped_vcf"
fi

if [ ! -s "$mutect_unfiltered_vcf" ]; then
  gatk Mutect2 \
    -R "$ref_bundle" \
    -I "$tumor_ready_bam" \
    -tumor "$tumor_sample" \
    -I "$normal_ready_bam" \
    -normal "$normal_sample" \
    --native-pair-hmm-threads "$threads" \
    -O "$mutect_unfiltered_vcf"
fi

if [ ! -s "$mutect_filtered_vcf" ]; then
  gatk FilterMutectCalls \
    -R "$ref_bundle" \
    -V "$mutect_unfiltered_vcf" \
    -O "$mutect_filtered_vcf"
fi

if [ ! -s "$mutect_tumor_only_unfiltered_vcf" ]; then
  gatk Mutect2 \
    -R "$ref_bundle" \
    -I "$tumor_ready_bam" \
    -tumor "$tumor_sample" \
    --native-pair-hmm-threads "$threads" \
    -O "$mutect_tumor_only_unfiltered_vcf"
fi

if [ ! -s "$mutect_tumor_only_filtered_vcf" ]; then
  gatk FilterMutectCalls \
    -R "$ref_bundle" \
    -V "$mutect_tumor_only_unfiltered_vcf" \
    -O "$mutect_tumor_only_filtered_vcf"
fi

echo "__GATK_MULTI_APPROACH_DONE__:${out_dir}"
echo "__READY_BAMS__:${normal_ready_bam},${tumor_ready_bam}"
echo "__HAPLOTYPECALLER__:${normal_hc_vcf},${tumor_hc_vcf}"
echo "__GVCF_JOINT__:${normal_gvcf},${tumor_gvcf},${joint_genotyped_vcf}"
echo "__MUTECT2__:${mutect_filtered_vcf},${mutect_tumor_only_filtered_vcf}"
