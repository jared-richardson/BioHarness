#!/usr/bin/env python3
"""Summarize plan-shape idioms from fast-signal planner fixtures."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.fast_signal import (  # noqa: E402
    load_replay_fixtures,
    parse_raw_emission,
    plan_idiom_key,
    plan_idiom_summary,
)

DEFAULT_FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "fast_signal" / "planner_shape"
DEFAULT_OUTPUT = PROJECT_ROOT / "workspace" / "studies" / "plan_shape_corpus_summary.json"


def build_parser() -> argparse.ArgumentParser:
    """Build the plan-corpus summary CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURE_ROOT)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--top-n", type=int, default=10)
    return parser


def main() -> int:
    """Summarize fixture idioms and write the corpus summary artifact."""

    args = build_parser().parse_args()
    fixtures = [
        fixture
        for fixture in load_replay_fixtures(args.fixtures)
        if fixture.kind == "planner_shape"
    ]
    summaries = [
        plan_idiom_summary(parse_raw_emission(fixture.raw_emission))
        for fixture in fixtures
    ]
    idiom_counter = Counter(plan_idiom_key(summary) for summary in summaries)
    total = sum(idiom_counter.values())
    top = idiom_counter.most_common(args.top_n)
    top_coverage = sum(count for _key, count in top) / max(total, 1)
    payload: dict[str, Any] = {
        "fixture_count": len(fixtures),
        "top_n": args.top_n,
        "top_coverage": top_coverage,
        "sufficient_for_fixture_seeding": top_coverage >= 0.80,
        "top_idioms": [
            {"idiom": key, "count": count, "fraction": count / max(total, 1)}
            for key, count in top
        ],
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
