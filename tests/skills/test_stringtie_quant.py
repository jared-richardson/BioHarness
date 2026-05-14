from __future__ import annotations

import pytest

from bio_harness.skills.library.stringtie_quant import stringtie_quant


def test_stringtie_quant_renders_reference_guided_quant_command() -> None:
    cmd = stringtie_quant(
        input_bam="/tmp/in/sample.bam",
        annotation_gtf="/refs/genes.gtf",
        output_gtf="/tmp/out/sample.gtf",
    )
    assert "set -euo pipefail;" in cmd
    assert "samtools index /tmp/in/sample.bam" in cmd
    assert "stringtie /tmp/in/sample.bam -G /refs/genes.gtf" in cmd
    assert "-o /tmp/out/sample.gtf" in cmd
    assert "-A /tmp/out/gene_abundances.tsv" in cmd
    assert "-p 4 -e" in cmd


def test_stringtie_quant_uses_shared_tool_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.stringtie_quant.which_with_pixi",
        lambda name: {
            "stringtie": "/opt/tools/stringtie",
            "samtools": "/opt/tools/samtools",
        }.get(name),
    )
    monkeypatch.setattr(
        "bio_harness.skills.library.stringtie_quant.shell_path_prefix",
        lambda *names: "/opt/tools",
    )
    cmd = stringtie_quant(
        input_bam="/tmp/in/sample.bam",
        annotation_gtf="/refs/genes.gtf",
        output_gtf="/tmp/out/sample.gtf",
        gene_abundance_tsv="/tmp/out/sample.tsv",
        ballgown_dir="/tmp/out/ballgown",
        estimate_reference_only=False,
        threads=8,
    )
    assert "export PATH=/opt/tools:$PATH" in cmd
    assert "/opt/tools/samtools index /tmp/in/sample.bam" in cmd
    assert "/opt/tools/stringtie /tmp/in/sample.bam -G /refs/genes.gtf" in cmd
    assert "-A /tmp/out/sample.tsv" in cmd
    assert "-p 8 -b /tmp/out/ballgown" in cmd
    assert " -B " not in cmd
