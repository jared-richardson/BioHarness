from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from bio_harness.core.tool_env import which_with_pixi
from bio_harness.skills.library.blast_formatter_run import blast_formatter_run
from bio_harness.skills.library.blastdb_aliastool_run import blastdb_aliastool_run
from bio_harness.skills.library.blastdbcheck_run import blastdbcheck_run
from bio_harness.skills.library.blastdbcmd_run import blastdbcmd_run
from bio_harness.skills.library.blastn_search import blastn_search
from bio_harness.skills.library.blastp_search import blastp_search
from bio_harness.skills.library.blastx_search import blastx_search
from bio_harness.skills.library.deltablast_search import deltablast_search
from bio_harness.skills.library.makeblastdb_run import makeblastdb_run
from bio_harness.skills.library.makeprofiledb_run import makeprofiledb_run
from bio_harness.skills.library.psiblast_search import psiblast_search
from bio_harness.skills.library.rpsblast_search import rpsblast_search
from bio_harness.skills.library.rpstblastn_search import rpstblastn_search
from bio_harness.skills.library.tblastn_search import tblastn_search
from bio_harness.skills.library.tblastx_search import tblastx_search

DNA_SEQ = (
    "ATGAAAACCGCTTACATTGCTAAACAACGTCAAATTTCTTTTGTTAAATCTCATTTTTCTCGTCAA"
    "GATATTCTGGATCTGTGGATTTACCATACTCAAGGTTACTTTCCTGATTGGCAAAATTAC"
)
PROT_SEQ = "MKTAYIAKQRQISFVKSHFSRQDILDLWIYHTQGYFPDWQNY"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_query_inputs(tmp_path: Path) -> tuple[Path, Path]:
    nucleotide_fasta = tmp_path / "query.fa"
    protein_fasta = tmp_path / "query.faa"
    nucleotide_fasta.write_text(f">n1\n{DNA_SEQ}\n", encoding="utf-8")
    protein_fasta.write_text(f">p1\n{PROT_SEQ}\n", encoding="utf-8")
    return nucleotide_fasta, protein_fasta


def _run_shell(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


HAS_LIVE_BLAST = all(
    which_with_pixi(name)
    for name in ("blastp", "blastn", "blastx", "tblastn", "tblastx", "psiblast", "makeblastdb")
)
LIVE_BLAST_SKIP = pytest.mark.skipif(
    not HAS_LIVE_BLAST,
    reason="Core BLAST+ binaries are not available in the local Pixi environment",
)
HAS_LIVE_BLAST_ADMIN = all(
    which_with_pixi(name)
    for name in ("blast_formatter", "blastdbcmd", "blastdbcheck", "blastdb_aliastool", "makeblastdb")
)
LIVE_BLAST_ADMIN_SKIP = pytest.mark.skipif(
    not HAS_LIVE_BLAST_ADMIN,
    reason="BLAST+ admin binaries are not available in the local Pixi environment",
)


@LIVE_BLAST_SKIP
def test_makeblastdb_run_renders_shared_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.which_with_pixi",
        lambda name: "/opt/tools/makeblastdb" if name == "makeblastdb" else None,
    )
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.shell_path_prefix",
        lambda *names: "/opt/tools",
    )
    cmd = makeblastdb_run(
        input_fasta="/tmp/query.fa",
        output_prefix="/tmp/db/query",
        dbtype="nucl",
        parse_seqids=True,
    )
    assert "export PATH=/opt/tools:$PATH" in cmd
    assert "/opt/tools/makeblastdb -in /tmp/query.fa" in cmd
    assert "-dbtype nucl" in cmd
    assert "-parse_seqids" in cmd


@LIVE_BLAST_SKIP
def test_makeblastdb_run_executes_locally(tmp_path: Path) -> None:
    nucleotide_fasta, _protein_fasta = _write_query_inputs(tmp_path)
    db_prefix = tmp_path / "nucl_db" / "query_db"
    cmd = makeblastdb_run(
        input_fasta=str(nucleotide_fasta),
        output_prefix=str(db_prefix),
        dbtype="nucl",
        parse_seqids=True,
    )
    completed = _run_shell(cmd, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "nucl_db" / "query_db.nsq").exists() or (tmp_path / "nucl_db" / "query_db.ndb").exists()


def test_makeblastdb_run_normalizes_human_readable_dbtype_aliases() -> None:
    protein_cmd = makeblastdb_run(
        input_fasta="/tmp/query.faa",
        output_prefix="/tmp/db/protein_db",
        dbtype="protein",
    )
    nucleotide_cmd = makeblastdb_run(
        input_fasta="/tmp/query.fa",
        output_prefix="/tmp/db/nucleotide_db",
        dbtype="nucleotide",
    )

    assert "-dbtype prot" in protein_cmd
    assert "-dbtype nucl" in nucleotide_cmd


@LIVE_BLAST_SKIP
def test_blastp_search_executes_locally(tmp_path: Path) -> None:
    _nucleotide_fasta, protein_fasta = _write_query_inputs(tmp_path)
    output_tsv = tmp_path / "blastp.tsv"
    cmd = blastp_search(
        query_fasta=str(protein_fasta),
        database=str(protein_fasta),
        output_tsv=str(output_tsv),
        threads=1,
    )
    completed = _run_shell(cmd, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "p1\tp1" in output_tsv.read_text(encoding="utf-8")


@LIVE_BLAST_SKIP
def test_blastn_search_executes_locally(tmp_path: Path) -> None:
    nucleotide_fasta, _protein_fasta = _write_query_inputs(tmp_path)
    output_tsv = tmp_path / "blastn.tsv"
    cmd = blastn_search(
        query_fasta=str(nucleotide_fasta),
        database=str(nucleotide_fasta),
        output_tsv=str(output_tsv),
        threads=1,
        task="blastn",
    )
    completed = _run_shell(cmd, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "n1\tn1" in output_tsv.read_text(encoding="utf-8")


@LIVE_BLAST_SKIP
def test_blastx_search_executes_locally(tmp_path: Path) -> None:
    nucleotide_fasta, protein_fasta = _write_query_inputs(tmp_path)
    output_tsv = tmp_path / "blastx.tsv"
    cmd = blastx_search(
        query_fasta=str(nucleotide_fasta),
        database=str(protein_fasta),
        output_tsv=str(output_tsv),
        threads=1,
    )
    completed = _run_shell(cmd, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "n1\tp1" in output_tsv.read_text(encoding="utf-8")


@LIVE_BLAST_SKIP
def test_tblastn_search_executes_locally(tmp_path: Path) -> None:
    nucleotide_fasta, protein_fasta = _write_query_inputs(tmp_path)
    output_tsv = tmp_path / "tblastn.tsv"
    cmd = tblastn_search(
        query_fasta=str(protein_fasta),
        database=str(nucleotide_fasta),
        output_tsv=str(output_tsv),
        threads=1,
    )
    completed = _run_shell(cmd, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "p1\tn1" in output_tsv.read_text(encoding="utf-8")


@LIVE_BLAST_SKIP
def test_tblastx_search_executes_locally(tmp_path: Path) -> None:
    nucleotide_fasta, _protein_fasta = _write_query_inputs(tmp_path)
    output_tsv = tmp_path / "tblastx.tsv"
    cmd = tblastx_search(
        query_fasta=str(nucleotide_fasta),
        database=str(nucleotide_fasta),
        output_tsv=str(output_tsv),
        threads=1,
    )
    completed = _run_shell(cmd, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "n1\tn1" in output_tsv.read_text(encoding="utf-8")


@LIVE_BLAST_SKIP
def test_psiblast_search_executes_locally(tmp_path: Path) -> None:
    _nucleotide_fasta, protein_fasta = _write_query_inputs(tmp_path)
    output_tsv = tmp_path / "psiblast.tsv"
    cmd = psiblast_search(
        query_fasta=str(protein_fasta),
        database=str(protein_fasta),
        output_tsv=str(output_tsv),
        threads=1,
        num_iterations=2,
    )
    completed = _run_shell(cmd, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "p1\tp1" in output_tsv.read_text(encoding="utf-8")


@LIVE_BLAST_ADMIN_SKIP
def test_blast_formatter_run_executes_locally(tmp_path: Path) -> None:
    _nucleotide_fasta, protein_fasta = _write_query_inputs(tmp_path)
    db_prefix = tmp_path / "prot_db" / "query_db"
    archive_file = tmp_path / "blastp.asn"
    formatted = tmp_path / "formatted.tsv"
    prep_cmd = (
        f"export PATH={shlex.quote(str(REPO_ROOT / '.pixi/envs/default/bin'))}:$PATH; "
        f"mkdir -p {shlex.quote(str(db_prefix.parent))}; "
        f"makeblastdb -in {shlex.quote(str(protein_fasta))} -dbtype prot -out {shlex.quote(str(db_prefix))} >/dev/null 2>&1; "
        f"blastp -query {shlex.quote(str(protein_fasta))} -db {shlex.quote(str(db_prefix))} "
        f"-out {shlex.quote(str(archive_file))} -outfmt 11 -num_threads 1 -evalue 1e-5 >/dev/null 2>&1"
    )
    prep = _run_shell(prep_cmd, tmp_path)
    assert prep.returncode == 0, prep.stderr
    cmd = blast_formatter_run(
        archive_file=str(archive_file),
        output_file=str(formatted),
        outfmt="6 qseqid sseqid",
    )
    completed = _run_shell(cmd, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "p1\tp1" in formatted.read_text(encoding="utf-8")


@LIVE_BLAST_ADMIN_SKIP
def test_blastdbcmd_run_executes_locally(tmp_path: Path) -> None:
    _nucleotide_fasta, protein_fasta = _write_query_inputs(tmp_path)
    db_prefix = tmp_path / "prot_db" / "query_db"
    prep_cmd = makeblastdb_run(
        input_fasta=str(protein_fasta),
        output_prefix=str(db_prefix),
        dbtype="prot",
        parse_seqids=True,
    )
    prep = _run_shell(prep_cmd, tmp_path)
    assert prep.returncode == 0, prep.stderr
    retrieved = tmp_path / "entry.faa"
    cmd = blastdbcmd_run(
        database=str(db_prefix),
        entry="p1",
        output_file=str(retrieved),
        dbtype="prot",
        outfmt="%f",
    )
    completed = _run_shell(cmd, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert PROT_SEQ in retrieved.read_text(encoding="utf-8")


def test_blastdbcheck_run_renders_shared_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.which_with_pixi",
        lambda name: f"/opt/tools/{name}",
    )
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.shell_path_prefix",
        lambda *names: "/opt/tools",
    )
    cmd = blastdbcheck_run(
        database="/tmp/query_db",
        dbtype="prot",
        verbosity=1,
    )
    assert "export PATH=/opt/tools:$PATH" in cmd
    assert "/opt/tools/blastdbcheck -dbtype prot -verbosity 1 -db /tmp/query_db" in cmd


@LIVE_BLAST_ADMIN_SKIP
def test_blastdb_aliastool_run_executes_locally(tmp_path: Path) -> None:
    _nucleotide_fasta, protein_fasta = _write_query_inputs(tmp_path)
    db_prefix = tmp_path / "prot_db" / "query_db"
    prep_cmd = makeblastdb_run(
        input_fasta=str(protein_fasta),
        output_prefix=str(db_prefix),
        dbtype="prot",
        parse_seqids=True,
    )
    prep = _run_shell(prep_cmd, tmp_path)
    assert prep.returncode == 0, prep.stderr
    alias_prefix = tmp_path / "prot_db" / "alias_db"
    alias_cmd = blastdb_aliastool_run(
        dblist=[str(db_prefix)],
        dbtype="prot",
        output_alias=str(alias_prefix),
        title="alias_db",
    )
    alias = _run_shell(alias_cmd, tmp_path)
    assert alias.returncode == 0, alias.stderr
    assert (tmp_path / "prot_db" / "alias_db.pal").exists()
    retrieved = tmp_path / "alias_entry.faa"
    read_cmd = blastdbcmd_run(
        database=str(alias_prefix),
        entry="p1",
        output_file=str(retrieved),
        dbtype="prot",
        outfmt="%f",
    )
    completed = _run_shell(read_cmd, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert PROT_SEQ in retrieved.read_text(encoding="utf-8")


def test_deltablast_search_renders_domain_database_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.which_with_pixi",
        lambda name: f"/opt/tools/{name}",
    )
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.shell_path_prefix",
        lambda *names: "/opt/tools",
    )
    cmd = deltablast_search(
        query_fasta="/tmp/query.faa",
        database="/tmp/protein_db",
        output_tsv="/tmp/out.tsv",
        domain_database="/tmp/cdd_db",
    )
    assert "deltablast -query /tmp/query.faa -db \"$db\"" in cmd
    assert "-rpsdb /tmp/cdd_db" in cmd


def test_rpsblast_search_renders_db_only_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.which_with_pixi",
        lambda name: f"/opt/tools/{name}",
    )
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.shell_path_prefix",
        lambda *names: "/opt/tools",
    )
    cmd = rpsblast_search(
        query_fasta="/tmp/query.faa",
        database="/tmp/domain_db",
        output_tsv="/tmp/out.tsv",
        threads=1,
    )
    assert "rpsblast -query /tmp/query.faa -db /tmp/domain_db" in cmd
    assert "-subject" not in cmd


def test_rpstblastn_search_renders_db_only_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.which_with_pixi",
        lambda name: f"/opt/tools/{name}",
    )
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.shell_path_prefix",
        lambda *names: "/opt/tools",
    )
    cmd = rpstblastn_search(
        query_fasta="/tmp/query.fa",
        database="/tmp/domain_db",
        output_tsv="/tmp/out.tsv",
        strand="plus",
    )
    assert "rpstblastn -query /tmp/query.fa -db /tmp/domain_db" in cmd
    assert "-strand plus" in cmd
    assert "-outfmt" in cmd
    assert "qseqid sseqid" in cmd
    assert "-subject" not in cmd


def test_makeprofiledb_run_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.which_with_pixi",
        lambda name: f"/opt/tools/{name}",
    )
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.shell_path_prefix",
        lambda *names: "/opt/tools",
    )
    cmd = makeprofiledb_run(
        input_list="/tmp/scoremats.txt",
        output_prefix="/tmp/domain_db",
        dbtype="delta",
        title="domain_db",
        index=False,
    )
    assert "makeprofiledb -in /tmp/scoremats.txt" in cmd
    assert "-out /tmp/domain_db" in cmd
    assert "-dbtype delta" in cmd
    assert "-index false" in cmd


def test_makeprofiledb_run_normalizes_pssm_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.which_with_pixi",
        lambda name: f"/opt/tools/{name}",
    )
    monkeypatch.setattr(
        "bio_harness.skills.library._blast_support.shell_path_prefix",
        lambda *names: "/opt/tools",
    )
    cmd = makeprofiledb_run(
        input_list="/tmp/scoremats.txt",
        output_prefix="/tmp/domain_db",
        dbtype="pssm",
    )
    assert "-dbtype rps" in cmd
