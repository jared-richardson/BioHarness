#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: run_fastqc_if_needed.sh <control_r1_list> <treatment_r1_list> <out_dir> <sentinel>" >&2
  exit 2
fi

control_r1_list="$1"
treatment_r1_list="$2"
out_dir="$3"
sentinel="$4"

if [ -f "$sentinel" ]; then
  echo "__FASTQC_SKIPPED__:cached"
  exit 0
fi

if [ ! -s "$control_r1_list" ] || [ ! -s "$treatment_r1_list" ]; then
  echo "__FASTQC_SKIPPED__:missing_inputs"
  exit 0
fi

mkdir -p "$out_dir"

if ! command -v fastqc >/dev/null 2>&1; then
  echo "__MISSING_TOOL__:fastqc"
  exit 1
fi

total_files=0
while IFS= read -r file_path; do
  [ -z "$file_path" ] && continue
  total_files=$((total_files + 1))
done < <(cat "$control_r1_list" "$treatment_r1_list")

if [ "$total_files" -eq 0 ]; then
  echo "__FASTQC_SKIPPED__:empty_lists"
  exit 0
fi

while IFS= read -r file_path; do
  [ -z "$file_path" ] && continue
  fastqc -o "$out_dir" -t 2 "$file_path"
done < <(cat "$control_r1_list" "$treatment_r1_list")

touch "$sentinel"
echo "__FASTQC_DONE__:${total_files}"
