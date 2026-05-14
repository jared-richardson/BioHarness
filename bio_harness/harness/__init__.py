"""Harness sub-package.

Re-exports ``HarnessConfig`` and ``PROJECT_ROOT`` for convenience.
The individual modules (``config``, ``stream_utils``, ``plan_helpers``,
``path_utils``, ``sample_groups``, ``contract_utils``, ``plan_repair``,
``deliverables``) contain all extracted utility functions.
"""
from __future__ import annotations

from bio_harness.harness.config import HarnessConfig, PROJECT_ROOT

__all__ = [
    "HarnessConfig",
    "PROJECT_ROOT",
]
