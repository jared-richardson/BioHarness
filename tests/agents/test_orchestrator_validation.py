from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from bio_harness.agents.execution_markers import detect_failure_marker
from bio_harness.agents.orchestrator import Orchestrator


def _orchestrator_stub() -> Orchestrator:
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.skill_registry = SimpleNamespace(
        get_skill=lambda name: {
            "featurecounts_run": {
                "parameters": {
                    "annotation_gtf": {},
                    "input_bams": {},
                    "output_counts": {},
                    "threads": {},
                }
            },
            "deseq2_run": {
                "parameters": {
                    "script_path": {"ownership": "harness_managed"},
                    "counts_matrix": {},
                    "metadata_table": {},
                    "design_formula": {},
                    "contrast": {},
                    "output_dir": {},
                    "engine": {},
                }
            }
        }.get(name)
    )
    return orchestrator


def test_find_stdin_blocking_commands_flags_head_without_input():
    orchestrator = _orchestrator_stub()
    assert orchestrator._find_stdin_blocking_commands("head -10") == ["head"]


def test_find_stdin_blocking_commands_allows_file_or_pipe_input():
    orchestrator = _orchestrator_stub()
    assert orchestrator._find_stdin_blocking_commands("head -10 sample.txt") == []
    assert orchestrator._find_stdin_blocking_commands("printf 'abc\\n' | head -1") == []


def test_step_validation_fails_for_stdin_blocking_head_command():
    orchestrator = _orchestrator_stub()
    step = SimpleNamespace(tool_name="bash_run", arguments={"command": "head -10"})
    result = orchestrator._step_validation_agent(step, cwd=None)
    assert result["passed"] is False
    assert "stdin_block:head" in result["issues"]


def test_step_validation_passes_when_head_has_upstream_stdin():
    orchestrator = _orchestrator_stub()
    step = SimpleNamespace(tool_name="bash_run", arguments={"command": "printf 'abc\\n' | head -1"})
    result = orchestrator._step_validation_agent(step, cwd=None)
    assert result["passed"] is True
    assert all(not issue.startswith("stdin_block:") for issue in result["issues"])


def test_find_stdin_blocking_commands_ignores_quoted_shell_operators():
    orchestrator = _orchestrator_stub()
    cmd = "echo 'a|b;c&&d' ; printf 'abc\\n' | head -1"
    assert orchestrator._find_stdin_blocking_commands(cmd) == []


def test_find_disallowed_git_commands_flags_runtime_git():
    orchestrator = _orchestrator_stub()
    assert orchestrator._find_disallowed_git_commands("git clone https://example.com/repo.git") == ["clone"]


def test_step_validation_fails_for_runtime_git_commands():
    orchestrator = _orchestrator_stub()
    step = SimpleNamespace(tool_name="bash_run", arguments={"command": "git pull && echo done"})
    result = orchestrator._step_validation_agent(step, cwd=None)
    assert result["passed"] is False
    assert "disallowed_git:pull" in result["issues"]


def test_step_validation_fails_for_wrapped_runtime_git_commands():
    orchestrator = _orchestrator_stub()
    step = SimpleNamespace(tool_name="bash_run", arguments={"command": "bash -lc \"git clone https://example.com/a.git\""})
    result = orchestrator._step_validation_agent(step, cwd=None)
    assert result["passed"] is False
    assert "disallowed_git:clone" in result["issues"]


def test_step_validation_rejects_command_override_on_non_bash_skill():
    orchestrator = _orchestrator_stub()
    step = SimpleNamespace(
        tool_name="featurecounts_run",
        arguments={"command": "featureCounts -a genes.gtf -o counts.tsv sample.bam"},
    )
    result = orchestrator._step_validation_agent(step, cwd=None)
    assert result["passed"] is False
    assert "disallowed_argument:command" in result["issues"]


def test_step_validation_strips_undocumented_argument_on_non_bash_skill():
    orchestrator = _orchestrator_stub()
    step = SimpleNamespace(
        tool_name="featurecounts_run",
        arguments={
            "annotation_gtf": "/tmp/genes.gtf",
            "input_bams": "/tmp/sample.bam",
            "output_counts": "/tmp/counts.tsv",
            "mystery_flag": "boom",
        },
    )
    result = orchestrator._step_validation_agent(step, cwd=None)
    assert result["passed"] is True
    assert "mystery_flag" not in result["arguments"]
    assert "stripped_undocumented:mystery_flag" in result["fixes"]


def test_step_validation_strips_harness_managed_arguments_on_non_bash_skill():
    orchestrator = _orchestrator_stub()
    step = SimpleNamespace(
        tool_name="deseq2_run",
        arguments={
            "script_path": "/tmp/invented_wrapper.R",
            "counts_matrix": "/tmp/counts.tsv",
            "metadata_table": "/tmp/meta.tsv",
            "design_formula": "~ condition",
            "contrast": "condition,treat,control",
            "output_dir": "/tmp/out",
            "final_csv": "/tmp/final.csv",
        },
    )
    result = orchestrator._step_validation_agent(step, cwd=None)
    assert result["passed"] is True
    assert "script_path" not in result["arguments"]
    assert "final_csv" not in result["arguments"]
    assert "stripped_harness_managed:script_path" in result["fixes"]
    assert "stripped_undocumented:final_csv" in result["fixes"]


def test_step_validation_repairs_direct_de_wrapper_semantics(tmp_path):
    orchestrator = _orchestrator_stub()
    metadata_path = tmp_path / "metadata.tsv"
    metadata_path.write_text(
        (
            "sample\tdex\tcondition\n"
            "SRR1039508\tuntrt\tunknown\n"
            "SRR1039509\ttrt\tunknown\n"
            "SRR1039512\tuntrt\tunknown\n"
            "SRR1039513\ttrt\tunknown\n"
        ),
        encoding="utf-8",
    )
    step = SimpleNamespace(
        tool_name="deseq2_run",
        arguments={
            "counts_matrix": str(tmp_path / "counts.tsv"),
            "metadata_table": str(metadata_path),
            "design_formula": "~ treatment",
            "contrast": '["treatment", "dex", "untrt"]',
            "output_dir": str(tmp_path / "out"),
        },
    )

    result = orchestrator._step_validation_agent(step, cwd=None)

    assert result["passed"] is True
    assert result["arguments"]["design_formula"] == "~ dex"
    assert result["arguments"]["contrast"] == "dex_trt_vs_untrt"
    assert "invalid_design_formula_columns:treatment" not in result["issues"]
    assert any(fix.startswith("semantic_repaired:design_formula:") for fix in result["fixes"])
    assert any(fix.startswith("semantic_repaired:contrast:") for fix in result["fixes"])


def test_step_validation_expands_factor_only_direct_de_wrapper_contrast(tmp_path):
    orchestrator = _orchestrator_stub()
    metadata_path = tmp_path / "metadata.tsv"
    metadata_path.write_text(
        (
            "sample\tdex\tcondition\n"
            "SRR1039508\tuntrt\tunknown\n"
            "SRR1039509\ttrt\tunknown\n"
            "SRR1039512\tuntrt\tunknown\n"
            "SRR1039513\ttrt\tunknown\n"
        ),
        encoding="utf-8",
    )
    step = SimpleNamespace(
        tool_name="deseq2_run",
        arguments={
            "counts_matrix": str(tmp_path / "counts.tsv"),
            "metadata_table": str(metadata_path),
            "design_formula": "~ dex",
            "contrast": "dex",
            "output_dir": str(tmp_path / "out"),
        },
    )

    result = orchestrator._step_validation_agent(step, cwd=None)

    assert result["passed"] is True
    assert result["arguments"]["contrast"] == "dex_trt_vs_untrt"
    assert any(fix.startswith("semantic_repaired:contrast:dex->") for fix in result["fixes"])


def test_step_validation_maps_direct_de_wrapper_level_aliases(tmp_path):
    orchestrator = _orchestrator_stub()
    metadata_path = tmp_path / "metadata.tsv"
    metadata_path.write_text(
        (
            "sample\tdex\tcondition\n"
            "SRR1039508\tuntrt\tunknown\n"
            "SRR1039509\ttrt\tunknown\n"
            "SRR1039512\tuntrt\tunknown\n"
            "SRR1039513\ttrt\tunknown\n"
        ),
        encoding="utf-8",
    )
    step = SimpleNamespace(
        tool_name="deseq2_run",
        arguments={
            "counts_matrix": str(tmp_path / "counts.tsv"),
            "metadata_table": str(metadata_path),
            "design_formula": "~ dex",
            "contrast": ["dex", "treated", "untreated"],
            "output_dir": str(tmp_path / "out"),
        },
    )

    result = orchestrator._step_validation_agent(step, cwd=None)

    assert result["passed"] is True
    assert result["arguments"]["contrast"] == "dex_trt_vs_untrt"
    assert any("treated" in fix and "untrt" in fix for fix in result["fixes"])


def test_step_validation_blocks_unresolved_direct_de_wrapper_semantics(tmp_path):
    orchestrator = _orchestrator_stub()
    metadata_path = tmp_path / "metadata.tsv"
    metadata_path.write_text(
        (
            "sample\tdex\tbatch\n"
            "s1\tuntrt\tA\n"
            "s2\ttrt\tA\n"
            "s3\tuntrt\tB\n"
            "s4\ttrt\tB\n"
        ),
        encoding="utf-8",
    )
    step = SimpleNamespace(
        tool_name="deseq2_run",
        arguments={
            "counts_matrix": str(tmp_path / "counts.tsv"),
            "metadata_table": str(metadata_path),
            "design_formula": "~ treatment",
            "contrast": '["treatment", "dex", "untrt"]',
            "output_dir": str(tmp_path / "out"),
        },
    )

    result = orchestrator._step_validation_agent(step, cwd=None)

    assert result["passed"] is False
    assert "invalid_design_formula_columns:treatment" in result["issues"]
    assert "invalid_contrast_factor:treatment" in result["issues"]


def test_step_validation_blocks_missing_non_bash_input_paths_when_cwd_is_set(tmp_path):
    orchestrator = _orchestrator_stub()
    metadata_path = tmp_path / "metadata.tsv"
    metadata_path.write_text("sample\tdex\ns1\tuntrt\n", encoding="utf-8")
    step = SimpleNamespace(
        tool_name="deseq2_run",
        arguments={
            "counts_matrix": str(tmp_path / "missing_counts.tsv"),
            "metadata_table": str(metadata_path),
            "design_formula": "~ dex",
            "contrast": "dex",
            "output_dir": str(tmp_path / "out"),
        },
    )

    result = orchestrator._step_validation_agent(step, cwd=str(tmp_path))

    assert result["passed"] is False
    assert any(issue.startswith("missing_input:") for issue in result["issues"])


def test_step_validation_surfaces_placeholder_token_in_path_for_structured_inputs(tmp_path):
    orchestrator = _orchestrator_stub()
    metadata_path = tmp_path / "metadata.tsv"
    metadata_path.write_text("sample\tdex\ns1\tuntrt\n", encoding="utf-8")
    step = SimpleNamespace(
        tool_name="deseq2_run",
        arguments={
            "counts_matrix": str(tmp_path / "<reference_fasta>" / "counts.tsv"),
            "metadata_table": str(metadata_path),
            "design_formula": "~ dex",
            "contrast": "dex",
            "output_dir": str(tmp_path / "out"),
        },
    )

    result = orchestrator._step_validation_agent(step, cwd=str(tmp_path))

    assert result["passed"] is False
    assert any(issue.startswith("placeholder_token_in_path:") for issue in result["issues"])


def test_step_validation_surfaces_placeholder_token_in_path_for_bash_run(tmp_path, monkeypatch):
    orchestrator = _orchestrator_stub()
    placeholder_path = Path("/tmp/<reference_fasta>/missing.fa")

    monkeypatch.setattr(
        orchestrator,
        "_extract_step_requirements",
        lambda command, cwd: {
            "tools": ["ls"],
            "input_paths": [placeholder_path],
            "must_be_nonempty": [],
            "gtf_paths": [],
            "fasta_paths": [],
        },
    )
    step = SimpleNamespace(
        tool_name="bash_run",
        arguments={"command": "ls placeholder"},
    )

    result = orchestrator._step_validation_agent(step, cwd=str(tmp_path))

    assert result["passed"] is False
    assert any(issue.startswith("placeholder_token_in_path:") for issue in result["issues"])


def test_step_validation_fails_for_inline_python_command():
    orchestrator = _orchestrator_stub()
    step = SimpleNamespace(tool_name="bash_run", arguments={"command": 'python3 -c "print(1)"'})
    result = orchestrator._step_validation_agent(step, cwd=None)
    assert result["passed"] is False
    assert "inline_interpreter:python -c" in result["issues"]


def test_step_validation_audits_runtime_download_in_audit_mode(monkeypatch):
    monkeypatch.setenv("BIO_HARNESS_EXECUTION_POLICY", "audit")
    orchestrator = _orchestrator_stub()
    step = SimpleNamespace(tool_name="bash_run", arguments={"command": "curl -L https://github.com/example/repo"})
    result = orchestrator._step_validation_agent(step, cwd=None)
    assert result["passed"] is True
    assert "execution_policy_audit:runtime_download:github.com" in result["issues"]


def test_step_validation_blocks_untrusted_runtime_download_in_trusted_only_mode(monkeypatch):
    monkeypatch.setenv("BIO_HARNESS_EXECUTION_POLICY", "trusted_only")
    orchestrator = _orchestrator_stub()
    step = SimpleNamespace(tool_name="bash_run", arguments={"command": "curl -L https://example.com/file.txt"})
    result = orchestrator._step_validation_agent(step, cwd=None)
    assert result["passed"] is False
    assert "execution_policy_block:runtime_download_untrusted_host:example.com" in result["issues"]


def test_step_validation_repairs_single_sample_bcftools_filter_namespace_ambiguity(tmp_path: Path):
    orchestrator = _orchestrator_stub()
    input_vcf = tmp_path / "calls.vcf"
    input_vcf.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "##INFO=<ID=DP,Number=1,Type=Integer,Description=\"Depth\">\n"
            "##INFO=<ID=AF,Number=A,Type=Float,Description=\"Allele frequency\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Sample depth\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\tDP=8;AF=0.9\tDP\t8\n"
        ),
        encoding="utf-8",
    )
    step = SimpleNamespace(
        tool_name="bash_run",
        arguments={
            "command": (
                f"bcftools filter -e 'QUAL<30 || DP<5 || AF<0.8' "
                f"-Oz -o {tmp_path / 'filtered.vcf.gz'} {input_vcf.name}"
            )
        },
    )

    result = orchestrator._step_validation_agent(step, cwd=str(tmp_path))

    assert result["passed"] is True
    assert "INFO/DP<5" in result["arguments"]["command"]
    assert any(fix == "qualified_bcftools_expression_tag:DP->INFO/DP" for fix in result["fixes"])
    assert all(not issue.startswith("ambiguous_bcftools_expression_namespace:") for issue in result["issues"])


def test_step_validation_blocks_multi_sample_bcftools_filter_namespace_ambiguity(tmp_path: Path):
    orchestrator = _orchestrator_stub()
    input_vcf = tmp_path / "calls.vcf"
    input_vcf.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "##INFO=<ID=DP,Number=1,Type=Integer,Description=\"Depth\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Sample depth\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\ts2\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\tDP=12\tDP\t8\t4\n"
        ),
        encoding="utf-8",
    )
    step = SimpleNamespace(
        tool_name="bash_run",
        arguments={
            "command": (
                f"bcftools filter -e 'QUAL<30 || DP<5' "
                f"-Oz -o {tmp_path / 'filtered.vcf.gz'} {input_vcf.name}"
            )
        },
    )

    result = orchestrator._step_validation_agent(step, cwd=str(tmp_path))

    assert result["passed"] is False
    assert "ambiguous_bcftools_expression_namespace:DP" in result["issues"]


def test_step_validation_rewrites_wrapper_name_to_binary_fix_12(monkeypatch):
    """Fix #12: when the LLM puts a harness wrapper name (e.g.
    ``prokka_annotate``) into a bash_run command, and the wrapper's
    underlying binary (``prokka``) is on PATH, the validator must rewrite
    the command to the binary instead of emitting ``missing_tool`` and
    blocking the step.

    Without this, the stepwise planner livelocks: prokka_annotate steps
    are rejected by the validator's man-based probe (which also fails for
    legitimate wrappers), Fix #11 masks prokka_annotate for subsequent
    attempts, and with no annotation alternative the LLM falls back to
    spades_assemble on every turn (observed in exp23: 15× spades, 0×
    annotation, 0× snpeff).
    """
    import shutil as _shutil
    orchestrator = _orchestrator_stub()

    # Pretend prokka_annotate is NOT on PATH but prokka IS.
    real_which = _shutil.which

    def fake_which(name, *a, **kw):
        if name == "prokka_annotate":
            return None
        if name == "prokka":
            return "/opt/homebrew/bin/prokka"
        return real_which(name, *a, **kw)

    monkeypatch.setattr(
        "bio_harness.agents.orchestrator.shutil.which", fake_which
    )

    step = SimpleNamespace(
        tool_name="bash_run",
        arguments={
            "command": "prokka_annotate --input assembly.fasta --outdir /tmp/annot"
        },
    )
    result = orchestrator._step_validation_agent(step, cwd=None)

    # Must not emit missing_tool:prokka_annotate.
    assert not any(
        issue.startswith("missing_tool:prokka_annotate")
        for issue in result.get("issues", [])
    ), f"Fix #12 regressed; issues={result.get('issues')}"
    # Command should now invoke prokka directly.
    rewritten = result.get("arguments", {}).get("command", "")
    assert "prokka" in rewritten and "prokka_annotate" not in rewritten, (
        f"Expected prokka_annotate→prokka rewrite; got: {rewritten!r}"
    )
    assert any(
        "replaced prokka_annotate with prokka" in fix
        for fix in result.get("fixes", [])
    ), f"Expected rewrite fix log; got fixes={result.get('fixes')}"


def test_step_validation_prokka_falls_through_to_prodigal_fix_15(monkeypatch):
    """Fix #15: when neither prokka_annotate nor prokka is on PATH but
    prodigal is available, the validator must rewrite the command to
    invoke prodigal directly.

    Observed in exp26 on this machine: pixi ships prodigal but not
    prokka. Fix #12's equivalence list only named ``prokka``, so the
    validator still reported ``missing_tool:prokka`` and blocked every
    bash_run calling prokka_annotate. Fix #15 extends the chain:
    prokka_annotate → prokka → prodigal_annotate → prodigal.
    """
    import shutil as _shutil
    orchestrator = _orchestrator_stub()

    # Pretend only prodigal is installed.
    real_which = _shutil.which

    def fake_which(name, *a, **kw):
        if name in ("prokka_annotate", "prokka", "prodigal_annotate"):
            return None
        if name == "prodigal":
            return "/opt/homebrew/bin/prodigal"
        return real_which(name, *a, **kw)

    monkeypatch.setattr(
        "bio_harness.agents.orchestrator.shutil.which", fake_which
    )

    step = SimpleNamespace(
        tool_name="bash_run",
        arguments={
            "command": "prokka_annotate --input assembly.fasta --outdir /tmp/annot"
        },
    )
    result = orchestrator._step_validation_agent(step, cwd=None)

    issues = result.get("issues", [])
    assert not any(
        issue.startswith("missing_tool:") for issue in issues
    ), f"Fix #15: expected no missing_tool issues; got {issues!r}"
    rewritten = result.get("arguments", {}).get("command", "")
    assert "prodigal" in rewritten, (
        f"Expected command to be rewritten to use prodigal; got {rewritten!r}"
    )
    # Either prokka_annotate was directly rewritten to prodigal, or it
    # went via prokka → prodigal. Check the fixes log records it.
    fixes = result.get("fixes", [])
    assert any("prodigal" in f for f in fixes), (
        f"Expected a prodigal rewrite in fixes log; got {fixes!r}"
    )


def test_detect_failure_marker_identifies_empty_input_guard():
    assert detect_failure_marker("__EMPTY_INPUT_FILE__:/tmp/a:/tmp/b") == "__EMPTY_INPUT_FILE__:"


def test_detect_failure_marker_identifies_missing_fastq_group():
    assert detect_failure_marker("__NO_CONTROL_FASTQ__") == "__NO_CONTROL_FASTQ__"
