#!/usr/bin/env python3
"""Measure prompt-feature effect size from paired planner fixture sets."""

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
    load_replay_fixtures,
    parse_raw_emission,
)
from bio_harness.core.fast_signal_prompt_sensitivity import (  # noqa: E402
    measure_prompt_sensitivity,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the prompt-sensitivity measurement CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-name", required=True)
    parser.add_argument("--control-fixtures", type=Path, required=True)
    parser.add_argument("--treatment-fixtures", type=Path, required=True)
    parser.add_argument("--keep-threshold", type=float, default=0.15)
    return parser


def main() -> int:
    """Measure prompt sensitivity from paired fixture directories."""

    args = build_parser().parse_args()
    control = [
        parse_raw_emission(fixture.raw_emission)
        for fixture in load_replay_fixtures(args.control_fixtures)
        if fixture.kind == "planner_shape"
    ]
    treatment = [
        parse_raw_emission(fixture.raw_emission)
        for fixture in load_replay_fixtures(args.treatment_fixtures)
        if fixture.kind == "planner_shape"
    ]
    result = measure_prompt_sensitivity(
        feature_name=args.feature_name,
        control_payloads=control,
        treatment_payloads=treatment,
        keep_threshold=args.keep_threshold,
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.keep_feature else 1


if __name__ == "__main__":
    raise SystemExit(main())
