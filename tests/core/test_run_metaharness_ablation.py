from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from bio_harness.core.bioagentbench_official import build_official_prompt
from bio_harness.core.benchmark_policy import OFFICIAL_BIOAGENTBENCH_POLICY
from scripts.run_bioagentbench_invocation_support import OfficialHarnessInvocationOptions
from run_metaharness_ablation import (  # noqa: E402
    ABLATION_VARIANTS,
    DEFAULT_TASK_TIMEOUT_SECONDS,
    _build_agent_command,
    _build_task_invocation,
    _prepare_selected_dir,
    _task_timeout_seconds,
)


def _giab_task_config(tmp_path: Path) -> dict[str, object]:
    data_root = tmp_path / "data"
    task_dir = tmp_path / "task"
    runs_root = tmp_path / "official_runs" / "giab"
    data_root.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    (data_root / "NA12878.cram").write_text("stub\n", encoding="utf-8")
    (task_dir / "results").mkdir(parents=True, exist_ok=True)
    (task_dir / "results" / "truth_variants.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    return {
        "task_id": "giab",
        "task_name": "GIAB Germline Variant Calling",
        "task_dir": str(task_dir),
        "data_root": str(data_root),
        "runs_root": str(runs_root),
        "task_prompt": "Use the provided NA12878 sequencing data and reference genome to perform germline variant calling.",
        "runner_defaults": {
            "strict_llm_planning": True,
            "planner_attempt_timeout_seconds": 600,
            "llm_timeout_seconds": 300,
            "planner_model_name": "manifest-planner",
            "executor_model_name": "manifest-executor",
        },
        "deliverables": [
            {
                "path": "final/variants.vcf",
                "description": "Write the final germline variant calls as a VCF.",
            }
        ],
        "validator_script": str(tmp_path / "validate_giab.py"),
        "validator_args": [
            "{task_dir}/results/truth_variants.vcf",
            "{selected_dir}/final/variants.vcf",
        ],
    }


def test_prepare_selected_dir_removes_stale_outputs(tmp_path: Path) -> None:
    runs_root = tmp_path / "official_runs" / "evolution"
    stale = runs_root / "ablation_baseline"
    stale.mkdir(parents=True)
    (stale / "result.json").write_text('{"status":"failed"}', encoding="utf-8")

    selected_dir = _prepare_selected_dir(runs_root, "baseline")

    assert selected_dir == stale
    assert selected_dir.is_dir()
    assert list(selected_dir.iterdir()) == []


def test_build_agent_command_uses_official_prompt_and_policy(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    task_config = _giab_task_config(tmp_path)
    command = _build_agent_command(
        task_config=task_config,
        selected_dir=selected_dir,
        options=OfficialHarnessInvocationOptions(),
    )
    prompt = command[command.index("--prompt") + 1]

    assert prompt == build_official_prompt(task_config, selected_dir=selected_dir)
    assert "final/variants.vcf" in prompt
    assert command[command.index("--benchmark-policy") + 1] == OFFICIAL_BIOAGENTBENCH_POLICY
    assert str(selected_dir / "result.json") in command


def test_build_task_invocation_layers_variant_overrides_after_manifest_defaults(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    task_config = _giab_task_config(tmp_path)

    command, env = _build_task_invocation(
        task_config,
        ABLATION_VARIANTS["qwen_baseline"],
        selected_dir=selected_dir,
    )

    assert command[command.index("--benchmark-policy") + 1] == OFFICIAL_BIOAGENTBENCH_POLICY
    assert env["BIO_HARNESS_STRICT_LLM_PLANNING"] == "1"
    assert env["BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS"] == "600"
    assert env["BIO_HARNESS_LLM_TIMEOUT_SECONDS"] == "300"
    assert env["BIO_HARNESS_MODEL"] == "qwen3-coder-next:latest"
    assert env["BIO_HARNESS_MODEL_HEAVY"] == "qwen3-coder-next:latest"


def test_build_task_invocation_keeps_no_recovery_max_repairs_override(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    task_config = _giab_task_config(tmp_path)

    command, _env = _build_task_invocation(
        task_config,
        ABLATION_VARIANTS["no_recovery"],
        selected_dir=selected_dir,
    )

    assert command.count("--max-repairs") == 1
    assert command[command.index("--max-repairs") + 1] == "0"


def test_task_timeout_seconds_prefers_cli_timeout() -> None:
    assert _task_timeout_seconds({}, 7200) == 7200


def test_task_timeout_seconds_uses_manifest_runner_default() -> None:
    task_config = {"runner_defaults": {"task_timeout_seconds": 6000}}

    assert _task_timeout_seconds(task_config, 0) == 6000


def test_task_timeout_seconds_falls_back_to_default() -> None:
    assert _task_timeout_seconds({}, 0) == DEFAULT_TASK_TIMEOUT_SECONDS
