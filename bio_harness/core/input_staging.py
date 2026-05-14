"""Helpers for staging local user inputs into the workspace."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Iterable


def _safe_destination(dest_root: Path, relative_name: str) -> Path:
    """Resolve a destination under the declared staging root."""

    target = (dest_root / relative_name).resolve(strict=False)
    root = dest_root.resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise ValueError(f"Destination '{target}' escapes staging root '{root}'.") from exc
    return target


def stage_inputs(
    sources: Iterable[str | Path],
    *,
    dest_root: str | Path,
    link_mode: str = "symlink",
) -> list[dict[str, Any]]:
    """Stage local files or directories into the workspace input root.

    Args:
        sources: Files or directories to stage.
        dest_root: Workspace ``inputs_readonly`` root.
        link_mode: ``symlink`` or ``copy``.

    Returns:
        A receipt row per staged source.
    """

    mode = str(link_mode or "symlink").strip().lower()
    if mode not in {"symlink", "copy"}:
        raise ValueError("link_mode must be one of: symlink, copy")

    root = Path(dest_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    receipts: list[dict[str, Any]] = []

    for source in sources:
        src = Path(source).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"Input staging source does not exist: {src}")
        dest = _safe_destination(root, src.name)
        if dest.exists() or dest.is_symlink():
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()

        if mode == "symlink":
            dest.symlink_to(src, target_is_directory=src.is_dir())
        elif src.is_dir():
            shutil.copytree(src, dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

        receipts.append(
            {
                "source": str(src),
                "destination": str(dest),
                "link_mode": mode,
                "kind": "directory" if src.is_dir() else "file",
            }
        )
    return receipts


def write_stage_receipt(receipt: list[dict[str, Any]], output_path: str | Path) -> Path:
    """Write an input-staging receipt JSON file."""

    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"staged": receipt}, indent=2), encoding="utf-8")
    return out
