from __future__ import annotations

from pathlib import Path

from bio_harness.core.scientific_tool_catalog import (
    load_curated_scientific_tool_catalog,
    load_scientific_tool_catalog,
    resolve_scientific_tool,
    save_curated_scientific_tool_catalog,
    scientific_tool_index,
    upsert_scientific_tool_entry,
)


def test_load_scientific_tool_catalog_includes_wrapped_entries():
    catalog = load_scientific_tool_catalog()
    tool = resolve_scientific_tool("bwa_mem_align", catalog)

    assert tool["support_tier"] == "wrapped"
    assert "alignment" in tool["capabilities"]
    assert "reference_fasta" in tool["required_parameters"]
    assert "bwa" in tool["aliases"]
    assert "samtools" in tool["executables"]


def test_load_scientific_tool_catalog_includes_curated_helpers_and_common_tools():
    catalog = load_scientific_tool_catalog()
    helper = resolve_scientific_tool("compare_pathways.py", catalog)
    common = resolve_scientific_tool("deepvariant", catalog)

    assert helper["support_tier"] == "helper_script"
    assert "pathway_enrichment" in helper["capabilities"]
    assert helper["augment_capability_catalog"] is True

    assert common["support_tier"] == "catalog_only"
    assert "variant_calling" in common["capabilities"]
    assert "gatk_haplotypecaller" in common["repo_alternatives"]
    assert common["documentation_url"].startswith("https://")


def test_scientific_tool_index_resolves_aliases():
    catalog = load_scientific_tool_catalog()
    index = scientific_tool_index(catalog)

    assert index["mutect2"]["name"] == "gatk_mutect2_call"
    assert index["iqtree2"]["name"] == "phylogenetics_iqtree_style"


def test_load_scientific_tool_catalog_includes_mafft_align_wrapper():
    catalog = load_scientific_tool_catalog()
    tool = resolve_scientific_tool("mafft_align", catalog)

    assert tool["support_tier"] == "wrapped"
    assert "phylogenetics" in tool["capabilities"]
    assert "mafft" in tool["aliases"]


def test_load_scientific_tool_catalog_includes_sniffles_wrapper():
    catalog = load_scientific_tool_catalog()
    tool = resolve_scientific_tool("sniffles", catalog)

    assert tool["name"] == "sniffles_sv_call"
    assert tool["support_tier"] == "wrapped"
    assert "structural_variant_calling" in tool["capabilities"]
    assert "sniffles" in tool["aliases"]


def test_load_scientific_tool_catalog_includes_bedtools_utility_wrappers():
    catalog = load_scientific_tool_catalog()
    tool = resolve_scientific_tool("genomecov", catalog)

    assert tool["name"] == "bedtools_genomecov"
    assert tool["support_tier"] == "wrapped"
    assert "coverage_profiling" in tool["capabilities"]
    assert "bedtools" in tool["executables"]


def test_load_scientific_tool_catalog_includes_samtools_qc_wrappers():
    catalog = load_scientific_tool_catalog()
    tool = resolve_scientific_tool("flagstat", catalog)

    assert tool["name"] == "samtools_flagstat"
    assert tool["support_tier"] == "wrapped"
    assert "alignment_qc" in tool["capabilities"]
    assert "samtools" in tool["executables"]


def test_upsert_scientific_tool_entry_round_trip(tmp_path: Path):
    target = tmp_path / "scientific_tools.json"
    catalog = {"version": 1, "tools": []}
    updated = upsert_scientific_tool_entry(
        catalog,
        {
            "name": "test_tool",
            "support_tier": "catalog_only",
            "description": "Synthetic unit-test entry.",
            "capabilities": ["quantification"],
            "executables": ["test-tool"],
            "augment_capability_catalog": True,
        },
    )

    save_curated_scientific_tool_catalog(updated, target)
    reloaded = load_curated_scientific_tool_catalog(target)
    entry = resolve_scientific_tool("test_tool", reloaded)

    assert entry["name"] == "test_tool"
    assert entry["support_tier"] == "catalog_only"
    assert "quantification" in entry["capabilities"]
