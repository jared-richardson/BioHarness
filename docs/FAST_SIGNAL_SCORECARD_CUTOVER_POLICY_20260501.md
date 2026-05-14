# Fast Signal Scorecard Cutover Policy, 2026-05-01

This records the accepted A6 policy for using the B1 reproduction baseline in
scorecard summaries and release-gate decisions.

## Decision

Use an explicit cutover, not a legacy-row backfill.

The canonical B1 artifact is:

`workspace/studies/reproduction_rates_b1_exp42_exp43_exp44_current_20260430.json`

It has kind:

`canonical_b1_reproduction_baseline`

When this artifact is present, `scripts/fast_signal_scorecard.py summarize`,
`snapshot`, and `gate-status` apply this policy:

1. Load raw append-only scorecard rows from `workspace/studies/scorecard.jsonl`.
2. Exclude raw reproduction rows whose `experiment_id` is either a canonical
   B1 experiment ID or a superseded predecessor ID.
3. Append the canonical B1 rows from the baseline artifact.
4. Run reproduction summaries, gate effectiveness, and `release_gate_status()`
   on that calibrated row set.

This prevents the polluted bootstrap run and stale restored exp44 rows from
being counted alongside the corrected B1 measurements.

## Canonical IDs

Canonical B1 experiment IDs:

- `exp42_current_release_abs_python`
- `exp43_current_release_abs_python`
- `exp44_after_parameter_profile_filter`

Superseded predecessor IDs:

- `exp42_current_release`
- `exp43_current_release`
- `exp44`

The loader infers common predecessor IDs from canonical names and also honors
any explicit `superseded_experiment_ids` listed in the baseline artifact.

## Excluded Sources

The canonical aggregate intentionally excludes:

- `workspace/studies/reproduction_rates_exp42_exp43_current_release_20260428.json`
  because it was an environment bootstrap artifact from a detached shell that
  lacked `pydantic`.
- `workspace/studies/reproduction_rates.json` because it was a stale restored
  aggregate with only three exp44 rows.

## Snapshot Fields

Scorecard snapshots expose the cutover in
`reproduction_baseline_cutover`:

- `kind`
- `cutover_policy`
- `legacy_row_policy`
- `legacy_rows_backfilled`
- `included_sources`
- `excluded_sources`
- `canonical_experiment_ids`
- `superseded_experiment_ids`
- `excluded_reproduction_rows`

As of this cutover, `legacy_rows_backfilled` is `false` and
`cutover_policy` is `canonical_replacement`.

## Gate Semantics

`release_gate_status()` checks the calibrated row set. That means:

- A launch for `exp42_current_release_abs_python`,
  `exp43_current_release_abs_python`, or
  `exp44_after_parameter_profile_filter` can use the canonical B1 baseline.
- A launch using the superseded IDs receives `wait` with
  `no_reproduction_baseline`.
- Missing reproduction evidence still degrades to `wait`, never `go`.
- Red relevant evidence still returns `blocked`.

This is intentionally conservative: the scorecard can validate current
canonical labels, while stale labels remain visibly uncalibrated.
