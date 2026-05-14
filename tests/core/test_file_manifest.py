"""Tests for bio_harness.core.file_manifest."""
from __future__ import annotations

from pathlib import Path


from bio_harness.core.file_manifest import (
    FileManifest,
    ManifestEntry,
    _assign_role,
    _classify_file_type,
    _guess_sample_id_from_fastq,
)


# ---------------------------------------------------------------------------
# _classify_file_type
# ---------------------------------------------------------------------------

class TestClassifyFileType:
    def test_fastq_gz(self):
        assert _classify_file_type("sample.fastq.gz") == "fastq"

    def test_fq(self):
        assert _classify_file_type("reads.fq") == "fastq"

    def test_fasta(self):
        assert _classify_file_type("genome.fasta") == "fasta"

    def test_fa_gz(self):
        assert _classify_file_type("ref.fa.gz") == "fasta"

    def test_vcf(self):
        assert _classify_file_type("variants.vcf") == "vcf"

    def test_gff(self):
        assert _classify_file_type("genes.gff") == "gff"

    def test_gff3(self):
        assert _classify_file_type("genes.gff3") == "gff"

    def test_gtf(self):
        assert _classify_file_type("annotation.gtf") == "gtf"

    def test_bam(self):
        assert _classify_file_type("aligned.bam") == "bam"

    def test_h5ad(self):
        assert _classify_file_type("data.h5ad") == "h5ad"

    def test_unknown(self):
        assert _classify_file_type("readme.md") is None

    def test_tsv(self):
        assert _classify_file_type("counts.tsv") == "tsv"


# ---------------------------------------------------------------------------
# _guess_sample_id_from_fastq
# ---------------------------------------------------------------------------

class TestGuessSampleId:
    def test_r1(self):
        assert _guess_sample_id_from_fastq(Path("sample1_R1.fastq.gz")) == "sample1"

    def test_r2(self):
        assert _guess_sample_id_from_fastq(Path("sample1_R2.fastq.gz")) == "sample1"

    def test_numeric_suffix(self):
        assert _guess_sample_id_from_fastq(Path("SRR123_1.fq.gz")) == "SRR123"

    def test_no_pair_suffix(self):
        assert _guess_sample_id_from_fastq(Path("reads.fastq")) == "reads"


# ---------------------------------------------------------------------------
# _assign_role
# ---------------------------------------------------------------------------

class TestAssignRole:
    def test_fastq_r1(self):
        role, sid = _assign_role(Path("sample_R1.fastq.gz"), "fastq", "")
        assert role == "input_fastq_r1"
        assert sid == "sample"

    def test_fastq_r2(self):
        role, sid = _assign_role(Path("sample_R2.fastq.gz"), "fastq", "")
        assert role == "input_fastq_r2"
        assert sid == "sample"

    def test_reference_genome(self):
        role, sid = _assign_role(Path("reference.fasta"), "fasta", "variant_annotation")
        assert role == "reference_genome"
        assert sid is None

    def test_gff(self):
        role, sid = _assign_role(Path("genes.gff"), "gff", "")
        assert role == "annotation_gff"

    def test_gtf(self):
        role, sid = _assign_role(Path("genes.gtf"), "gtf", "")
        assert role == "annotation_gtf"

    def test_vcf(self):
        role, sid = _assign_role(Path("variants.vcf"), "vcf", "")
        assert role == "input_vcf"

    def test_bam(self):
        role, sid = _assign_role(Path("sample1.bam"), "bam", "")
        assert role == "input_bam"
        assert sid is not None

    def test_metadata_csv(self):
        role, sid = _assign_role(Path("metadata.csv"), "csv", "")
        assert role == "sample_metadata"

    def test_fasta_context_variant_calling(self):
        role, _ = _assign_role(Path("genome.fa"), "fasta", "germline_variant_calling")
        assert role == "reference_genome"

    def test_fasta_context_rna_seq_de(self):
        role, _ = _assign_role(
            Path("C_parapsilosis_CDC317_current_chromosomes.fasta"),
            "fasta",
            "rna_seq_differential_expression",
        )
        assert role == "reference_genome"

    def test_fasta_generic(self):
        role, _ = _assign_role(Path("sequences.fasta"), "fasta", "phylogenetics")
        assert role == "input_fasta"


# ---------------------------------------------------------------------------
# ManifestEntry
# ---------------------------------------------------------------------------

class TestManifestEntry:
    def test_as_dict(self):
        entry = ManifestEntry(
            role="reference_genome",
            resolved_path="/data/genome.fa",
            file_type="fasta",
        )
        d = entry.as_dict()
        assert d["role"] == "reference_genome"
        assert d["path"] == "/data/genome.fa"
        assert "sample_id" not in d

    def test_as_dict_with_sample(self):
        entry = ManifestEntry(
            role="input_fastq_r1",
            resolved_path="/data/s1_R1.fq.gz",
            file_type="fastq",
            sample_id="s1",
        )
        d = entry.as_dict()
        assert d["sample_id"] == "s1"


# ---------------------------------------------------------------------------
# FileManifest
# ---------------------------------------------------------------------------

class TestFileManifest:
    def _make_data_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "data"
        root.mkdir()
        (root / "genome.fasta").write_text(">chr1\nATCG\n")
        (root / "genes.gff").write_text("##gff\n")
        (root / "sample1_R1.fastq.gz").write_bytes(b"")
        (root / "sample1_R2.fastq.gz").write_bytes(b"")
        (root / "variants.vcf").write_text("##vcf\n")
        return root

    def test_from_data_root(self, tmp_path):
        root = self._make_data_root(tmp_path)
        manifest = FileManifest.from_data_root(root, "variant_annotation")
        assert len(manifest.entries) == 5
        assert manifest.has_role("reference_genome")
        assert manifest.has_role("annotation_gff")
        assert manifest.has_role("input_vcf")

    def test_resolve(self, tmp_path):
        root = self._make_data_root(tmp_path)
        manifest = FileManifest.from_data_root(root, "variant_annotation")
        ref = manifest.resolve("reference_genome")
        assert ref is not None
        assert ref.endswith("genome.fasta")

    def test_resolve_all(self, tmp_path):
        root = self._make_data_root(tmp_path)
        manifest = FileManifest.from_data_root(root, "variant_annotation")
        fastqs = manifest.resolve_all("input_fastq_r1")
        assert len(fastqs) >= 1

    def test_sample_ids(self, tmp_path):
        root = self._make_data_root(tmp_path)
        manifest = FileManifest.from_data_root(root, "variant_annotation")
        ids = manifest.sample_ids()
        assert "sample1" in ids

    def test_file_types(self, tmp_path):
        root = self._make_data_root(tmp_path)
        manifest = FileManifest.from_data_root(root, "variant_annotation")
        types = manifest.file_types()
        assert "fasta" in types
        assert "gff" in types
        assert "vcf" in types

    def test_as_brief_block(self, tmp_path):
        root = self._make_data_root(tmp_path)
        manifest = FileManifest.from_data_root(root, "variant_annotation", output_dir="/out")
        block = manifest.as_brief_block()
        assert "reference_genome" in block
        assert "output_dir" in block

    def test_inject_into_step(self, tmp_path):
        root = self._make_data_root(tmp_path)
        manifest = FileManifest.from_data_root(root, "variant_annotation")
        step = {
            "tool_name": "snpeff_annotate",
            "arguments": {
                "reference_fasta": "{reference_genome}",
                "annotation_gff": "{annotation_gff}",
            },
        }
        injected = manifest.inject_into_step(step)
        ref = injected["arguments"]["reference_fasta"]
        assert ref.endswith("genome.fasta")
        assert "{" not in ref

    def test_inject_into_plan(self, tmp_path):
        root = self._make_data_root(tmp_path)
        manifest = FileManifest.from_data_root(root, "variant_annotation")
        plan = [
            {"tool_name": "t1", "arguments": {"ref": "{reference_genome}"}},
            {"tool_name": "t2", "arguments": {"vcf": "{input_vcf}"}},
        ]
        result = manifest.inject_into_plan(plan)
        assert len(result) == 2
        assert "{" not in str(result[0]["arguments"]["ref"])

    def test_from_discovered_files(self):
        discovered = [
            {"name": "ref.fasta", "path": "/data/ref.fasta"},
            {"name": "reads_R1.fastq.gz", "path": "/data/reads_R1.fastq.gz"},
        ]
        manifest = FileManifest.from_discovered_files(discovered, "germline_variant_calling")
        assert manifest.has_role("reference_genome")
        assert manifest.has_role("input_fastq_r1")

    def test_empty_data_root(self, tmp_path):
        root = tmp_path / "empty"
        manifest = FileManifest.from_data_root(root, "")
        assert len(manifest.entries) == 0

    def test_as_role_instructions(self, tmp_path):
        root = self._make_data_root(tmp_path)
        manifest = FileManifest.from_data_root(root, "variant_annotation")
        instr = manifest.as_role_instructions()
        assert "FILE ROLES" in instr
        assert "reference_genome" in instr
