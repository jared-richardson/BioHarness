from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

FASTQ_SUFFIXES = (".fastq", ".fq", ".fastq.gz", ".fq.gz")


class PathResolutionError(ValueError):
    """Raised when a candidate path cannot pass canonical resolution checks."""


def has_repeated_workspace_segments(path: Path) -> bool:
    """Check if a path contains duplicate workspace/inputs_readonly segments.

    Detects accidental path nesting like workspace/inputs_readonly/.../workspace/inputs_readonly.

    Args:
        path: Path to check.

    Returns:
        True if the workspace/inputs_readonly segment appears more than once.
    """
    parts = [p.lower() for p in path.parts]
    needle = ["workspace", "inputs_readonly"]
    hits = 0
    for i in range(0, len(parts) - 1):
        if parts[i : i + 2] == needle:
            hits += 1
    return hits > 1


def discover_read_roots(workspace_root: Path, readonly_root: Path) -> List[Path]:
    """Build a deduplicated list of allowed read roots.

    Includes the workspace root, readonly root, and any symlink targets
    found under the readonly root.

    Args:
        workspace_root: Path to the workspace directory.
        readonly_root: Path to the read-only inputs directory.

    Returns:
        Deduplicated list of resolved root Paths.
    """
    roots = [workspace_root.resolve(), readonly_root.resolve()]
    for entry in readonly_root.iterdir() if readonly_root.exists() else []:
        if not entry.is_symlink():
            continue
        try:
            roots.append(entry.resolve(strict=True))
        except Exception:
            continue
    dedup: List[Path] = []
    seen = set()
    for r in roots:
        s = str(r)
        if s in seen:
            continue
        seen.add(s)
        dedup.append(r)
    return dedup


def _in_any_root(path: Path, roots: Iterable[Path]) -> bool:
    """Check if a path is under any of the given root directories.

    Args:
        path: Resolved path to check.
        roots: Iterable of allowed root directories.

    Returns:
        True if the path is a descendant of any root.
    """
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def canonical_resolve(
    candidate: str | Path,
    *,
    read_roots: Iterable[Path],
    write_roots: Iterable[Path],
    mode: str,
    readonly_root: Path,
) -> tuple[Path | None, str | None]:
    """Resolve and validate a path against sandbox rules.

    For 'read' mode, the path must be under any read root. For 'write' mode,
    the path must be under a write root and not inside the readonly root.

    Args:
        candidate: Raw path string or Path to resolve.
        read_roots: Allowed roots for read access.
        write_roots: Allowed roots for write access.
        mode: Either 'read' or 'write'.
        readonly_root: Root directory that is always write-protected.

    Returns:
        Tuple of (resolved_path, rejection_reason). One will be None.
    """
    p = Path(str(candidate)).expanduser()
    try:
        resolved = p.resolve(strict=False)
    except Exception as exc:
        return None, f"resolution_failed:{exc}"

    if has_repeated_workspace_segments(resolved):
        return None, "repeated_workspace_segment"

    if mode == "read":
        if not _in_any_root(resolved, read_roots):
            return None, "outside_allowed_read_roots"
        return resolved, None

    if mode == "write":
        if not _in_any_root(resolved, write_roots):
            return None, "outside_allowed_write_roots"
        try:
            resolved.relative_to(readonly_root.resolve())
            return None, "writes_to_inputs_readonly_forbidden"
        except ValueError:
            return resolved, None

    return None, f"invalid_mode:{mode}"


def resolve_with_rejections(
    candidates: Iterable[str | Path],
    *,
    read_roots: Iterable[Path],
    write_roots: Iterable[Path],
    mode: str,
    readonly_root: Path,
) -> tuple[Path | None, List[Dict[str, Any]]]:
    """Try to resolve the first valid path from a list of candidates.

    Args:
        candidates: Iterable of path strings or Paths to try.
        read_roots: Allowed roots for read access.
        write_roots: Allowed roots for write access.
        mode: Either 'read' or 'write'.
        readonly_root: Root directory that is always write-protected.

    Returns:
        Tuple of (first_valid_path, list_of_rejection_records).
    """
    rejected: List[Dict[str, Any]] = []
    for candidate in candidates:
        resolved, reason = canonical_resolve(
            candidate,
            read_roots=read_roots,
            write_roots=write_roots,
            mode=mode,
            readonly_root=readonly_root,
        )
        if resolved is not None:
            return resolved, rejected
        rejected.append({"candidate": str(candidate), "reason": reason})
    return None, rejected


def discover_fastq_files_guarded(
    root: Path,
    *,
    include_subdirs: bool,
    name_filter: str,
    max_files: int,
) -> list[str]:
    """Discover FASTQ files under a root directory with loop protection.

    Recursively scans directories (guarding against symlink loops) for
    files matching FASTQ extensions and an optional name filter.

    Args:
        root: Directory or file to scan.
        include_subdirs: Whether to recurse into subdirectories.
        name_filter: Case-insensitive substring filter for filenames.
        max_files: Maximum number of files to return.

    Returns:
        Sorted list of resolved FASTQ file path strings.
    """
    if not root.exists():
        return []

    matched: list[str] = []
    seen_dirs: set[Tuple[int, int]] = set()
    name_filter_l = name_filter.lower().strip()

    def _accept_file(file_path: Path) -> bool:
        s = str(file_path).lower()
        if not s.endswith(FASTQ_SUFFIXES):
            return False
        if name_filter_l and name_filter_l not in file_path.name.lower():
            return False
        return True

    def _scan_dir(dir_path: Path) -> None:
        nonlocal matched
        try:
            st = dir_path.stat(follow_symlinks=True)
            inode_key = (st.st_dev, st.st_ino)
        except Exception:
            return

        if inode_key in seen_dirs:
            # Symlink loop guard: never revisit the same inode.
            return
        seen_dirs.add(inode_key)

        try:
            entries = list(dir_path.iterdir())
        except Exception:
            return

        for entry in entries:
            if len(matched) >= max_files:
                return
            try:
                if entry.is_file():
                    if _accept_file(entry):
                        matched.append(str(entry.resolve(strict=False)))
                    continue
                if entry.is_dir() and include_subdirs:
                    _scan_dir(entry)
            except Exception:
                continue

    if root.is_file():
        if _accept_file(root):
            return [str(root.resolve(strict=False))]
        return []

    _scan_dir(root)
    return sorted(matched)
