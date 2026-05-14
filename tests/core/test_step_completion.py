"""Tests for the step completion manifest system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bio_harness.core.step_completion import (
    MANIFEST_FILENAME,
    CompletionCheck,
    check_completion_manifest,
    find_completion_manifest,
    resolved_step_outputs_for_completion,
    write_completion_manifest,
)


class TestWriteCompletionManifest:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        path = write_completion_manifest(
            tmp_path, tool_name="star_align", outputs=["out.bam"], exit_code=0
        )
        assert path == tmp_path / MANIFEST_FILENAME
        payload = json.loads(path.read_text())
        assert payload["tool_name"] == "star_align"
        assert payload["success"] is True
        assert payload["exit_code"] == 0
        assert payload["outputs"] == ["out.bam"]
        assert "completed_at" in payload

    def test_writes_failure_manifest(self, tmp_path: Path) -> None:
        path = write_completion_manifest(
            tmp_path,
            tool_name="bwa_mem",
            outputs=[],
            exit_code=1,
            success=False,
            error="segfault",
        )
        payload = json.loads(path.read_text())
        assert payload["success"] is False
        assert payload["exit_code"] == 1
        assert payload["error"] == "segfault"

    def test_creates_output_dir(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b"
        write_completion_manifest(nested, tool_name="t", outputs=[])
        assert (nested / MANIFEST_FILENAME).exists()

    def test_includes_metadata(self, tmp_path: Path) -> None:
        write_completion_manifest(
            tmp_path,
            tool_name="t",
            outputs=[],
            metadata={"reads": 42},
        )
        payload = json.loads((tmp_path / MANIFEST_FILENAME).read_text())
        assert payload["metadata"] == {"reads": 42}

    def test_outputs_stringified(self, tmp_path: Path) -> None:
        write_completion_manifest(
            tmp_path,
            tool_name="t",
            outputs=[Path("/a/b.bam"), "c.bam"],
        )
        payload = json.loads((tmp_path / MANIFEST_FILENAME).read_text())
        assert payload["outputs"] == ["/a/b.bam", "c.bam"]


class TestCheckCompletionManifest:
    def test_missing_manifest(self, tmp_path: Path) -> None:
        result = check_completion_manifest(tmp_path, "star_align")
        assert result.completed is False
        assert result.manifest_missing is True

    def test_valid_success_manifest(self, tmp_path: Path) -> None:
        write_completion_manifest(
            tmp_path, tool_name="star_align", outputs=["out.bam"]
        )
        result = check_completion_manifest(tmp_path, "star_align")
        assert result.completed is True
        assert result.manifest_missing is False
        assert result.outputs == ["out.bam"]
        assert result.exit_code == 0

    def test_valid_failure_manifest(self, tmp_path: Path) -> None:
        write_completion_manifest(
            tmp_path,
            tool_name="star_align",
            outputs=[],
            success=False,
            error="crashed",
        )
        result = check_completion_manifest(tmp_path, "star_align")
        assert result.completed is False
        assert result.manifest_missing is False
        assert result.error == "crashed"

    def test_tool_name_mismatch(self, tmp_path: Path) -> None:
        write_completion_manifest(
            tmp_path, tool_name="bwa_mem", outputs=["out.bam"]
        )
        result = check_completion_manifest(tmp_path, "star_align")
        assert result.completed is False
        assert "mismatch" in result.error.lower()

    def test_output_scoped_check_ignores_disjoint_tool_mismatch(self, tmp_path: Path) -> None:
        variants = tmp_path / "variants"
        raw_vcf = variants / "evol2.raw.vcf.gz"
        filtered_vcf = variants / "evol2.filtered.vcf.gz"
        write_completion_manifest(
            variants,
            tool_name="freebayes_call",
            outputs=[str(raw_vcf)],
        )

        result = check_completion_manifest(
            variants,
            "bcftools_filter_run",
            expected_outputs=[str(filtered_vcf)],
        )

        assert result.completed is False
        assert result.manifest_missing is True
        assert result.error == ""

    def test_output_scoped_check_preserves_overlap_tool_mismatch(self, tmp_path: Path) -> None:
        variants = tmp_path / "variants"
        filtered_vcf = variants / "evol2.filtered.vcf.gz"
        write_completion_manifest(
            variants,
            tool_name="freebayes_call",
            outputs=[str(filtered_vcf)],
        )

        result = check_completion_manifest(
            variants,
            "bcftools_filter_run",
            expected_outputs=[str(filtered_vcf)],
        )

        assert result.completed is False
        assert result.manifest_missing is False
        assert "mismatch" in result.error.lower()

    def test_output_scoped_check_ignores_same_tool_sibling_output(self, tmp_path: Path) -> None:
        variants = tmp_path / "variants"
        evol1_vcf = variants / "evol1.filtered.vcf.gz"
        evol2_vcf = variants / "evol2.filtered.vcf.gz"
        write_completion_manifest(
            variants,
            tool_name="bcftools_filter_run",
            outputs=[str(evol1_vcf)],
        )

        result = check_completion_manifest(
            variants,
            "bcftools_filter_run",
            expected_outputs=[str(evol2_vcf)],
        )

        assert result.completed is False
        assert result.manifest_missing is True

    def test_output_scoped_check_resolves_relative_manifest_outputs(
        self,
        tmp_path: Path,
    ) -> None:
        write_completion_manifest(
            tmp_path,
            tool_name="bcftools_filter_run",
            outputs=["evol2.filtered.vcf.gz"],
        )

        result = check_completion_manifest(
            tmp_path,
            "bcftools_filter_run",
            expected_outputs=[str(tmp_path / "evol2.filtered.vcf.gz")],
        )

        assert result.completed is True
        assert result.manifest_missing is False

    def test_corrupt_json(self, tmp_path: Path) -> None:
        (tmp_path / MANIFEST_FILENAME).write_text("not json {{{")
        result = check_completion_manifest(tmp_path, "star_align")
        assert result.completed is False
        assert result.manifest_missing is False
        assert result.error  # should have error message

    def test_non_dict_json(self, tmp_path: Path) -> None:
        (tmp_path / MANIFEST_FILENAME).write_text('"just a string"')
        result = check_completion_manifest(tmp_path, "star_align")
        assert result.completed is False
        assert "not a JSON object" in result.error

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        write_completion_manifest(
            tmp_path, tool_name="t", outputs=["x"]
        )
        result = check_completion_manifest(str(tmp_path), "t")
        assert result.completed is True


class TestFindCompletionManifest:
    def test_finds_in_output_dir(self, tmp_path: Path) -> None:
        out = tmp_path / "results"
        write_completion_manifest(out, tool_name="t", outputs=[])
        found = find_completion_manifest({"output_dir": str(out)})
        assert found is not None
        assert found.name == MANIFEST_FILENAME

    def test_finds_in_outdir_key(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        write_completion_manifest(out, tool_name="t", outputs=[])
        found = find_completion_manifest({"outdir": str(out)})
        assert found is not None

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        found = find_completion_manifest({"output_dir": str(tmp_path / "nope")})
        assert found is None

    def test_resolves_relative_with_cwd(self, tmp_path: Path) -> None:
        out = tmp_path / "rel"
        write_completion_manifest(out, tool_name="t", outputs=[])
        found = find_completion_manifest({"output_dir": "rel"}, cwd=tmp_path)
        assert found is not None

    def test_empty_arguments(self) -> None:
        assert find_completion_manifest({}) is None

    def test_ignores_stale_manifest_for_sibling_output(self, tmp_path: Path) -> None:
        alignments = tmp_path / "alignments"
        write_completion_manifest(
            alignments,
            tool_name="bwa_mem_align",
            outputs=[str(alignments / "evol1.bam")],
        )

        found = find_completion_manifest(
            {"output_bam": str(alignments / "evol2.bam")},
            tool_name="bwa_mem_align",
            cwd=tmp_path,
        )

        assert found is None

    def test_returns_manifest_when_outputs_match_current_step(self, tmp_path: Path) -> None:
        alignments = tmp_path / "alignments"
        output_bam = alignments / "evol2.bam"
        write_completion_manifest(
            alignments,
            tool_name="bwa_mem_align",
            outputs=[str(output_bam)],
        )

        found = find_completion_manifest(
            {"output_bam": str(output_bam)},
            tool_name="bwa_mem_align",
            cwd=tmp_path,
        )

        assert found == alignments / MANIFEST_FILENAME

    def test_ignores_stale_different_tool_manifest_for_shared_output_dir(self, tmp_path: Path) -> None:
        variants = tmp_path / "variants"
        write_completion_manifest(
            variants,
            tool_name="freebayes_call",
            outputs=[str(variants / "evol2.raw.vcf.gz")],
        )

        found = find_completion_manifest(
            {"output_vcf": str(variants / "evol2.filtered.vcf.gz")},
            tool_name="bcftools_filter_run",
            cwd=tmp_path,
        )

        assert found is None


class TestResolvedStepOutputsForCompletion:
    def test_resolves_output_bam_for_bwa_mem_align(self, tmp_path: Path) -> None:
        output_bam = tmp_path / "alignments" / "evol2.bam"

        outputs = resolved_step_outputs_for_completion(
            tool_name="bwa_mem_align",
            step_arguments={"output_bam": str(output_bam)},
            cwd=tmp_path,
        )

        assert outputs == [str(output_bam.resolve(strict=False))]


class TestCompletionCheckDefaults:
    def test_outputs_default_to_empty_list(self) -> None:
        check = CompletionCheck(completed=False, manifest_missing=True)
        assert check.outputs == []

    def test_error_default_empty_string(self) -> None:
        check = CompletionCheck(completed=True, manifest_missing=False)
        assert check.error == ""
