from __future__ import annotations


from bio_harness.core.capability_catalog import load_capability_catalog
from bio_harness.core.tool_cards import read_tool_card
from bio_harness.core.curated_tool_batches import CURATED_TOOL_BATCHES, install_curated_batch
from bio_harness.core.tool_onboarding import install_tool_onboarding_batch, install_tool_onboarding_draft
from bio_harness.skills.registry import SkillRegistry


def test_install_tool_onboarding_draft_validates_required_fields(tmp_path):
    defs_dir = tmp_path / "defs"
    lib_dir = tmp_path / "lib"
    catalog_path = tmp_path / "capabilities" / "catalog.json"
    defs_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    draft = {
        "skill_name": "bad_skill",
        "description": "",
        "risk_level": "critical",
        "parameters": {},
        "tools_required": [],
        "capabilities": ["alignment"],
    }
    ok, msg = install_tool_onboarding_draft(
        draft,
        {"source": "https://example.org/docs", "mode": "official_docs"},
        skills_definitions_dir=defs_dir,
        skills_library_dir=lib_dir,
        capability_catalog_path=catalog_path,
    )
    assert ok is False
    assert "Risk level must be one of" in msg
    assert "Skill description is required." in msg


def test_install_tool_onboarding_draft_writes_skill_library_and_catalog_updates(tmp_path):
    defs_dir = tmp_path / "defs"
    lib_dir = tmp_path / "lib"
    catalog_path = tmp_path / "capabilities" / "catalog.json"
    tool_cards_dir = tmp_path / "tool_cards"
    defs_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    draft = {
        "skill_name": "demo_align",
        "description": "Demo aligner command",
        "risk_level": "medium",
        "tools_required": ["star"],
        "capabilities": ["alignment"],
        "parameters": {
            "genome_dir": {"type": "path", "description": "Genome index.", "required": True},
            "reads_1": {"type": "path", "description": "Read 1.", "required": True},
        },
        "command_template": "STAR --genomeDir {genome_dir} --readFilesIn {reads_1}",
        "usage_guide": "Demo usage",
    }

    ok, msg = install_tool_onboarding_draft(
        draft,
        {"source": "https://github.com/alexdobin/STAR", "mode": "official_docs"},
        manual_summary={
            "canonical_outputs": ["Aligned.out.bam"],
            "dangerous_flags": ["--force"],
            "common_errors": [{"pattern": "missing genome index", "cause": "index absent", "fix": "rebuild index"}],
            "source_documents": ["https://github.com/alexdobin/STAR"],
        },
        skills_definitions_dir=defs_dir,
        skills_library_dir=lib_dir,
        capability_catalog_path=catalog_path,
        tool_cards_dir=tool_cards_dir,
        install_workflow="controlled_test",
    )
    assert ok is True
    assert "demo_align" in msg
    assert (defs_dir / "demo_align.md").exists()
    assert (lib_dir / "demo_align.py").exists()
    assert (tool_cards_dir / "demo_align.json").exists()
    registry = SkillRegistry(defs_dir)
    skill = registry.get_skill("demo_align")
    assert skill is not None
    assert skill["when_to_use"] == "Demo aligner command"
    assert skill["analysis_categories"] == ["alignment"]
    assert set(skill["input_types"]) == {"fastq", "fasta_reference"}
    assert skill["output_types"] == ["bam"]
    card = read_tool_card(tool_cards_dir / "demo_align.json")
    assert "Aligned.out.bam" in card.canonical_outputs
    assert list(card.dangerous_flags) == ["--force"]
    assert card.common_errors[0]["pattern"] == "missing genome index"

    catalog = load_capability_catalog(catalog_path)
    alignment = next(cap for cap in catalog["capabilities"] if cap["id"] == "alignment")
    assert "star" in alignment["tool_hints"]
    assert "demo_align" in alignment["plan_signals"]
    assert catalog["custom_tools"][-1]["tool_card_path"].endswith("demo_align.json")


def test_install_tool_onboarding_draft_replaces_generic_capability_with_inferred_tags(tmp_path):
    defs_dir = tmp_path / "defs"
    lib_dir = tmp_path / "lib"
    catalog_path = tmp_path / "capabilities" / "catalog.json"
    defs_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    draft = {
        "skill_name": "demo_dge",
        "description": "Run DESeq2 differential expression between treatment and control from a count matrix and metadata table.",
        "risk_level": "medium",
        "tools_required": ["deseq2", "rscript"],
        "capabilities": ["analysis"],
        "parameters": {
            "counts_matrix": {"type": "path", "description": "Gene counts TSV.", "required": True},
            "metadata_table": {"type": "path", "description": "Sample metadata TSV.", "required": True},
            "output_dir": {"type": "path", "description": "Result directory.", "required": True},
        },
        "command_template": "Rscript run_deseq2.R --counts {counts_matrix} --metadata {metadata_table} --outdir {output_dir}",
    }

    ok, _ = install_tool_onboarding_draft(
        draft,
        {"source": "https://bioconductor.org/packages/DESeq2/", "mode": "official_docs"},
        skills_definitions_dir=defs_dir,
        skills_library_dir=lib_dir,
        capability_catalog_path=catalog_path,
    )

    assert ok is True
    registry = SkillRegistry(defs_dir)
    skill = registry.get_skill("demo_dge")
    assert skill is not None
    assert "differential_analysis" in skill["capabilities"]
    assert "group_comparison" in skill["capabilities"]
    assert skill["analysis_categories"] == ["rna_seq_differential_expression"]
    assert "tsv" in skill["input_types"]


def test_install_tool_onboarding_batch_reports_partial_failures(tmp_path):
    defs_dir = tmp_path / "defs"
    lib_dir = tmp_path / "lib"
    catalog_path = tmp_path / "capabilities" / "catalog.json"
    defs_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    entries = [
        {
            "draft": {
                "skill_name": "batch_good",
                "description": "good",
                "risk_level": "low",
                "tools_required": ["echo"],
                "capabilities": ["annotation"],
                "parameters": {"input_vcf": {"type": "path", "description": "Input", "required": True}},
                "command_template": "echo {input_vcf}",
            },
            "source_meta": {"source": "https://www.ensembl.org/info/docs/tools/vep/index.html", "mode": "official_docs"},
        },
        {
            "draft": {
                "skill_name": "batch_bad",
                "description": "",
                "risk_level": "invalid",
                "tools_required": ["echo"],
                "capabilities": ["annotation"],
                "parameters": {},
            },
            "source_meta": {"source": "https://example.org", "mode": "official_docs"},
        },
    ]

    report = install_tool_onboarding_batch(
        entries,
        skills_definitions_dir=defs_dir,
        skills_library_dir=lib_dir,
        capability_catalog_path=catalog_path,
        install_workflow="controlled_test_batch",
    )
    assert report["attempted"] == 2
    assert len(report["installed"]) == 1
    assert len(report["failed"]) == 1
    assert report["passed"] is False


def test_install_curated_batch_has_known_batch_ids(tmp_path):
    defs_dir = tmp_path / "defs"
    lib_dir = tmp_path / "lib"
    catalog_path = tmp_path / "capabilities" / "catalog.json"
    defs_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    batch_ids = {str(batch.get("id", "")) for batch in CURATED_TOOL_BATCHES}
    assert "expression_core" in batch_ids
    assert "chromatin_core" in batch_ids

    report = install_curated_batch(
        "chromatin_core",
        skills_definitions_dir=defs_dir,
        skills_library_dir=lib_dir,
        capability_catalog_path=catalog_path,
        record_custom_tool=False,
    )
    assert report["attempted"] == 2
    assert report["passed"] is True
