"""Path extraction, resolution, and repair utilities."""
from __future__ import annotations

import re
import shlex
from functools import lru_cache
from pathlib import Path
from typing import Any

from bio_harness.core.pathing import discover_fastq_files_guarded
from bio_harness.core.shell_output_hints import extract_shell_output_hints
from bio_harness.core.shell_parse import split_shell_segments
from bio_harness.core.tool_registry import default_tool_registry
from bio_harness.harness.config import _OUTPUT_PATH_KEYS
from bio_harness.harness.plan_helpers import _normalize_steps, _renumber_plan_steps


# ---------------------------------------------------------------------------
# Low-level token / path helpers
# ---------------------------------------------------------------------------

_PATH_ARGUMENT_KEYS = frozenset(
    {
        "annotation_gtf",
        "counts_matrix",
        "fasta",
        "genome_dir",
        "genome_fasta",
        "gtf",
        "index_dir",
        "input_bam",
        "input_bams",
        "input_cram",
        "input_file",
        "input_files",
        "input_path",
        "input_paths",
        "metadata_table",
        "reads_1",
        "reads_2",
        "reference_fasta",
        "reference_gtf",
        "run_input",
        "transcriptome_fasta",
    }
)
_PATH_ARGUMENT_SUFFIXES = (
    "_bam",
    "_bams",
    "_cram",
    "_csv",
    "_dir",
    "_dirs",
    "_fa",
    "_fasta",
    "_fna",
    "_file",
    "_files",
    "_gff",
    "_gff3",
    "_gtf",
    "_matrix",
    "_path",
    "_paths",
    "_table",
    "_tsv",
    "_vcf",
    "_vcf_gz",
)
_PATH_PLACEHOLDER_RE = re.compile(r"\[PATH:([A-Za-z0-9_.-]+)\]")
_SELECTED_DIR_PLACEHOLDER_LABELS = frozenset(
    {
        "output_dir",
        "results",
        "results_dir",
        "selected",
        "selected_dir",
        "workspace",
        "workspace_dir",
    }
)
_DATA_ROOT_PLACEHOLDER_LABELS = frozenset(
    {
        "data",
        "data_dir",
        "data_root",
        "input_dir",
        "inputs",
        "inputs_dir",
        "inputs_readonly",
    }
)

def _looks_like_path_token(token: str) -> bool:
    t = str(token or "").strip()
    if not t:
        return False
    if t.startswith("-"):
        return False
    if "/" in t or t.startswith(".") or t.startswith("~"):
        return True
    low = t.lower()
    suffixes = (
        ".txt",
        ".tsv",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".bam",
        ".sam",
        ".fastq",
        ".fq",
        ".fastq.gz",
        ".fq.gz",
        ".vcf",
        ".vcf.gz",
    )
    return low.endswith(suffixes)


def _resolve_candidate_path(token: str, selected_dir: Path) -> Path:
    p = Path(str(token or "").strip()).expanduser()
    if not p.is_absolute():
        p = selected_dir / p
    try:
        return p.resolve(strict=False)
    except Exception:
        return p


def _resolve_path_placeholder_value(
    label: str,
    *,
    selected_dir: Path,
    data_root: Path,
) -> str:
    """Return a deterministic replacement for one compacted path placeholder."""

    normalized = str(label or "").strip().lower()
    if not normalized:
        return ""
    if normalized in _SELECTED_DIR_PLACEHOLDER_LABELS:
        return str(selected_dir)
    if normalized in _DATA_ROOT_PLACEHOLDER_LABELS:
        return str(data_root)
    if normalized.endswith("_dir"):
        if any(token in normalized for token in ("output", "result", "selected", "workspace")):
            return str(selected_dir)
        if any(token in normalized for token in ("data", "input")):
            return str(data_root)
    if any(token in normalized for token in ("output", "result", "selected", "workspace")):
        return str(selected_dir)
    if any(token in normalized for token in ("data", "input")):
        return str(data_root)
    return ""


def _looks_like_path_argument_key(key: str, *, tool_name: str = "") -> bool:
    """Return whether *key* is expected to carry a filesystem path."""

    key_l = str(key or "").strip().lower()
    if not key_l:
        return False
    if _is_output_argument_key(key_l, tool_name) or key_l in _PATH_ARGUMENT_KEYS:
        return True
    if key_l.startswith(("input_", "reference_")):
        return True
    return key_l.endswith(_PATH_ARGUMENT_SUFFIXES)


@lru_cache(maxsize=128)
def _registry_output_argument_keys(tool_name: str) -> frozenset[str]:
    """Return registry-declared output argument keys for one tool."""

    normalized_tool = str(tool_name or "").strip().lower()
    if not normalized_tool:
        return frozenset()
    registry = default_tool_registry()
    keys = {
        str(key).strip().lower()
        for key in (
            list(registry.output_argument_keys_for(normalized_tool))
            + list(registry.execution_output_parameters_for(normalized_tool))
        )
        if str(key).strip()
    }
    return frozenset(keys)


def _is_output_argument_key(key: str, tool_name: str = "") -> bool:
    """Return whether *key* is an output path argument for *tool_name*."""

    key_l = str(key or "").strip().lower()
    if not key_l:
        return False
    if key_l in _OUTPUT_PATH_KEYS:
        return True
    return key_l in _registry_output_argument_keys(tool_name)


def _rewrite_path_value_against_remaps(
    value: Any,
    *,
    key: str,
    tool_name: str = "",
    selected_dir: Path,
    path_remaps: list[tuple[Path, Path]],
) -> Any:
    """Rewrite one path-like argument against normalized output remaps."""

    if not path_remaps or not _looks_like_path_argument_key(key, tool_name=tool_name):
        return value

    def _rewrite_text(raw_text: str) -> str:
        text = str(raw_text or "").strip()
        if not text:
            return text
        candidate = _resolve_candidate_path(text, selected_dir)
        for old_path, new_path in path_remaps:
            try:
                relative = candidate.relative_to(old_path)
            except ValueError:
                continue
            if str(relative) == ".":
                return str(new_path)
            return str((new_path / relative).resolve(strict=False))
        return text

    if isinstance(value, str):
        return _rewrite_text(value)
    if isinstance(value, list):
        return [
            _rewrite_text(item) if isinstance(item, str) else item
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _rewrite_text(item) if isinstance(item, str) else item
            for item in value
        )
    return value


def _extract_paths_from_command(command: str, selected_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for seg in split_shell_segments(command or ""):
        segment = str(seg or "").strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except Exception:
            tokens = segment.split()
        if not tokens:
            continue
        i = 0
        while i < len(tokens):
            tok = str(tokens[i]).strip()
            if not tok:
                i += 1
                continue
            if tok in {">", ">>", "1>", "2>", "<"} and i + 1 < len(tokens):
                next_tok = str(tokens[i + 1]).strip()
                if _looks_like_path_token(next_tok):
                    candidates.append(_resolve_candidate_path(next_tok, selected_dir))
                i += 2
                continue
            if tok.startswith("--") and "=" in tok:
                _, rhs = tok.split("=", 1)
                if _looks_like_path_token(rhs):
                    candidates.append(_resolve_candidate_path(rhs, selected_dir))
                i += 1
                continue
            if _looks_like_path_token(tok):
                candidates.append(_resolve_candidate_path(tok, selected_dir))
            i += 1
    dedup: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        ps = str(p)
        if ps in seen:
            continue
        seen.add(ps)
        dedup.append(p)
    return dedup


def _extract_paths_from_argument_value(value: Any, selected_dir: Path) -> list[Path]:
    raw_tokens: list[str] = []
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        for item in value:
            if item is None:
                continue
            raw_tokens.extend(str(item).split(","))
    else:
        raw_tokens.extend(str(value).split(","))

    parsed: list[Path] = []
    for raw in raw_tokens:
        txt = str(raw).strip()
        if not txt:
            continue
        try:
            tokens = shlex.split(txt, posix=True)
        except Exception:
            tokens = txt.split()
        for tok in tokens:
            if _looks_like_path_token(tok):
                parsed.append(_resolve_candidate_path(tok, selected_dir))

    dedup: list[Path] = []
    seen: set[str] = set()
    for p in parsed:
        ps = str(p)
        if ps in seen:
            continue
        seen.add(ps)
        dedup.append(p)
    return dedup


# ---------------------------------------------------------------------------
# Group / evidence helpers
# ---------------------------------------------------------------------------

def _text_mentions_group_token(text: str, aliases: set[str]) -> bool:
    body = str(text or "").strip().lower()
    if not body or not aliases:
        return False
    for alias in aliases:
        if not alias:
            continue
        pat = rf"(^|[^a-z0-9]){re.escape(alias)}($|[^a-z0-9])"
        if re.search(pat, body):
            return True
    return False


def _path_has_positive_group_evidence(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_file():
        try:
            return int(path.stat().st_size) > 0
        except Exception:
            return False
    if path.is_dir():
        try:
            for child in path.rglob("*"):
                if child.is_file() and child.stat().st_size > 0:
                    return True
        except Exception:
            return False
    return False


def _path_text_has_group_evidence(path: Path, aliases: set[str]) -> bool:
    if not path.exists() or not path.is_file() or not aliases:
        return False
    suffix = path.suffix.lower()
    allowed_suffixes = {".txt", ".tsv", ".csv", ".json", ".yaml", ".yml", ".lst", ".list", ".tab"}
    if suffix and suffix not in allowed_suffixes:
        return False
    try:
        size = int(path.stat().st_size)
    except Exception:
        return False
    if size <= 0:
        return False
    try:
        with path.open("rb") as handle:
            chunk = handle.read(min(size, 256 * 1024))
    except Exception:
        return False
    if not chunk or b"\x00" in chunk:
        return False
    body = chunk.decode("utf-8", errors="ignore")
    return _text_mentions_group_token(body, aliases)


# ---------------------------------------------------------------------------
# FASTQ discovery
# ---------------------------------------------------------------------------

def _discover_fastq_files(root_path: str, include_subdirs: bool, name_filter: str, max_files: int) -> list[str]:
    try:
        root = Path(root_path).expanduser().resolve()
    except Exception:
        return []
    return discover_fastq_files_guarded(
        root,
        include_subdirs=include_subdirs,
        name_filter=name_filter,
        max_files=max_files,
    )


# ---------------------------------------------------------------------------
# Plan path repair / redirect
# ---------------------------------------------------------------------------

def _repair_workspace_placeholder_paths_in_plan(
    plan: dict[str, Any],
    *,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve workspace and compacted path placeholders inside one plan."""

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    replacements: list[dict[str, Any]] = []
    unresolved_placeholders: set[str] = set()

    def _replace_workspace_tokens(text: str) -> str:
        updated = str(text or "")
        updated = re.sub(
            r"(?<![A-Za-z0-9_./-])/workspace/data(?=/|\b)",
            str(data_root).rstrip("/"),
            updated,
        )
        updated = re.sub(
            r"(?<![A-Za-z0-9_./-])/workspace/results(?=/|\b)",
            str(selected_dir / "results").rstrip("/"),
            updated,
        )
        updated = re.sub(
            r"(?<![A-Za-z0-9_./-])/workspace(?=/|\b)",
            str(selected_dir).rstrip("/"),
            updated,
        )
        return updated

    def _replace_path_placeholders(text: str) -> str:
        updated = str(text or "")

        def _replace(match: re.Match[str]) -> str:
            label = str(match.group(1) or "").strip()
            replacement = _resolve_path_placeholder_value(
                label,
                selected_dir=selected_dir,
                data_root=data_root,
            )
            if replacement:
                return replacement
            unresolved_placeholders.add(label)
            return match.group(0)

        return _PATH_PLACEHOLDER_RE.sub(_replace, updated)

    def _rewrite_text(value: str, *, embedded: bool = False) -> str:
        text = str(value or "")
        text = _replace_path_placeholders(text)
        if embedded:
            return _replace_workspace_tokens(text)
        return _replace_workspace_tokens(text)

    def _rewrite_value(value: Any, *, embedded: bool = False) -> Any:
        if isinstance(value, str):
            return _rewrite_text(value, embedded=embedded)
        if isinstance(value, list):
            return [_rewrite_value(item, embedded=embedded) for item in value]
        if isinstance(value, dict):
            rewritten: dict[str, Any] = {}
            for key, item in value.items():
                rewritten[key] = _rewrite_value(item, embedded=str(key).strip().lower() == "command")
            return rewritten
        return value

    changed = False
    for idx, step in enumerate(steps, start=1):
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        updated_args = _rewrite_value(args)
        if updated_args != args:
            step["arguments"] = updated_args
            replacements.append({"step_id": int(step.get("step_id", idx)), "tool_name": str(step.get("tool_name", "")).strip()})
            changed = True

    if not changed:
        return plan, {"changed": False, "why": "no_workspace_placeholder_paths"}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    meta = {
        "changed": True,
        "replacements": replacements,
        "diff_summary": {"replacement_count": len(replacements)},
    }
    if unresolved_placeholders:
        meta["unresolved_placeholders"] = sorted(unresolved_placeholders)
    return patched, meta


def _redirect_output_paths_to_selected_dir(
    plan: dict[str, Any],
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Redirect any output argument paths that fall outside selected_dir.

    When the LLM generates a plan with output paths in the data directory or
    elsewhere, this function rewrites them into *selected_dir* using the same
    leaf structure.  Input-like keys (reads_1/2, reference_fasta, etc.) are
    left alone.
    """
    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    del data_root  # Output redirection is selected-dir scoped.
    selected_resolved = selected_dir.resolve(strict=False)
    replacements: list[dict[str, Any]] = []
    path_remap_pairs: list[tuple[Path, Path]] = []

    for idx, step in enumerate(steps, start=1):
        tool_name = str(step.get("tool_name", "")).strip().lower()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        if not args:
            continue
        updated_args = dict(args)
        step_changed = False
        for key, value in args.items():
            if not _is_output_argument_key(key, tool_name):
                continue
            raw = str(value or "").strip()
            if not raw:
                continue
            try:
                raw_path = Path(raw).expanduser()
                normalized_old = _resolve_candidate_path(raw, selected_resolved)
            except Exception:
                continue
            if raw_path.is_absolute():
                try:
                    normalized_old.relative_to(selected_resolved)
                    continue
                except ValueError:
                    pass
                leaf = normalized_old.name or raw_path.name
                new_path = (selected_resolved / leaf).resolve(strict=False)
            else:
                new_path = normalized_old
            updated_args[key] = str(new_path)
            replacements.append({
                "step_id": int(step.get("step_id", idx)),
                "tool_name": str(step.get("tool_name", "")).strip(),
                "argument": key,
                "from": raw,
                "from_normalized": str(normalized_old),
                "to": str(new_path),
            })
            if str(normalized_old) != str(new_path):
                path_remap_pairs.append((normalized_old, new_path))
            step_changed = True
        if step_changed:
            step["arguments"] = updated_args

    if not replacements:
        return plan, {"changed": False, "why": "no_output_redirects_needed"}

    # Build exact old→new output path remap (safe for all args) and a
    # broader parent-directory remap (only safe for bash commands).
    exact_remap: dict[str, str] = {}
    parent_remap: dict[str, str] = {}
    for r in replacements:
        old_p = str(r.get("from_normalized", "")).strip()
        new_p = str(r.get("to", "")).strip()
        if old_p and new_p and old_p != new_p:
            exact_remap[old_p] = new_p
            # Also map the parent dir if it's outside selected_dir
            old_parent = str(Path(old_p).parent)
            try:
                Path(old_parent).resolve(strict=False).relative_to(selected_resolved)
            except (ValueError, Exception):
                parent_remap[old_parent] = str(selected_resolved)

    # Build fuzzy stem-based remaps: when evol1.bam -> selected_dir/evol1.bam
    # is in exact_remap, also remap sibling paths with same stem
    # (e.g. evol1_sorted.bam, evol1.bam.bai) in the same parent directory.
    stem_remap: dict[str, str] = {}
    for old_p, new_p in exact_remap.items():
        old_path = Path(old_p)
        new_path = Path(new_p)
        old_parent = str(old_path.parent)
        stem = old_path.stem.split(".")[0]  # e.g. evol1 from evol1.sorted.bam
        if not stem or len(stem) < 3:
            continue
        # For all steps, check arguments for paths with same stem in same parent
        for step in steps:
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            for key, value in args.items():
                raw = str(value or "").strip()
                if not raw or raw in exact_remap:
                    continue
                try:
                    vp = Path(raw)
                    if str(vp.parent) == old_parent and stem in vp.name and raw not in stem_remap:
                        stem_remap[raw] = str(new_path.parent / vp.name)
                except Exception:
                    continue

    # Merge stem remaps into exact_remap (exact takes precedence)
    combined_remap = {**stem_remap, **exact_remap}

    # Apply exact + stem output path remap to ALL arguments in ALL steps.
    # This fixes inter-step references (e.g. step N's output_bam →
    # step N+1's input_bam) that would otherwise point to old paths.
    if combined_remap:
        sorted_exact = sorted(combined_remap.items(), key=lambda kv: -len(kv[0]))
        for step in steps:
            tool_name = str(step.get("tool_name", "")).strip().lower()
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            if not args:
                continue
            updated_args = dict(args)
            step_changed = False
            for key, value in args.items():
                if _is_output_argument_key(key, tool_name):
                    continue  # Already redirected above
                updated_val = _rewrite_path_value_against_remaps(
                    value,
                    key=key,
                    tool_name=tool_name,
                    selected_dir=selected_resolved,
                    path_remaps=path_remap_pairs,
                )
                if updated_val != value:
                    updated_args[key] = updated_val
                    step_changed = True
            if step_changed:
                step["arguments"] = updated_args

    # Apply both exact AND parent remaps to bash_run command strings.
    # Parent dir remaps catch sibling file references (e.g. quant_results.tsv
    # written next to an output directory).
    full_remap = {**parent_remap, **stem_remap, **exact_remap}
    if full_remap:
        sorted_full = sorted(full_remap.items(), key=lambda kv: -len(kv[0]))
        for step in steps:
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            command = str(args.get("command", "")).strip()
            if not command:
                continue
            updated_command = command
            for old_p, new_p in sorted_full:
                if old_p in updated_command:
                    updated_command = updated_command.replace(old_p, new_p)
            # Safety check: reject the rewritten command if it contains
            # obviously corrupted compound paths (e.g., doubled home dirs
            # like /Users/foo/Users/foo/) which indicate a cascading
            # replacement failure.  Fall back to the original command.
            if updated_command != command:
                import re as _re
                _corrupted = _re.search(
                    r"/Users/.+/Users/|/home/.+/home/",
                    updated_command,
                )
                if _corrupted:
                    print(
                        f"[WARNING] Path redirect produced corrupted compound path in "
                        f"bash command (step {step.get('step_id', '?')}); reverting to original command."
                    )
                    # Keep original command unchanged
                else:
                    step["arguments"] = {**args, "command": updated_command}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "replacements": replacements,
        "diff_summary": {"replacement_count": len(replacements)},
    }


# ---------------------------------------------------------------------------
# Path resolution / normalization for plan values
# ---------------------------------------------------------------------------

def _resolve_existing_input_path(path_text: str, selected_dir: Path, data_root: Path) -> str:
    raw = str(path_text or "").strip()
    if not raw:
        return ""
    p = Path(raw).expanduser()
    candidates: list[Path] = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.extend(
            [
                selected_dir / raw,
                data_root / raw,
                selected_dir / "inputs_readonly" / raw,
            ]
        )
    for candidate in candidates:
        try:
            if candidate.exists():
                return str(candidate.resolve(strict=False))
        except OSError:
            continue
    return ""


def _normalize_plan_path_text(path_text: str, selected_dir: Path) -> str:
    raw = str(path_text or "").strip()
    if not raw:
        return ""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = selected_dir / p
    try:
        return str(p.resolve(strict=False))
    except OSError:
        return str(p)


def _iter_pathlike_values(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, (list, tuple, set)):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    text = str(raw_value).strip()
    return [text] if text else []


def _collect_planned_output_paths(plan: dict[str, Any], selected_dir: Path) -> set[str]:
    registry = default_tool_registry()
    planned: set[str] = set()
    for step in plan.get("plan", []) if isinstance(plan, dict) else []:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        output_keys = {
            str(key).strip().lower()
            for key in (
                list(registry.output_argument_keys_for(tool_name))
                + list(registry.execution_output_parameters_for(tool_name))
            )
            if str(key).strip()
        }
        for key, raw_value in args.items():
            key_l = str(key).strip().lower()
            if not key_l.startswith("output") and key_l not in output_keys:
                continue
            for item in _iter_pathlike_values(raw_value):
                normalized = _normalize_plan_path_text(item, selected_dir)
                if normalized:
                    planned.add(normalized)
        if tool_name == "spades_assemble":
            out_dir = str(args.get("output_dir", "")).strip()
            if out_dir:
                for name in ("contigs.fasta", "scaffolds.fasta"):
                    normalized = _normalize_plan_path_text(str(Path(out_dir) / name), selected_dir)
                    if normalized:
                        planned.add(normalized)
        if tool_name == "bash_run":
            command = str(args.get("command", "")).strip()
            if not command:
                continue
            hints = extract_shell_output_hints(
                command,
                extra_output_flags=(
                    "-O",
                    "-h",
                    "-j",
                    "--out1",
                    "--out2",
                    "--report",
                    "--detected",
                    "--bam",
                    "--ref",
                ),
            )
            for candidate in hints.output_paths + hints.output_roots:
                normalized = _normalize_plan_path_text(candidate, selected_dir)
                if normalized:
                    planned.add(normalized)
    return planned


# ---------------------------------------------------------------------------
# Path containment / redirection helpers
# ---------------------------------------------------------------------------

def _path_within_root(path_value: str, root: Path) -> bool:
    raw = str(path_value or "").strip()
    if not raw:
        return False
    try:
        path = Path(raw).expanduser().resolve(strict=False)
        root_resolved = Path(root).expanduser().resolve(strict=False)
        path.relative_to(root_resolved)
        return True
    except Exception:
        return False


def _path_within_any_root(path_value: str, roots: list[Path] | tuple[Path, ...]) -> bool:
    return any(_path_within_root(path_value, root) for root in roots)


def _redirection_parent_dirs(command: str, selected_dir: Path) -> list[str]:
    parents: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?:^|[^>])>>?\s*([^\s]+)", str(command or "")):
        raw = str(match.group(1) or "").strip().strip("\"'").rstrip(");,")
        if raw in {"/dev/null", "NUL"}:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = selected_dir / path
        resolved = path.resolve(strict=False)
        parent = str(resolved.parent)
        if not _path_within_root(parent, selected_dir):
            continue
        if parent in seen:
            continue
        seen.add(parent)
        parents.append(parent)
    return parents
