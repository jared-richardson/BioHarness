from __future__ import annotations

from bio_harness.skills.library.vep_annotate import vep_annotate


def test_vep_annotate_custom_reference_mode_uses_launcher_and_indexes_annotation(monkeypatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.vep_annotate.tool_launcher_command",
        lambda name: "/tmp/vep-launcher" if name == "vep" else None,
    )
    monkeypatch.setattr(
        "bio_harness.skills.library.vep_annotate.which_with_pixi",
        lambda name: f"/opt/tools/{name}",
    )

    cmd = vep_annotate(
        input_vcf="/tmp/input.vcf",
        output_vcf="/tmp/out/annotated.vcf",
        annotation_gff="/refs/genes.gff",
        reference_fasta="/refs/genome.fa",
    )

    assert "mkdir -p " in cmd
    assert "_vep/genes.gff.gz" in cmd
    assert "/opt/tools/bgzip -c /refs/genes.gff > " in cmd
    assert "/opt/tools/tabix -f -p gff " in cmd
    assert "/tmp/vep-launcher --format vcf -i /tmp/input.vcf -o /tmp/out/annotated.vcf --vcf --no_stats --force_overwrite" in cmd
    assert "--gff " in cmd
    assert "--fasta /refs/genome.fa --species custom" in cmd


def test_vep_annotate_database_mode_uses_database_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.vep_annotate.tool_launcher_command",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "bio_harness.skills.library.vep_annotate.which_with_pixi",
        lambda name: f"/opt/tools/{name}",
    )

    cmd = vep_annotate(
        input_vcf="/tmp/input.vcf",
        output_vcf="/tmp/out.vcf",
        assembly="GRCh38",
        species="homo_sapiens",
        use_database=True,
    )

    assert cmd.startswith("/opt/tools/vep --format vcf -i /tmp/input.vcf -o /tmp/out.vcf")
    assert "--database --assembly GRCh38 --species homo_sapiens" in cmd
    assert "--cache" not in cmd


def test_vep_annotate_cache_mode_supports_cache_dir(monkeypatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.vep_annotate.tool_launcher_command",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "bio_harness.skills.library.vep_annotate.which_with_pixi",
        lambda name: f"/opt/tools/{name}",
    )

    cmd = vep_annotate(
        input_vcf="/tmp/input.vcf",
        output_vcf="/tmp/out.vcf",
        assembly="GRCh38",
        cache_dir="/tmp/cache",
    )

    assert "--cache --offline --assembly GRCh38 --species homo_sapiens --dir_cache /tmp/cache" in cmd
