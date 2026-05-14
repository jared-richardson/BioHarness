#!/usr/bin/env python3
"""Bootstrap a local Bio-Harness development/runtime environment."""

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
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Repository root containing pixi.toml and the requirements directory.",
    )
    parser.add_argument(
        "--python-bin",
        type=str,
        default=sys.executable,
        help="Python interpreter used to create the virtual environment.",
    )
    parser.add_argument(
        "--venv-path",
        type=Path,
        default=Path(".venv"),
        help="Virtual environment path relative to the project root unless absolute.",
    )
    parser.add_argument(
        "--tool",
        action="append",
        dest="tools",
        default=None,
        help="Specific tool requirement to provision. Repeat to request multiple tools.",
    )
    parser.add_argument(
        "--skill",
        action="append",
        dest="skills",
        default=None,
        help="Specific Bio-Harness skill name whose tool requirements should be provisioned.",
    )
    parser.add_argument(
        "--all-installable-tools",
        action="store_true",
        help="Install every pixi-managed optional environment declared by the repo.",
    )
    parser.add_argument(
        "--skip-python",
        action="store_true",
        help="Skip virtual-environment creation and editable package installation.",
    )
    parser.add_argument(
        "--skip-pixi",
        action="store_true",
        help="Skip pixi environment installation.",
    )
    parser.add_argument(
        "--skip-isolated",
        action="store_true",
        help="Skip isolated-tool recipe setup for tools such as CNVkit, Prokka, and STAR-Fusion.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the bootstrap plan without executing install commands.",
    )
    parser.add_argument(
        "--probe-llm-backend",
        action="store_true",
        help=(
            "Probe the configured local LLM backend after bootstrap planning/install "
            "and include the result in the report."
        ),
    )
    parser.add_argument(
        "--llm-backend",
        type=str,
        default=os.getenv("BIO_HARNESS_LLM_BACKEND", "ollama"),
        help="LLM backend to probe when --probe-llm-backend is set.",
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default=os.getenv("BIO_HARNESS_MODEL", "qwen3-coder-next:latest"),
        help="Model name to probe when --probe-llm-backend is set.",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="",
        help="Optional LLM backend host override when --probe-llm-backend is set.",
    )
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
        help="Optional JSON path for the bootstrap report.",
    )
    return parser.parse_args()


def main() -> int:
    """Run bootstrap planning, optional setup, and receipt writing."""
    from bio_harness.core.environment_bootstrap import bootstrap_bioharness_environment
    from bio_harness.core.install_receipts import write_install_receipt

    args = _parse_args()
    payload = bootstrap_bioharness_environment(
        project_root=args.project_root,
        python_bin=str(args.python_bin),
        venv_path=args.venv_path,
        tool_names=list(args.tools or []),
        skill_names=list(args.skills or []),
        install_python=not bool(args.skip_python),
        install_pixi=not bool(args.skip_pixi),
        install_isolated=not bool(args.skip_isolated),
        install_all_known_pixi_envs=bool(args.all_installable_tools),
        dry_run=bool(args.dry_run),
        probe_llm_backend_status=bool(args.probe_llm_backend),
        llm_backend=args.llm_backend,
        model_name=args.model_name,
        host=args.host,
        llm_probe_text=bool(args.llm_probe_text),
        llm_probe_plan=bool(args.llm_probe_plan),
    )
    receipt_path = write_install_receipt(
        payload,
        prefix="bootstrap",
        output_path=args.output_json,
    )
    payload["receipt_path"] = str(receipt_path)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    receipt_path.write_text(rendered, encoding="utf-8")
    if args.output_json is not None:
        print(receipt_path.resolve())
    else:
        print(rendered, end="")
    return 0 if payload.get("success", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
