from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from bio_harness.core import tool_env, tool_launchers


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def teardown_function() -> None:
    tool_launchers.refresh_tool_launchers()


def test_which_with_pixi_finds_repo_tool(monkeypatch):
    pixi_bin = Path(__file__).resolve().parents[2] / ".pixi" / "envs" / "default" / "bin"
    star_bin = pixi_bin / "STAR"

    monkeypatch.setattr(tool_env.shutil, "which", lambda _name: "")
    monkeypatch.setattr(tool_env, "pixi_env_bin_dirs", lambda: [pixi_bin])
    monkeypatch.setattr(tool_env.os, "access", lambda path, mode: Path(path) == star_bin)

    assert tool_env.which_with_pixi("STAR") == str(star_bin)


def test_ensure_pixi_tooling_on_path_prepends_missing_bins(monkeypatch):
    pixi_bins = [Path("/tmp/pixi/default/bin"), Path("/tmp/pixi/reports/bin")]
    pixi_jvm_bins = [Path("/tmp/pixi/default/jvm/bin")]
    monkeypatch.setattr(tool_env, "pixi_env_bin_dirs", lambda: pixi_bins)
    monkeypatch.setattr(tool_env, "pixi_jvm_bin_dirs", lambda: pixi_jvm_bins)
    monkeypatch.setenv("PATH", "/usr/bin")

    tool_env.ensure_pixi_tooling_on_path()

    path_entries = tool_env.os.environ["PATH"].split(tool_env.os.pathsep)
    assert path_entries[0] == str(pixi_bins[0])
    assert path_entries[1] == str(pixi_bins[1])
    assert path_entries[2] == str(pixi_jvm_bins[0])
    assert "/usr/bin" in path_entries


def test_build_pixi_execution_env_reorders_conflicting_bins(monkeypatch):
    default_bin = Path("/tmp/pixi/default/bin")
    specialty_bin = Path("/tmp/pixi/specialty/bin")
    monkeypatch.setattr(tool_env, "pixi_env_bin_dirs", lambda: [default_bin, specialty_bin])
    monkeypatch.setattr(tool_env, "pixi_jvm_bin_dirs", lambda: [])

    env = tool_env.build_pixi_execution_env(
        {
            "PATH": os.pathsep.join(
                [
                    str(specialty_bin),
                    "/usr/local/bin",
                    str(default_bin),
                    "/usr/bin",
                ]
            )
        }
    )

    assert env["PATH"].split(os.pathsep)[:4] == [
        str(default_bin),
        str(specialty_bin),
        "/usr/local/bin",
        "/usr/bin",
    ]


def test_shell_path_prefix_includes_requested_tools_and_repo_bins(monkeypatch):
    default_bin = Path("/tmp/pixi/default/bin")
    reports_bin = Path("/tmp/pixi/reports/bin")
    monkeypatch.setattr(tool_env, "pixi_env_bin_dirs", lambda: [default_bin, reports_bin])
    monkeypatch.setattr(tool_env, "pixi_jvm_bin_dirs", lambda: [])
    monkeypatch.setattr(
        tool_env,
        "which_with_pixi",
        lambda name: {
            "flye": str(default_bin / "flye"),
            "minimap2": str(default_bin / "minimap2"),
        }.get(name),
    )

    rendered = tool_env.shell_path_prefix("flye", "minimap2")

    assert rendered.split(":")[:2] == [
        str(default_bin.resolve()),
        str(reports_bin.resolve()),
    ]


def test_which_with_pixi_uses_requirement_aliases(monkeypatch, tmp_path):
    pixi_bin = tmp_path / "pixi" / "reports" / "bin"
    pixi_bin.mkdir(parents=True, exist_ok=True)
    featurecounts_bin = pixi_bin / "featureCounts"
    featurecounts_bin.write_text("", encoding="utf-8")

    monkeypatch.setattr(tool_env.shutil, "which", lambda _name: "")
    monkeypatch.setattr(tool_env, "pixi_env_bin_dirs", lambda: [pixi_bin])
    monkeypatch.setattr(
        tool_env.os,
        "access",
        lambda path, mode: Path(path) == featurecounts_bin,
    )

    assert tool_env.which_with_pixi("featurecounts") == str(featurecounts_bin)


def test_requirement_available_accepts_bwa_mem2_as_bwa_alias(monkeypatch, tmp_path):
    pixi_bin = tmp_path / "pixi" / "alignment-extra" / "bin"
    pixi_bin.mkdir(parents=True, exist_ok=True)
    bwa_mem2_bin = pixi_bin / "bwa-mem2"
    bwa_mem2_bin.write_text("", encoding="utf-8")

    monkeypatch.setattr(tool_env.shutil, "which", lambda _name: "")
    monkeypatch.setattr(tool_env, "pixi_env_bin_dirs", lambda: [pixi_bin])
    monkeypatch.setattr(
        tool_env.os,
        "access",
        lambda path, mode: Path(path) == bwa_mem2_bin,
    )

    assert tool_env.which_with_pixi("bwa") == str(bwa_mem2_bin)
    assert tool_env.requirement_available("bwa") is True


def test_which_with_pixi_prefers_repo_pixi_order_over_shell_path(monkeypatch, tmp_path):
    default_bin = tmp_path / ".pixi" / "envs" / "default" / "bin"
    specialty_bin = tmp_path / ".pixi" / "envs" / "specialty-annotation" / "bin"
    default_tool = _make_executable(default_bin / "bcftools")
    _make_executable(specialty_bin / "bcftools")

    monkeypatch.setattr(tool_env, "pixi_env_bin_dirs", lambda: [default_bin, specialty_bin])
    monkeypatch.setattr(tool_env.shutil, "which", lambda _name: str(specialty_bin / "bcftools"))

    assert tool_env.which_with_pixi("bcftools") == str(default_tool)


def test_r_requirement_available_checks_multiple_rscripts(monkeypatch):
    monkeypatch.setattr(
        tool_env,
        "_rscript_candidates",
        lambda: ["/tmp/default/Rscript", "/tmp/r-bulk/Rscript"],
    )

    calls: list[str] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv[0])
        if argv[0] == "/tmp/default/Rscript":
            return SimpleNamespace(returncode=0, stdout="false", stderr="")
        return SimpleNamespace(returncode=0, stdout="true", stderr="")

    monkeypatch.setattr(tool_env.subprocess, "run", fake_run)

    assert tool_env.r_requirement_available("edger") is True
    assert calls == ["/tmp/default/Rscript", "/tmp/r-bulk/Rscript"]


def test_rscript_for_requirement_returns_matching_sidecar(monkeypatch):
    monkeypatch.setattr(
        tool_env,
        "_rscript_candidates",
        lambda: ["/tmp/r-bulk/Rscript", "/tmp/r-splicing/Rscript"],
    )

    def fake_run(argv, **_kwargs):
        if argv[0] == "/tmp/r-bulk/Rscript":
            return SimpleNamespace(returncode=0, stdout="false", stderr="")
        return SimpleNamespace(returncode=0, stdout="true", stderr="")

    monkeypatch.setattr(tool_env.subprocess, "run", fake_run)

    assert tool_env.rscript_for_requirement("dexseq") == "/tmp/r-splicing/Rscript"


def test_requirement_available_supports_pixi_sidecars_and_launchers_together(monkeypatch, tmp_path):
    reports_bin = tmp_path / ".pixi" / "envs" / "reports" / "bin"
    alignment_bin = tmp_path / ".pixi" / "envs" / "alignment-extra" / "bin"
    multiqc_bin = _make_executable(reports_bin / "multiqc")
    bowtie2_bin = _make_executable(alignment_bin / "bowtie2")
    prokka_bin = _make_executable(tmp_path / ".tool-envs" / "prokka" / "bin" / "prokka")
    config_path = tmp_path / "tool_launchers.json"
    config_path.write_text(
        json.dumps({"tools": {"prokka": {"argv": [str(prokka_bin)]}}}, indent=2),
        encoding="utf-8",
    )

    monkeypatch.setattr(tool_env, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tool_env.shutil, "which", lambda _name: None)
    monkeypatch.setenv("BIO_HARNESS_TOOL_LAUNCHERS_PATH", str(config_path))
    tool_launchers.refresh_tool_launchers()

    assert tool_env.which_with_pixi("multiqc") == str(multiqc_bin)
    assert tool_env.which_with_pixi("bowtie2") == str(bowtie2_bin)
    assert tool_env.requirement_available("multiqc") is True
    assert tool_env.requirement_available("bowtie2") is True
    assert tool_env.requirement_available("prokka") is True


def test_requirement_available_rejects_unhealthy_local_vep_without_launcher(monkeypatch, tmp_path):
    specialty_bin = tmp_path / ".pixi" / "envs" / "specialty-annotation" / "bin"
    vep_bin = _make_executable(specialty_bin / "vep")

    monkeypatch.setattr(tool_env, "pixi_env_bin_dirs", lambda: [specialty_bin])
    monkeypatch.setattr(tool_env.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        tool_env.subprocess,
        "run",
        lambda argv, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="broken vep"),
    )
    monkeypatch.setenv("BIO_HARNESS_TOOL_LAUNCHERS_PATH", str(tmp_path / "missing_launchers.json"))
    tool_launchers.refresh_tool_launchers()

    assert tool_env.which_with_pixi("vep") == str(vep_bin)
    assert tool_env.requirement_available("vep") is False


def test_requirement_available_accepts_launcher_backed_vep(monkeypatch, tmp_path):
    launcher_bin = _make_executable(tmp_path / ".tool-envs" / "vep" / "bin" / "vep")
    config_path = tmp_path / "tool_launchers.json"
    config_path.write_text(
        json.dumps({"tools": {"vep": {"argv": [str(launcher_bin)]}}}, indent=2),
        encoding="utf-8",
    )

    monkeypatch.setenv("BIO_HARNESS_TOOL_LAUNCHERS_PATH", str(config_path))
    tool_launchers.refresh_tool_launchers()

    assert tool_env.requirement_available("vep") is True
