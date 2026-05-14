from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECEIPT_ROOT = PROJECT_ROOT / "workspace" / "bootstrap_reports"


def write_install_receipt(
    payload: dict[str, Any],
    *,
    prefix: str,
    output_path: str | Path | None = None,
    receipt_root: str | Path | None = None,
) -> Path:
    target = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else Path(receipt_root or DEFAULT_RECEIPT_ROOT).expanduser().resolve()
        / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{prefix}.json"
    )
    record = dict(payload)
    record.setdefault("receipt_prefix", prefix)
    record.setdefault("receipt_created_at", datetime.now(timezone.utc).isoformat())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
