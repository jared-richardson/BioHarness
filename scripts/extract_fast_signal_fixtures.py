#!/usr/bin/env python3
"""Extract fast-signal replay fixtures from stored BioHarness run traces."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.fast_signal import (  # noqa: E402
    build_planner_shape_fixture,
    load_replay_fixtures,
    write_replay_fixture,
)

DEFAULT_RUNS_ROOT = PROJECT_ROOT / "workspace" / "runs"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "tests" / "fixtures" / "fast_signal" / "planner_shape"


def build_parser() -> argparse.ArgumentParser:
    """Build the fixture extraction CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--run-glob",
        action="append",
        default=[],
        help="Run directory glob relative to --runs-root. Repeatable.",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--dedupe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip fixtures whose behavior signature already exists in output.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    """Extract planner-shape fixtures from raw-response traces."""

    args = build_parser().parse_args()
    run_dirs = _select_run_dirs(args.runs_root, args.run_glob)
    fixtures_written = 0
    duplicate_count = 0
    existing_signatures = _existing_signature_hashes(args.output_dir) if args.dedupe else set()
    planned: list[dict[str, Any]] = []
    for run_dir in run_dirs:
        trace_paths = sorted((run_dir / "planner").glob("*_raw_response.json"))
        for trace_path in trace_paths:
            if args.limit and fixtures_written >= args.limit:
                break
            trace_payload = _load_json(trace_path)
            if not trace_payload:
                continue
            fixture_id = _fixture_id(run_dir.name, trace_path.stem)
            fixture = build_planner_shape_fixture(
                fixture_id=fixture_id,
                source_run=run_dir.name,
                trace_payload=trace_payload,
                run_dir=run_dir,
                analysis_type=_analysis_type_from_run(run_dir),
                tags=_tags_for_run(run_dir),
            )
            if args.dedupe and fixture.fixture_signature_hash in existing_signatures:
                duplicate_count += 1
                planned.append(
                    {
                        "fixture_id": fixture.id,
                        "duplicate": True,
                        "signature": fixture.fixture_signature_hash,
                    }
                )
                continue
            out_path = args.output_dir / f"{fixture.id}.json"
            planned.append(
                {
                    "fixture_id": fixture.id,
                    "path": str(out_path),
                    "signature": fixture.fixture_signature_hash,
                }
            )
            existing_signatures.add(fixture.fixture_signature_hash)
            if not args.dry_run:
                write_replay_fixture(fixture, out_path)
            fixtures_written += 1
        if args.limit and fixtures_written >= args.limit:
            break
    print(
        json.dumps(
            {
                "fixtures": planned,
                "count": len(planned),
                "written_or_planned": fixtures_written,
                "duplicates_skipped": duplicate_count,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _select_run_dirs(runs_root: Path, run_globs: list[str]) -> list[Path]:
    root = runs_root.expanduser().resolve(strict=False)
    if run_globs:
        seen: set[Path] = set()
        selected: list[Path] = []
        for pattern in run_globs:
            for path in sorted(root.glob(pattern)):
                if path.is_dir() and path not in seen:
                    seen.add(path)
                    selected.append(path)
        return selected
    return sorted(path for path in root.iterdir() if path.is_dir())


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _analysis_type_from_run(run_dir: Path) -> str:
    state = _load_json(run_dir / "state.json")
    analysis_spec = state.get("analysis_spec", {}) if isinstance(state, dict) else {}
    if isinstance(analysis_spec, dict):
        return str(analysis_spec.get("analysis_type", "") or "")
    return ""


def _tags_for_run(run_dir: Path) -> list[str]:
    state = _load_json(run_dir / "state.json")
    tags: set[str] = {f"source_run:{run_dir.name}"}
    analysis_spec = state.get("analysis_spec", {}) if isinstance(state, dict) else {}
    if isinstance(analysis_spec, dict):
        analysis_type = str(analysis_spec.get("analysis_type", "") or "").strip()
        if analysis_type:
            tags.add(f"analysis_type:{analysis_type}")
    return sorted(tags)


def _existing_signature_hashes(output_dir: Path) -> set[str]:
    root = output_dir.expanduser().resolve(strict=False)
    if not root.exists():
        return set()
    signatures: set[str] = set()
    for fixture in load_replay_fixtures(root):
        if fixture.fixture_signature_hash:
            signatures.add(fixture.fixture_signature_hash)
    return signatures


def _fixture_id(run_id: str, trace_stem: str) -> str:
    clean_run = re.sub(r"[^a-zA-Z0-9_]+", "_", run_id).strip("_")
    clean_trace = re.sub(r"[^a-zA-Z0-9_]+", "_", trace_stem).strip("_")
    return f"{clean_run}_{clean_trace}"


if __name__ == "__main__":
    raise SystemExit(main())
