#!/usr/bin/env python3
"""Run a deterministic Bio-Harness self-check and bootstrap readiness report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skill",
        action="append",
        default=[],
        help="Skill name to include in readiness checks. Repeatable.",
    )
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        help="Tool name to include in readiness checks. Repeatable.",
    )
    parser.add_argument(
        "--selected-dir",
        type=Path,
        default=Path.cwd(),
        help="Directory whose filesystem should be checked.",
    )
    parser.add_argument(
        "--reference-root",
        type=Path,
        default=None,
        help="Optional reference bundle root to audit.",
    )
    parser.add_argument(
        "--min-free-disk-gb",
        type=float,
        default=20.0,
        help="Minimum desired free disk capacity in GiB.",
    )
    parser.add_argument(
        "--probe-llm-backend",
        action="store_true",
        help="Probe the configured local LLM backend and include readiness details.",
    )
    parser.add_argument(
        "--llm-backend",
        type=str,
        default=os.getenv("BIO_HARNESS_LLM_BACKEND", "ollama"),
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default=os.getenv("BIO_HARNESS_MODEL", "qwen3-coder-next:latest"),
    )
    parser.add_argument("--host", type=str, default="")
    parser.add_argument(
        "--llm-probe-text",
        action="store_true",
        help="When probing the LLM backend, run a tiny text generation check.",
    )
    parser.add_argument(
        "--llm-probe-plan",
        action="store_true",
        help="When probing the LLM backend, run a tiny structured planner check.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional output JSON path.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the Bio-Harness doctor and write an optional JSON report."""
    from bio_harness.core.harness_doctor import assess_harness_doctor

    args = _parse_args()
    payload = assess_harness_doctor(
        skill_names=list(args.skill or []),
        tool_names=list(args.tool or []),
        selected_dir=args.selected_dir,
        reference_root=args.reference_root,
        min_free_disk_gb=float(args.min_free_disk_gb),
        probe_llm_backend_status=bool(args.probe_llm_backend),
        llm_backend=args.llm_backend,
        model_name=args.model_name,
        host=args.host,
        llm_probe_text=bool(args.llm_probe_text),
        llm_probe_plan=bool(args.llm_probe_plan),
    )
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered, encoding="utf-8")
        print(args.output_json.resolve())
    else:
        print(rendered, end="")
    return 0 if payload.get("ready", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
