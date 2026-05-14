"""Tests for the ``run_bcftools_isec`` helper, including Fix #27 auto-indexing."""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_harness.pipeline_scripts.run_bcftools_isec import (
    build_bcftools_isec_command,
    run_bcftools_isec,
)


def test_build_bcftools_isec_command_intersection_defaults() -> None:
    cmd = build_bcftools_isec_command(
        input_vcfs=[Path("/tmp/a.vcf.gz"), Path("/tmp/b.vcf.gz")],
        output_dir=Path("/tmp/isec"),
        mode="intersection",
        min_matches=2,
    )
    assert cmd[0] == "bcftools"
    assert cmd[1] == "isec"
    assert "-p" in cmd
    assert "/tmp/isec" in cmd
    assert "-n" in cmd
    assert "+2" in cmd


def test_build_bcftools_isec_command_complement_uses_w1() -> None:
    cmd = build_bcftools_isec_command(
        input_vcfs=[Path("/tmp/a.vcf.gz"), Path("/tmp/b.vcf.gz")],
        output_dir=Path("/tmp/isec"),
        mode="complement",
    )
    # -C with -w1 writes only the private-to-first-input records.
    assert "-C" in cmd
    assert "-w1" in cmd


def test_build_bcftools_isec_command_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode must be one of"):
        build_bcftools_isec_command(
            input_vcfs=[Path("/tmp/a.vcf.gz"), Path("/tmp/b.vcf.gz")],
            output_dir=Path("/tmp/isec"),
            mode="diff",
        )


def test_build_bcftools_isec_command_requires_two_inputs() -> None:
    with pytest.raises(ValueError, match="at least two"):
        build_bcftools_isec_command(
            input_vcfs=[Path("/tmp/a.vcf.gz")],
            output_dir=Path("/tmp/isec"),
            mode="intersection",
        )


def test_run_bcftools_isec_materializes_named_output_vcf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper should expose a stable branch-named VCF for downstream steps."""

    a = tmp_path / "evol2.filtered.vcf.gz"
    b = tmp_path / "anc.filtered.vcf.gz"
    a.write_bytes(b"\x1f\x8b\x08")
    b.write_bytes(b"\x1f\x8b\x08")
    (tmp_path / "evol2.filtered.vcf.gz.tbi").write_bytes(b"\x00")
    (tmp_path / "anc.filtered.vcf.gz.tbi").write_bytes(b"\x00")
    output_dir = tmp_path / ".isec_evol2.ancestor_subtracted"
    output_vcf = tmp_path / "variants" / "evol2.ancestor_subtracted.vcf.gz"
    captured: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        del check
        parts = list(cmd)
        captured.append(parts)
        if parts[:2] == ["bcftools", "isec"]:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "0000.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
        elif parts[:2] == ["bcftools", "view"]:
            output_vcf.parent.mkdir(parents=True, exist_ok=True)
            output_vcf.write_bytes(b"\x1f\x8b\x08")
        elif parts and parts[0] == "tabix":
            Path(str(output_vcf) + ".tbi").write_bytes(b"\x00")
        return _FakeCompleted()

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_isec.subprocess.run",
        _fake_run,
    )

    rc = run_bcftools_isec(
        input_vcfs=[a, b],
        output_dir=output_dir,
        output_vcf=output_vcf,
        mode="complement",
    )

    assert rc == 0
    assert output_vcf.exists()
    assert Path(str(output_vcf) + ".tbi").exists()
    assert any(parts[:2] == ["bcftools", "isec"] for parts in captured)
    assert any(parts[:2] == ["bcftools", "view"] for parts in captured)


def test_fix_27_consumer_auto_indexes_bgzipped_inputs_without_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix #27 consumer-side: unindexed ``.vcf.gz`` inputs are auto-indexed.

    Root cause: exp43 emitted ``bcftools_filter_run`` outputs as ``.vcf.gz``
    with no ``.tbi``/``.csi`` alongside. Every subsequent ``bcftools isec``
    retry failed with "Could not retrieve index file" (exit 255). The
    producer-side fix in ``run_bcftools_filter`` handles outputs we emit
    ourselves; the consumer-side fix here handles inputs produced outside
    our toolchain (e.g. a planner-emitted ``bash_run`` using raw
    ``bcftools filter`` without ``--write-index``).

    The check is tool-agnostic: whenever isec sees a bgzipped/BCF input
    file that exists on disk but lacks a sibling ``.tbi``/``.csi`` index,
    it runs ``tabix -p vcf -f`` before handing the inputs to bcftools.
    """

    # Create two real bgzipped-looking files on disk (content is irrelevant
    # — we're stubbing subprocess — but ``Path.exists()`` is checked).
    a = tmp_path / "a.vcf.gz"
    b = tmp_path / "b.vcf.gz"
    a.write_bytes(b"\x1f\x8b\x08")  # minimal gzip magic bytes
    b.write_bytes(b"\x1f\x8b\x08")
    # No .tbi/.csi alongside — this is exactly the exp43 failure state.

    out_dir = tmp_path / "isec"

    class _FakeCompleted:
        returncode = 0

    captured: list[list[str]] = []

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_isec.subprocess.run",
        _fake_run,
    )

    rc = run_bcftools_isec(
        input_vcfs=[a, b],
        output_dir=out_dir,
        mode="complement",
    )
    assert rc == 0

    # Expect: tabix for a, tabix for b, then bcftools isec.
    tabix_calls = [c for c in captured if c and c[0] == "tabix"]
    bcftools_calls = [c for c in captured if c and c[0] == "bcftools"]

    assert len(tabix_calls) == 2, (
        f"Expected two tabix invocations (one per unindexed input). "
        f"Got {len(tabix_calls)} in {captured!r}"
    )
    assert any(str(a) in c for c in tabix_calls)
    assert any(str(b) in c for c in tabix_calls)
    # Each tabix call must use the VCF preset and force-overwrite.
    for c in tabix_calls:
        assert "-p" in c
        assert "vcf" in c
        assert "-f" in c

    assert len(bcftools_calls) == 1
    assert bcftools_calls[0][1] == "isec"


def test_fix_27_consumer_skips_tabix_when_tbi_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix #27: inputs with existing ``.tbi`` are not re-indexed."""

    a = tmp_path / "a.vcf.gz"
    b = tmp_path / "b.vcf.gz"
    a.write_bytes(b"\x1f\x8b\x08")
    b.write_bytes(b"\x1f\x8b\x08")
    # Pre-existing indexes: one .tbi, one .csi.
    (tmp_path / "a.vcf.gz.tbi").write_bytes(b"\x00")
    (tmp_path / "b.vcf.gz.csi").write_bytes(b"\x00")

    out_dir = tmp_path / "isec"

    class _FakeCompleted:
        returncode = 0

    captured: list[list[str]] = []

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_isec.subprocess.run",
        _fake_run,
    )

    rc = run_bcftools_isec(
        input_vcfs=[a, b],
        output_dir=out_dir,
        mode="intersection",
        min_matches=2,
    )
    assert rc == 0

    tabix_calls = [c for c in captured if c and c[0] == "tabix"]
    assert tabix_calls == [], (
        f"No tabix calls expected when indexes already exist; got "
        f"{tabix_calls!r}"
    )
    bcftools_calls = [c for c in captured if c and c[0] == "bcftools"]
    assert len(bcftools_calls) == 1


def test_fix_27_consumer_skips_tabix_for_plain_vcf_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix #27: plain (uncompressed) ``.vcf`` inputs are not tabix-indexed.

    Tabix requires a bgzipped or BCF file. Attempting to index a plain VCF
    would fail. The consumer guard only fires for ``.vcf.gz``/``.bcf`` —
    bcftools itself will accept plain VCF inputs without an index.
    """

    a = tmp_path / "a.vcf"
    b = tmp_path / "b.vcf"
    a.write_text("##fileformat=VCFv4.2\n")
    b.write_text("##fileformat=VCFv4.2\n")
    out_dir = tmp_path / "isec"

    class _FakeCompleted:
        returncode = 0

    captured: list[list[str]] = []

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_isec.subprocess.run",
        _fake_run,
    )

    rc = run_bcftools_isec(
        input_vcfs=[a, b],
        output_dir=out_dir,
        mode="complement",
    )
    assert rc == 0

    tabix_calls = [c for c in captured if c and c[0] == "tabix"]
    assert tabix_calls == [], (
        f"Plain VCF inputs should not be tabix-indexed; got {tabix_calls!r}"
    )


def test_fix_27_consumer_skips_tabix_when_input_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix #27: missing input files are left for bcftools' own error.

    We never try to tabix a file that doesn't exist on disk — that would
    mask the real "input missing" failure with a confusing tabix error.
    """

    a = tmp_path / "a.vcf.gz"
    a.write_bytes(b"\x1f\x8b\x08")
    b_missing = tmp_path / "missing.vcf.gz"  # intentionally not created
    out_dir = tmp_path / "isec"

    class _FakeCompleted:
        returncode = 2  # simulate bcftools' own error

    captured: list[list[str]] = []

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_isec.subprocess.run",
        _fake_run,
    )

    rc = run_bcftools_isec(
        input_vcfs=[a, b_missing],
        output_dir=out_dir,
        mode="complement",
    )
    assert rc == 2

    tabix_calls = [c for c in captured if c and c[0] == "tabix"]
    # Only the existing 'a' lacks an index — it should be indexed.
    # The missing 'b_missing' should NOT be tabix-attempted.
    assert len(tabix_calls) == 1
    assert str(a) in tabix_calls[0]
    assert not any(str(b_missing) in c for c in tabix_calls)


def test_fix_27_consumer_tolerates_missing_tabix_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix #27: FileNotFoundError from tabix does not crash the run."""

    a = tmp_path / "a.vcf.gz"
    a.write_bytes(b"\x1f\x8b\x08")
    b = tmp_path / "b.vcf.gz"
    b.write_bytes(b"\x1f\x8b\x08")
    out_dir = tmp_path / "isec"

    class _FakeCompleted:
        returncode = 0

    captured: list[list[str]] = []

    def _fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        if list(cmd)[0] == "tabix":
            raise FileNotFoundError("tabix: command not found")
        return _FakeCompleted()

    monkeypatch.setattr(
        "bio_harness.pipeline_scripts.run_bcftools_isec.subprocess.run",
        _fake_run,
    )

    rc = run_bcftools_isec(
        input_vcfs=[a, b],
        output_dir=out_dir,
        mode="complement",
    )
    # The tabix FileNotFoundError must be swallowed; bcftools isec runs
    # and returns its own exit code (here stubbed to 0 for simplicity).
    assert rc == 0
    # tabix was attempted before the FileNotFoundError; bcftools isec
    # still ran afterwards.
    assert any(c[0] == "bcftools" and c[1] == "isec" for c in captured)
