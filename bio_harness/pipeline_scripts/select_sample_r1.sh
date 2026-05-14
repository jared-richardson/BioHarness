#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: select_sample_r1.sh <manifest> <sample_tag> <out_file> <label>" >&2
  exit 2
fi

manifest="$1"
sample_tag="$2"
out_file="$3"
label="$4"

mkdir -p "$(dirname "$out_file")"
: > "$out_file"

tags_csv="$(printf '%s' "$sample_tag" | tr ' ' ',' | tr -s ',' | sed 's/^,//; s/,$//')"
IFS=',' read -r -a tags <<< "$tags_csv"
if [ "${#tags[@]}" -eq 0 ]; then
  tags=("$sample_tag")
fi

selected_all=""
for tag in "${tags[@]}"; do
  clean_tag="$(printf '%s' "$tag" | xargs || true)"
  if [ -z "$clean_tag" ]; then
    continue
  fi
  escaped_tag="$(printf '%s' "$clean_tag" | sed -e 's/[][(){}.^$+*?|\\/]/\\&/g')"
  pattern="/[^/]*_${escaped_tag}(_[^/]*)?_R1_001\\.f(ast)?q(\\.gz)?$"

  selected_tag="$(grep -E "$pattern" "$manifest" | grep -v '/trimmed/' || true)"
  if [ -z "$selected_tag" ]; then
    selected_tag="$(grep -E "$pattern" "$manifest" || true)"
  fi
  if [ -n "$selected_tag" ]; then
    selected_all="${selected_all}"$'\n'"${selected_tag}"
  fi
done

selected_dedup="$(printf '%s\n' "$selected_all" | awk 'NF && !seen[$0]++')"

if [ -n "$selected_dedup" ]; then
  printf '%s\n' "$selected_dedup" > "$out_file"
  count="$(printf '%s\n' "$selected_dedup" | awk 'NF{c++} END{print c+0}')"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    echo "__SELECTED_${label}_R1__:${line}"
  done <<< "$selected_dedup"
  echo "__SELECTED_${label}_R1_COUNT__:${count}"
else
  echo "__NO_${label}_FASTQ__"
  exit 1
fi
