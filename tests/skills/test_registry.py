import json
from pathlib import Path

from bio_harness.core.tool_cards import tool_card_from_draft, write_tool_card
from bio_harness.skills.registry import SkillRegistry


def test_registry_loads_valid_skills():
    registry = SkillRegistry(Path("bio_harness/skills/definitions"))
    assert registry.get_skill("fastqc_run") is not None


def test_registry_rejects_invalid_risk_level(tmp_path):
    skills_dir = tmp_path / "defs"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skills_dir / "bad_skill.md"
    skill_path.write_text(
        """---
name: bad_skill
description: test
risk_level: critical
parameters: {}
---
invalid
""",
        encoding="utf-8",
    )

    registry = SkillRegistry(skills_dir)
    assert registry.get_skill("bad_skill") is None


def test_generate_index_file(tmp_path):
    skills_dir = tmp_path / "defs"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skills_dir / "ok_skill.md"
    skill_path.write_text(
        """---
name: ok_skill
description: test
risk_level: low
parameters:
  input:
    type: path
canonical_output_filenames:
  input: example.txt
---
content
""",
        encoding="utf-8",
    )

    registry = SkillRegistry(skills_dir)
    out = registry.generate_index()
    text = out.read_text(encoding="utf-8")
    payload = json.loads(text)

    assert out.name == "index.json"
    assert "ok_skill" in text
    assert "skills_count" in text
    assert payload["skills"][0]["file_path"] == "ok_skill.md"
    assert payload["skills"][0]["canonical_output_filenames"] == {"input": "example.txt"}


def test_generate_retrieval_index_file(tmp_path):
    skills_dir = tmp_path / "defs"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "stringtie_quant.md").write_text(
        """---
name: stringtie_quant
description: assemble and quantify transcripts
risk_level: low
parameters:
  input_bam:
    type: path
analysis_categories:
- rna_seq_quantification
capabilities:
- transcript_quantification
system_requirements:
  min_ram_gb: 1
  min_cores: 1
when_to_use: Use for transcript assembly from aligned reads
---
content
""",
        encoding="utf-8",
    )

    registry = SkillRegistry(skills_dir)
    out = registry.generate_retrieval_index()
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert out.name == "retrieval_index.json"
    assert payload["skills_count"] == 1
    assert payload["skills"][0]["name"] == "stringtie_quant"
    assert "transcript" in payload["skills"][0]["lexical_terms"]


def test_search_skills_uses_tool_card_enrichment(tmp_path):
    skills_dir = tmp_path / "defs"
    cards_dir = tmp_path / "cards"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "stringtie_quant.md").write_text(
        """---
name: stringtie_quant
description: transcript assembly
risk_level: low
parameters:
  input_bam:
    type: path
analysis_categories:
- rna_seq_quantification
capabilities:
- transcript_quantification
system_requirements:
  min_ram_gb: 1
  min_cores: 1
---
content
""",
        encoding="utf-8",
    )
    (skills_dir / "featurecounts_run.md").write_text(
        """---
name: featurecounts_run
description: gene counting
risk_level: low
parameters:
  input_bams:
    type: path
analysis_categories:
- gene_counting
capabilities:
- read_counting
system_requirements:
  min_ram_gb: 1
  min_cores: 1
---
content
""",
        encoding="utf-8",
    )

    card = tool_card_from_draft(
        {
            "skill_name": "stringtie_quant",
            "description": "Assemble and quantify transcripts from aligned reads.",
            "tools_required": ["stringtie"],
            "capabilities": ["transcript_quantification"],
            "parameters": {"input_bam": {"type": "path", "required": True}},
            "command_template": "stringtie {input_bam} -o {output_gtf}",
            "output_types": ["assembled.gtf", "gene_abundances.tsv"],
        }
    )
    write_tool_card(card, tool_cards_dir=cards_dir)

    registry = SkillRegistry(skills_dir)
    results = registry.search_skills(
        "gene abundance table assembled gtf",
        tool_cards_dir=cards_dir,
        limit=2,
    )

    assert results[0]["name"] == "stringtie_quant"
    assert results[0]["retrieval_score"] >= results[0]["lexical_score"]
    assert "gtf" in results[0]["matched_terms"]


def test_generate_index_uses_repo_relative_file_paths_when_repo_layout_exists(tmp_path):
    project_root = tmp_path / "project"
    skills_dir = project_root / "bio_harness" / "skills" / "definitions"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skills_dir / "ok_skill.md"
    skill_path.write_text(
        """---
name: ok_skill
description: test
risk_level: low
parameters:
  input:
    type: path
analysis_categories:
- test_analysis
capabilities:
- test_capability
system_requirements:
  min_ram_gb: 1
  min_cores: 1
---
content
""",
        encoding="utf-8",
    )
    (project_root / "scripts").mkdir(parents=True, exist_ok=True)
    (project_root / "pixi.toml").write_text("[workspace]\n", encoding="utf-8")

    registry = SkillRegistry(skills_dir)
    out = registry.generate_index()
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["skills"][0]["file_path"] == "bio_harness/skills/definitions/ok_skill.md"


def test_registry_reports_incomplete_enriched_metadata(tmp_path):
    skills_dir = tmp_path / "defs"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skills_dir / "incomplete_skill.md"
    skill_path.write_text(
        """---
name: incomplete_skill
description: test
risk_level: low
parameters: {}
analysis_categories: []
capabilities: []
system_requirements: {}
---
content
""",
        encoding="utf-8",
    )

    registry = SkillRegistry(skills_dir)
    issues = registry.find_incomplete_skills()

    assert "incomplete_skill" in issues
    assert "field 'analysis_categories' must be a non-empty list" in issues["incomplete_skill"]
    assert "field 'capabilities' must be a non-empty list" in issues["incomplete_skill"]
    assert "field 'system_requirements' must be a non-empty mapping/object" in issues["incomplete_skill"]


def test_official_skill_definitions_have_complete_enriched_metadata():
    registry = SkillRegistry(Path("bio_harness/skills/definitions"))
    assert registry.find_incomplete_skills() == {}


def test_official_generated_index_uses_repo_neutral_file_paths():
    payload = json.loads(Path("bio_harness/skills/definitions/index.json").read_text(encoding="utf-8"))

    assert payload["skills"]
    assert all(not Path(str(row.get("file_path", ""))).is_absolute() for row in payload["skills"])


def test_prokka_definition_declares_extended_metadata_parameters():
    registry = SkillRegistry(Path("bio_harness/skills/definitions"))

    skill = registry.get_skill("prokka_annotate")

    assert skill is not None
    parameters = skill.get("parameters", {})
    for key in ("cpus", "kingdom", "genus", "species", "strain", "locustag"):
        assert key in parameters


def test_bwa_mem_align_definition_declares_extended_metadata_parameters():
    registry = SkillRegistry(Path("bio_harness/skills/definitions"))

    skill = registry.get_skill("bwa_mem_align")

    assert skill is not None
    parameters = skill.get("parameters", {})
    for key in (
        "postprocess_mode",
        "output_unmapped_bam",
        "read_group",
        "sample_name",
    ):
        assert key in parameters


def test_snpeff_annotate_definition_declares_extended_metadata_parameters():
    registry = SkillRegistry(Path("bio_harness/skills/definitions"))

    skill = registry.get_skill("snpeff_annotate")

    assert skill is not None
    parameters = skill.get("parameters", {})
    for key in ("genome_label", "codon_table", "check_protein", "check_cds"):
        assert key in parameters


def test_featurecounts_run_definition_declares_extended_metadata_parameters():
    registry = SkillRegistry(Path("bio_harness/skills/definitions"))

    skill = registry.get_skill("featurecounts_run")

    assert skill is not None
    parameters = skill.get("parameters", {})
    for key in (
        "annotation_format",
        "feature_type",
        "attribute_type",
        "is_paired_end",
        "count_read_pairs",
        "strand_specificity",
    ):
        assert key in parameters


def test_deseq2_run_definition_declares_engine_parameter():
    registry = SkillRegistry(Path("bio_harness/skills/definitions"))

    skill = registry.get_skill("deseq2_run")

    assert skill is not None
    parameters = skill.get("parameters", {})
    assert "engine" in parameters
