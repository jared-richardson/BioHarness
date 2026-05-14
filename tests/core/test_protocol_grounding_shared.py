from __future__ import annotations

from bio_harness.core.protocol_grounding._shared import (
    _build_normalize_vcf_command,
    _build_variant_filter_command,
)


def test_variant_filter_command_uses_resolved_tool_paths(monkeypatch) -> None:
    monkeypatch.setattr(
        "bio_harness.core.protocol_grounding._shared.which_with_pixi",
        lambda name: {
            "bcftools": "/opt/tools/bcftools",
            "bgzip": "/opt/tools/bgzip",
            "tabix": "/opt/tools/tabix",
        }.get(name),
    )

    command = _build_variant_filter_command(
        "/tmp/in.raw.vcf",
        "/tmp/out.filtered.vcf.gz",
        "QUAL > 1 & QUAL / AO > 10",
    )

    assert "/opt/tools/bcftools filter -i" in command
    assert "/opt/tools/tabix -f -p vcf /tmp/out.filtered.vcf.gz" in command


def test_normalize_vcf_command_uses_resolved_tool_paths(monkeypatch) -> None:
    monkeypatch.setattr(
        "bio_harness.core.protocol_grounding._shared.which_with_pixi",
        lambda name: {
            "bcftools": "/opt/tools/bcftools",
            "bgzip": "/opt/tools/bgzip",
            "tabix": "/opt/tools/tabix",
        }.get(name),
    )

    command = _build_normalize_vcf_command(
        "/tmp/in.annotated.vcf",
        "/tmp/out.normalized.vcf.gz",
        "/tmp/ref.fa",
    )

    assert "/opt/tools/bcftools norm -f /tmp/ref.fa -m -any /tmp/in.annotated.vcf -Oz -o /tmp/out.normalized.vcf.gz" in command
    assert "/opt/tools/tabix -f -p vcf /tmp/out.normalized.vcf.gz" in command
