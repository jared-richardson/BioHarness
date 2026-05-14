"""Helpers for resolving UI run data roots deterministically."""

from __future__ import annotations

from typing import Any, Mapping


def resolve_effective_run_data_root(
    *,
    session_data_root: str,
    run: Mapping[str, Any],
    fallback_selected_dir: str,
) -> str:
    """Return the best available data root for a UI run.

    Args:
        session_data_root: Current chat data root stored in session state.
        run: In-memory run mapping that may already persist a requested data
            root or selected directory.
        fallback_selected_dir: Session-selected directory used as the final
            fallback when no explicit data root is available.

    Returns:
        The current session data root when present, otherwise the run's
        persisted requested data root, then the run's selected directory, and
        finally the session fallback selected directory.
    """
    session_root = str(session_data_root or "").strip()
    if session_root:
        return session_root
    run_root = str(run.get("requested_data_root", "") or "").strip()
    if run_root:
        return run_root
    selected_dir = str(run.get("selected_dir", "") or "").strip()
    if selected_dir:
        return selected_dir
    return str(fallback_selected_dir or "").strip()
