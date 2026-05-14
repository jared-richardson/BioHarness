#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/star_index_cache.sh"

if [ "$#" -ne 6 ]; then
  echo "usage: align_r1_list_with_star.sh <r1_list> <star_index_dir> <star_out_dir> <bam_list_out> <label> <threads>" >&2
  exit 2
fi

r1_list="$1"
star_index_dir="$2"
star_out_dir="$3"
bam_list_out="$4"
label="$5"
threads="$6"

STAR_BIN="$(bh_resolve_star_bin || true)"
if [ -z "$STAR_BIN" ]; then
  echo "__MISSING_TOOL__:star"
  exit 1
fi

mkdir -p "$star_out_dir"
mkdir -p "$(dirname "$bam_list_out")"
: > "$bam_list_out"

if [ ! -s "$r1_list" ]; then
  echo "__EMPTY_R1_LIST__:$r1_list"
  exit 0
fi

while IFS= read -r r1; do
  [ -z "$r1" ] && continue
  r2="${r1/_R1_001/_R2_001}"
  if [ ! -f "$r2" ]; then
    echo "__MISSING_PAIR__:$r1"
    continue
  fi

  prefix="${star_out_dir}/${label}_$(basename "$r1")_"
  bam="${prefix}Aligned.sortedByCoord.out.bam"
  if [ -s "$bam" ]; then
    printf '%s\n' "$bam" >> "$bam_list_out"
    continue
  fi

  r1_use="$r1"
  r2_use="$r2"
  tmp1=""
  tmp2=""

  case "$r1" in
    *.gz)
      tmp1="${prefix}R1.tmp.fastq"
      if ! gzip -dc "$r1" > "$tmp1"; then
        echo "__READ_DECOMPRESS_FAILED__:$r1"
        rm -f "$tmp1" "$tmp2"
        continue
      fi
      r1_use="$tmp1"
      ;;
  esac

  case "$r2" in
    *.gz)
      tmp2="${prefix}R2.tmp.fastq"
      if ! gzip -dc "$r2" > "$tmp2"; then
        echo "__READ_DECOMPRESS_FAILED__:$r2"
        rm -f "$tmp1" "$tmp2"
        continue
      fi
      r2_use="$tmp2"
      ;;
  esac

  "$STAR_BIN" \
    --runThreadN "$threads" \
    --genomeDir "$star_index_dir" \
    --readFilesIn "$r1_use" "$r2_use" \
    --outSAMtype BAM SortedByCoordinate \
    --outFileNamePrefix "$prefix"

  rm -f "$tmp1" "$tmp2"

  if [ ! -s "$bam" ]; then
    echo "__EMPTY_BAM__:$bam"
    continue
  fi

  printf '%s\n' "$bam" >> "$bam_list_out"
done < "$r1_list"

count="$(wc -l < "$bam_list_out" | tr -d '[:space:]')"
echo "__BAM_LIST_COUNT__:${label}:${count}"
