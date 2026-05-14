#!/usr/bin/env python3
"""Run the starter Qwen-through-harness smoke matrix for non-benchmark skills."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.analysis.qwen_skill_smoke import run_qwen_skill_smoke_matrix  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Repository root containing the workspace and scripts directory.",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        default=None,
        help="Optional smoke case name to run. Repeat to run multiple named cases.",
    )
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help="Optional label for the smoke run output directory under workspace/skill_smoke.",
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default="qwen3-coder-next:latest",
        help="Model name passed through to the harness.",
    )
    parser.add_argument(
        "--llm-backend",
        type=str,
        default="ollama",
        help="LLM backend passed through to the harness.",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="",
        help="Optional backend base URL passed through to the harness.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=1200,
        help="Timeout for each individual smoke case.",
    )
    parser.add_argument(
        "--tranche",
        type=str,
        choices=("starter", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth", "twentieth", "twentyfirst", "twentysecond", "twentythird", "twentyfourth", "all_supported"),
        default="starter",
        help="Which smoke case tranche to run.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to write the smoke summary JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    payload = run_qwen_skill_smoke_matrix(
        args.project_root,
        case_names=list(args.cases or []),
        label=args.label,
        model_name=str(args.model_name),
        llm_backend=str(args.llm_backend),
        host=str(args.host),
        timeout_seconds=max(1, int(args.timeout_seconds)),
        tranche=str(args.tranche),
    )
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered, encoding="utf-8")
        print(args.output_json.resolve())
    else:
        print(rendered, end="")
    return 0 if payload.get("all_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
