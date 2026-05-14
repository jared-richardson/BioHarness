#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: check_required_tools.sh <tool1> [tool2 ...]" >&2
  exit 2
fi

for tool_name in "$@"; do
  case "$tool_name" in
    star)
      if [ -x /usr/local/bin/STAR ] || [ -x /usr/local/bin/star ] || command -v STAR >/dev/null 2>&1 || command -v star >/dev/null 2>&1; then
        :
      else
        echo "__MISSING_TOOL__:star"
      fi
      ;;
    bwa)
      if command -v bwa >/dev/null 2>&1 || command -v bwa-mem2 >/dev/null 2>&1; then
        :
      else
        echo "__MISSING_TOOL__:bwa"
      fi
      ;;
    subread)
      if command -v subjunc >/dev/null 2>&1 || command -v subread-align >/dev/null 2>&1 || command -v subread >/dev/null 2>&1; then
        :
      else
        echo "__MISSING_TOOL__:subread"
      fi
      ;;
    varscan)
      if command -v varscan >/dev/null 2>&1 || command -v VarScan >/dev/null 2>&1; then
        :
      else
        echo "__MISSING_TOOL__:varscan"
      fi
      ;;
    rmats)
      if command -v rmats.py >/dev/null 2>&1 || command -v rmats >/dev/null 2>&1; then
        :
      else
        echo "__MISSING_TOOL__:rmats"
      fi
      ;;
    *)
      if command -v "$tool_name" >/dev/null 2>&1; then
        :
      else
        echo "__MISSING_TOOL__:${tool_name}"
      fi
      ;;
  esac
done
