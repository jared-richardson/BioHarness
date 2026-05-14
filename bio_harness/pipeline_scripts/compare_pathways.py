"""Build the Alzheimer pathway-comparison deliverable deterministically."""

from __future__ import annotations

import argparse
import hashlib
import time
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact
from scipy.stats import ttest_ind
from statsmodels.stats.multitest import multipletests

from bio_harness.pipeline_scripts.export_multi_model_pathway_comparison import (
    export_multi_model_pathway_comparison,
)
from bio_harness.pipeline_scripts.kegg_reference import (
    KeggHumanReference,
    load_kegg_hsa_reference,
)


DEFAULT_PRECOMPUTED_LOG2FC_CUTOFF = 0.75
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENRICHR_CACHE_DIR = PROJECT_ROOT / "workspace" / "reference_cache" / "enrichr_kegg_2016_mouse"
ENRICHR_MAX_ATTEMPTS = 3
ENRICHR_RETRY_SECONDS = 5.0


def _infer_label(path: Path) -> str:
    name = path.name.lower()
    if "5xfad" in name or "168137" in name:
        return "5xFAD"
    if "3xtg" in name or "161904" in name:
        return "3xTG_AD"
    if "ps3o1s" in name or "ps301s" in name:
        return "PS3O1S"
    return path.stem


def _read_table(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep="," if path.suffix.lower() == ".csv" else "\t", index_col=0)


def _annotate_ensembl_ids(
    index: pd.Index,
    *,
    gene_name_map: Mapping[str, str] | None = None,
) -> pd.Series:
    ensembl_ids = [str(value).split(".", 1)[0] for value in index]
    mapped: dict[str, str] = {
        str(key).split(".", 1)[0]: str(value).strip()
        for key, value in (gene_name_map or {}).items()
        if str(key).strip() and str(value).strip()
    }
    unresolved = [gene_id for gene_id in ensembl_ids if gene_id not in mapped]
    if unresolved:
        try:
            import mygene

            mg = mygene.MyGeneInfo()
            gene_info = mg.querymany(unresolved, scopes="ensembl.gene", fields="symbol", species="mouse")
            for row in gene_info:
                query = str(row.get("query", "") or "").strip()
                symbol = str(row.get("symbol", "") or "").strip()
                if query and symbol and query not in mapped:
                    mapped[query] = symbol
        except Exception:
            pass
    return pd.Series([mapped.get(gene_id, "") for gene_id in ensembl_ids], index=index)


def _filter_normalize(counts: pd.DataFrame, *, min_cpm: float = 0.7, min_samples: int = 2) -> pd.DataFrame:
    cpm = counts.apply(lambda col: (col / col.sum()) * 1e6, axis=0)
    keep = (cpm > min_cpm).sum(axis=1) >= min_samples
    filtered = counts.loc[keep].copy()
    geometric_means = filtered.apply(lambda row: np.exp(np.log(row[row > 0]).mean()) if (row > 0).any() else np.nan, axis=1)
    valid = geometric_means.replace([np.inf, -np.inf], np.nan).dropna()
    filtered = filtered.loc[valid.index]
    size_factors = filtered.div(valid, axis=0).median(axis=0)
    return filtered.div(size_factors, axis=1)


def _infer_case_control_columns(df: pd.DataFrame, *, label: str) -> tuple[list[str], list[str]]:
    cols = list(df.columns)
    label_l = str(label).lower()
    if label_l == "5xfad":
        case = [col for col in cols if "5xfad;" in str(col).lower()]
        control = [col for col in cols if "5xfad;" not in str(col).lower() and "bl6" in str(col).lower()]
        if case and control:
            return case, control
    if label_l == "3xtg_ad":
        case = [col for col in cols if "3xtgad" in str(col).lower() or "3xtg_ad" in str(col).lower()]
        control = [col for col in cols if "wt" in str(col).lower()]
        if case and control:
            return case, control
    midpoint = len(cols) // 2
    return cols[:midpoint], cols[midpoint:]


def _differential_expression_from_counts(
    path: Path,
    *,
    label: str,
    output_dir: Path,
    gene_name_map: Mapping[str, str] | None = None,
    use_pydeseq2: bool = False,
) -> tuple[list[str], Path]:
    gene_names, _background_gene_names, deg_path = _differential_expression_with_background_from_counts(
        path,
        label=label,
        output_dir=output_dir,
        gene_name_map=gene_name_map,
        use_pydeseq2=use_pydeseq2,
    )
    return gene_names, deg_path


def _differential_expression_with_background_from_counts(
    path: Path,
    *,
    label: str,
    output_dir: Path,
    gene_name_map: Mapping[str, str] | None = None,
    use_pydeseq2: bool = False,
) -> tuple[list[str], list[str], Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    counts = _read_table(path)
    result = _run_counts_differential_expression(
        counts,
        label=label,
        gene_name_map=gene_name_map,
        use_pydeseq2=use_pydeseq2,
    )
    sig = result[(result["p_adj"] < 0.075) & (result["log2fc"].abs() > 0.75)].copy()
    deg_path = output_dir / f"DEG_{label}.csv"
    sig.to_csv(deg_path, index=False)
    gene_names = [str(item).strip() for item in sig["gene_name"].tolist() if str(item).strip()]
    background_gene_names = [str(item).strip() for item in result["gene_name"].tolist() if str(item).strip()]
    return gene_names, background_gene_names, deg_path


def _genes_from_precomputed_de(
    path: Path,
    *,
    label: str,
    output_dir: Path,
    log2fc_cutoff: float = DEFAULT_PRECOMPUTED_LOG2FC_CUTOFF,
) -> tuple[list[str], Path]:
    gene_names, _background_gene_names, deg_path = _genes_with_background_from_precomputed_de(
        path,
        label=label,
        output_dir=output_dir,
        log2fc_cutoff=log2fc_cutoff,
    )
    return gene_names, deg_path


def _genes_with_background_from_precomputed_de(
    path: Path,
    *,
    label: str,
    output_dir: Path,
    log2fc_cutoff: float = DEFAULT_PRECOMPUTED_LOG2FC_CUTOFF,
) -> tuple[list[str], list[str], Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    df = _read_table(path)
    lower_to_actual = {str(col).strip().lower(): str(col).strip() for col in df.columns}
    gene_col = lower_to_actual.get("gene_name") or lower_to_actual.get("gene")
    pval_col = lower_to_actual.get("pval") or lower_to_actual.get("p_val") or lower_to_actual.get("pvalue")
    lfc_col = lower_to_actual.get("log2fc") or lower_to_actual.get("log2foldchange") or lower_to_actual.get("log2fc")
    if not gene_col or not pval_col or not lfc_col:
        raise ValueError(f"Precomputed DE table {path} is missing expected gene/p-value/log2FC columns.")
    filtered = df[
        (pd.to_numeric(df[pval_col], errors="coerce") < 0.075)
        & (pd.to_numeric(df[lfc_col], errors="coerce").abs() > float(log2fc_cutoff))
    ].copy()
    deg_path = output_dir / f"DEG_{label}.csv"
    filtered.to_csv(deg_path, index=False)
    gene_names = [str(item).strip() for item in filtered[gene_col].tolist() if str(item).strip()]
    background_gene_names = [str(item).strip() for item in df[gene_col].tolist() if str(item).strip()]
    return gene_names, background_gene_names, deg_path


def _run_kegg_enrichment_from_reference(
    gene_names: list[str],
    *,
    background_gene_names: list[str],
    label: str,
    output_dir: Path,
    reference: KeggHumanReference,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"KEGG_{label}.csv"
    de_gids = _symbols_to_kegg_gids(gene_names, reference)
    bg_gids = _symbols_to_kegg_gids(background_gene_names, reference)
    rows: list[dict[str, float | str]] = []
    total_background = len(bg_gids)
    total_de = len(de_gids)
    if total_background and total_de:
        for pathway_id, pathway_gene_ids in reference.pathway_gids.items():
            pathway_name = reference.pathway_names.get(pathway_id, "")
            if not pathway_name:
                continue
            pathway_background = set(pathway_gene_ids) & bg_gids
            if not pathway_background:
                continue
            overlap = de_gids & pathway_background
            if not overlap:
                continue
            overlap_count = len(overlap)
            pathway_count = len(pathway_background)
            table = [
                [overlap_count, total_de - overlap_count],
                [
                    pathway_count - overlap_count,
                    total_background - total_de - pathway_count + overlap_count,
                ],
            ]
            if any(cell < 0 for row in table for cell in row):
                continue
            _odds_ratio, pvalue = fisher_exact(table, alternative="greater")
            rows.append({"Pathway": pathway_name, "P-value": float(pvalue)})
    pd.DataFrame(sorted(rows, key=lambda row: float(row["P-value"]))).to_csv(out_path, index=False)
    return out_path


def _run_kegg_enrichment(
    gene_names: list[str],
    *,
    background_gene_names: list[str],
    label: str,
    output_dir: Path,
    reference: KeggHumanReference,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"KEGG_{label}.csv"
    cached = _load_enrichr_cache(gene_names)
    if cached is not None:
        cached.to_csv(out_path, index=False)
        return out_path

    for attempt in range(ENRICHR_MAX_ATTEMPTS):
        try:
            import gseapy as gp

            enriched = gp.enrichr(
                gene_list=gene_names,
                gene_sets=["KEGG_2016"],
                organism="mouse",
                outdir=None,
                no_plot=True,
                verbose=False,
            )
            result_df = enriched.results.copy()
            result_df.to_csv(out_path, index=False)
            _write_enrichr_cache(gene_names, result_df)
            return out_path
        except Exception:  # pragma: no cover - exercised via fallback test
            if attempt + 1 < ENRICHR_MAX_ATTEMPTS:
                time.sleep(ENRICHR_RETRY_SECONDS * float(attempt + 1))

    return _run_kegg_enrichment_from_reference(
        gene_names,
        background_gene_names=background_gene_names,
        label=label,
        output_dir=output_dir,
        reference=reference,
    )


def _parse_labeled_paths(items: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for item in items:
        raw = str(item).strip()
        if not raw:
            continue
        label, sep, path_text = raw.partition("=")
        if sep and label.strip() and path_text.strip():
            parsed[label.strip()] = Path(path_text.strip()).expanduser().resolve(strict=False)
            continue
        path = Path(raw).expanduser().resolve(strict=False)
        parsed[_infer_label(path)] = path
    return parsed


def _build_gene_name_map_from_precomputed_tables(paths: dict[str, Path]) -> dict[str, str]:
    gene_name_map: dict[str, str] = {}
    for path in paths.values():
        df = _read_table(path)
        lower_to_actual = {str(col).strip().lower(): str(col).strip() for col in df.columns}
        gene_col = lower_to_actual.get("gene_name") or lower_to_actual.get("gene")
        gene_id_col = lower_to_actual.get("gene_id") or lower_to_actual.get("ensembl_id")
        if not gene_col or not gene_id_col:
            continue
        for gene_id, gene_name in zip(df[gene_id_col].tolist(), df[gene_col].tolist()):
            normalized_gene_id = str(gene_id).split(".", 1)[0].strip()
            normalized_gene_name = str(gene_name).strip()
            if normalized_gene_id and normalized_gene_name and normalized_gene_id not in gene_name_map:
                gene_name_map[normalized_gene_id] = normalized_gene_name
    return gene_name_map


def _symbols_to_kegg_gids(
    gene_names: list[str],
    reference: KeggHumanReference,
) -> set[str]:
    gids: set[str] = set()
    for gene_name in gene_names:
        normalized = str(gene_name).strip().upper()
        if not normalized:
            continue
        gids.update(reference.symbol_to_gids.get(normalized, ()))
    return gids


def _enrichr_cache_path(gene_names: list[str]) -> Path:
    normalized = "\n".join(sorted({str(gene).strip().upper() for gene in gene_names if str(gene).strip()}))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return DEFAULT_ENRICHR_CACHE_DIR / f"{digest}.csv"


def _load_enrichr_cache(gene_names: list[str]) -> pd.DataFrame | None:
    cache_path = _enrichr_cache_path(gene_names)
    if not cache_path.exists():
        return None
    try:
        return pd.read_csv(cache_path)
    except Exception:
        return None


def _write_enrichr_cache(gene_names: list[str], results: pd.DataFrame) -> None:
    cache_path = _enrichr_cache_path(gene_names)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(cache_path, index=False)


def _run_counts_differential_expression(
    counts: pd.DataFrame,
    *,
    label: str,
    gene_name_map: Mapping[str, str] | None = None,
    use_pydeseq2: bool = False,
) -> pd.DataFrame:
    counts.index = [str(value).split(".", 1)[0] for value in counts.index]
    if use_pydeseq2:
        deseq_result = _try_run_pydeseq2(
            counts,
            label=label,
            gene_name_map=gene_name_map,
        )
        if deseq_result is not None:
            return deseq_result
    min_samples = 1 if str(label).strip().lower() == "3xtg_ad" else 2
    normed = _filter_normalize(counts, min_samples=min_samples)
    case_cols, control_cols = _infer_case_control_columns(normed, label=label)
    case = normed[case_cols].apply(pd.to_numeric, errors="coerce")
    control = normed[control_cols].apply(pd.to_numeric, errors="coerce")
    _t_stat, p_values = ttest_ind(case, control, axis=1, nan_policy="omit")
    log2fc = np.log2(case.mean(axis=1) + 1) - np.log2(control.mean(axis=1) + 1)
    gene_index = normed.index
    _, fdr, _, _ = multipletests(p_values, method="fdr_bh")
    result = pd.DataFrame(
        {
            "gene_id": gene_index.astype(str),
            "log2fc": log2fc,
            "pval": p_values,
            "p_adj": fdr,
        },
        index=gene_index,
    )
    result["gene_name"] = _annotate_ensembl_ids(result.index, gene_name_map=gene_name_map)
    return result


def _try_run_pydeseq2(
    counts: pd.DataFrame,
    *,
    label: str,
    gene_name_map: Mapping[str, str] | None = None,
) -> pd.DataFrame | None:
    try:
        from pydeseq2.dds import DeseqDataSet
        from pydeseq2.ds import DeseqStats
    except Exception:
        return None

    case_cols, control_cols = _infer_case_control_columns(counts, label=label)
    if not case_cols or not control_cols:
        return None
    cpm = counts.apply(lambda col: (col / col.sum()) * 1e6, axis=0)
    keep = (cpm > 1).sum(axis=1) >= 2
    filtered = counts.loc[keep].apply(pd.to_numeric, errors="coerce").fillna(0).astype(int)
    if filtered.empty:
        return None
    metadata = pd.DataFrame(
        {
            "condition": [
                "treatment" if column in case_cols else "control"
                for column in filtered.columns
            ]
        },
        index=filtered.columns,
    )
    try:
        dds = DeseqDataSet(counts=filtered.T, metadata=metadata, design="~condition", quiet=True)
        dds.deseq2()
        stats = DeseqStats(dds, contrast=["condition", "treatment", "control"], quiet=True)
        stats.summary()
    except Exception:
        return None
    results = stats.results_df.copy()
    results["gene_id"] = results.index.astype(str)
    results["log2fc"] = pd.to_numeric(results.get("log2FoldChange"), errors="coerce")
    results["pval"] = pd.to_numeric(results.get("pvalue"), errors="coerce")
    results["p_adj"] = pd.to_numeric(results.get("padj"), errors="coerce")
    results["gene_name"] = _annotate_ensembl_ids(results.index, gene_name_map=gene_name_map)
    return results[["gene_id", "log2fc", "pval", "p_adj", "gene_name"]]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare shared KEGG pathways across multiple models.")
    parser.add_argument("--input_csv", action="append", default=[])
    parser.add_argument("--input_txt", action="append", default=[])
    parser.add_argument("--count-table", action="append", default=[])
    parser.add_argument("--precomputed-de-table", action="append", default=[])
    parser.add_argument("--output_dir", default="")
    parser.add_argument("--output-csv", default="")
    parser.add_argument("--run-differential-analysis", action="store_true")
    parser.add_argument(
        "--use-pydeseq2",
        action="store_true",
        help="Opt into PyDESeq2-based differential expression for count tables.",
    )
    parser.add_argument(
        "--precomputed-log2fc-cutoff",
        type=float,
        default=DEFAULT_PRECOMPUTED_LOG2FC_CUTOFF,
        help="Absolute log2 fold-change cutoff for precomputed differential-expression tables.",
    )
    args = parser.parse_args()

    count_tables = _parse_labeled_paths(args.count_table + args.input_txt)
    precomputed_tables = _parse_labeled_paths(args.precomputed_de_table + args.input_csv)
    gene_name_map = _build_gene_name_map_from_precomputed_tables(precomputed_tables)
    reference = load_kegg_hsa_reference()
    output_dir = Path(args.output_dir or ".").expanduser().resolve(strict=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_csv = Path(args.output_csv).expanduser().resolve(strict=False) if args.output_csv else output_dir / "pathway_comparison.csv"

    kegg_files: dict[str, Path] = {}
    for label, path in count_tables.items():
        gene_names, background_gene_names, _deg_path = _differential_expression_with_background_from_counts(
            path,
            label=label,
            output_dir=output_dir,
            gene_name_map=gene_name_map,
            use_pydeseq2=args.use_pydeseq2,
        )
        if gene_names:
            kegg_files[label] = _run_kegg_enrichment(
                gene_names,
                background_gene_names=background_gene_names,
                label=label,
                output_dir=output_dir,
                reference=reference,
            )
    for label, path in precomputed_tables.items():
        gene_names, background_gene_names, _deg_path = _genes_with_background_from_precomputed_de(
            path,
            label=label,
            output_dir=output_dir,
            log2fc_cutoff=args.precomputed_log2fc_cutoff,
        )
        if gene_names:
            kegg_files[label] = _run_kegg_enrichment(
                gene_names,
                background_gene_names=background_gene_names,
                label=label,
                output_dir=output_dir,
                reference=reference,
            )

    if len(kegg_files) < 3:
        raise SystemExit(f"Expected enrichment files for three models, found {sorted(kegg_files)}")

    export_multi_model_pathway_comparison(label_to_csv=kegg_files, output_csv=output_csv)
    print(output_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
