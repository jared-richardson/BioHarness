"""Reference and STAR-index resolution helpers for workflow templates."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Callable

from bio_harness.core.shell_parse import split_shell_segments

BASH_REFERENCE_FLAG_KINDS = {
    "--gtf": "gtf",
    "--gtf_path": "gtf",
    "--annotation_gtf": "gtf",
    "--annotation": "gtf",
    "--sjdbgtffile": "gtf",
    "--fasta": "fasta",
    "--reference_fasta": "fasta",
    "--genome_fasta": "fasta",
    "--genome_fasta_file": "fasta",
    "--genomefastafiles": "fasta",
}
_ANNOTATION_GFF_SUFFIXES = (".gff", ".gff.gz", ".gff3", ".gff3.gz")


def infer_workspace_root(path_hint: str) -> Path:
    """Infer the nearest workspace root from one path hint."""

    path = Path(str(path_hint or "")).expanduser().resolve(strict=False)
    for candidate in (path, *path.parents):
        if candidate.name.lower() == "workspace":
            return candidate
    return path if path.is_dir() else path.parent


def discover_star_index_dirs(*roots: Path) -> list[Path]:
    """Discover STAR index directories beneath candidate roots."""

    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root or not root.exists():
            continue
        try:
            iterator = root.rglob("genomeParameters.txt")
        except Exception:
            continue
        for genome_parameters in iterator:
            try:
                parent = genome_parameters.parent.resolve(strict=False)
            except Exception:
                parent = genome_parameters.parent
            parent_s = str(parent)
            if "inputs_readonly" in parent_s or parent_s in seen:
                continue
            seen.add(parent_s)
            found.append(parent)
    return found


def pick_star_index_dir(requested_genome_dir: str, *, data_root: str) -> str:
    """Choose the best STAR index directory for one requested path."""

    requested_dir = Path(str(requested_genome_dir or "")).expanduser()
    try:
        requested_dir = requested_dir.resolve(strict=False)
    except Exception:
        pass
    if requested_dir and (requested_dir / "genomeParameters.txt").exists():
        return str(requested_dir)
    if requested_dir and requested_dir.is_absolute():
        requested_parts = {part.lower() for part in requested_dir.parts}
        if "star_index" in requested_parts or requested_dir.name.lower() == "star_index":
            return str(requested_dir)

    roots: list[Path] = []
    if str(requested_genome_dir or "").strip():
        roots.append(infer_workspace_root(str(requested_genome_dir)))
    if str(data_root or "").strip():
        roots.append(infer_workspace_root(str(data_root)))
    candidates = discover_star_index_dirs(*roots)
    if not candidates:
        return ""

    def _score(path: Path) -> tuple[int, int, int]:
        rendered = str(path).lower()
        score_cache = 0 if "/outputs/_cache/star_indexes/" in rendered else 1
        score_outputs = 0 if "/outputs/" in rendered else 1
        return (score_cache, score_outputs, len(rendered))

    ranked = sorted(candidates, key=_score)
    return str(ranked[0])


def pick_reference_file(requested_path: str, *, kind: str, data_root: str) -> str:
    """Choose the best reference file candidate for one requested path."""

    requested = Path(str(requested_path or "")).expanduser()
    if str(requested_path or "").strip():
        try:
            if requested.exists():
                if requested.is_absolute():
                    return str(requested)
                return str(requested.resolve(strict=False))
        except OSError:
            pass

    workspace = infer_workspace_root(str(data_root or requested_path))
    if kind == "gtf":
        aliases = ("mouse_gtf",)
        suffixes = (".gtf", ".gtf.gz")
    elif kind == "gff":
        aliases = ()
        suffixes = _ANNOTATION_GFF_SUFFIXES
    else:
        aliases = ("mouse_fasta", "mouse_fa")
        suffixes = (".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz")

    candidates: list[Path] = []
    alias_candidates: list[Path] = []
    roots = [workspace / "inputs_readonly", workspace / "references", workspace]
    for root in roots:
        try:
            root_resolved = root.resolve(strict=False)
        except Exception:
            root_resolved = root
        if root_resolved == Path(root_resolved.anchor) or not root.exists():
            continue
        for alias_path in [root / alias for alias in aliases]:
            try:
                if alias_path.exists() or alias_path.is_symlink():
                    alias_candidates.append(alias_path.resolve(strict=False))
            except OSError:
                continue
        try:
            iterator = root.rglob("*")
        except Exception:
            continue
        for item in iterator:
            if len(candidates) >= 300:
                break
            try:
                if not item.is_file():
                    continue
            except OSError:
                continue
            if item.name.lower().endswith(suffixes):
                candidates.append(item.resolve(strict=False))

    if alias_candidates:
        ranked_aliases = sorted({str(path): path for path in alias_candidates}.values(), key=lambda path: len(str(path)))
        return str(ranked_aliases[0])
    if not candidates:
        return ""
    ranked = sorted({str(path): path for path in candidates}.values(), key=lambda path: len(str(path)))
    return str(ranked[0])


def rewrite_bash_reference_flags(
    command: str,
    *,
    data_root: str,
    preserve_reference_path: Callable[[str], bool] | None = None,
) -> tuple[str, bool]:
    """Rewrite reference-valued bash flags to stable workspace-local paths.

    Args:
        command: Raw shell command to inspect.
        data_root: Active workspace data root used for reference discovery.
        preserve_reference_path: Optional callback that vetoes rebinding for
            already-owned or otherwise trusted reference paths.

    Returns:
        Tuple of ``(rewritten_command, changed)``.
    """

    raw = (command or "").strip()
    if not raw:
        return raw, False
    if any(op in raw for op in ("&&", "||", "|")):
        return raw, False

    changed = False
    rewritten_segments: list[str] = []
    for segment_raw in split_shell_segments(raw):
        segment = segment_raw.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except Exception:
            rewritten_segments.append(segment)
            continue
        if not tokens:
            continue

        seg_changed = False
        index = 0
        while index < len(tokens):
            token = tokens[index]
            token_l = token.lower()
            if token_l in BASH_REFERENCE_FLAG_KINDS and index + 1 < len(tokens):
                kind = BASH_REFERENCE_FLAG_KINDS[token_l]
                old_val = tokens[index + 1]
                if preserve_reference_path and preserve_reference_path(old_val):
                    index += 2
                    continue
                replacement = pick_reference_file(old_val, kind=kind, data_root=data_root)
                if replacement and replacement != old_val:
                    tokens[index + 1] = replacement
                    seg_changed = True
                index += 2
                continue

            if token.startswith("--") and "=" in token:
                flag, old_val = token.split("=", 1)
                kind = BASH_REFERENCE_FLAG_KINDS.get(flag.lower(), "")
                if kind and old_val:
                    if preserve_reference_path and preserve_reference_path(old_val):
                        index += 1
                        continue
                    replacement = pick_reference_file(old_val, kind=kind, data_root=data_root)
                    if replacement and replacement != old_val:
                        tokens[index] = f"{flag}={replacement}"
                        seg_changed = True
            index += 1

        if seg_changed:
            changed = True
            rewritten_segments.append(" ".join(shlex.quote(token) for token in tokens))
        else:
            rewritten_segments.append(segment)

    if not changed:
        return raw, False
    return " ; ".join(rewritten_segments).strip(), True
