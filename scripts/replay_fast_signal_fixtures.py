#!/usr/bin/env python3
"""Replay fast-signal fixtures and report gate outcomes."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.fast_signal import (  # noqa: E402
    ReplayFixture,
    ReplayResult,
    load_replay_fixtures,
    run_planner_shape_replay,
)
from bio_harness.core.fast_signal_stepwise import (  # noqa: E402
    run_candidate_gate_auto_replay,
)

DEFAULT_FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "fast_signal"


def build_parser() -> argparse.ArgumentParser:
    """Build the fast-signal replay CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", nargs="*", type=Path, default=[DEFAULT_FIXTURE_ROOT])
    parser.add_argument("--jsonl", type=Path, default=None)
    return parser


def main() -> int:
    """Replay requested fixtures."""

    args = build_parser().parse_args()
    fixtures: list[ReplayFixture] = []
    for root in args.fixtures:
        fixtures.extend(load_replay_fixtures(root))
    results = [_replay_fixture(fixture) for fixture in fixtures]
    if args.jsonl:
        args.jsonl.parent.mkdir(parents=True, exist_ok=True)
        with args.jsonl.open("w", encoding="utf-8") as handle:
            for result in results:
                handle.write(json.dumps(asdict(result), sort_keys=True) + "\n")
    print(json.dumps([asdict(result) for result in results], indent=2, sort_keys=True))
    return 0 if all(result.passed for result in results) else 1


def _replay_fixture(fixture: ReplayFixture) -> ReplayResult:
    if fixture.kind == "planner_shape":
        return run_planner_shape_replay(fixture)
    if fixture.kind == "candidate_gate":
        return run_candidate_gate_auto_replay(fixture)
    return ReplayResult(
        fixture_id=fixture.id,
        kind=fixture.kind,
        passed=False,
        reason=f"Unsupported fixture kind: {fixture.kind}",
    )


if __name__ == "__main__":
    raise SystemExit(main())
