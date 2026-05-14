"""Load and cache human KEGG reference data for pathway enrichment.

This module centralizes the public KEGG REST lookups used by the Alzheimer
pathway-comparison helper so benchmark execution can prefer a repo-local cache
over live network calls.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEGG_HSA_REFERENCE_PATH = (
    PROJECT_ROOT / "benchmark_data" / "reference" / "kegg_hsa_reference.json"
)


@dataclass(frozen=True)
class KeggHumanReference:
    """Normalized human KEGG symbol and pathway membership data.

    Attributes:
        symbol_to_gids: Uppercase gene symbol to KEGG human gene identifiers.
        pathway_names: KEGG pathway identifier to display label.
        pathway_gids: KEGG pathway identifier to member KEGG gene identifiers.
    """

    symbol_to_gids: dict[str, tuple[str, ...]]
    pathway_names: dict[str, str]
    pathway_gids: dict[str, tuple[str, ...]]


def default_kegg_hsa_reference_path() -> Path:
    """Return the default repo-local KEGG HSA cache path."""

    return DEFAULT_KEGG_HSA_REFERENCE_PATH


def load_kegg_hsa_reference(
    cache_path: Path | None = None,
    *,
    allow_download: bool = True,
    write_back: bool = True,
) -> KeggHumanReference:
    """Load cached human KEGG reference data, fetching it if needed.

    Args:
        cache_path: Optional cache path override.
        allow_download: Whether the helper may call KEGG REST when the cache is
            missing.
        write_back: Whether freshly fetched data should be persisted to
            ``cache_path``.

    Returns:
        A normalized KEGG reference payload.

    Raises:
        RuntimeError: If no cache is available and download is disabled or
            fetching the reference fails.
    """

    resolved_cache = (cache_path or DEFAULT_KEGG_HSA_REFERENCE_PATH).expanduser().resolve(
        strict=False
    )
    if resolved_cache.exists():
        return _load_reference_file(resolved_cache)
    if not allow_download:
        raise RuntimeError(f"KEGG reference cache is missing: {resolved_cache}")
    reference = _fetch_kegg_hsa_reference()
    if write_back:
        resolved_cache.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbol_to_gids": {key: list(value) for key, value in reference.symbol_to_gids.items()},
            "pathway_names": reference.pathway_names,
            "pathway_gids": {key: list(value) for key, value in reference.pathway_gids.items()},
        }
        resolved_cache.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return reference


def _load_reference_file(path: Path) -> KeggHumanReference:
    payload = json.loads(path.read_text(encoding="utf-8"))
    symbol_to_gids = {
        str(symbol).strip().upper(): tuple(sorted({str(gid).strip() for gid in gids if str(gid).strip()}))
        for symbol, gids in (payload.get("symbol_to_gids", {}) or {}).items()
        if str(symbol).strip()
    }
    pathway_names = {
        str(pathway_id).strip(): str(name).strip()
        for pathway_id, name in (payload.get("pathway_names", {}) or {}).items()
        if str(pathway_id).strip() and str(name).strip()
    }
    pathway_gids = {
        str(pathway_id).strip(): tuple(
            sorted({str(gid).strip() for gid in gids if str(gid).strip()})
        )
        for pathway_id, gids in (payload.get("pathway_gids", {}) or {}).items()
        if str(pathway_id).strip()
    }
    return KeggHumanReference(
        symbol_to_gids=symbol_to_gids,
        pathway_names=pathway_names,
        pathway_gids=pathway_gids,
    )


def _fetch_kegg_hsa_reference() -> KeggHumanReference:
    gene_raw = _download_text("https://rest.kegg.jp/list/hsa", timeout=120)
    link_raw = _download_text("https://rest.kegg.jp/link/hsa/pathway", timeout=120)
    names_raw = _download_text("https://rest.kegg.jp/list/pathway/hsa", timeout=60)

    symbol_to_gids: dict[str, set[str]] = {}
    for line in gene_raw.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        gid = parts[0].strip()
        if not gid:
            continue
        symbols = [token.strip() for token in parts[3].split(";", 1)[0].split(",")]
        for symbol in symbols:
            if symbol:
                symbol_to_gids.setdefault(symbol.upper(), set()).add(gid)

    pathway_gids: dict[str, set[str]] = {}
    for line in link_raw.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        pathway_id = parts[0].strip().replace("path:", "")
        gene_id = parts[1].strip()
        if pathway_id and gene_id:
            pathway_gids.setdefault(pathway_id, set()).add(gene_id)

    pathway_names: dict[str, str] = {}
    for line in names_raw.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        pathway_id = parts[0].strip().replace("path:", "")
        full_name = parts[1].strip()
        short_name = full_name.split(" - Homo sapiens", 1)[0].strip()
        if pathway_id and short_name:
            pathway_names[pathway_id] = f"{short_name} Homo sapiens {pathway_id}"

    return KeggHumanReference(
        symbol_to_gids={
            symbol: tuple(sorted(gids))
            for symbol, gids in symbol_to_gids.items()
            if gids
        },
        pathway_names=pathway_names,
        pathway_gids={
            pathway_id: tuple(sorted(gids))
            for pathway_id, gids in pathway_gids.items()
            if gids
        },
    )


def _download_text(url: str, *, timeout: int) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8")
