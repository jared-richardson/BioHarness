#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 8 ]; then
  echo "usage: create_test_subset_from_r1_lists.sh <control_r1_list> <treatment_r1_list> <subset_dir> <control_out_list> <treatment_out_list> <reads_per_fastq> <control_label> <treatment_label>" >&2
  exit 2
fi

control_r1_list="$1"
treatment_r1_list="$2"
subset_dir="$3"
control_out_list="$4"
treatment_out_list="$5"
reads_per_fastq="$6"
control_label="$7"
treatment_label="$8"

if [ ! -s "$control_r1_list" ] || [ ! -s "$treatment_r1_list" ]; then
  echo "__TEST_SUBSET_SKIPPED__:missing_inputs"
  exit 1
fi

if ! [[ "$reads_per_fastq" =~ ^[0-9]+$ ]] || [ "$reads_per_fastq" -le 0 ]; then
  echo "__TEST_SUBSET_SKIPPED__:invalid_reads_per_fastq"
  exit 1
fi

max_lines=$((reads_per_fastq * 4))
mkdir -p "$subset_dir"
mkdir -p "$(dirname "$control_out_list")"
mkdir -p "$(dirname "$treatment_out_list")"

cache_key_file="${subset_dir}/.test_subset_cache_key"

file_sig() {
  target="$1"
  if [ ! -e "$target" ]; then
    echo "missing"
    return 0
  fi
  if stat -f "%m:%z" "$target" >/dev/null 2>&1; then
    stat -f "%m:%z" "$target"
    return 0
  fi
  if stat -c "%Y:%s" "$target" >/dev/null 2>&1; then
    stat -c "%Y:%s" "$target"
    return 0
  fi
  echo "unknown"
}

hash_stdin_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
    return 0
  fi
  python -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
}

emit_group_signature_rows() {
  in_list="$1"
  label="$2"
  while IFS= read -r r1; do
    [ -z "$r1" ] && continue
    r2="${r1/_R1_001/_R2_001}"
    printf '%s|%s|%s|%s|%s\n' "$label" "$r1" "$r2" "$(file_sig "$r1")" "$(file_sig "$r2")"
  done < "$in_list"
}

list_is_materialized() {
  in_list="$1"
  if [ ! -s "$in_list" ]; then
    return 1
  fi
  while IFS= read -r fp; do
    [ -z "$fp" ] && continue
    if [ ! -s "$fp" ]; then
      return 1
    fi
  done < "$in_list"
  return 0
}

cache_key="$(
  {
    printf 'reads_per_fastq=%s\n' "$reads_per_fastq"
    emit_group_signature_rows "$control_r1_list" "$control_label"
    emit_group_signature_rows "$treatment_r1_list" "$treatment_label"
  } | hash_stdin_sha256
)"

if [ -f "$cache_key_file" ] && [ "$(cat "$cache_key_file" 2>/dev/null)" = "$cache_key" ]; then
  if list_is_materialized "$control_out_list" && list_is_materialized "$treatment_out_list"; then
    control_count="$(awk 'NF{c++} END{print c+0}' "$control_out_list")"
    treatment_count="$(awk 'NF{c++} END{print c+0}' "$treatment_out_list")"
    echo "__TEST_SUBSET_SKIPPED__:cached"
    echo "__TEST_SUBSET_GROUP_COUNT__:${control_label}:${control_count}"
    echo "__TEST_SUBSET_GROUP_COUNT__:${treatment_label}:${treatment_count}"
    echo "__TEST_SUBSET_DONE__:reads_per_fastq:${reads_per_fastq}"
    exit 0
  fi
fi

: > "$control_out_list"
: > "$treatment_out_list"

subset_file() {
  src="$1"
  dst="$2"
  case "$src" in
    *.gz)
      gzip -dc "$src" | awk -v m="$max_lines" 'NR<=m{print} NR>=m{exit}' > "$dst"
      ;;
    *)
      awk -v m="$max_lines" 'NR<=m{print} NR>=m{exit}' "$src" > "$dst"
      ;;
  esac
}

process_group() {
  in_list="$1"
  out_list="$2"
  label="$3"
  count=0
  while IFS= read -r r1; do
    [ -z "$r1" ] && continue
    r2="${r1/_R1_001/_R2_001}"
    if [ ! -f "$r2" ]; then
      echo "__TEST_SUBSET_MISSING_PAIR__:${r1}"
      continue
    fi
    base_r1="$(basename "$r1" .gz)"
    base_r2="$(basename "$r2" .gz)"
    out_r1="${subset_dir}/${label}_${base_r1}"
    out_r2="${subset_dir}/${label}_${base_r2}"
    subset_file "$r1" "$out_r1"
    subset_file "$r2" "$out_r2"
    if [ ! -s "$out_r1" ] || [ ! -s "$out_r2" ]; then
      echo "__TEST_SUBSET_EMPTY__:${r1}"
      continue
    fi
    printf '%s\n' "$out_r1" >> "$out_list"
    count=$((count + 1))
  done < "$in_list"
  echo "__TEST_SUBSET_GROUP_COUNT__:${label}:${count}"
  LAST_GROUP_COUNT="$count"
}

process_group "$control_r1_list" "$control_out_list" "$control_label"
control_count="$LAST_GROUP_COUNT"
process_group "$treatment_r1_list" "$treatment_out_list" "$treatment_label"
treatment_count="$LAST_GROUP_COUNT"
if [ "${control_count:-0}" -le 0 ] || [ "${treatment_count:-0}" -le 0 ]; then
  echo "__TEST_SUBSET_SKIPPED__:empty_group_output"
  exit 1
fi
printf '%s\n' "$cache_key" > "$cache_key_file"
echo "__TEST_SUBSET_DONE__:reads_per_fastq:${reads_per_fastq}"
