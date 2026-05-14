#!/usr/bin/env python3
"""Run or preview the Bio-Harness first-run setup flow."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan setup actions without installing Python/Pixi dependencies or pulling models.",
    )
    parser.add_argument(
        "--skip-bootstrap",
        action="store_true",
        help="Skip the Python/Pixi bootstrap step and only report current setup state.",
    )
    parser.add_argument(
        "--all-installable-tools",
        action="store_true",
        help="Install all known optional Pixi environments during bootstrap.",
    )
    parser.add_argument(
        "--pull-if-missing",
        action="store_true",
        help="Pull the selected Ollama model if the backend is reachable and the model is missing.",
    )
    parser.add_argument(
        "--llm-backend",
        default="ollama",
        help="Model backend to configure. Defaults to ollama.",
    )
    parser.add_argument(
        "--model-name",
        default="qwen3-coder-next:latest",
        help="Requested local model. Defaults to the recommended public model.",
    )
    parser.add_argument("--host", default="", help="Optional model backend host override.")
    parser.add_argument(
        "--selected-dir",
        type=Path,
        default=Path("workspace"),
        help="Workspace directory used by doctor/resource checks.",
    )
    parser.add_argument("--json", action="store_true", help="Print only the JSON setup report.")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional receipt path.")
    return parser.parse_args()


def _resource_snapshot(path: Path) -> dict[str, float | None]:
    """Return local disk and RAM data for setup recommendations."""
    target = path.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(target)
    ram_gb: float | None = None
    try:
        import psutil

        ram_gb = float(psutil.virtual_memory().available / (1024**3))
    except (ImportError, ModuleNotFoundError):
        ram_gb = None
    return {
        "free_disk_gb": round(float(disk.free / (1024**3)), 2),
        "available_ram_gb": None if ram_gb is None else round(ram_gb, 2),
    }


def main() -> int:
    """Run first-run setup planning and optional bootstrap/model pull."""
    from bio_harness.core.environment_bootstrap import bootstrap_bioharness_environment
    from bio_harness.core.first_run_setup import build_first_run_setup_status
    from bio_harness.core.harness_doctor import assess_harness_doctor
    from bio_harness.core.install_receipts import write_install_receipt
    from bio_harness.core.llm_setup_support import build_llm_setup_report

    args = _parse_args()
    selected_dir = (PROJECT_ROOT / args.selected_dir).resolve()
    selected_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_report: dict[str, Any] | None = None
    if not args.skip_bootstrap:
        bootstrap_report = bootstrap_bioharness_environment(
            project_root=PROJECT_ROOT,
            install_all_known_pixi_envs=bool(args.all_installable_tools),
            dry_run=bool(args.dry_run),
            probe_llm_backend_status=False,
        )

    doctor_report = assess_harness_doctor(
        selected_dir=selected_dir,
        probe_llm_backend_status=False,
    )
    llm_report = build_llm_setup_report(
        llm_backend=args.llm_backend,
        model_name=args.model_name,
        host=args.host,
        pull_if_missing=bool(args.pull_if_missing and not args.dry_run),
    )
    resources = _resource_snapshot(selected_dir)
    status = build_first_run_setup_status(
        bootstrap_report=bootstrap_report,
        doctor_report=doctor_report,
        llm_setup_report=llm_report,
        free_disk_gb=resources["free_disk_gb"],
        available_ram_gb=resources["available_ram_gb"],
        requested_model=args.model_name,
    )
    payload = {
        "schema_version": 1,
        "dry_run": bool(args.dry_run),
        "bootstrap_report": bootstrap_report,
        "doctor_report": doctor_report,
        "llm_setup_report": llm_report,
        "resources": resources,
        "first_run_status": status,
    }
    receipt_path = write_install_receipt(
        payload,
        prefix="first_run_setup",
        output_path=args.output_json,
        receipt_root=PROJECT_ROOT / "workspace" / "setup_reports",
    )
    payload["receipt_path"] = str(receipt_path)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    receipt_path.write_text(rendered, encoding="utf-8")
    if args.json:
        print(rendered, end="")
    else:
        print(f"First-run setup receipt: {receipt_path}")
        print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
