from __future__ import annotations

from bio_harness.pipeline_scripts.kegg_reference import (
    default_kegg_hsa_reference_path,
    load_kegg_hsa_reference,
)


def test_default_kegg_reference_cache_is_checked_in() -> None:
    path = default_kegg_hsa_reference_path()

    assert path.exists() is True
    reference = load_kegg_hsa_reference(path, allow_download=False, write_back=False)

    assert reference.symbol_to_gids
    assert reference.pathway_names
    assert reference.pathway_gids
