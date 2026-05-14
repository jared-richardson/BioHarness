from __future__ import annotations

# ruff: noqa: F403,F405
from tests.core_cases.harness_guard_support import *

def test_prepare_plan_strict_llm_planning_rejects_contract_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        """{
  "thought_process": "minimal",
  "plan": [{"tool_name": "bash_run", "arguments": {"command": "echo hello"}, "step_id": 1}]
}""",
        encoding="utf-8",
    )
    cfg = HarnessConfig(
        prompt="Run differential expression analysis comparing control vs treatment with replicates.",
        selected_dir=selected_dir,
        data_root=data_root,
        workspace_root=selected_dir,
        max_repairs=0,
        heartbeat_seconds=15,
        stall_timeout_seconds=45,
        live_process_grace_seconds=900,
        model_name=None,
        host=None,
        auto_install_missing_tools=False,
        allow_replan=True,
        allow_canonicalize=True,
        plan_path=plan_path,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    monkeypatch.setenv("BIO_HARNESS_STRICT_LLM_PLANNING", "1")
    harness.orchestrator.build_analysis_spec = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "analysis_type": "rna_seq_differential_expression",
        "chosen_method": "DESeq2",
        "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
        "protocol_grounding": {},
    }

    with pytest.raises(ValueError, match="failed contract validation"):
        harness._prepare_plan()
def test_select_sample_r1_script_supports_multi_tag_groups(tmp_path: Path):
    manifest = tmp_path / "manifest.txt"
    out_file = tmp_path / "control_r1.txt"
    entries = [
        "/data/clip_1/1_S1_R1_001.fastq",
        "/data/clip_1/2_S2_R1_001.fastq",
        "/data/clip_1/3_S3_R1_001.fastq",
        "/data/clip_1/6_S6_R1_001.fastq",
    ]
    manifest.write_text("\n".join(entries) + "\n", encoding="utf-8")

    script = Path("bio_harness/pipeline_scripts/select_sample_r1.sh").resolve()
    result = subprocess.run(
        ["bash", str(script), str(manifest), "S1,S2,S3", str(out_file), "CONTROL"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    selected = [ln.strip() for ln in out_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert selected == entries[:3]
    assert "__SELECTED_CONTROL_R1_COUNT__:3" in result.stdout


def test_select_sample_r1_script_fails_when_group_is_missing(tmp_path: Path):
    manifest = tmp_path / "manifest.txt"
    out_file = tmp_path / "control_r1.txt"
    manifest.write_text("/data/clip_1/6_S6_R1_001.fastq\n", encoding="utf-8")

    script = Path("bio_harness/pipeline_scripts/select_sample_r1.sh").resolve()
    result = subprocess.run(
        ["bash", str(script), str(manifest), "S1", str(out_file), "CONTROL"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "__NO_CONTROL_FASTQ__" in result.stdout
