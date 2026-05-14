#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.llm_backend_probe import probe_llm_backend


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check a local LLM backend for BioHarness.")
    parser.add_argument("--llm-backend", type=str, default=os.getenv("BIO_HARNESS_LLM_BACKEND", "ollama"))
    parser.add_argument("--model-name", type=str, default=os.getenv("BIO_HARNESS_MODEL", "qwen3-coder-next:latest"))
    parser.add_argument("--host", type=str, default="")
    parser.add_argument("--probe-text", action="store_true", help="Run a tiny text completion probe.")
    parser.add_argument("--probe-plan", action="store_true", help="Run a tiny planner JSON probe.")
    args = parser.parse_args()

    report = probe_llm_backend(
        llm_backend=args.llm_backend,
        model_name=args.model_name,
        host=args.host,
        probe_text=bool(args.probe_text),
        probe_plan=bool(args.probe_plan),
    )
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if bool(report.get("available", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
