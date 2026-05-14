"""Tests for bio_harness.core.tool_registry."""

from __future__ import annotations

import json
from pathlib import Path
import textwrap

from bio_harness.core.tool_registry import ToolMeta, ToolRegistry


# ---------------------------------------------------------------------------
# ToolMeta
# ---------------------------------------------------------------------------

class TestToolMeta:

    def test_defaults(self) -> None:
        meta = ToolMeta(name="test_tool")
        assert meta.name == "test_tool"
        assert meta.exec_hints == []
        assert meta.stall_grace == 0
        assert meta.is_heavy is False

    def test_as_dict(self) -> None:
        meta = ToolMeta(name="star_align", exec_hints=["star"], is_heavy=True, stall_grace=2700)
        d = meta.as_dict()
        assert d["name"] == "star_align"
        assert d["exec_hints"] == ["star"]
        assert d["is_heavy"] is True
        assert d["stall_grace"] == 2700


# ---------------------------------------------------------------------------
# ToolRegistry construction
# ---------------------------------------------------------------------------

class TestToolRegistryConstruction:

    def test_from_defaults_loads_constants(self) -> None:
        """Registry should include tools from constants.py."""
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        names = registry.known_tool_names()
        assert "star_align" in names
        assert "bwa_mem_align" in names
        assert "deseq2_run" in names

    def test_exec_hints_populated(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        meta = registry.get("star_align")
        assert meta is not None
        assert "star" in meta.exec_hints

    def test_stall_grace_populated(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        assert registry.stall_grace_for("star_align") == 2700
        assert registry.stall_grace_for("nonexistent") == 0

    def test_heavy_tools_populated(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        heavy = registry.heavy_tools()
        assert "star_align" in heavy
        assert "fastqc_run" in heavy

    def test_input_path_keys_populated(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        keys = registry.input_keys_for("bwa_mem_align")
        assert "reads_1" in keys
        assert "reference_fasta" in keys

    def test_buildable_required_path_is_not_treated_as_existing_input(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        keys = registry.input_keys_for("salmon_quant")
        assert "reads_1" in keys
        assert "reads_2" in keys
        assert "index_dir" not in keys
        assert registry.output_argument_keys_for("salmon_quant") == ["output_dir"]
        assert registry.buildable_path_keys_for("salmon_quant") == ["index_dir"]
        assert registry.expected_output_files_by_key_for("salmon_quant") == {
            "output_dir": ["quant.sf"]
        }
        schema = registry.parameter_schema_for("salmon_quant")
        assert schema["index_dir"].file_role == "buildable_index"

    def test_star_align_exposes_buildable_genome_dir_and_optional_build_inputs(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        assert registry.buildable_path_keys_for("star_align") == ["genome_dir"]
        schema = registry.parameter_schema_for("star_align")
        assert schema["genome_dir"].file_role == "buildable_genome_index"
        assert "reference_fasta" in schema
        assert "annotation_gtf" in schema
        assert registry.expected_output_files_by_key_for("star_align") == {
            "output_prefix": ["Aligned.out.bam", "Aligned.sortedByCoord.out.bam"]
        }

    def test_subread_align_exposes_buildable_index_base(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        assert "index_base" not in registry.input_keys_for("subread_align")
        assert registry.buildable_path_keys_for("subread_align") == ["index_base"]
        schema = registry.parameter_schema_for("subread_align")
        assert schema["index_base"].file_role == "buildable_index"

    def test_spades_assemble_registers_contigs_and_scaffolds_outputs(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        assert registry.expected_output_files_by_key_for("spades_assemble") == {
            "output_dir": ["contigs.fasta", "scaffolds.fasta"]
        }

    def test_deseq2_run_exposes_harness_managed_script_path(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        schema = registry.parameter_schema_for("deseq2_run")
        assert schema["script_path"].ownership == "harness_managed"
        assert "script_path" in registry.harness_managed_parameters_for("deseq2_run")
        assert "script_path" not in registry.required_parameters_for("deseq2_run")
        assert schema["counts_matrix"].file_role == "counts_matrix"
        assert "output_dir" in registry.execution_output_parameters_for("deseq2_run")

    def test_stringtie_quant_exposes_canonical_output_filenames(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        assert registry.primary_output_parameter_for("stringtie_quant") == "output_gtf"
        assert registry.canonical_output_filenames_for("stringtie_quant") == {
            "output_gtf": "assembled.gtf",
            "gene_abundance_tsv": "gene_abundances.tsv",
        }

    def test_stringtie_quant_classifies_reference_inputs_separately_from_outputs(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        assert registry.input_keys_for("stringtie_quant") == [
            "annotation_gtf",
            "input_bam",
        ]
        assert registry.output_argument_keys_for("stringtie_quant") == [
            "ballgown_dir",
            "gene_abundance_tsv",
            "output_gtf",
        ]
        assert registry.buildable_path_keys_for("stringtie_quant") == []

    def test_rmats_run_classifies_annotation_gtf_as_input_not_output(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        assert "annotation_gtf" in registry.input_keys_for("rmats_run")
        assert "annotation_gtf" not in registry.output_argument_keys_for("rmats_run")
        assert "output_dir" in registry.output_argument_keys_for("rmats_run")

    def test_snpeff_optional_custom_reference_paths_are_checked_inputs(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )

        input_keys = registry.input_keys_for("snpeff_annotate")
        assert "input_vcf" in input_keys
        assert "reference_fasta" in input_keys
        assert "annotation_gff" in input_keys
        assert "config_dir" not in input_keys
        assert registry.output_argument_keys_for("snpeff_annotate") == ["output_vcf"]

    def test_report_bundle_tools_keep_run_input_as_existing_input(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )

        assert "run_input" in registry.input_keys_for("multiqc_report")
        assert "run_input" not in registry.output_argument_keys_for("multiqc_report")
        assert "output_dir" in registry.output_argument_keys_for("multiqc_report")

        assert "run_input" in registry.input_keys_for("quarto_report")
        assert "run_input" not in registry.output_argument_keys_for("quarto_report")
        assert "output_dir" in registry.output_argument_keys_for("quarto_report")

    def test_markdown_definition_parameters_override_stale_index_metadata(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "repo"
        skills_dir = repo_root / "bio_harness" / "skills" / "definitions"
        skills_dir.mkdir(parents=True)
        definition = skills_dir / "flye_assemble.md"
        definition.write_text(
            textwrap.dedent(
                """\
                ---
                name: flye_assemble
                description: Assemble long reads with Flye.
                risk_level: high
                tools_required:
                - flye
                parameters:
                  reads_fastq:
                    type: path
                    required: true
                  read_mode:
                    type: string
                    required: false
                  meta_mode:
                    type: boolean
                    required: false
                ---
                """
            ),
            encoding="utf-8",
        )
        index = skills_dir / "index.json"
        index.write_text(
            json.dumps(
                {
                    "version": 1,
                    "skills": [
                        {
                            "name": "flye_assemble",
                            "description": "Assemble long reads with Flye.",
                            "parameters": {
                                "reads_fastq": {
                                    "type": "path",
                                    "required": True,
                                }
                            },
                            "file_path": "bio_harness/skills/definitions/flye_assemble.md",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=index,
        )

        schema = registry.parameter_schema_for("flye_assemble")
        assert "reads_fastq" in schema
        assert "read_mode" in schema
        assert "meta_mode" in schema

    def test_atomic_variant_wrappers_register_stage_metadata(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )

        assert registry.get("bcftools_filter_run") is not None
        assert registry.get("bcftools_filter_run").consumes_stages == ["raw"]
        assert registry.get("bcftools_filter_run").produces_stages == ["filtered"]
        assert registry.get("bcftools_norm_run") is not None
        assert registry.get("bcftools_norm_run").produces_stages == ["normalized"]
        assert registry.get("shared_variants_export_run") is not None
        assert registry.get("shared_variants_export_run").produces_stages == ["shared"]
        assert registry.get("tabix_index_run") is not None
        assert registry.get("tabix_index_run").produces_stages == ["indexed"]


# ---------------------------------------------------------------------------
# Signal equivalences
# ---------------------------------------------------------------------------

class TestSignalEquivalences:

    def test_signal_equivalences_merged(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={
                "freebayes": ["freebayes", "freebayes_call"],
                "star": ["star", "STAR", "star_align"],
            },
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        meta = registry.get("star_align")
        assert meta is not None
        assert "star" in meta.signal_equivalences


# ---------------------------------------------------------------------------
# Parameter knowledge base
# ---------------------------------------------------------------------------

class TestParameterKnowledgeBase:

    def test_parameter_defaults_merged(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={
                "freebayes_call": {"ploidy": 1},
                "salmon_quant": {"library_type": "A"},
            },
            skill_index_path=None,
        )
        defaults = registry.parameter_defaults_for("freebayes_call")
        assert defaults["ploidy"] == 1
        defaults = registry.parameter_defaults_for("salmon_quant")
        assert defaults["library_type"] == "A"
        assert registry.parameter_defaults_for("nonexistent") == {}


# ---------------------------------------------------------------------------
# Skill index loading
# ---------------------------------------------------------------------------

class TestSkillIndexLoading:

    def test_loads_from_skill_index(self, tmp_path: Path) -> None:
        index = {
            "version": 1,
            "skills_count": 2,
            "skills": [
                {
                    "name": "custom_tool",
                    "description": "A custom test tool.",
                    "capabilities": ["alignment"],
                    "tools_required": ["custom_bin"],
                },
                {
                    "name": "star_align",
                    "description": "STAR aligner.",
                    "capabilities": ["alignment"],
                },
            ],
        }
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index), encoding="utf-8")

        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=index_path,
        )
        meta = registry.get("custom_tool")
        assert meta is not None
        assert meta.description == "A custom test tool."
        assert meta.capabilities == ["alignment"]
        # Backfill exec_hints from tools_required.
        assert meta.exec_hints == ["custom_bin"]

        # Star already has exec_hints from constants; should not be overwritten.
        star = registry.get("star_align")
        assert star is not None
        assert star.exec_hints == ["star"]  # from constants, not overwritten
        assert "alignment" in star.capabilities

    def test_missing_index_file_no_error(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=Path("/nonexistent/index.json"),
        )
        assert len(registry.known_tool_names()) > 0  # still has constants-based tools


# ---------------------------------------------------------------------------
# Bulk queries
# ---------------------------------------------------------------------------

class TestBulkQueries:

    def test_all_input_path_keys(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        all_keys = registry.all_input_path_keys()
        assert "bwa_mem_align" in all_keys
        assert "reads_1" in all_keys["bwa_mem_align"]

    def test_tools_with_exec_hints(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        hints = registry.tools_with_exec_hints()
        assert "star_align" in hints
        assert hints["star_align"] == ["star"]

    def test_get_unknown_returns_none(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        assert registry.get("totally_unknown_tool_xyz") is None

    def test_fix_26_output_type_string_param_not_classified_as_output_path(self) -> None:
        """Fix #26: ``output_type`` (a string flag) MUST NOT be in the registry's
        ``output_argument_keys`` for ``bcftools_filter_run``.

        Root cause addressed: before Fix #26, ``_is_output_parameter`` used a
        name-based heuristic that flagged any name starting with ``output``
        as an output parameter — ignoring the declared ``"type": "string"``.
        Similarly ``_is_path_parameter`` flagged any name starting with
        ``output_`` as a path parameter. Together, the ``bcftools_filter_run``
        wrapper's ``output_type`` (which emits a bcftools encoding flag
        ``z``/``v``/``b``) was mistakenly added to the registry's
        ``output_argument_keys``. Downstream path-rewriting passes
        (``_looks_like_path_argument_key`` in ``bio_harness/harness/
        path_utils.py``) then treated the literal string ``"z"`` as a path
        and resolved it against ``selected_dir`` → ``/selected/z``, which
        crashed the wrapper with "output_type must be one of: b, v, z"
        (exp42 stalled at turn 7 on this).

        This regression asserts the registry classifies the parameter
        correctly for every wrapper that declares a scalar output parameter.
        """

        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        output_keys = registry.output_argument_keys_for("bcftools_filter_run")
        assert "output_type" not in output_keys, (
            f"Fix #26 regression: output_type should NOT be in "
            f"output_argument_keys for bcftools_filter_run "
            f"(got: {sorted(output_keys)})"
        )
        # The real filesystem output should still be classified as such.
        assert "output_vcf" in output_keys

    def test_fix_26_scalar_type_overrides_name_heuristic(self) -> None:
        """Fix #26: ``_is_output_parameter`` / ``_is_path_parameter`` should
        honour an explicit ``"type": "string"`` declaration and short-circuit
        the name-based fallback. Any skill parameter whose name happens to
        start with ``output_`` or ``input_`` but which is declared as a
        scalar (``string`` / ``number`` / ``boolean`` / ...) should NOT be
        treated as an output or path key."""

        from bio_harness.core.tool_registry import (
            _is_output_parameter,
            _is_path_parameter,
        )

        # Before Fix #26 both returned True because the name starts with
        # ``output_``. After Fix #26 the ``type: string`` declaration wins.
        assert _is_output_parameter("output_type", {"type": "string"}) is False
        assert _is_path_parameter("output_type", {"type": "string"}) is False

        # Coverage for other common scalar types — all should short-circuit.
        for scalar_type in ("string", "str", "number", "integer", "int", "float", "boolean", "bool"):
            assert _is_path_parameter("output_threshold", {"type": scalar_type}) is False, (
                f"{scalar_type!r} should short-circuit path classification"
            )
            assert _is_output_parameter("output_threshold", {"type": scalar_type}) is False, (
                f"{scalar_type!r} should short-circuit output classification"
            )

        # Sanity check: real path parameters are still classified as such.
        assert _is_path_parameter("output_vcf", {"type": "path"}) is True
        assert _is_output_parameter("output_vcf", {"type": "path"}) is True
        # Name-based fallback still fires when no type is declared (backward
        # compatibility for skill specs that omit the type field).
        assert _is_path_parameter("output_vcf", {}) is True
        assert _is_output_parameter("output_vcf", {}) is True
