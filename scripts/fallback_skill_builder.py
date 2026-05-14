#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.fallback_skill_builder import (  # noqa: E402
    FallbackBuilderRequest,
    run_fallback_skill_builder,
)


def _parse_csv(raw: str) -> list[str]:
    return [tok.strip() for tok in str(raw or "").split(",") if tok.strip()]


def _load_prompts(path: Path) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    if not path.exists():
        return payload
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return payload
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        rows = data if isinstance(data, list) else []
    elif path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        rows = [{"name": f"prompt_{idx:02d}", "prompt": line.strip()} for idx, line in enumerate(text.splitlines(), start=1) if line.strip()]

    for idx, row in enumerate(rows, start=1):
        if isinstance(row, str):
            payload.append({"name": f"prompt_{idx:02d}", "prompt": row.strip()})
            continue
        if not isinstance(row, dict):
            continue
        prompt = str(row.get("prompt", "")).strip()
        if not prompt:
            continue
        name = str(row.get("name", f"prompt_{idx:02d}")).strip() or f"prompt_{idx:02d}"
        payload.append({"name": name, "prompt": prompt})
    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build, validate, and troubleshoot deterministic fallback skill/template coverage.")
    parser.add_argument("--target-capabilities", type=str, default="", help="Comma-separated capability IDs required by the target contract.")
    parser.add_argument("--allowed-tools", type=str, default="", help="Comma-separated allowed tools/tool wrappers.")
    parser.add_argument(
        "--data-constraints-json",
        type=str,
        default="{}",
        help="Inline JSON object describing data/reference constraints (for example required_paths, annotation_gtf, reference_fasta).",
    )
    parser.add_argument(
        "--strictness-mode",
        type=str,
        default="conservative",
        help="Requested strictness mode. Unknown values are normalized conservatively by the core request parser.",
    )
    parser.add_argument("--request-text", type=str, default="", help="Free-text request used for capability/tool inference.")
    parser.add_argument("--selected-dir", type=str, default=str(PROJECT_ROOT / "workspace"))
    parser.add_argument("--data-root", type=str, default=str(PROJECT_ROOT / "workspace" / "inputs_readonly"))
    parser.add_argument("--run-id", action="append", default=[], help="Run ID (or run directory path) to analyze/troubleshoot. Repeat for multiple.")
    parser.add_argument("--batch-prompts-file", type=str, default="", help="Optional prompts file (.txt/.json/.jsonl) for representative e2e batch execution.")
    parser.add_argument("--run-e2e", action="store_true", help="Execute representative prompts with scripts/run_agent_e2e_batch.py.")
    parser.add_argument("--rerun-failures", action="store_true", help="Rerun failed prompts with same prompt and record regression status.")
    parser.add_argument("--apply-missing-pieces", action="store_true", help="Write generated draft artifacts/stubs for missing fallback pieces.")
    parser.add_argument("--path-graph-db", type=str, default="", help="Optional SQLite path graph DB path.")
    parser.add_argument("--path-graph-user-key", type=str, default="fallback_builder", help="Path-graph preference user key.")
    parser.add_argument("--path-graph-scope", type=str, default="global", help="Path-graph preference scope.")
    parser.add_argument(
        "--path-graph-persist-preference-updates",
        action="store_true",
        help="Persist preference updates on successful fallback builder runs.",
    )
    parser.add_argument("--out-json", type=str, default=str(PROJECT_ROOT / "workspace" / "outputs" / "fallback" / "fallback_skill_builder_report.json"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        constraints = json.loads(str(args.data_constraints_json or "{}"))
        if not isinstance(constraints, dict):
            raise ValueError("--data-constraints-json must decode to an object")
    except Exception as exc:
        raise SystemExit(f"Failed to parse --data-constraints-json: {exc}") from exc

    if str(args.path_graph_db).strip():
        constraints["path_graph_db"] = str(Path(args.path_graph_db).expanduser().resolve())
    constraints["path_graph_user_key"] = str(args.path_graph_user_key or "fallback_builder").strip() or "fallback_builder"
    constraints["path_graph_scope"] = str(args.path_graph_scope or "global").strip() or "global"
    if bool(args.path_graph_persist_preference_updates):
        constraints["path_graph_persist_preference_updates"] = True

    prompts: list[dict[str, str]] = []
    if str(args.batch_prompts_file).strip():
        prompts = _load_prompts(Path(str(args.batch_prompts_file)).expanduser().resolve())

    request = FallbackBuilderRequest.from_raw(
        target_capability_set=_parse_csv(args.target_capabilities),
        allowed_tools=_parse_csv(args.allowed_tools),
        data_reference_constraints=constraints,
        strictness_mode=str(args.strictness_mode),
        request_text=str(args.request_text or ""),
        selected_dir=str(args.selected_dir or ""),
        data_root=str(args.data_root or ""),
        run_ids=[str(x) for x in (args.run_id or []) if str(x).strip()],
        batch_prompts=prompts,
        apply_missing_pieces=bool(args.apply_missing_pieces),
        run_e2e=bool(args.run_e2e),
        rerun_failures=bool(args.rerun_failures),
    )

    report = run_fallback_skill_builder(project_root=PROJECT_ROOT, request=request)

    out_path = Path(str(args.out_json)).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
