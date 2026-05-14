#!/usr/bin/env python3
"""Check and explain local LLM-backend setup for Bio-Harness."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--llm-backend",
        type=str,
        default=os.getenv("BIO_HARNESS_LLM_BACKEND", os.getenv("BIO_HARNESS_LLM_PROVIDER", "ollama")),
        help="Backend to probe. Defaults to BIO_HARNESS_LLM_BACKEND or ollama.",
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default=os.getenv("BIO_HARNESS_MODEL", "qwen3-coder-next:latest"),
        help="Requested model name. Defaults to BIO_HARNESS_MODEL or qwen3-coder-next:latest.",
    )
    parser.add_argument("--host", type=str, default="", help="Optional backend host override.")
    parser.add_argument(
        "--pull-if-missing",
        action="store_true",
        help="For Ollama only: attempt `ollama pull <model>` when the requested model is missing.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the structured setup report as JSON.")
    return parser


def main() -> int:
    """Run the LLM-backend setup helper."""
    args = build_parser().parse_args()
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from bio_harness.core.llm_setup_support import (  # noqa: E402
        build_llm_setup_report,
        render_llm_setup_text,
    )

    report = build_llm_setup_report(
        llm_backend=args.llm_backend,
        model_name=args.model_name,
        host=args.host,
        pull_if_missing=bool(args.pull_if_missing),
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(render_llm_setup_text(report).rstrip())
    return 0 if bool(report.get("ready", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
