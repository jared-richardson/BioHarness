#!/usr/bin/env python3
"""Deterministic table-first metabolomics differential-abundance workflow.

This workflow is intentionally scoped to processed metabolite-feature tables
plus sample metadata. It does not attempt raw LC-MS processing, peak picking,
or downstream spectral annotation.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import ttest_ind


@dataclass(frozen=True)
class MetabolomicsWorkflowSummary:
    """Compact summary for one deterministic metabolomics run."""

    feature_table: str
    metadata_table: str
    features_input: int
    features_retained: int
    samples_input: int
    sample_id_column: str
    group_column: str
    group_a: str
    group_b: str
    filtered_high_missingness_count: int
    imputed_value_count: int


def _read_table(path: Path) -> pd.DataFrame:
    """Load a delimited table with pandas delimiter inference."""

    try:
        table = pd.read_csv(path, sep=None, engine="python")
    except Exception as exc:  # pragma: no cover - exercised through CLI/runtime
        raise ValueError(f"Could not read tabular input: {path}") from exc
    if table.empty:
        raise ValueError(f"Input table is empty: {path}")
    return table


def _normalize_column_name(value: str) -> str:
    """Normalize one column token for deterministic matching."""

    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def _semantic_column_match(explicit: str, column: str, aliases: set[str]) -> int:
    """Return one preference score for an explicit semantic column hint."""

    explicit_norm = _normalize_column_name(explicit)
    column_norm = _normalize_column_name(column)
    if not explicit_norm:
        return 0
    if explicit_norm == column_norm:
        return 3
    if explicit_norm in aliases and column_norm in aliases:
        return 2
    return 0


def _load_feature_table(path: Path) -> pd.DataFrame:
    """Load one feature-intensity table and validate numeric payload columns."""

    table = _read_table(path)
    if table.shape[1] < 3:
        raise ValueError("Metabolomics feature table must include one feature column and at least two sample columns.")
    feature_column = str(table.columns[0])
    feature_ids = table[feature_column].astype(str).str.strip()
    if feature_ids.eq("").any():
        raise ValueError("Metabolomics feature table contains blank feature identifiers.")
    sample_columns = [str(column) for column in table.columns[1:]]
    numeric = table[sample_columns].apply(pd.to_numeric, errors="coerce")
    invalid_mask = numeric.isna() & table[sample_columns].notna() & table[sample_columns].astype(str).ne("")
    if invalid_mask.any().any():
        bad_column = invalid_mask.any(axis=0)[lambda s: s].index[0]
        bad_row = int(np.flatnonzero(invalid_mask[bad_column].to_numpy())[0]) + 2
        bad_value = str(table.loc[bad_row - 2, bad_column])
        raise ValueError(
            f"Metabolomics feature table contains non-numeric values at row {bad_row}, column `{bad_column}`: {bad_value!r}"
        )
    numeric.insert(0, "feature_id", feature_ids)
    return numeric


def _load_metadata_table(path: Path) -> pd.DataFrame:
    """Load one metadata table and normalize obvious empty rows."""

    table = _read_table(path)
    table = table.dropna(axis=0, how="all").copy()
    if table.empty:
        raise ValueError("Metabolomics metadata table is empty after dropping blank rows.")
    return table


def _infer_sample_id_column(metadata: pd.DataFrame, sample_names: list[str], explicit: str = "") -> str:
    """Infer the metadata sample-ID column from the feature-table sample names."""

    candidates = [str(column) for column in metadata.columns]
    sample_set = {str(item).strip() for item in sample_names}
    scored: list[tuple[int, int, int, str]] = []
    preferred_names = {"sample", "sampleid", "sample_id", "id"}
    for column in candidates:
        values = metadata[column].astype(str).str.strip()
        value_set = set(values)
        overlap = len(sample_set.intersection(value_set))
        exact = int(overlap == len(sample_names) and len(value_set) >= len(sample_names))
        preferred = int(_normalize_column_name(str(column)) in preferred_names)
        explicit_match = _semantic_column_match(explicit, str(column), preferred_names)
        if overlap:
            scored.append((explicit_match, exact, preferred * 1000 + overlap, str(column)))
    if not scored:
        raise ValueError("Could not infer the metabolomics metadata sample-ID column.")
    scored.sort(reverse=True)
    return scored[0][3]


def _infer_group_column(
    metadata: pd.DataFrame,
    *,
    sample_id_column: str,
    explicit: str = "",
    group_a: str = "",
    group_b: str = "",
) -> str:
    """Infer the grouping column used for differential-abundance testing."""

    candidates = [str(column) for column in metadata.columns if str(column) != sample_id_column]
    target_groups = {str(value).strip().lower() for value in (group_a, group_b) if str(value).strip()}
    preferred_names = {"condition", "group", "treatment", "class", "phenotype"}
    scored: list[tuple[int, int, int, int, str]] = []
    for column in candidates:
        values = metadata[column].astype(str).str.strip()
        unique = [item for item in sorted(set(values)) if item]
        if len(unique) < 2 or len(unique) > 8:
            continue
        target_overlap = len(target_groups.intersection({item.lower() for item in unique}))
        explicit_match = _semantic_column_match(explicit, str(column), preferred_names)
        preferred = int(_normalize_column_name(str(column)) in preferred_names)
        binary = int(len(unique) == 2)
        scored.append((explicit_match, target_overlap, preferred, binary, str(column)))
    if not scored:
        raise ValueError("Could not infer the metabolomics grouping column from metadata.")
    scored.sort(reverse=True)
    return scored[0][4]


def _infer_group_labels(values: pd.Series, *, explicit_a: str = "", explicit_b: str = "") -> tuple[str, str]:
    """Return one deterministic pair of comparison groups."""

    if explicit_a and explicit_b:
        return str(explicit_a), str(explicit_b)
    unique = [str(item).strip() for item in values.astype(str).str.strip().tolist() if str(item).strip()]
    distinct = sorted(set(unique))
    if len(distinct) != 2:
        raise ValueError(f"Metabolomics grouping column must contain exactly two groups, found {distinct!r}.")
    normalized = {_normalize_column_name(item): item for item in distinct}
    control = normalized.get("control")
    treatment = normalized.get("treatment")
    if control and treatment:
        return control, treatment
    return distinct[0], distinct[1]


def _median_center(matrix: pd.DataFrame) -> pd.DataFrame:
    """Median-center each sample column on a log-scale intensity matrix."""

    medians = matrix.median(axis=0, skipna=True)
    global_median = float(np.nanmedian(medians.to_numpy(dtype=float)))
    return matrix.subtract(medians, axis=1).add(global_median, axis=1)


def _impute_missing(matrix: pd.DataFrame, *, method: str) -> tuple[pd.DataFrame, int]:
    """Impute missing intensity entries deterministically."""

    lowered = str(method or "").strip().lower() or "feature_median"
    if lowered not in {"feature_median", "global_low"}:
        raise ValueError(f"Unsupported metabolomics imputation method: {method}")
    rendered = matrix.copy()
    missing_mask = rendered.isna()
    imputed_count = int(missing_mask.sum().sum())
    if imputed_count == 0:
        return rendered, 0
    if lowered == "feature_median":
        row_medians = rendered.median(axis=1, skipna=True)
        fallback = float(np.nanmedian(rendered.to_numpy(dtype=float))) - 1.0
        row_medians = row_medians.fillna(fallback)
        for column in rendered.columns:
            rendered[column] = rendered[column].fillna(row_medians)
        return rendered, imputed_count
    global_low = float(np.nanquantile(rendered.to_numpy(dtype=float), 0.05)) - 1.0
    return rendered.fillna(global_low), imputed_count


def _benjamini_hochberg(pvalues: np.ndarray) -> np.ndarray:
    """Return Benjamini-Hochberg adjusted p-values."""

    order = np.argsort(pvalues)
    ranked = np.empty_like(pvalues, dtype=float)
    total = float(len(pvalues))
    running = 1.0
    for rank, index in enumerate(order[::-1], start=1):
        original_rank = len(pvalues) - rank + 1
        value = float(pvalues[index]) * total / float(original_rank)
        running = min(running, value)
        ranked[index] = min(max(running, 0.0), 1.0)
    return ranked


def _compute_differential_abundance(
    matrix: pd.DataFrame,
    metadata: pd.DataFrame,
    *,
    sample_id_column: str,
    group_column: str,
    group_a: str,
    group_b: str,
    min_present_fraction: float,
    impute_method: str,
) -> tuple[pd.DataFrame, int, int, pd.DataFrame]:
    """Compute differential abundance with deterministic preprocessing."""

    metadata_indexed = metadata.copy()
    metadata_indexed[sample_id_column] = metadata_indexed[sample_id_column].astype(str).str.strip()
    metadata_indexed[group_column] = metadata_indexed[group_column].astype(str).str.strip()
    metadata_indexed = metadata_indexed.set_index(sample_id_column, drop=False)
    sample_columns = [str(column) for column in matrix.columns if str(column) != "feature_id"]
    missing_samples = [sample for sample in sample_columns if sample not in metadata_indexed.index]
    if missing_samples:
        raise ValueError(f"Metadata is missing feature-table samples: {missing_samples!r}")
    aligned_metadata = metadata_indexed.loc[sample_columns]
    group_values = aligned_metadata[group_column]
    if not {group_a, group_b}.issubset(set(group_values)):
        raise ValueError(f"Requested metabolomics groups are not both present in metadata: {group_a!r}, {group_b!r}")

    numeric = matrix.set_index("feature_id")
    min_present = max(0.0, min(float(min_present_fraction), 1.0))
    retain_mask = numeric.notna().mean(axis=1) >= min_present
    retained = numeric.loc[retain_mask].copy()
    filtered_count = int((~retain_mask).sum())
    if retained.empty:
        raise ValueError("No metabolomics features remain after missingness filtering.")

    normalized = _median_center(retained)
    imputed, imputed_count = _impute_missing(normalized, method=impute_method)

    group_a_samples = [sample for sample in sample_columns if str(group_values.loc[sample]) == group_a]
    group_b_samples = [sample for sample in sample_columns if str(group_values.loc[sample]) == group_b]
    if len(group_a_samples) < 2 or len(group_b_samples) < 2:
        raise ValueError("Metabolomics comparison requires at least two samples per group.")

    rows: list[dict[str, Any]] = []
    for feature_id, row in imputed.iterrows():
        a_values = row[group_a_samples].to_numpy(dtype=float)
        b_values = row[group_b_samples].to_numpy(dtype=float)
        if not np.isfinite(a_values).all() or not np.isfinite(b_values).all():
            raise ValueError(f"Metabolomics imputation left non-finite values for feature {feature_id!r}.")
        stat = ttest_ind(b_values, a_values, equal_var=False, nan_policy="omit")
        pvalue = float(stat.pvalue) if stat.pvalue is not None and math.isfinite(float(stat.pvalue)) else 1.0
        log2_fc = float(np.mean(b_values) - np.mean(a_values))
        rows.append(
            {
                "feature_id": str(feature_id),
                "log2FoldChange": log2_fc,
                "pvalue": min(max(pvalue, 0.0), 1.0),
                "mean_group_a": float(np.mean(a_values)),
                "mean_group_b": float(np.mean(b_values)),
            }
        )
    result = pd.DataFrame(rows)
    result["padj"] = _benjamini_hochberg(result["pvalue"].to_numpy(dtype=float))
    result = result.sort_values(["padj", "pvalue", "log2FoldChange"], ascending=[True, True, False]).reset_index(drop=True)
    normalized_out = imputed.reset_index().rename(columns={"index": "feature_id"})
    return result, filtered_count, imputed_count, normalized_out


def run_metabolomics_diff_abundance(
    *,
    feature_table: Path,
    metadata_table: Path,
    output_dir: Path,
    output_csv: Path | None = None,
    sample_id_column: str = "",
    group_column: str = "",
    group_a: str = "",
    group_b: str = "",
    normalization_method: str = "median_center",
    min_present_fraction: float = 0.5,
    impute_method: str = "feature_median",
) -> dict[str, Any]:
    """Run one deterministic metabolomics differential-abundance analysis.

    Args:
        feature_table: Feature-intensity matrix path.
        metadata_table: Sample metadata path.
        output_dir: Directory where canonical outputs should be written.
        output_csv: Optional explicit output CSV path.
        sample_id_column: Optional explicit sample-ID column.
        group_column: Optional explicit grouping column.
        group_a: Optional explicit reference group label.
        group_b: Optional explicit comparison group label.
        normalization_method: Deterministic normalization method.
        min_present_fraction: Minimum retained observation fraction per feature.
        impute_method: Missing-value imputation method.

    Returns:
        JSON-serializable workflow summary.

    Raises:
        ValueError: If the feature or metadata inputs are malformed.
    """

    feature_matrix = _load_feature_table(feature_table)
    metadata = _load_metadata_table(metadata_table)
    sample_names = [str(column) for column in feature_matrix.columns if str(column) != "feature_id"]
    inferred_sample_id = _infer_sample_id_column(metadata, sample_names, explicit=sample_id_column)
    inferred_group_column = _infer_group_column(
        metadata,
        sample_id_column=inferred_sample_id,
        explicit=group_column,
        group_a=group_a,
        group_b=group_b,
    )
    inferred_group_a, inferred_group_b = _infer_group_labels(
        metadata[inferred_group_column],
        explicit_a=group_a,
        explicit_b=group_b,
    )
    if str(normalization_method or "").strip().lower() != "median_center":
        raise ValueError(f"Unsupported metabolomics normalization method: {normalization_method}")
    result, filtered_count, imputed_count, normalized = _compute_differential_abundance(
        feature_matrix,
        metadata,
        sample_id_column=inferred_sample_id,
        group_column=inferred_group_column,
        group_a=inferred_group_a,
        group_b=inferred_group_b,
        min_present_fraction=min_present_fraction,
        impute_method=impute_method,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_csv or (output_dir / "metabolomics_differential_abundance.csv")
    normalized_path = output_dir / "normalized_feature_matrix.tsv"
    qc_path = output_dir / "metabolomics_qc_summary.json"
    volcano_path = output_dir / "volcano_plot_data.tsv"
    summary_md_path = output_dir / "metabolomics_summary.md"

    result.to_csv(result_path, index=False)
    normalized.to_csv(normalized_path, sep="\t", index=False)
    volcano = result[["feature_id", "log2FoldChange", "pvalue", "padj"]].copy()
    volcano["neg_log10_padj"] = [-math.log10(max(float(value), 1e-300)) for value in volcano["padj"]]
    volcano.to_csv(volcano_path, sep="\t", index=False)

    summary = MetabolomicsWorkflowSummary(
        feature_table=str(feature_table),
        metadata_table=str(metadata_table),
        features_input=int(feature_matrix.shape[0]),
        features_retained=int(result.shape[0]),
        samples_input=len(sample_names),
        sample_id_column=inferred_sample_id,
        group_column=inferred_group_column,
        group_a=inferred_group_a,
        group_b=inferred_group_b,
        filtered_high_missingness_count=filtered_count,
        imputed_value_count=imputed_count,
    )
    qc_path.write_text(json.dumps(summary.__dict__, indent=2) + "\n", encoding="utf-8")
    summary_md_path.write_text(
        (
            "# Metabolomics Differential Abundance Summary\n\n"
            f"- Features input: `{summary.features_input}`\n"
            f"- Features retained: `{summary.features_retained}`\n"
            f"- Samples input: `{summary.samples_input}`\n"
            f"- Sample ID column: `{summary.sample_id_column}`\n"
            f"- Group column: `{summary.group_column}`\n"
            f"- Group A: `{summary.group_a}`\n"
            f"- Group B: `{summary.group_b}`\n"
            f"- Filtered high-missingness features: `{summary.filtered_high_missingness_count}`\n"
            f"- Imputed values: `{summary.imputed_value_count}`\n"
        ),
        encoding="utf-8",
    )
    return dict(summary.__dict__)


def main() -> int:
    """CLI entrypoint for deterministic metabolomics differential abundance."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-table", required=True, help="Metabolomics feature CSV/TSV.")
    parser.add_argument("--metadata-table", required=True, help="Sample metadata CSV/TSV.")
    parser.add_argument("--output-dir", required=True, help="Output directory.")
    parser.add_argument("--output-csv", default="", help="Optional explicit output CSV path.")
    parser.add_argument("--sample-id-column", default="", help="Optional explicit sample-ID column.")
    parser.add_argument("--group-column", default="", help="Optional explicit grouping column.")
    parser.add_argument("--group-a", default="", help="Optional explicit reference group.")
    parser.add_argument("--group-b", default="", help="Optional explicit comparison group.")
    parser.add_argument("--normalization-method", default="median_center")
    parser.add_argument("--min-present-fraction", type=float, default=0.5)
    parser.add_argument("--impute-method", default="feature_median")
    args = parser.parse_args()

    try:
        summary = run_metabolomics_diff_abundance(
            feature_table=Path(args.feature_table).expanduser().resolve(),
            metadata_table=Path(args.metadata_table).expanduser().resolve(),
            output_dir=Path(args.output_dir).expanduser().resolve(),
            output_csv=Path(args.output_csv).expanduser().resolve() if args.output_csv else None,
            sample_id_column=str(args.sample_id_column or "").strip(),
            group_column=str(args.group_column or "").strip(),
            group_a=str(args.group_a or "").strip(),
            group_b=str(args.group_b or "").strip(),
            normalization_method=str(args.normalization_method or "").strip(),
            min_present_fraction=float(args.min_present_fraction),
            impute_method=str(args.impute_method or "").strip(),
        )
    except ValueError as exc:
        print(f"__FORMAT_INPUT_ERROR__:{exc}", file=sys.stderr, flush=True)
        return 2

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
