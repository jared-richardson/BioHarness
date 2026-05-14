from __future__ import annotations

from pathlib import Path
from typing import Any


def normalize_plan_json(plan_json: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(plan_json or {})
    normalized.setdefault("thought_process", "No thought process provided by model.")
    normalized.setdefault("plan", [])
    normalized.setdefault("final_deliverables", [])
    return normalized


def extract_step_contracts(plan_json: dict[str, Any]) -> dict[int, dict[str, Any]]:
    contracts: dict[int, dict[str, Any]] = {}
    for raw_step in (plan_json or {}).get("plan", []):
        if not isinstance(raw_step, dict):
            continue
        try:
            step_id = int(raw_step.get("step_id"))
        except Exception:
            continue
        expected = raw_step.get("expected_files") or raw_step.get("deliverables") or []
        validation = raw_step.get("validation_method", "exists_non_empty")
        success_criteria = raw_step.get("success_criteria", "")
        if not expected:
            continue
        contracts[step_id] = {
            "expected_files": expected,
            "validation_method": validation,
            "success_criteria": success_criteria,
        }
    return contracts


def validate_deliverables(contract: dict[str, Any], cwd: str | None) -> dict[str, Any]:
    expected_files = contract.get("expected_files", [])
    validation_method = str(contract.get("validation_method", "exists_non_empty")).strip().lower()
    base = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    if not expected_files:
        return {"passed": True, "reason": "no_expected_files"}

    for item in expected_files:
        path = Path(str(item)).expanduser()
        if not path.is_absolute():
            path = (base / path).resolve()
        else:
            path = path.resolve()
        if not path.exists():
            return {"passed": False, "reason": f"missing:{path}", "validation_method": validation_method}
        if validation_method in {"exists_non_empty", "non_empty"} and path.is_file() and path.stat().st_size <= 0:
            return {"passed": False, "reason": f"empty:{path}", "validation_method": validation_method}
    return {"passed": True, "reason": "ok", "validation_method": validation_method}
