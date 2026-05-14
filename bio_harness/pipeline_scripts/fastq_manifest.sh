#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: fastq_manifest.sh <data_root> <manifest_path>" >&2
  exit 2
fi

data_root="$1"
manifest_path="$2"

mkdir -p "$(dirname "$manifest_path")"
# Follow symlinked input roots (common in readonly staging) while still
# only materializing concrete FASTQ files in the manifest.
find -L "$data_root" -type f \( -name '*.fastq' -o -name '*.fq' -o -name '*.fastq.gz' -o -name '*.fq.gz' \) | sort > "$manifest_path"

count="$(wc -l < "$manifest_path" | tr -d '[:space:]')"
echo "__FASTQ_MANIFEST_COUNT__:${count}"
