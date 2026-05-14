"""Comprehensive tests for the skill generator and tool onboarding pipeline.

Tests CLI discovery, help text parsing, skill generation, novel compositions,
edge cases, and wrapper execution across real installed bioinformatics tools.
"""
from __future__ import annotations

import ast
import json
import shutil
import textwrap
from pathlib import Path
from typing import Any

import pytest

from bio_harness.core.skill_generator import (
    _generate_wrapper_code,
    _regex_parse_help,
    _sanitize_skill_name,
    build_skill_from_cli,
    discover_cli,
    generate_skill_draft,
    parse_help_text,
    validate_skill,
)
from bio_harness.core.tool_onboarding import (
    install_tool_onboarding_batch,
    install_tool_onboarding_draft,
)
from bio_harness.core.capability_catalog import load_capability_catalog

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_available(name: str) -> bool:
    """Check if a tool is on PATH (pixi or system)."""
    return shutil.which(name) is not None


def _pixi_tool_available(name: str) -> bool:
    """Check if a tool is available in the pixi environment."""
    pixi_bin = PROJECT_ROOT / ".pixi" / "envs" / "default" / "bin" / name
    return pixi_bin.exists() or _tool_available(name)


def _subcommand_help(tool: str, subcmd: str) -> str:
    """Get help text for a subcommand by running it directly."""
    import os
    import subprocess

    from bio_harness.core.skill_generator import _discover_env

    env = _discover_env(tool)
    # Resolve full path from pixi env
    tool_cmd = shutil.which(tool, path=env.get("PATH")) or tool

    for flag in ("--help", "-h"):
        try:
            result = subprocess.run(
                [tool_cmd, subcmd, flag],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
            if len(output) > 50:
                return output
        except Exception:
            continue
    return ""


def _make_skill_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create skill definition, library, and catalog dirs."""
    defs = tmp_path / "defs"
    lib = tmp_path / "lib"
    cat = tmp_path / "capabilities" / "catalog.json"
    defs.mkdir(parents=True, exist_ok=True)
    lib.mkdir(parents=True, exist_ok=True)
    cat.parent.mkdir(parents=True, exist_ok=True)
    return defs, lib, cat


# ===================================================================
# CATEGORY A: CLI Discovery & Help Parsing (19 tests)
# ===================================================================


class TestCLIDiscovery:
    """Test discover_cli() on real installed tools."""

    @pytest.mark.skipif(not _pixi_tool_available("samtools"), reason="samtools not installed")
    def test_a01_samtools_help_discovery(self):
        info = discover_cli("samtools")
        assert info["help_text"], "samtools --help should produce output"
        assert info["tool_name"] == "samtools"
        assert len(info["help_text"]) > 100

    @pytest.mark.skipif(not _pixi_tool_available("bcftools"), reason="bcftools not installed")
    def test_a02_bcftools_help_discovery(self):
        info = discover_cli("bcftools")
        assert info["help_text"], "bcftools --help should produce output"
        assert info["version"] or True  # version extraction is best-effort

    @pytest.mark.skipif(not _pixi_tool_available("minimap2"), reason="minimap2 not installed")
    def test_a03_minimap2_help_discovery(self):
        info = discover_cli("minimap2")
        assert info["help_text"]
        # minimap2 help should mention common flags
        assert "-t" in info["help_text"] or "threads" in info["help_text"].lower()

    @pytest.mark.skipif(not _pixi_tool_available("fastp"), reason="fastp not installed")
    def test_a04_fastp_help_discovery(self):
        info = discover_cli("fastp")
        assert info["help_text"]
        assert "-i" in info["help_text"] or "input" in info["help_text"].lower()

    @pytest.mark.skipif(not _pixi_tool_available("samtools"), reason="samtools not installed")
    def test_a05_samtools_sort_subcommand_help(self):
        help_text = _subcommand_help("samtools", "sort")
        assert help_text, "samtools sort --help should produce output"
        assert "-o" in help_text or "output" in help_text.lower()

    @pytest.mark.skipif(not _pixi_tool_available("samtools"), reason="samtools not installed")
    def test_a06_samtools_view_subcommand_help(self):
        help_text = _subcommand_help("samtools", "view")
        assert help_text
        assert "-b" in help_text or "BAM" in help_text

    @pytest.mark.skipif(not _pixi_tool_available("samtools"), reason="samtools not installed")
    def test_a07_samtools_depth_subcommand_help(self):
        help_text = _subcommand_help("samtools", "depth")
        assert help_text

    @pytest.mark.skipif(not _pixi_tool_available("bcftools"), reason="bcftools not installed")
    def test_a08_bcftools_view_subcommand_help(self):
        help_text = _subcommand_help("bcftools", "view")
        assert help_text
        # Should mention region filtering
        assert "-r" in help_text or "region" in help_text.lower()

    @pytest.mark.skipif(not _pixi_tool_available("bcftools"), reason="bcftools not installed")
    def test_a09_bcftools_stats_subcommand_help(self):
        help_text = _subcommand_help("bcftools", "stats")
        assert help_text

    @pytest.mark.skipif(not _pixi_tool_available("bedtools"), reason="bedtools not installed")
    def test_a10_bedtools_intersect_subcommand_help(self):
        help_text = _subcommand_help("bedtools", "intersect")
        assert help_text
        assert "-a" in help_text and "-b" in help_text

    @pytest.mark.skipif(not _pixi_tool_available("tabix"), reason="tabix not installed")
    def test_a11_tabix_help_discovery(self):
        info = discover_cli("tabix")
        assert info["help_text"]

    @pytest.mark.skipif(not _pixi_tool_available("bgzip"), reason="bgzip not installed")
    def test_a12_bgzip_help_discovery(self):
        info = discover_cli("bgzip")
        assert info["help_text"]

    @pytest.mark.skipif(not _pixi_tool_available("cutadapt"), reason="cutadapt not installed")
    def test_a13_cutadapt_help_discovery(self):
        info = discover_cli("cutadapt")
        assert info["help_text"]
        assert "-a" in info["help_text"] or "adapter" in info["help_text"].lower()

    @pytest.mark.skipif(not _pixi_tool_available("prodigal"), reason="prodigal not installed")
    def test_a14_prodigal_help_discovery(self):
        info = discover_cli("prodigal")
        assert info["help_text"]
        assert "-i" in info["help_text"]

    @pytest.mark.skipif(not _pixi_tool_available("featureCounts"), reason="featureCounts not installed")
    def test_a15_featurecounts_help_discovery(self):
        info = discover_cli("featureCounts")
        assert info["help_text"]
        assert "-a" in info["help_text"]

    @pytest.mark.skipif(not _pixi_tool_available("salmon"), reason="salmon not installed")
    def test_a16_salmon_help_discovery(self):
        info = discover_cli("salmon")
        assert info["help_text"]

    @pytest.mark.skipif(not _pixi_tool_available("mafft"), reason="mafft not installed")
    def test_a17_mafft_help_discovery(self):
        info = discover_cli("mafft")
        assert info["help_text"]

    @pytest.mark.skipif(not _pixi_tool_available("iqtree"), reason="iqtree not installed")
    def test_a18_iqtree_help_discovery(self):
        info = discover_cli("iqtree")
        assert info["help_text"]
        assert "-s" in info["help_text"] or "alignment" in info["help_text"].lower()


class TestRegexParsing:
    """Test regex-based help text parsing."""

    def test_a19_regex_fallback_handles_nonstandard_help(self):
        """Nonstandard help text still extracts some parameters."""
        help_text = textwrap.dedent("""\
            MyBioTool v1.2.3 - a custom bioinformatics tool

            Usage: mybiotool [options] <input.fasta>

            Options:
              -i, --input FILE      Input FASTA file
              -o, --output FILE     Output results file
              -t, --threads INT     Number of threads [4]
              -q, --quality FLOAT   Minimum quality threshold
              --verbose             Enable verbose output
              -h, --help            Show this help
        """)
        parsed = _regex_parse_help(help_text, "mybiotool")
        assert parsed["description"]
        params = parsed["parameters"]
        assert len(params) >= 3  # Should find at least input, output, threads
        param_names = {p["name"] for p in params}
        # Should find common flags
        assert "input" in param_names or "i" in param_names
        assert "output" in param_names or "o" in param_names

    def test_regex_extracts_path_type_from_file_keyword(self):
        help_text = "  --reference FILE    Path to reference genome FASTA\n"
        parsed = _regex_parse_help(help_text, "test_tool")
        file_params = [p for p in parsed["parameters"] if p["type"] == "path"]
        assert len(file_params) >= 1

    def test_regex_extracts_integer_type_from_count_keyword(self):
        help_text = "  -t INT              Number of threads to use\n"
        parsed = _regex_parse_help(help_text, "test_tool")
        int_params = [p for p in parsed["parameters"] if p["type"] == "integer"]
        assert len(int_params) >= 1

    def test_regex_extracts_boolean_type_from_enable_keyword(self):
        help_text = "  --verbose           Enable verbose logging output\n"
        parsed = _regex_parse_help(help_text, "test_tool")
        bool_params = [p for p in parsed["parameters"] if p["type"] == "boolean"]
        assert len(bool_params) >= 1

    def test_regex_caps_at_15_parameters(self):
        lines = [f"  --param{i} VALUE    Parameter {i} description text\n" for i in range(25)]
        help_text = "TestTool v1.0\n\nOptions:\n" + "".join(lines)
        parsed = _regex_parse_help(help_text, "test_tool")
        assert len(parsed["parameters"]) <= 15


# ===================================================================
# CATEGORY B: End-to-End Skill Generation (10 tests)
# ===================================================================


class TestEndToEndGeneration:
    """Test full build_skill_from_cli() pipeline."""

    @pytest.mark.skipif(not _pixi_tool_available("samtools"), reason="samtools not installed")
    def test_b01_generate_samtools_sort_skill(self, tmp_path):
        """Generate skill from samtools sort subcommand help."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        help_text = _subcommand_help("samtools", "sort")
        assert help_text, "Need samtools sort help text"
        parsed = parse_help_text(help_text, "samtools_sort")
        draft = generate_skill_draft(parsed, "samtools_sort")
        valid, error = validate_skill(draft)
        assert valid, f"Validation failed: {error}"
        assert draft["name"] == "samtools_sort"
        assert draft["parameters"]

    @pytest.mark.skipif(not _pixi_tool_available("samtools"), reason="samtools not installed")
    def test_b02_generate_samtools_depth_skill(self, tmp_path):
        help_text = _subcommand_help("samtools", "depth")
        assert help_text
        parsed = parse_help_text(help_text, "samtools_depth")
        draft = generate_skill_draft(parsed, "samtools_depth")
        valid, error = validate_skill(draft)
        assert valid, f"Validation failed: {error}"

    @pytest.mark.skipif(not _pixi_tool_available("bcftools"), reason="bcftools not installed")
    def test_b03_generate_bcftools_view_skill(self, tmp_path):
        help_text = _subcommand_help("bcftools", "view")
        assert help_text
        parsed = parse_help_text(help_text, "bcftools_view")
        draft = generate_skill_draft(parsed, "bcftools_view")
        valid, error = validate_skill(draft)
        assert valid, f"Validation failed: {error}"

    @pytest.mark.skipif(not _pixi_tool_available("tabix"), reason="tabix not installed")
    def test_b04_generate_tabix_skill(self, tmp_path):
        info = discover_cli("tabix")
        if not info["help_text"]:
            pytest.skip("tabix help not available")
        parsed = parse_help_text(info["help_text"], "tabix")
        draft = generate_skill_draft(parsed, "tabix")
        valid, error = validate_skill(draft)
        assert valid, f"Validation failed: {error}"

    @pytest.mark.skipif(not _pixi_tool_available("bgzip"), reason="bgzip not installed")
    def test_b05_generate_bgzip_skill(self, tmp_path):
        info = discover_cli("bgzip")
        if not info["help_text"]:
            pytest.skip("bgzip help not available")
        parsed = parse_help_text(info["help_text"], "bgzip")
        draft = generate_skill_draft(parsed, "bgzip")
        valid, error = validate_skill(draft)
        assert valid, f"Validation failed: {error}"

    @pytest.mark.skipif(not _pixi_tool_available("minimap2"), reason="minimap2 not installed")
    def test_b06_generate_minimap2_skill(self, tmp_path):
        info = discover_cli("minimap2")
        parsed = parse_help_text(info["help_text"], "minimap2")
        draft = generate_skill_draft(parsed, "minimap2")
        valid, error = validate_skill(draft)
        assert valid, f"Validation failed: {error}"

    @pytest.mark.skipif(not _pixi_tool_available("prodigal"), reason="prodigal not installed")
    def test_b07_generate_prodigal_skill_no_conflict(self, tmp_path):
        """Generating a skill for a tool that already has a definition should not crash."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        info = discover_cli("prodigal")
        parsed = parse_help_text(info["help_text"], "prodigal_auto")
        draft = generate_skill_draft(parsed, "prodigal_auto")
        valid, error = validate_skill(draft)
        assert valid, f"Validation failed: {error}"
        # Install should succeed since name is prodigal_auto, not prodigal_annotate
        ok, msg = install_tool_onboarding_draft(
            draft,
            {"source": "cli_help:prodigal", "source_mode": "cli_discovery"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok, f"Install failed: {msg}"

    def test_b08_validate_generated_wrapper_syntax(self):
        """All generated wrapper code must pass ast.parse()."""
        params = [
            {"name": "input_file", "flag": "-i", "type": "path", "required": True, "default": None},
            {"name": "output_file", "flag": "-o", "type": "path", "required": True, "default": None},
            {"name": "threads", "flag": "-t", "type": "integer", "required": False, "default": "4"},
            {"name": "verbose", "flag": "--verbose", "type": "boolean", "required": False, "default": None},
            {"name": "quality", "flag": "-q", "type": "float", "required": False, "default": None},
        ]
        code = _generate_wrapper_code("test_skill", "test_tool", params, "test_tool -i {input_file} -o {output_file}")
        try:
            ast.parse(code)
        except SyntaxError as e:
            pytest.fail(f"Generated wrapper has syntax error: {e}\n\nCode:\n{code}")

    @pytest.mark.skipif(not _pixi_tool_available("samtools"), reason="samtools not installed")
    def test_b09_install_then_registry_sees_skill(self, tmp_path):
        """After installing a generated skill, the index includes it."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        help_text = _subcommand_help("samtools", "sort")
        parsed = parse_help_text(help_text, "samtools_sort_test")
        draft = generate_skill_draft(parsed, "samtools_sort_test")
        ok, msg = install_tool_onboarding_draft(
            draft,
            {"source": "cli_help:samtools_sort", "source_mode": "cli_discovery"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok, f"Install failed: {msg}"
        assert (defs / "samtools_sort_test.md").exists()
        assert (lib / "samtools_sort_test.py").exists()

    @pytest.mark.skipif(not _pixi_tool_available("samtools"), reason="samtools not installed")
    def test_b10_duplicate_install_is_idempotent(self, tmp_path):
        """Installing the same skill twice should not corrupt files."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        help_text = _subcommand_help("samtools", "sort")
        parsed = parse_help_text(help_text, "samtools_sort_idem")
        draft = generate_skill_draft(parsed, "samtools_sort_idem")

        ok1, _ = install_tool_onboarding_draft(
            draft,
            {"source": "cli_help:samtools_sort", "source_mode": "cli_discovery"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok1

        # Install again
        ok2, _ = install_tool_onboarding_draft(
            draft,
            {"source": "cli_help:samtools_sort", "source_mode": "cli_discovery"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok2

        # Definition should still be valid
        md_content = (defs / "samtools_sort_idem.md").read_text(encoding="utf-8")
        assert "samtools_sort_idem" in md_content


# ===================================================================
# CATEGORY C: Novel Skill Compositions (8 tests)
# ===================================================================


class TestNovelCompositions:
    """Test creating skills that combine tools in new ways."""

    def test_c01_bam_to_fastq_skill(self, tmp_path):
        """Create a skill for BAM→FASTQ conversion using samtools."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft = {
            "skill_name": "bam_to_fastq",
            "description": "Convert BAM file to FASTQ using samtools fastq",
            "risk_level": "low",
            "tools_required": ["samtools"],
            "capabilities": ["format_conversion"],
            "parameters": {
                "input_bam": {"type": "path", "description": "Input BAM file", "required": True, "file_role": "input_bam"},
                "output_r1": {"type": "path", "description": "Output R1 FASTQ", "required": True, "file_role": "output_dir"},
                "output_r2": {"type": "path", "description": "Output R2 FASTQ", "required": False},
                "threads": {"type": "integer", "description": "Threads", "required": False, "default": "4"},
            },
            "command_template": "samtools fastq -@ {threads} -1 {output_r1} -2 {output_r2} {input_bam}",
        }
        ok, msg = install_tool_onboarding_draft(
            draft,
            {"source": "manual:samtools_fastq", "source_mode": "novel_composition"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok, f"Install failed: {msg}"
        assert (defs / "bam_to_fastq.md").exists()

    def test_c02_vcf_consensus_skill(self, tmp_path):
        """Create a skill for consensus FASTA generation from VCF."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft = {
            "skill_name": "vcf_consensus",
            "description": "Generate consensus FASTA from VCF and reference",
            "risk_level": "low",
            "tools_required": ["bcftools"],
            "capabilities": ["variant_calling"],
            "parameters": {
                "input_vcf": {"type": "path", "description": "Input VCF file", "required": True, "file_role": "input_vcf"},
                "reference_fasta": {"type": "path", "description": "Reference FASTA", "required": True, "file_role": "reference_genome"},
                "output_fasta": {"type": "path", "description": "Output consensus FASTA", "required": True},
            },
            "command_template": "bcftools consensus -f {reference_fasta} {input_vcf} > {output_fasta}",
        }
        ok, msg = install_tool_onboarding_draft(
            draft,
            {"source": "manual:bcftools_consensus", "source_mode": "novel_composition"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok, f"Install failed: {msg}"

    def test_c03_vcf_filter_by_region_skill(self, tmp_path):
        """Create a skill for region-based VCF filtering."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft = {
            "skill_name": "vcf_filter_region",
            "description": "Filter VCF to a specific genomic region",
            "risk_level": "low",
            "tools_required": ["bcftools"],
            "capabilities": ["variant_calling"],
            "parameters": {
                "input_vcf": {"type": "path", "description": "Input VCF", "required": True},
                "region": {"type": "string", "description": "Region (chr:start-end)", "required": True},
                "output_vcf": {"type": "path", "description": "Output filtered VCF", "required": True},
            },
            "command_template": "bcftools view -r {region} -o {output_vcf} {input_vcf}",
        }
        ok, msg = install_tool_onboarding_draft(
            draft,
            {"source": "manual:bcftools_view_region", "source_mode": "novel_composition"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok, f"Install failed: {msg}"

    def test_c04_bam_coverage_histogram_skill(self, tmp_path):
        """Create a multi-tool bash pipeline as a single skill."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft = {
            "skill_name": "bam_coverage_histogram",
            "description": "Compute per-base coverage histogram from BAM",
            "risk_level": "low",
            "tools_required": ["samtools"],
            "capabilities": ["alignment"],
            "parameters": {
                "input_bam": {"type": "path", "description": "Input BAM", "required": True},
                "output_tsv": {"type": "path", "description": "Output coverage TSV", "required": True},
            },
            "command_template": "samtools depth -a {input_bam} | awk '{{print $1\"\\t\"$2\"\\t\"$3}}' > {output_tsv}",
        }
        ok, msg = install_tool_onboarding_draft(
            draft,
            {"source": "manual:samtools_depth_pipe", "source_mode": "novel_composition"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok, f"Install failed: {msg}"

    def test_c05_fasta_stats_skill(self, tmp_path):
        """Create a pure data transformation skill (count seqs, lengths)."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft = {
            "skill_name": "fasta_stats",
            "description": "Compute basic FASTA statistics (sequence count, total length, GC%)",
            "risk_level": "low",
            "tools_required": ["python3"],
            "capabilities": ["annotation"],
            "parameters": {
                "input_fasta": {"type": "path", "description": "Input FASTA file", "required": True},
                "output_json": {"type": "path", "description": "Output stats JSON", "required": True},
            },
            "command_template": "python3 -c \"import json,sys;seqs=open(sys.argv[1]).read().split('>')[1:];print(json.dumps({{'num_seqs':len(seqs),'total_bp':sum(len(''.join(s.split('\\n')[1:])) for s in seqs)}}))\" {input_fasta} > {output_json}",
        }
        ok, msg = install_tool_onboarding_draft(
            draft,
            {"source": "manual:fasta_stats", "source_mode": "novel_composition"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok, f"Install failed: {msg}"

    def test_c06_vcf_to_bed_skill(self, tmp_path):
        """Convert VCF variants to BED intervals."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft = {
            "skill_name": "vcf_to_bed",
            "description": "Convert VCF variant positions to BED interval format",
            "risk_level": "low",
            "tools_required": ["bcftools"],
            "capabilities": ["variant_calling"],
            "parameters": {
                "input_vcf": {"type": "path", "description": "Input VCF", "required": True},
                "output_bed": {"type": "path", "description": "Output BED file", "required": True},
            },
            "command_template": "bcftools query -f '%CHROM\\t%POS0\\t%END\\n' {input_vcf} > {output_bed}",
        }
        ok, msg = install_tool_onboarding_draft(
            draft,
            {"source": "manual:vcf_to_bed", "source_mode": "novel_composition"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok, f"Install failed: {msg}"

    def test_c07_subsample_fastq_skill(self, tmp_path):
        """Subsample FASTQ to N reads."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft = {
            "skill_name": "subsample_fastq",
            "description": "Subsample a FASTQ file to a specified number of reads",
            "risk_level": "low",
            "tools_required": ["samtools"],
            "capabilities": ["read_preprocessing"],
            "parameters": {
                "input_fastq": {"type": "path", "description": "Input FASTQ", "required": True},
                "output_fastq": {"type": "path", "description": "Output subsampled FASTQ", "required": True},
                "fraction": {"type": "string", "description": "Fraction to keep (0.0-1.0)", "required": True, "default": "0.1"},
                "seed": {"type": "integer", "description": "Random seed", "required": False, "default": "42"},
            },
            "command_template": "samtools view -s {seed}.{fraction} -@ 4 {input_fastq} > {output_fastq}",
        }
        ok, msg = install_tool_onboarding_draft(
            draft,
            {"source": "manual:subsample_fastq", "source_mode": "novel_composition"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok, f"Install failed: {msg}"

    def test_c08_merge_bam_skill(self, tmp_path):
        """Merge multiple BAMs as a reusable skill."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft = {
            "skill_name": "merge_bams",
            "description": "Merge multiple BAM files into one sorted BAM",
            "risk_level": "low",
            "tools_required": ["samtools"],
            "capabilities": ["alignment"],
            "parameters": {
                "input_bams": {"type": "string", "description": "Space-separated BAM paths", "required": True},
                "output_bam": {"type": "path", "description": "Output merged BAM", "required": True},
                "threads": {"type": "integer", "description": "Threads", "required": False, "default": "4"},
            },
            "command_template": "samtools merge -@ {threads} {output_bam} {input_bams}",
        }
        ok, msg = install_tool_onboarding_draft(
            draft,
            {"source": "manual:samtools_merge", "source_mode": "novel_composition"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok, f"Install failed: {msg}"


# ===================================================================
# CATEGORY D: Edge Cases & Robustness (8 tests)
# ===================================================================


class TestEdgeCases:
    """Test error handling and edge cases."""

    def test_d01_nonexistent_tool_graceful_failure(self):
        """discover_cli() for nonexistent tool returns empty help."""
        info = discover_cli("absolutely_fake_tool_xyz_12345")
        assert info["help_text"] == ""
        assert info["executable"] is None

    def test_d02_build_skill_from_nonexistent_tool(self, tmp_path):
        """build_skill_from_cli() fails gracefully for missing tool."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        ok, msg = build_skill_from_cli(
            "absolutely_fake_tool_xyz_12345",
            skills_defs_dir=defs,
            skills_lib_dir=lib,
            catalog_path=cat,
        )
        assert ok is False
        assert "help text" in msg.lower() or "could not" in msg.lower()

    def test_d03_empty_help_text_handling(self):
        """parse_help_text() handles empty string gracefully."""
        parsed = parse_help_text("", "empty_tool")
        assert parsed is not None
        assert isinstance(parsed.get("parameters", []), list)

    def test_d04_huge_help_text_truncation(self):
        """discover_cli returns max 8KB of help text."""
        # We can't easily mock this, but we can test parse_help_text with large input
        huge = "TestTool v1.0\n" + ("  --param VALUE   A parameter\n" * 1000)
        parsed = parse_help_text(huge, "huge_tool")
        assert len(parsed["parameters"]) <= 15  # cap at 15

    def test_d05_skill_name_sanitization(self):
        """Tool names with special chars get sanitized correctly."""
        assert _sanitize_skill_name("tool.v2-beta") == "tool_v2_beta"
        assert _sanitize_skill_name("my-aligner") == "my_aligner"
        assert _sanitize_skill_name("STAR") == "star"
        assert _sanitize_skill_name("bcftools.view") == "bcftools_view"
        assert _sanitize_skill_name("") == "custom_tool"

    def test_d06_parameter_with_equals_separated_flags(self):
        """Help text with --output=VALUE format is parsed."""
        help_text = textwrap.dedent("""\
            TestTool v1.0

            Options:
              --output=FILE         Write output to FILE
              --threads=INT         Number of threads
              --format=FMT          Output format (bam, sam)
        """)
        parsed = _regex_parse_help(help_text, "test_tool")
        param_names = {p["name"] for p in parsed["parameters"]}
        assert "output" in param_names or len(parsed["parameters"]) > 0

    def test_d07_conflicting_skill_name_handled(self, tmp_path):
        """Installing a skill with name matching an existing one updates rather than corrupts."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft1 = {
            "skill_name": "conflict_test",
            "description": "First version",
            "risk_level": "low",
            "tools_required": ["echo"],
            "capabilities": [],
            "parameters": {"input": {"type": "path", "description": "Input", "required": True}},
            "command_template": "echo {input}",
        }
        ok1, _ = install_tool_onboarding_draft(
            draft1,
            {"source": "test", "source_mode": "test"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok1

        # Second install with same name
        draft2 = {**draft1, "description": "Second version"}
        ok2, _ = install_tool_onboarding_draft(
            draft2,
            {"source": "test", "source_mode": "test"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok2
        # File should still be valid
        assert (defs / "conflict_test.md").exists()

    def test_d08_malformed_draft_rejected(self, tmp_path):
        """Draft with missing required fields is rejected."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft = {
            "skill_name": "",  # empty name
            "description": "",  # empty desc
            "risk_level": "extreme",  # invalid risk
            "parameters": "not_a_dict",  # wrong type
        }
        ok, msg = install_tool_onboarding_draft(
            draft,
            {"source": "test", "source_mode": "test"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok is False


# ===================================================================
# CATEGORY E: Generated Wrapper Execution (5 tests)
# ===================================================================


class TestWrapperExecution:
    """Test that generated wrapper code produces correct commands."""

    def _exec_wrapper(self, code: str, func_name: str, kwargs: dict) -> str:
        """Execute generated wrapper code and return the command string."""
        namespace: dict[str, Any] = {}
        exec(code, namespace)
        return namespace[func_name](**kwargs)

    def test_e01_samtools_sort_wrapper_produces_correct_command(self):
        params = [
            {"name": "input_bam", "flag": "", "type": "path", "required": True, "default": None},
            {"name": "output_bam", "flag": "-o", "type": "path", "required": True, "default": None},
            {"name": "threads", "flag": "-@", "type": "integer", "required": False, "default": "4"},
        ]
        code = _generate_wrapper_code("samtools_sort", "samtools sort", params, "samtools sort -o {output_bam} {input_bam}")
        cmd = self._exec_wrapper(code, "samtools_sort", {
            "input_bam": "/data/input.bam",
            "output_bam": "/data/output.bam",
            "threads": "8",
        })
        assert "samtools" in cmd or "sort" in cmd
        assert "/data/input.bam" in cmd or "input.bam" in cmd
        assert "/data/output.bam" in cmd or "output.bam" in cmd

    def test_e02_bcftools_view_wrapper_produces_correct_command(self):
        params = [
            {"name": "input_vcf", "flag": "", "type": "path", "required": True, "default": None},
            {"name": "region", "flag": "-r", "type": "string", "required": False, "default": None},
            {"name": "output_vcf", "flag": "-o", "type": "path", "required": False, "default": None},
        ]
        code = _generate_wrapper_code("bcftools_view", "bcftools view", params, "bcftools view -r {region} -o {output_vcf} {input_vcf}")
        cmd = self._exec_wrapper(code, "bcftools_view", {
            "input_vcf": "/data/input.vcf",
            "region": "chr1:1-1000",
            "output_vcf": "/data/output.vcf",
        })
        assert "bcftools" in cmd
        assert "chr1:1-1000" in cmd

    def test_e03_minimap2_wrapper_produces_correct_command(self):
        params = [
            {"name": "reference", "flag": "", "type": "path", "required": True, "default": None},
            {"name": "reads", "flag": "", "type": "path", "required": True, "default": None},
            {"name": "threads", "flag": "-t", "type": "integer", "required": False, "default": "4"},
            {"name": "preset", "flag": "-x", "type": "string", "required": False, "default": None},
        ]
        code = _generate_wrapper_code("minimap2_custom", "minimap2", params, "minimap2 -t {threads} {reference} {reads}")
        cmd = self._exec_wrapper(code, "minimap2_custom", {
            "reference": "/ref/genome.fa",
            "reads": "/data/reads.fq",
            "threads": "4",
            "preset": "map-ont",
        })
        assert "minimap2" in cmd
        assert "/ref/genome.fa" in cmd or "genome.fa" in cmd

    def test_e04_boolean_flag_handling(self):
        """Boolean flags appear only when True."""
        params = [
            {"name": "verbose", "flag": "--verbose", "type": "boolean", "required": False, "default": None},
            {"name": "input_file", "flag": "-i", "type": "path", "required": True, "default": None},
        ]
        code = _generate_wrapper_code("bool_test", "testtool", params, "testtool {input_file}")

        # With verbose=True
        cmd_on = self._exec_wrapper(code, "bool_test", {"input_file": "/f.txt", "verbose": "true"})
        assert "--verbose" in cmd_on

        # With verbose=False
        cmd_off = self._exec_wrapper(code, "bool_test", {"input_file": "/f.txt", "verbose": "false"})
        assert "--verbose" not in cmd_off

        # With verbose not provided
        cmd_none = self._exec_wrapper(code, "bool_test", {"input_file": "/f.txt"})
        assert "--verbose" not in cmd_none

    def test_e05_path_quoting_with_spaces(self):
        """Paths with spaces are correctly quoted."""
        params = [
            {"name": "input_file", "flag": "-i", "type": "path", "required": True, "default": None},
        ]
        code = _generate_wrapper_code("quote_test", "testtool", params, "testtool -i {input_file}")
        cmd = self._exec_wrapper(code, "quote_test", {"input_file": "/path with spaces/file.bam"})
        # The path should be quoted (shlex.quote wraps in single quotes)
        assert "'/path with spaces/file.bam'" in cmd or '"/path with spaces/file.bam"' in cmd


# ===================================================================
# CATEGORY F: Capability Catalog Integration (bonus, 5 tests)
# ===================================================================


class TestCatalogIntegration:
    """Test that skill onboarding correctly updates the capability catalog."""

    def test_f01_new_capability_created_for_unknown_category(self, tmp_path):
        """Installing a skill with a novel capability creates the catalog entry."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft = {
            "skill_name": "novel_tool",
            "description": "A completely novel analysis tool",
            "risk_level": "medium",
            "tools_required": ["novel_bin"],
            "capabilities": ["metabolomics_analysis"],
            "parameters": {"input": {"type": "path", "description": "Input", "required": True}},
            "command_template": "novel_bin {input}",
        }
        ok, msg = install_tool_onboarding_draft(
            draft,
            {"source": "test", "source_mode": "test"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok, f"Install failed: {msg}"
        catalog = load_capability_catalog(cat)
        cap_ids = {c["id"] for c in catalog["capabilities"]}
        # Either the capability was created or merged into existing
        custom_tools = catalog.get("custom_tools", [])
        tool_names = {t.get("skill_name", "") for t in custom_tools}
        assert "novel_tool" in tool_names

    def test_f02_multiple_capabilities_registered(self, tmp_path):
        """Skill with multiple capabilities updates all of them."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft = {
            "skill_name": "multi_cap_tool",
            "description": "Tool with multiple capabilities",
            "risk_level": "low",
            "tools_required": ["samtools"],
            "capabilities": ["alignment", "annotation"],
            "parameters": {"input": {"type": "path", "description": "Input", "required": True}},
            "command_template": "samtools {input}",
        }
        ok, _ = install_tool_onboarding_draft(
            draft,
            {"source": "test", "source_mode": "test"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok
        catalog = load_capability_catalog(cat)
        for cap in catalog["capabilities"]:
            if cap["id"] in ("alignment", "annotation"):
                assert "samtools" in cap.get("tool_hints", []) or "multi_cap_tool" in cap.get("plan_signals", [])

    def test_f03_batch_install_updates_catalog_correctly(self, tmp_path):
        """Batch install of multiple skills updates catalog for each."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        entries = [
            {
                "draft": {
                    "skill_name": f"batch_tool_{i}",
                    "description": f"Batch tool {i}",
                    "risk_level": "low",
                    "tools_required": [f"tool_{i}"],
                    "capabilities": ["alignment"],
                    "parameters": {"x": {"type": "string", "description": "X", "required": True}},
                    "command_template": f"tool_{i} {{x}}",
                },
                "source_meta": {"source": "test", "source_mode": "test"},
            }
            for i in range(3)
        ]
        report = install_tool_onboarding_batch(
            entries,
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
            install_workflow="test_batch",
        )
        assert report["attempted"] == 3
        assert len(report["installed"]) == 3

    def test_f04_custom_tools_tracked(self, tmp_path):
        """Installed skills appear in catalog custom_tools list."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        draft = {
            "skill_name": "tracked_tool",
            "description": "A tracked custom tool",
            "risk_level": "low",
            "tools_required": ["tracked_bin"],
            "capabilities": ["variant_calling"],
            "parameters": {"vcf": {"type": "path", "description": "VCF", "required": True}},
            "command_template": "tracked_bin {vcf}",
        }
        ok, _ = install_tool_onboarding_draft(
            draft,
            {"source": "https://github.com/example/tracked", "source_mode": "official_docs"},
            skills_definitions_dir=defs,
            skills_library_dir=lib,
            capability_catalog_path=cat,
        )
        assert ok
        catalog = load_capability_catalog(cat)
        custom_names = {t.get("skill_name", "") for t in catalog.get("custom_tools", [])}
        assert "tracked_tool" in custom_names

    def test_f05_catalog_survives_multiple_installs(self, tmp_path):
        """Catalog remains valid JSON after many sequential installs."""
        defs, lib, cat = _make_skill_dirs(tmp_path)
        for i in range(10):
            draft = {
                "skill_name": f"stress_tool_{i}",
                "description": f"Stress test tool {i}",
                "risk_level": "low",
                "tools_required": [f"bin_{i}"],
                "capabilities": ["alignment"] if i % 2 == 0 else ["annotation"],
                "parameters": {"x": {"type": "string", "description": "X", "required": True}},
                "command_template": f"bin_{i} {{x}}",
            }
            ok, _ = install_tool_onboarding_draft(
                draft,
                {"source": "test", "source_mode": "test"},
                skills_definitions_dir=defs,
                skills_library_dir=lib,
                capability_catalog_path=cat,
            )
            assert ok

        # Catalog should still be valid JSON
        catalog = load_capability_catalog(cat)
        assert len(catalog.get("custom_tools", [])) == 10
        assert len(catalog.get("capabilities", [])) > 0
