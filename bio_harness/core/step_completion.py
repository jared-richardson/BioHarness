"""Step completion manifests for executor integrity checks."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from bio_harness.core.tool_registry import default_tool_registry, render_expected_output_path

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = ".step_completion.json"


@dataclass
class CompletionCheck:
    """Result of checking one completion manifest."""

    completed: bool
    manifest_missing: bool
    exit_code: Optional[int] = None
    outputs: List[str] | None = None
    error: str = ""

    def __post_init__(self) -> None:
        if self.outputs is None:
            self.outputs = []


def _normalize_output_root(
    *,
    key: str,
    value: str,
    cwd: Optional[Path],
    expected_outputs: list[str],
) -> Path:
    path = Path(str(value or "").strip()).expanduser()
    if not path.is_absolute() and cwd is not None:
        path = (cwd / path).resolve(strict=False)
    else:
        path = path.resolve(strict=False)
    key_l = str(key or "").strip().lower()
    if key_l.endswith("prefix") or "_prefix" in key_l:
        return path.parent.resolve(strict=False)
    if expected_outputs and path.name in expected_outputs:
        return path.parent.resolve(strict=False)
    if path.suffix:
        return path.parent.resolve(strict=False)
    return path


def completion_roots_for_step(
    *,
    tool_name: str,
    step_arguments: Dict[str, Any],
    cwd: Optional[Path] = None,
) -> list[Path]:
    """Return candidate directories where manifests should live."""

    registry = default_tool_registry()
    expected_outputs_by_key = registry.expected_output_files_by_key_for(tool_name)
    fallback_expected_outputs = (
        registry.expected_output_files_for(tool_name)
        if not expected_outputs_by_key
        else []
    )
    roots: list[Path] = []
    for key in registry.output_argument_keys_for(tool_name):
        raw_value = step_arguments.get(key, "")
        values: Iterable[Any]
        if isinstance(raw_value, (list, tuple, set)):
            values = raw_value
        else:
            values = [raw_value]
        expected_outputs = expected_outputs_by_key.get(key, fallback_expected_outputs)
        for value in values:
            rendered = str(value or "").strip()
            if not rendered:
                continue
            roots.append(
                _normalize_output_root(
                    key=key,
                    value=rendered,
                    cwd=cwd,
                    expected_outputs=expected_outputs,
                )
            )
    fallback_keys = ("output_dir", "outdir", "output", "output_directory")
    for key in fallback_keys:
        rendered = str(step_arguments.get(key, "") or "").strip()
        if not rendered:
            continue
        roots.append(
            _normalize_output_root(
                key=key,
                value=rendered,
                cwd=cwd,
                expected_outputs=fallback_expected_outputs,
            )
        )
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def resolved_step_outputs_for_completion(
    *,
    tool_name: str,
    step_arguments: Dict[str, Any],
    cwd: Optional[Path] = None,
) -> list[str]:
    """Return expected concrete outputs for manifest matching.

    Args:
        tool_name: Name of the executing tool.
        step_arguments: Step argument payload.
        cwd: Optional execution working directory used to resolve relative
            output paths.

    Returns:
        A deduplicated list of absolute concrete output paths associated with
        the current step.
    """

    registry = default_tool_registry()
    expected_outputs_by_key = registry.expected_output_files_by_key_for(tool_name)
    fallback_expected_outputs = (
        registry.expected_output_files_for(tool_name)
        if not expected_outputs_by_key
        else []
    )
    resolved: list[str] = []
    for key in registry.output_argument_keys_for(tool_name):
        raw_value = step_arguments.get(key, "")
        values: Iterable[Any]
        if isinstance(raw_value, (list, tuple, set)):
            values = raw_value
        else:
            values = [raw_value]
        expected_outputs = expected_outputs_by_key.get(key, fallback_expected_outputs)
        for value in values:
            rendered = str(value or "").strip()
            if not rendered:
                continue
            path = Path(rendered).expanduser()
            if not path.is_absolute() and cwd is not None:
                path = (cwd / path).resolve(strict=False)
            else:
                path = path.resolve(strict=False)
            if expected_outputs:
                for relative_name in expected_outputs:
                    output_path = render_expected_output_path(
                        key=key,
                        output_root=str(path),
                        relative_name=relative_name,
                    )
                    if output_path:
                        resolved.append(str(Path(output_path).resolve(strict=False)))
            else:
                resolved.append(str(path))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in resolved:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def write_completion_manifest(
    output_dir: Path,
    *,
    tool_name: str,
    outputs: List[str],
    exit_code: int = 0,
    success: bool = True,
    error: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write one completion manifest sidecar."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / MANIFEST_FILENAME
    payload = {
        "tool_name": tool_name,
        "success": success,
        "exit_code": exit_code,
        "outputs": [str(item) for item in outputs],
        "error": error,
        "completed_at": datetime.now().isoformat(),
    }
    if metadata:
        payload["metadata"] = metadata
    manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def write_step_completion_manifest(
    *,
    tool_name: str,
    step_arguments: Dict[str, Any],
    cwd: Optional[Path],
    outputs: List[str],
    exit_code: int,
    success: bool,
    error: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> list[Path]:
    """Write completion manifests for every resolved output root."""

    written: list[Path] = []
    for root in completion_roots_for_step(
        tool_name=tool_name,
        step_arguments=step_arguments,
        cwd=cwd,
    ):
        written.append(
            write_completion_manifest(
                root,
                tool_name=tool_name,
                outputs=outputs,
                exit_code=exit_code,
                success=success,
                error=error,
                metadata=metadata,
            )
        )
    return written


def _normalized_output_set(
    outputs: Iterable[Any],
    *,
    base_dir: Path | None = None,
) -> set[str]:
    normalized: set[str] = set()
    for item in outputs:
        rendered = str(item or "").strip()
        if not rendered:
            continue
        path = Path(rendered).expanduser()
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
        normalized.add(str(path.resolve(strict=False)))
    return normalized


def check_completion_manifest(
    output_dir: Path | str,
    tool_name: str,
    *,
    expected_outputs: Iterable[str] | None = None,
) -> CompletionCheck:
    """Check for a step completion manifest in an output directory.

    Args:
        output_dir: Directory that may contain a completion manifest.
        tool_name: Tool expected to have written the manifest.
        expected_outputs: Optional concrete outputs for the current step. When
            provided, a manifest for disjoint sibling outputs is treated as
            missing so shared output directories do not poison later steps.

    Returns:
        Completion status for the manifest scoped to the requested outputs.
    """

    output_dir = Path(str(output_dir))
    manifest_path = output_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return CompletionCheck(completed=False, manifest_missing=True)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(
            "Failed to parse completion manifest %s: %s",
            manifest_path,
            exc,
        )
        return CompletionCheck(
            completed=False,
            manifest_missing=False,
            error=str(exc),
        )
    if not isinstance(payload, dict):
        return CompletionCheck(
            completed=False,
            manifest_missing=False,
            error="Manifest is not a JSON object.",
        )
    manifest_tool = str(payload.get("tool_name", "") or "").strip()
    success = bool(payload.get("success", False))
    manifest_outputs = list(payload.get("outputs", []))
    expected_output_set = _normalized_output_set(
        expected_outputs or [],
        base_dir=output_dir,
    )
    manifest_output_set = _normalized_output_set(
        manifest_outputs,
        base_dir=output_dir,
    )
    if (
        expected_output_set
        and manifest_output_set
        and expected_output_set.isdisjoint(manifest_output_set)
    ):
        return CompletionCheck(completed=False, manifest_missing=True)
    if manifest_tool and manifest_tool != tool_name:
        return CompletionCheck(
            completed=False,
            manifest_missing=False,
            error=f"Tool mismatch: expected {tool_name}, manifest says {manifest_tool}.",
        )
    return CompletionCheck(
        completed=success,
        manifest_missing=False,
        exit_code=payload.get("exit_code"),
        outputs=manifest_outputs,
        error=str(payload.get("error", "") or "").strip(),
    )


def find_completion_manifest(
    step_arguments: Dict[str, Any],
    *,
    tool_name: str = "",
    cwd: Optional[Path] = None,
) -> Path | None:
    """Locate an existing completion manifest for one step."""

    expected_outputs = resolved_step_outputs_for_completion(
        tool_name=tool_name,
        step_arguments=step_arguments,
        cwd=cwd,
    )
    roots = completion_roots_for_step(
        tool_name=tool_name,
        step_arguments=step_arguments,
        cwd=cwd,
    )
    for root in roots:
        manifest = root / MANIFEST_FILENAME
        if manifest.exists():
            manifest_check = check_completion_manifest(
                root,
                tool_name,
                expected_outputs=expected_outputs,
            )
            if manifest_check.manifest_missing:
                continue
            return manifest
    return None
