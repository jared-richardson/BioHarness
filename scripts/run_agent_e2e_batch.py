#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS_SCRIPT = PROJECT_ROOT / "scripts" / "run_agent_e2e.py"
DEFAULT_OUT_DIR = PROJECT_ROOT / "workspace" / "runs" / "_batch_reports"


def _load_prompts(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() in {".json", ".jsonl"}:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return []
        if path.suffix.lower() == ".json":
            payload = json.loads(text)
            if isinstance(payload, list):
                rows: list[dict[str, Any]] = []
                for idx, item in enumerate(payload, start=1):
                    if isinstance(item, str):
                        rows.append({"name": f"prompt_{idx:02d}", "prompt": item})
                    elif isinstance(item, dict):
                        rows.append(
                            {
                                "name": str(item.get("name", f"prompt_{idx:02d}")),
                                "prompt": str(item.get("prompt", "")),
                            }
                        )
                return [r for r in rows if r.get("prompt", "").strip()]
            raise ValueError("JSON prompt file must contain a list.")

        rows = []
        for idx, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, str):
                rows.append({"name": f"prompt_{idx:02d}", "prompt": obj})
            elif isinstance(obj, dict):
                rows.append(
                    {
                        "name": str(obj.get("name", f"prompt_{idx:02d}")),
                        "prompt": str(obj.get("prompt", "")),
                    }
                )
        return [r for r in rows if r.get("prompt", "").strip()]

    prompts: list[dict[str, Any]] = []
    for idx, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        prompts.append({"name": f"prompt_{idx:02d}", "prompt": line})
    return prompts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch runner for scripts/run_agent_e2e.py (multiple prompts, summarized results)."
    )
    parser.add_argument(
        "--prompts-file",
        type=str,
        required=True,
        help="Path to prompts file (.txt one prompt/line, .json list, or .jsonl entries).",
    )
    parser.add_argument("--selected-dir", type=str, default=str(PROJECT_ROOT / "workspace"))
    parser.add_argument("--data-root", type=str, default=str(PROJECT_ROOT / "workspace" / "inputs_readonly"))
    parser.add_argument("--plan-file", type=str, default="", help="Optional static JSON plan to execute for every prompt.")
    parser.add_argument("--max-repairs", type=int, default=3)
    parser.add_argument("--heartbeat-seconds", type=int, default=15)
    parser.add_argument("--stall-timeout-seconds", type=int, default=45)
    parser.add_argument("--live-process-grace-seconds", type=int, default=900)
    parser.add_argument("--model-name", type=str, default="")
    parser.add_argument("--llm-backend", type=str, default="")
    parser.add_argument("--host", type=str, default="")
    parser.add_argument("--auto-install-missing-tools", dest="auto_install_missing_tools", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--auto-setup-isolated-tools", dest="auto_setup_isolated_tools", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--no-replan", action="store_true")
    parser.add_argument("--no-canonicalize", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--out-dir", type=str, default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--path-graph-db", type=str, default="")
    parser.add_argument("--path-graph-user-key", type=str, default="default")
    parser.add_argument("--path-graph-scope", type=str, default="global")
    parser.add_argument("--path-graph-persist-preference-updates", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    prompts_path = Path(args.prompts_file).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    prompts = _load_prompts(prompts_path)
    if not prompts:
        raise SystemExit("No prompts found in prompts file.")

    summary: list[dict[str, Any]] = []
    failures = 0
    for idx, item in enumerate(prompts, start=1):
        name = str(item.get("name", f"prompt_{idx:02d}")).strip() or f"prompt_{idx:02d}"
        prompt = str(item.get("prompt", "")).strip()
        if not prompt:
            continue

        result_path = out_dir / f"{idx:02d}_{name}_result.json"
        cmd = [
            sys.executable,
            str(HARNESS_SCRIPT),
            "--prompt",
            prompt,
            "--selected-dir",
            str(Path(args.selected_dir).expanduser().resolve()),
            "--data-root",
            str(Path(args.data_root).expanduser().resolve()),
            "--max-repairs",
            str(int(args.max_repairs)),
            "--heartbeat-seconds",
            str(int(args.heartbeat_seconds)),
            "--stall-timeout-seconds",
            str(int(args.stall_timeout_seconds)),
            "--live-process-grace-seconds",
            str(int(args.live_process_grace_seconds)),
            "--result-json",
            str(result_path),
        ]
        if args.model_name.strip():
            cmd.extend(["--model-name", args.model_name.strip()])
        if args.llm_backend.strip():
            cmd.extend(["--llm-backend", args.llm_backend.strip()])
        if args.host.strip():
            cmd.extend(["--host", args.host.strip()])
        if args.plan_file.strip():
            cmd.extend(["--plan-file", str(Path(args.plan_file).expanduser().resolve())])
        if args.auto_install_missing_tools is True:
            cmd.append("--auto-install-missing-tools")
        elif args.auto_install_missing_tools is False:
            cmd.append("--no-auto-install-missing-tools")
        if args.auto_setup_isolated_tools is True:
            cmd.append("--auto-setup-isolated-tools")
        elif args.auto_setup_isolated_tools is False:
            cmd.append("--no-auto-setup-isolated-tools")
        if args.no_replan:
            cmd.append("--no-replan")
        if args.no_canonicalize:
            cmd.append("--no-canonicalize")
        if args.quiet:
            cmd.append("--quiet")
        if args.path_graph_db.strip():
            cmd.extend(["--path-graph-db", str(Path(args.path_graph_db).expanduser().resolve())])
        if args.path_graph_user_key.strip():
            cmd.extend(["--path-graph-user-key", args.path_graph_user_key.strip()])
        if args.path_graph_scope.strip():
            cmd.extend(["--path-graph-scope", args.path_graph_scope.strip()])
        if args.path_graph_persist_preference_updates:
            cmd.append("--path-graph-persist-preference-updates")

        with tempfile.NamedTemporaryFile(prefix=f"e2e_{idx:02d}_{name}_", suffix=".log", delete=False) as temp_log:
            log_path = Path(temp_log.name)

        print(f"[batch] ({idx}/{len(prompts)}) {name}")
        print(f"[batch] prompt: {prompt}")
        with log_path.open("w", encoding="utf-8") as log_fh:
            proc = subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )

        if result_path.exists():
            result_obj = json.loads(result_path.read_text(encoding="utf-8"))
        else:
            result_obj = {
                "status": "failed",
                "error": f"Harness did not write result file (exit={proc.returncode})",
                "run_dir": "",
            }

        status = str(result_obj.get("status", "failed"))
        row = {
            "name": name,
            "prompt": prompt,
            "status": status,
            "error": str(result_obj.get("error", "")),
            "benchmark_policy": str(result_obj.get("benchmark_policy", "")),
            "run_dir": str(result_obj.get("run_dir", "")),
            "result_json": str(result_path),
            "assistance_manifest_file": str(result_obj.get("assistance_manifest_file", "")),
            "generic_template_fallback_used": bool(result_obj.get("generic_template_fallback_used", False)),
            "generic_template_fallback_pipeline_id": str(result_obj.get("generic_template_fallback_pipeline_id", "")),
            "protocol_template_fallback_used": bool(result_obj.get("protocol_template_fallback_used", False)),
            "forbidden_benchmark_sources_visible": bool(result_obj.get("forbidden_benchmark_sources_visible", False)),
            "forbidden_benchmark_sources": list(result_obj.get("forbidden_benchmark_sources", []) or []),
            "log_file": str(log_path),
            "harness_exit_code": int(proc.returncode),
        }
        summary.append(row)
        print(f"[batch] status={status} run_dir={row['run_dir']}")
        if row["error"]:
            print(f"[batch] error={row['error']}")

        if status != "completed":
            failures += 1
            if args.stop_on_failure:
                print("[batch] stopping on first failure.")
                break

    summary_path = out_dir / "batch_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "created_at": __import__("datetime").datetime.now().isoformat(),
                "prompt_file": str(prompts_path),
                "count": len(summary),
                "failures": failures,
                "items": summary,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"[batch] summary={summary_path}")
    print(f"[batch] completed={len(summary) - failures} failed={failures}")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
