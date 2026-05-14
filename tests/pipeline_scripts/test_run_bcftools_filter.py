"""Tests for the ``run_bcftools_filter`` helper's header-driven validator."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bio_harness.pipeline_scripts.run_bcftools_filter import (
    EXIT_FILTER_EXPRESSION_INVALID,
    _parse_header_tags,
    _read_vcf_header_lines,
    extract_referenced_tags,
    repair_known_filter_expression_aliases,
    run_bcftools_filter,
    validate_filter_expression_against_header,
)


def _write_freebayes_like_vcf(path: Path) -> None:
    """Write a minimal freebayes-like VCF (no MQ tag declared)."""

    lines = [
        "##fileformat=VCFv4.2",
        '##INFO=<ID=AO,Number=A,Type=Integer,Description="Alternate allele observations">',
        '##INFO=<ID=RO,Number=1,Type=Integer,Description="Reference allele observations">',
        '##INFO=<ID=DP,Number=1,Type=Integer,Description="Total read depth">',
        (
            '##INFO=<ID=MQM,Number=A,Type=Float,Description="Mean mapping '
            'quality of observed alt alleles">'
        ),
        (
            '##INFO=<ID=MQMR,Number=1,Type=Float,Description="Mean mapping '
            'quality of observed reference alleles">'
        ),
        '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele frequency">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1",
        "chr1\t10\t.\tA\tG\t100\tPASS\tDP=25;AO=12;RO=13;MQM=40.5;MQMR=38.0;AF=0.48\tGT:DP\t0/1:25",
    ]
    path.write_text("\n".join(lines) + "\n")


def _write_vcf_without_info_depth(path: Path) -> None:
    """Write a minimal VCF that lacks the INFO/DP depth tag."""

    lines = [
        "##fileformat=VCFv4.2",
        '##INFO=<ID=AO,Number=A,Type=Integer,Description="Alternate allele observations">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1",
        "chr1\t10\t.\tA\tG\t100\tPASS\tAO=12\tGT\t0/1",
    ]
    path.write_text("\n".join(lines) + "\n")


def test_read_vcf_header_lines_stops_at_first_data_line(tmp_path: Path) -> None:
    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    header_lines = _read_vcf_header_lines(vcf)
    assert any(line.startswith("##INFO=<ID=MQM,") for line in header_lines)
    assert header_lines[-1].startswith("#CHROM")
    assert all(line.startswith("#") for line in header_lines)


def test_parse_header_tags_groups_info_and_format(tmp_path: Path) -> None:
    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    header_lines = _read_vcf_header_lines(vcf)
    tags = _parse_header_tags(header_lines)
    assert "MQM" in tags["INFO"]
    assert "MQ" not in tags["INFO"]
    assert "DP" in tags["INFO"]
    assert "DP" in tags["FORMAT"]
    assert "GT" in tags["FORMAT"]


def test_extract_referenced_tags_handles_mixed_scope_and_literals() -> None:
    tags = extract_referenced_tags("QUAL>30 && MQ>40 && DP>10 && INFO/AF>0.05 && FMT/GQ>20")
    # QUAL is a core column — not returned
    assert ("ANY", "QUAL") not in tags
    # Unqualified tags have ANY scope
    assert ("ANY", "MQ") in tags
    assert ("ANY", "DP") in tags
    # Explicit INFO/FORMAT scopes preserved
    assert ("INFO", "AF") in tags
    assert ("FORMAT", "GQ") in tags


def test_extract_referenced_tags_skips_bcftools_keywords() -> None:
    tags = extract_referenced_tags("FILTER='PASS' && TYPE='snp' && N_ALT=1")
    names = {t for _, t in tags}
    # FILTER, TYPE, N_ALT are core columns/functions — not tag references
    assert "FILTER" not in names
    assert "TYPE" not in names
    assert "N_ALT" not in names


def test_validate_filter_expression_flags_missing_mq_with_hint(tmp_path: Path) -> None:
    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    header_tags = _parse_header_tags(_read_vcf_header_lines(vcf))
    missing = validate_filter_expression_against_header("QUAL>30 && MQ>40 && DP>10", header_tags)
    tags_missing = [m["tag"] for m in missing]
    assert "MQ" in tags_missing
    assert "DP" not in tags_missing  # DP declared under INFO
    mq_record = next(m for m in missing if m["tag"] == "MQ")
    assert "MQM" in mq_record.get("hint", "")


def test_validate_filter_expression_accepts_fully_declared_expression(tmp_path: Path) -> None:
    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    header_tags = _parse_header_tags(_read_vcf_header_lines(vcf))
    missing = validate_filter_expression_against_header("QUAL>30 && MQM>40 && DP>10", header_tags)
    assert missing == []


def test_run_bcftools_filter_qualifies_single_sample_ambiguous_dp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    output = tmp_path / "out" / "filtered.vcf"

    class _FakeCompleted:
        returncode = 0

    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured_cmds.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_filter.subprocess.run",
        _fake_run,
    )

    rc = run_bcftools_filter(
        input_vcf=vcf,
        output_vcf=output,
        filter_expression="QUAL>30 && MQM>40 && DP>10",
        output_type="v",
    )

    assert rc == 0
    assert captured_cmds
    command = captured_cmds[0]
    assert command[command.index("-i") + 1] == "QUAL>30 && MQM>40 && INFO/DP>10"
    assert "qualified ambiguous filter tag DP as INFO/DP" in capsys.readouterr().err


def test_repair_known_filter_expression_aliases_rewrites_qd_for_freebayes_depth(
    tmp_path: Path,
) -> None:
    """Missing GATK QD can be represented from FreeBayes QUAL and INFO/DP."""

    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    header_tags = _parse_header_tags(_read_vcf_header_lines(vcf))

    repaired, repairs = repair_known_filter_expression_aliases(
        "QD >= 2.0 && QUAL >= 30 && DP >= 5",
        header_tags,
    )

    assert repaired == "(QUAL / INFO/DP) >= 2.0 && QUAL >= 30 && DP >= 5"
    assert repairs == [
        {
            "tag": "QD",
            "from": "QD",
            "to": "QUAL / INFO/DP",
            "reason": "freebayes_missing_gatk_qual_by_depth",
        }
    ]
    assert validate_filter_expression_against_header(repaired, header_tags) == []


def test_run_bcftools_filter_rewrites_qd_before_invoking_bcftools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fresh mini evolution preflight: Qwen emitted GATK QD for FreeBayes VCFs."""

    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    output = tmp_path / "out" / "filtered.vcf"

    class _FakeCompleted:
        returncode = 0

    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured_cmds.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_filter.subprocess.run",
        _fake_run,
    )

    rc = run_bcftools_filter(
        input_vcf=vcf,
        output_vcf=output,
        filter_expression="QD >= 2.0 && QUAL >= 30 && DP >= 5",
        output_type="v",
    )

    assert rc == 0
    assert captured_cmds
    command = captured_cmds[0]
    assert command[command.index("-i") + 1] == (
        "(QUAL / INFO/DP) >= 2.0 && QUAL >= 30 && INFO/DP >= 5"
    )
    captured = capsys.readouterr()
    assert "rewrote filter tag QD as QUAL / INFO/DP" in captured.err
    assert "qualified ambiguous filter tag DP as INFO/DP" in captured.err


def test_run_bcftools_filter_keeps_missing_qd_strict_when_depth_absent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The QD repair is header-driven and does not fabricate missing depth."""

    vcf = tmp_path / "t.vcf"
    _write_vcf_without_info_depth(vcf)
    output = tmp_path / "out" / "filtered.vcf.gz"

    rc = run_bcftools_filter(
        input_vcf=vcf,
        output_vcf=output,
        filter_expression="QD >= 2.0 && QUAL >= 30",
        output_type="z",
    )

    assert rc == EXIT_FILTER_EXPRESSION_INVALID
    assert not output.exists()
    captured = capsys.readouterr()
    assert "missing: QD" in captured.err
    assert "QUAL/DP" in captured.err


def test_run_bcftools_filter_returns_validation_exit_code_on_missing_mq(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    output = tmp_path / "out" / "filtered.vcf.gz"
    rc = run_bcftools_filter(
        input_vcf=vcf,
        output_vcf=output,
        filter_expression="QUAL>30 && MQ>40 && DP>10",
        output_type="z",
    )
    assert rc == EXIT_FILTER_EXPRESSION_INVALID
    # Output file must not be created when validation fails
    assert not output.exists()
    captured = capsys.readouterr()
    assert "filter expression references tag(s) not declared" in captured.err
    # Structured diagnostic JSON for downstream repair consumers
    marker = "BCFTOOLS_FILTER_DIAGNOSTIC_JSON="
    assert marker in captured.err
    json_payload = captured.err.split(marker, 1)[1].splitlines()[0]
    payload = json.loads(json_payload)
    assert payload["failure_class"] == "filter_expression_tag_not_in_header"
    assert payload["tool"] == "bcftools_filter_run"
    assert any(entry["tag"] == "MQ" for entry in payload["missing_tags"])
    assert "MQM" in payload["available_info_tags"]


def test_run_bcftools_filter_returns_66_when_input_missing(tmp_path: Path) -> None:
    output = tmp_path / "out" / "filtered.vcf.gz"
    rc = run_bcftools_filter(
        input_vcf=tmp_path / "missing.vcf",
        output_vcf=output,
        filter_expression="QUAL>1",
    )
    assert rc == 66


def test_run_bcftools_filter_skip_header_validation_allows_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With validation skipped, we reach subprocess.run (which we stub)."""

    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    output = tmp_path / "out" / "filtered.vcf.gz"

    class _FakeCompleted:
        returncode = 0

    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured_cmds.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_filter.subprocess.run",
        _fake_run,
    )
    rc = run_bcftools_filter(
        input_vcf=vcf,
        output_vcf=output,
        filter_expression="QUAL>30 && MQ>40",
        skip_header_validation=True,
    )
    assert rc == 0
    assert captured_cmds, "subprocess.run should be invoked when validation is skipped"
    assert captured_cmds[0][0] == "bcftools"


def test_fix_27_auto_tabix_index_emitted_when_output_type_z(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix #27 producer-side: bgzipped output auto-indexes with tabix.

    Root cause: exp43 failed every ``bcftools_isec_run`` retry with
    "Could not retrieve index file for evol1.filtered.vcf.gz" because
    ``bcftools filter -Oz -o out.vcf.gz`` produces the compressed VCF but no
    ``.tbi``. Any downstream bcftools consumer (isec, merge, concat, view -r
    ...) then fails with exit 255. The producer-side fix is tool-agnostic:
    whenever the filter output is bgzipped, invoke ``tabix -p vcf -f`` on it
    so the file-plus-index pair is always coherent.

    Asserts: when ``output_type='z'``, tabix is invoked exactly once on the
    output path with the ``vcf`` preset and ``-f`` (force-overwrite) flag
    AFTER the main bcftools filter command succeeds.
    """

    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    output = tmp_path / "out" / "filtered.vcf.gz"

    class _FakeCompleted:
        def __init__(self, rc: int = 0) -> None:
            self.returncode = rc

    captured: list[list[str]] = []

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        return _FakeCompleted(0)

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_filter.subprocess.run",
        _fake_run,
    )
    rc = run_bcftools_filter(
        input_vcf=vcf,
        output_vcf=output,
        filter_expression="QUAL>30 && MQM>40 && DP>10",
        output_type="z",
    )
    assert rc == 0

    assert len(captured) == 2, (
        "Expected bcftools filter invocation followed by tabix indexing; "
        f"got {len(captured)} subprocess calls: {captured!r}"
    )
    assert captured[0][0] == "bcftools"
    assert captured[0][1] == "filter"

    tabix_cmd = captured[1]
    assert tabix_cmd[0] == "tabix"
    assert "-p" in tabix_cmd
    assert "vcf" in tabix_cmd
    assert "-f" in tabix_cmd  # force-overwrite any stale index
    assert str(output) in tabix_cmd


def test_fix_27_auto_tabix_skipped_for_plain_vcf_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix #27 producer-side: plain ``output_type='v'`` does NOT invoke tabix.

    Tabix is only meaningful for bgzipped/BCF outputs; indexing a plain
    VCF is not useful and would fail. The guard on ``output_type == "z"``
    ensures plain-VCF filter runs remain a single subprocess call.
    """

    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    output = tmp_path / "out" / "filtered.vcf"

    class _FakeCompleted:
        returncode = 0

    captured: list[list[str]] = []

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_filter.subprocess.run",
        _fake_run,
    )
    rc = run_bcftools_filter(
        input_vcf=vcf,
        output_vcf=output,
        filter_expression="QUAL>30 && MQM>40 && DP>10",
        output_type="v",
    )
    assert rc == 0
    assert len(captured) == 1, (
        "Plain VCF output should not auto-index; only bcftools filter "
        f"should be invoked. Got {len(captured)} calls: {captured!r}"
    )
    assert captured[0][0] == "bcftools"


def test_fix_27_auto_tabix_skipped_when_filter_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix #27 producer-side: failed filter does NOT invoke tabix.

    Indexing a partial/missing output would fail noisily and obscure the
    real failure. The returncode==0 guard short-circuits tabix when the
    primary filter command didn't succeed.
    """

    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    output = tmp_path / "out" / "filtered.vcf.gz"

    class _FakeCompleted:
        def __init__(self, rc: int) -> None:
            self.returncode = rc

    captured: list[list[str]] = []

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        # Make the bcftools filter call fail.
        return _FakeCompleted(1)

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_filter.subprocess.run",
        _fake_run,
    )
    rc = run_bcftools_filter(
        input_vcf=vcf,
        output_vcf=output,
        filter_expression="QUAL>30 && MQM>40 && DP>10",
        output_type="z",
    )
    assert rc == 1
    assert len(captured) == 1, (
        "Filter failure should not trigger auto-indexing; got "
        f"{len(captured)} subprocess calls: {captured!r}"
    )


def test_fix_27_auto_tabix_tolerates_missing_tabix_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix #27 producer-side: missing tabix binary does not fail the run.

    If the system doesn't have tabix installed (unusual, but possible on
    minimal CI runners), the filter should still return its own exit code
    rather than a confusing ``FileNotFoundError``.
    """

    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    output = tmp_path / "out" / "filtered.vcf.gz"

    class _FakeCompleted:
        returncode = 0

    calls: list[list[str]] = []

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        cmd_list = list(cmd)
        calls.append(cmd_list)
        if cmd_list[0] == "tabix":
            raise FileNotFoundError("tabix: command not found")
        return _FakeCompleted()

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_filter.subprocess.run",
        _fake_run,
    )
    rc = run_bcftools_filter(
        input_vcf=vcf,
        output_vcf=output,
        filter_expression="QUAL>30 && MQM>40 && DP>10",
        output_type="z",
    )
    # The primary filter call succeeded; the tabix FileNotFoundError must be
    # suppressed so the filter step's own exit code is preserved.
    assert rc == 0
    assert any(c[0] == "bcftools" for c in calls)
    assert any(c[0] == "tabix" for c in calls)


def test_main_cli_exits_with_validation_exit_code(tmp_path: Path) -> None:
    """Exercise the CLI entrypoint end-to-end via subprocess."""

    vcf = tmp_path / "t.vcf"
    _write_freebayes_like_vcf(vcf)
    output = tmp_path / "out" / "filtered.vcf.gz"
    script = (
        Path(__file__).resolve().parents[2]
        / "bio_harness"
        / "pipeline_scripts"
        / "run_bcftools_filter.py"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--input-vcf",
            str(vcf),
            "--output-vcf",
            str(output),
            "--filter-expression",
            "QUAL>30 && MQ>40 && DP>10",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == EXIT_FILTER_EXPRESSION_INVALID
    assert "BCFTOOLS_FILTER_DIAGNOSTIC_JSON=" in result.stderr
