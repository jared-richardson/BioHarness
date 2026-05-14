#!/usr/bin/env bash
set -euo pipefail

STAR_INDEX_REQUIRED_FILES=(
  Genome
  SA
  SAindex
  chrLength.txt
  chrName.txt
  chrNameLength.txt
  chrStart.txt
  genomeParameters.txt
)

bh_resolve_star_bin() {
  local candidate=""
  for candidate in "${BIO_HARNESS_STAR_BIN:-}" "$(command -v STAR || true)" "$(command -v star || true)" /usr/local/bin/STAR /usr/local/bin/star; do
    if [ -n "${candidate}" ] && [ -x "${candidate}" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

bh_hash_file() {
  local file_path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${file_path}" | awk '{print $1}'
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${file_path}" | awk '{print $1}'
    return 0
  fi
  if command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "${file_path}" | awk '{print $NF}'
    return 0
  fi
  echo "sha256_unavailable"
  return 0
}

bh_hash_text() {
  local text="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "${text}" | sha256sum | awk '{print $1}'
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "${text}" | shasum -a 256 | awk '{print $1}'
    return 0
  fi
  if command -v openssl >/dev/null 2>&1; then
    printf '%s' "${text}" | openssl dgst -sha256 | awk '{print $NF}'
    return 0
  fi
  echo "sha256_unavailable"
  return 0
}

bh_star_version() {
  local star_bin="$1"
  local raw=""
  raw="$("${star_bin}" --version 2>/dev/null | head -n 1 || true)"
  raw="$(printf '%s' "${raw}" | tr '\t' ' ' | tr -s ' ')"
  raw="${raw#"${raw%%[![:space:]]*}"}"
  raw="${raw%"${raw##*[![:space:]]}"}"
  if [ -z "${raw}" ]; then
    raw="unknown"
  fi
  printf '%s\n' "${raw}"
}

bh_build_star_cache_key() {
  local fasta_hash="$1"
  local gtf_hash="$2"
  local sjdb_overhang="$3"
  local star_version="$4"
  local payload=""
  payload="fasta_sha256=${fasta_hash}
gtf_sha256=${gtf_hash}
sjdb_overhang=${sjdb_overhang}
star_version=${star_version}"
  bh_hash_text "${payload}"
}

bh_star_manifest_path() {
  local index_dir="$1"
  printf '%s\n' "${index_dir}/.star_index_manifest"
}

bh_star_manifest_key() {
  local manifest_path="$1"
  if [ ! -f "${manifest_path}" ]; then
    return 1
  fi
  awk -F= '$1=="cache_key"{print substr($0, index($0, "=")+1)}' "${manifest_path}" | head -n 1
}

bh_validate_star_index_dir() {
  local index_dir="$1"
  local expected_key="$2"
  local manifest_path=""
  local key=""
  local required=""
  manifest_path="$(bh_star_manifest_path "${index_dir}")"
  if [ ! -f "${manifest_path}" ]; then
    return 1
  fi
  key="$(bh_star_manifest_key "${manifest_path}" || true)"
  if [ -z "${key}" ] || [ "${key}" != "${expected_key}" ]; then
    return 1
  fi
  for required in "${STAR_INDEX_REQUIRED_FILES[@]}"; do
    if [ ! -s "${index_dir}/${required}" ]; then
      return 1
    fi
  done
  return 0
}

bh_write_star_manifest() {
  local index_dir="$1"
  local cache_key="$2"
  local star_version="$3"
  local fasta_path="$4"
  local fasta_hash="$5"
  local gtf_path="$6"
  local gtf_hash="$7"
  local sjdb_overhang="$8"
  local manifest_path=""
  manifest_path="$(bh_star_manifest_path "${index_dir}")"
  mkdir -p "${index_dir}"
  cat > "${manifest_path}" <<EOF
schema_version=1
cache_key=${cache_key}
star_version=${star_version}
fasta_path=${fasta_path}
fasta_sha256=${fasta_hash}
gtf_path=${gtf_path}
gtf_sha256=${gtf_hash}
sjdb_overhang=${sjdb_overhang}
generated_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF
}

bh_copy_index_dir() {
  local src_dir="$1"
  local dest_dir="$2"
  mkdir -p "${dest_dir}"
  cp -R "${src_dir}/." "${dest_dir}/"
}
