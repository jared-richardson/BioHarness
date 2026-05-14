#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 7 ]; then
  echo "usage: run_rmats_if_needed.sh <b1_list> <b2_list> <gtf> <out_dir> <tmp_dir> <read_length> <threads>" >&2
  exit 2
fi

b1_list="$1"
b2_list="$2"
gtf_path="$3"
out_dir="$4"
tmp_dir="$5"
read_length="$6"
threads="$7"

if [ -f "$out_dir/SE.MATS.JCEC.txt" ] || [ -f "$out_dir/SE.MATS.JC.txt" ]; then
  echo "__RMATS_SKIPPED__:cached"
  exit 0
fi

if [ ! -s "$b1_list" ] || [ ! -s "$b2_list" ]; then
  echo "__RMATS_INPUT_LIST_EMPTY__"
  exit 0
fi

mkdir -p "$out_dir" "$tmp_dir"
run_tmp="${tmp_dir}/run_$(date +%Y%m%d_%H%M%S)_$$"
mkdir -p "$run_tmp"

rmats_cmd="${RMATS_BIN:-}"
rmats_python_bin="${RMATS_PYTHON_BIN:-}"
rmats_pythonpath="${RMATS_PYTHONPATH:-}"
if [ -n "$rmats_cmd" ] && [ -e "$rmats_cmd" ]; then
  if [ -n "$rmats_python_bin" ] && [ -x "$rmats_python_bin" ]; then
    if [ -n "$rmats_pythonpath" ]; then
      PYTHONPATH="${rmats_pythonpath}${PYTHONPATH:+:${PYTHONPATH}}" \
      "$rmats_python_bin" "$rmats_cmd" \
        --b1 "$b1_list" \
        --b2 "$b2_list" \
        --gtf "$gtf_path" \
        --od "$out_dir" \
        --tmp "$run_tmp" \
        --readLength "$read_length" \
        --nthread "$threads" || {
          rc=$?
          echo "__RMATS_FAILED__:exit_code:${rc}"
          exit "$rc"
        }
    else
      "$rmats_python_bin" "$rmats_cmd" \
        --b1 "$b1_list" \
        --b2 "$b2_list" \
        --gtf "$gtf_path" \
        --od "$out_dir" \
        --tmp "$run_tmp" \
        --readLength "$read_length" \
        --nthread "$threads" || {
          rc=$?
          echo "__RMATS_FAILED__:exit_code:${rc}"
          exit "$rc"
        }
    fi
  else
    "$rmats_cmd" \
      --b1 "$b1_list" \
      --b2 "$b2_list" \
      --gtf "$gtf_path" \
      --od "$out_dir" \
      --tmp "$run_tmp" \
      --readLength "$read_length" \
      --nthread "$threads" || {
        rc=$?
        echo "__RMATS_FAILED__:exit_code:${rc}"
        exit "$rc"
      }
  fi
elif command -v rMATS.py >/dev/null 2>&1; then
  rMATS.py \
    --b1 "$b1_list" \
    --b2 "$b2_list" \
    --gtf "$gtf_path" \
    --od "$out_dir" \
    --tmp "$run_tmp" \
    --readLength "$read_length" \
    --nthread "$threads" || {
      rc=$?
      echo "__RMATS_FAILED__:exit_code:${rc}"
      exit "$rc"
    }
elif command -v rmats.py >/dev/null 2>&1; then
  rmats.py \
    --b1 "$b1_list" \
    --b2 "$b2_list" \
    --gtf "$gtf_path" \
    --od "$out_dir" \
    --tmp "$run_tmp" \
    --readLength "$read_length" \
    --nthread "$threads" || {
      rc=$?
      echo "__RMATS_FAILED__:exit_code:${rc}"
      exit "$rc"
    }
elif command -v rmats >/dev/null 2>&1; then
  rmats \
    --b1 "$b1_list" \
    --b2 "$b2_list" \
    --gtf "$gtf_path" \
    --od "$out_dir" \
    --tmp "$run_tmp" \
    --readLength "$read_length" \
    --nthread "$threads" || {
      rc=$?
      echo "__RMATS_FAILED__:exit_code:${rc}"
      exit "$rc"
    }
else
  echo "__MISSING_TOOL__:rmats"
  exit 1
fi

echo "__RMATS_DONE__:$out_dir"
echo "__RMATS_TMP_RUN_DIR__:${run_tmp}"
