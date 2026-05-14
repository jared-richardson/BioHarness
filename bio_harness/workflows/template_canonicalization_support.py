"""Small support helpers for plan canonicalization."""

from __future__ import annotations

from typing import Any


def empty_canonicalization_result(original: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the standard invalid-plan canonicalization response."""

    return {"thought_process": original.get("thought_process", ""), "plan": []}, {
        "changed": False,
        "reason": "invalid_plan_format",
        "diff_summary": {"before_step_count": 0, "after_step_count": 0, "changed_command_steps": 0},
    }


def summarize_plan_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return a compact before/after summary for one canonicalization pass."""

    before_steps = before.get("plan", []) if isinstance(before, dict) else []
    after_steps = after.get("plan", []) if isinstance(after, dict) else []
    changed_commands = 0
    for idx in range(min(len(before_steps), len(after_steps))):
        before_step = before_steps[idx] if isinstance(before_steps[idx], dict) else {}
        after_step = after_steps[idx] if isinstance(after_steps[idx], dict) else {}
        before_cmd = str((before_step.get("arguments") or {}).get("command", ""))
        after_cmd = str((after_step.get("arguments") or {}).get("command", ""))
        if before_cmd != after_cmd:
            changed_commands += 1
    return {
        "before_step_count": len(before_steps),
        "after_step_count": len(after_steps),
        "changed_command_steps": changed_commands,
    }
