"""Tests for bio_harness.core.skill_generator."""
from __future__ import annotations

import ast

from bio_harness.core.skill_generator import (
    _extract_json_from_response,
    _generate_wrapper_code,
    _regex_parse_help,
    _sanitize_skill_name,
    generate_skill_draft,
    parse_help_text,
    validate_skill,
)


# ---------------------------------------------------------------------------
# _sanitize_skill_name
# ---------------------------------------------------------------------------

class TestSanitizeSkillName:
    def test_simple(self):
        assert _sanitize_skill_name("bwa") == "bwa"

    def test_hyphenated(self):
        assert _sanitize_skill_name("bwa-mem2") == "bwa_mem2"

    def test_dots(self):
        assert _sanitize_skill_name("samtools.1.0") == "samtools_1_0"

    def test_empty(self):
        assert _sanitize_skill_name("") == "custom_tool"

    def test_uppercase(self):
        assert _sanitize_skill_name("STAR") == "star"


# ---------------------------------------------------------------------------
# _extract_json_from_response
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_json(self):
        text = '{"description": "test", "parameters": []}'
        result = _extract_json_from_response(text)
        assert result["description"] == "test"

    def test_json_in_code_block(self):
        text = "```json\n{\"description\": \"test\", \"parameters\": []}\n```"
        result = _extract_json_from_response(text)
        assert result["description"] == "test"

    def test_json_with_prefix(self):
        text = "Here is the result:\n{\"description\": \"test\", \"parameters\": []}\nDone."
        result = _extract_json_from_response(text)
        assert result["description"] == "test"

    def test_invalid_json(self):
        result = _extract_json_from_response("not json at all")
        assert result is None


# ---------------------------------------------------------------------------
# _regex_parse_help
# ---------------------------------------------------------------------------

class TestRegexParseHelp:
    def test_basic_flags(self):
        help_text = """\
bwa-mem2 - fast short-read alignment

Usage: bwa-mem2 mem [options] <idxbase> <in1.fq> [in2.fq]

Options:
  -t INT        number of threads [1]
  -o FILE       output SAM file name
  -R STR        read group header line
  -M             mark shorter split hits as secondary
"""
        result = _regex_parse_help(help_text, "bwa-mem2")
        assert "bwa-mem2" in result["description"].lower() or "fast" in result["description"].lower()
        params = result["parameters"]
        assert len(params) >= 2
        names = [p["name"] for p in params]
        assert "t" in names or "o" in names

    def test_long_flags(self):
        help_text = """\
samtools sort - sort BAM files

  --threads INT     number of sorting threads
  --output FILE     output file path
  --reference FILE  reference genome FASTA
"""
        result = _regex_parse_help(help_text, "samtools")
        params = result["parameters"]
        assert len(params) >= 2
        # Check that path types are detected
        path_params = [p for p in params if p["type"] == "path"]
        assert len(path_params) >= 1

    def test_empty_help(self):
        result = _regex_parse_help("", "unknown_tool")
        assert result["description"]  # should have a fallback description
        assert result["parameters"] == []


# ---------------------------------------------------------------------------
# parse_help_text (without LLM)
# ---------------------------------------------------------------------------

class TestParseHelpText:
    def test_regex_fallback(self):
        help_text = """\
salmon quant - quantify transcript abundance

Options:
  -i, --index DIR       salmon index directory
  -l, --libType STR     library type
  -1, --mates1 FILE     read 1 FASTQ file
  -2, --mates2 FILE     read 2 FASTQ file
  -o, --output DIR      output directory
  -p, --threads INT     number of threads
"""
        result = parse_help_text(help_text, "salmon", llm=None)
        assert result["parameters"]  # should have params
        assert len(result["parameters"]) >= 4


# ---------------------------------------------------------------------------
# generate_skill_draft
# ---------------------------------------------------------------------------

class TestGenerateSkillDraft:
    def test_basic_draft(self):
        parsed = {
            "description": "Align reads with BWA",
            "when_to_use": "Use for short reads",
            "when_not_to_use": "Not for long reads",
            "input_types": ["fastq", "fasta_reference"],
            "output_types": ["bam"],
            "analysis_categories": ["alignment"],
            "parameters": [
                {"name": "reference", "flag": "-r", "type": "path", "description": "Reference FASTA", "required": True, "default": None, "file_role": "reference_genome"},
                {"name": "input_fastq", "flag": "-i", "type": "path", "description": "Input FASTQ", "required": True, "default": None, "file_role": "input_fastq_r1"},
                {"name": "output_bam", "flag": "-o", "type": "path", "description": "Output BAM", "required": True, "default": None, "file_role": "output_dir"},
                {"name": "threads", "flag": "-t", "type": "integer", "description": "Thread count", "required": False, "default": "4", "file_role": None},
            ],
            "command_template": "bwa mem -r {reference} -i {input_fastq} -o {output_bam}",
        }
        draft = generate_skill_draft(parsed, "bwa")
        assert draft["name"] == "bwa"
        assert draft["description"] == "Align reads with BWA"
        assert "reference" in draft["parameters"]
        assert draft["parameters"]["reference"]["file_role"] == "reference_genome"
        assert draft["input_types"] == ["fastq", "fasta_reference"]
        assert draft["output_types"] == ["bam"]

    def test_wrapper_code_compiles(self):
        parsed = {
            "description": "Test tool",
            "parameters": [
                {"name": "input_file", "flag": "-i", "type": "path", "description": "Input", "required": True, "default": None, "file_role": None},
            ],
            "command_template": "test_tool -i {input_file}",
        }
        draft = generate_skill_draft(parsed, "test_tool")
        # Wrapper code should compile without errors
        ast.parse(draft["wrapper_code"])

    def test_draft_has_all_fields(self):
        parsed = {
            "description": "Test",
            "parameters": [],
            "command_template": "test",
        }
        draft = generate_skill_draft(parsed, "test")
        required_keys = {"name", "description", "risk_level", "tools_required", "parameters",
                         "system_requirements", "command_template", "wrapper_code"}
        assert required_keys.issubset(set(draft.keys()))


# ---------------------------------------------------------------------------
# validate_skill
# ---------------------------------------------------------------------------

class TestValidateSkill:
    def test_valid_draft(self):
        draft = {
            "name": "test_tool",
            "description": "A test tool",
            "parameters": {"input": {"type": "path", "required": True}},
            "wrapper_code": "def test_tool(**kwargs): return 'test'",
        }
        valid, error = validate_skill(draft)
        assert valid
        assert error == ""

    def test_missing_name(self):
        draft = {
            "description": "A test",
            "parameters": {},
        }
        valid, error = validate_skill(draft)
        assert not valid
        assert "name" in error

    def test_invalid_syntax(self):
        draft = {
            "name": "test",
            "description": "Test",
            "parameters": {},
            "wrapper_code": "def broken(:\n  pass",
        }
        valid, error = validate_skill(draft)
        assert not valid
        assert "syntax" in error.lower()

    def test_bad_param_structure(self):
        draft = {
            "name": "test",
            "description": "Test",
            "parameters": {"bad_param": "not_a_dict"},
        }
        valid, error = validate_skill(draft)
        assert not valid
        assert "bad_param" in error


# ---------------------------------------------------------------------------
# _generate_wrapper_code
# ---------------------------------------------------------------------------

class TestGenerateWrapperCode:
    def test_compiles(self):
        code = _generate_wrapper_code(
            "my_tool", "my-tool",
            [
                {"name": "input", "flag": "-i", "type": "path", "required": True, "default": None},
                {"name": "output", "flag": "-o", "type": "path", "required": True, "default": None},
                {"name": "verbose", "flag": "-v", "type": "boolean", "required": False, "default": None},
            ],
            "my-tool -i {input} -o {output}",
        )
        ast.parse(code)
        assert "def my_tool(**kwargs)" in code
        assert "shlex.quote" in code

    def test_has_command_override(self):
        code = _generate_wrapper_code(
            "simple_tool", "simple-tool", [], "simple-tool",
        )
        assert 'if "command" in kwargs' in code
