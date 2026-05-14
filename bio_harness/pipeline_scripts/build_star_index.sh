#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/star_index_cache.sh"

if [ "$#" -lt 4 ] || [ "$#" -gt 6 ]; then
  echo "usage: build_star_index.sh <star_index_dir> <fasta_path> <gtf_path> <threads> [cache_root] [sjdb_overhang]" >&2
  exit 2
fi

star_index_dir="$1"
fasta_path="$2"
gtf_path="$3"
threads="$4"
cache_root="${5:-outputs/_cache/star_indexes}"
sjdb_overhang="${6:-149}"

STAR_BIN="$(bh_resolve_star_bin || true)"
if [ -z "$STAR_BIN" ]; then
  echo "__MISSING_TOOL__:star"
  exit 1
fi

if [ ! -f "${fasta_path}" ]; then
  echo "__MISSING_REFERENCE__:fasta:${fasta_path}"
  exit 1
fi
if [ ! -f "${gtf_path}" ]; then
  echo "__MISSING_REFERENCE__:gtf:${gtf_path}"
  exit 1
fi

mkdir -p "${star_index_dir}"
mkdir -p "${cache_root}"

star_version="$(bh_star_version "${STAR_BIN}")"
fasta_hash="$(bh_hash_file "${fasta_path}")"
gtf_hash="$(bh_hash_file "${gtf_path}")"
cache_key="$(bh_build_star_cache_key "${fasta_hash}" "${gtf_hash}" "${sjdb_overhang}" "${star_version}")"
cache_dir="${cache_root}/${cache_key}"

if bh_validate_star_index_dir "${star_index_dir}" "${cache_key}"; then
  echo "__STAR_INDEX_SKIPPED__:manifest_match:${cache_key}"
  exit 0
fi

if bh_validate_star_index_dir "${cache_dir}" "${cache_key}"; then
  bh_copy_index_dir "${cache_dir}" "${star_index_dir}"
  echo "__STAR_INDEX_CACHE_HIT__:${cache_key}"
  echo "__STAR_INDEX_REUSED__:${cache_dir}"
  exit 0
fi

echo "__STAR_INDEX_CACHE_MISS__:${cache_key}"

build_dir="${cache_dir}.building.$$"
rm -rf "${build_dir}"
mkdir -p "${build_dir}"

"$STAR_BIN" \
  --runThreadN "$threads" \
  --runMode genomeGenerate \
  --genomeDir "${build_dir}" \
  --genomeFastaFiles "$fasta_path" \
  --sjdbGTFfile "$gtf_path" \
  --sjdbOverhang "${sjdb_overhang}"

if ! bh_validate_star_index_dir "${build_dir}" "${cache_key}"; then
  bh_write_star_manifest "${build_dir}" "${cache_key}" "${star_version}" "${fasta_path}" "${fasta_hash}" "${gtf_path}" "${gtf_hash}" "${sjdb_overhang}"
fi

if ! bh_validate_star_index_dir "${build_dir}" "${cache_key}"; then
  echo "__STAR_INDEX_INVALID__:build_output_missing_required_files"
  rm -rf "${build_dir}"
  exit 1
fi

rm -rf "${cache_dir}"
mkdir -p "${cache_dir}"
bh_copy_index_dir "${build_dir}" "${cache_dir}"
bh_copy_index_dir "${build_dir}" "${star_index_dir}"
rm -rf "${build_dir}"

echo "__STAR_INDEX_REBUILT__:${cache_key}"
echo "__STAR_INDEX_CACHE_WRITE__:${cache_dir}"
echo "__STAR_INDEX_DONE__:${star_index_dir}"
