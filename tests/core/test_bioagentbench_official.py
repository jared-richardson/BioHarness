from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from bio_harness.core.bioagentbench_official import (
    build_official_scoreboard,
    build_official_prompt,
    build_validator_argv,
    extract_model_config,
    official_report_bucket,
    render_official_scoreboard_markdown,
    resolve_manifest_entries,
    summarize_official_run,
)
from bio_harness.core.benchmark_policy import BIOAGENTBENCH_PLANNING_STRICT_POLICY, OFFICIAL_BIOAGENTBENCH_POLICY
from scripts.run_bioagentbench_official import (
    _apply_manifest_runner_defaults,
    _build_harness_env,
    _run_harness_subprocess,
    _task_timeout_seconds,
)


def test_resolve_manifest_entries_resolves_relative_paths(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    manifest_dir = project_root / "benchmark_data"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "task_id": "demo",
                    "task_dir": "workspace/tasks/demo",
                    "data_root": "workspace/tasks/demo/data",
                    "runs_root": "workspace/runs/demo",
                    "validator_script": "scripts/validate_demo.py",
                }
            ]
        ),
        encoding="utf-8",
    )

    entries = resolve_manifest_entries(manifest_path)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["task_dir"] == str((project_root / "workspace/tasks/demo").resolve(strict=False))
    assert entry["data_root"] == str((project_root / "workspace/tasks/demo/data").resolve(strict=False))
    assert entry["runs_root"] == str((project_root / "workspace/runs/demo").resolve(strict=False))
    assert entry["validator_script"] == str((project_root / "scripts/validate_demo.py").resolve(strict=False))


def test_supplemental_manifest_declares_comparative_genomics_case() -> None:
    manifest_path = Path(__file__).resolve().parents[2] / "benchmark_data" / "bioagentbench_supplemental_manifest.json"

    entries = resolve_manifest_entries(manifest_path)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["task_id"] == "comparative_genomics"
    assert entry["validator_script"].endswith("scripts/validate_comparative_genomics.py")
    assert entry["validator_args"] == [
        "{task_dir}/truth.json",
        "{selected_dir}/output",
    ]


def test_build_official_prompt_avoids_selected_dir_paths_and_keeps_guard(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    selected_dir = tmp_path / "run"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "reads_1.fq.gz").write_text("stub\n", encoding="utf-8")
    (data_root / "reads_2.fq.gz").write_text("stub\n", encoding="utf-8")
    entry = {
        "task_id": "transcript-quant",
        "task_name": "Transcript Quantification",
        "data_root": str(data_root),
        "task_prompt": "Quantify transcripts from the provided paired-end reads.",
        "output_requirements": ["Write any intermediate files under {selected_dir}/work."],
        "deliverables": [
            {
                "path": "final/transcript_counts.tsv",
                "description": "Write the final transcript-count TSV.",
                "columns": ["transcript_id", "count"],
            }
        ],
    }

    prompt = build_official_prompt(entry, selected_dir=selected_dir)

    assert "reads_1.fq.gz, reads_2.fq.gz" in prompt
    assert "canonical relative location final/transcript_counts.tsv" in prompt
    assert "the selected output directory/work" in prompt
    assert str(selected_dir) not in prompt
    assert str(data_root) not in prompt
    assert "Do not invent or emit filesystem paths in the plan" in prompt
    assert "Do not read benchmark truth files" in prompt
    assert "results/" not in prompt


def test_build_official_prompt_renders_task_prompt_placeholders(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    task_dir = tmp_path / "task"
    selected_dir = tmp_path / "run"
    data_root.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    (data_root / "sample_R1.fastq.gz").write_text("stub\n", encoding="utf-8")
    entry = {
        "task_id": "metagenomics",
        "task_name": "Metagenomics",
        "task_dir": str(task_dir),
        "data_root": str(data_root),
        "task_prompt": "Use the prebuilt Kraken2 database at {task_dir}/references/kraken2_db.",
        "deliverables": [],
    }

    prompt = build_official_prompt(entry, selected_dir=selected_dir)

    assert "the task directory/references/kraken2_db" in prompt
    assert str(task_dir) not in prompt
    assert "{task_dir}" not in prompt


def test_build_official_prompt_dedupes_repeated_output_lines(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    selected_dir = tmp_path / "run"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "sample_R1.fastq.gz").write_text("stub\n", encoding="utf-8")
    entry = {
        "task_id": "viral-metagenomics",
        "task_name": "Viral Metagenomics",
        "data_root": str(data_root),
        "task_prompt": "Write the detected viruses list at {selected_dir}/output/detected_viruses.txt.",
        "output_requirements": [
            "Write the detected viruses list at {selected_dir}/output/detected_viruses.txt.",
        ],
        "deliverables": [
            {
                "path": "output/detected_viruses.txt",
                "description": "Write the detected viruses list.",
            }
        ],
    }

    prompt = build_official_prompt(entry, selected_dir=selected_dir)
    expected = "Write the detected viruses list at the selected output directory/output/detected_viruses.txt."

    assert prompt.count(expected) == 1


def test_build_validator_argv_renders_placeholders(tmp_path: Path) -> None:
    selected_dir = tmp_path / "attempt1"
    entry = {
        "task_id": "evolution",
        "task_dir": str(tmp_path / "task"),
        "data_root": str(tmp_path / "task" / "data"),
        "runs_root": str(tmp_path / "runs"),
        "validator_script": str(tmp_path / "scripts" / "validate_demo.py"),
        "validator_args": [
            "{task_dir}/results/truth.csv",
            "{selected_dir}/final/output.csv",
        ],
    }

    argv = build_validator_argv(entry, selected_dir=selected_dir, python_executable="python3")

    assert argv[0] == "python3"
    assert argv[1].endswith("validate_demo.py")
    assert argv[2].endswith("results/truth.csv")
    assert argv[3] == str(selected_dir / "final" / "output.csv")


def test_apply_manifest_runner_defaults_sets_hierarchical_mode_when_configured() -> None:
    env = {}
    entry = {
        "runner_defaults": {
            "planner_hierarchical_mode": "off",
        }
    }
    args = Namespace(
        strict_llm_planning=False,
        planner_attempt_timeout_seconds=0,
        llm_timeout_seconds=0,
    )

    merged = _apply_manifest_runner_defaults(env, entry=entry, args=args)

    assert merged["BIO_HARNESS_PLANNER_HIERARCHICAL_MODE"] == "off"


def test_build_harness_env_sets_planner_model_override() -> None:
    args = Namespace(
        strict_llm_planning=False,
        planner_model_name="codellama:34b",
        planner_attempt_timeout_seconds=0,
        llm_timeout_seconds=0,
    )

    merged = _build_harness_env(args)

    assert merged["BIO_HARNESS_MODEL_HEAVY"] == "codellama:34b"


def test_build_harness_env_defaults_planner_to_executor_in_planning_strict() -> None:
    args = Namespace(
        strict_llm_planning=False,
        benchmark_policy=BIOAGENTBENCH_PLANNING_STRICT_POLICY,
        planner_model_name="",
        executor_model_name="qwen3-coder-next:latest",
        planner_attempt_timeout_seconds=0,
        llm_timeout_seconds=0,
    )

    merged = _build_harness_env(args)

    assert merged["BIO_HARNESS_MODEL_HEAVY"] == "qwen3-coder-next:latest"


def test_build_harness_env_enables_hierarchical_mode_for_planning_strict() -> None:
    args = Namespace(
        strict_llm_planning=False,
        benchmark_policy=BIOAGENTBENCH_PLANNING_STRICT_POLICY,
        planner_model_name="",
        executor_model_name="",
        planner_attempt_timeout_seconds=0,
        llm_timeout_seconds=0,
    )

    merged = _build_harness_env(args)

    assert merged["BIO_HARNESS_PLANNER_HIERARCHICAL_MODE"] == "always"


def test_build_harness_env_defaults_qwen_coder_next_for_planning_strict() -> None:
    args = Namespace(
        strict_llm_planning=False,
        benchmark_policy=BIOAGENTBENCH_PLANNING_STRICT_POLICY,
        planner_model_name="",
        executor_model_name="",
        planner_attempt_timeout_seconds=0,
        llm_timeout_seconds=0,
    )

    merged = _build_harness_env(args)

    assert merged["BIO_HARNESS_MODEL"] == "qwen3-coder-next:latest"
    assert merged["BIO_HARNESS_MODEL_HEAVY"] == "qwen3-coder-next:latest"


def test_apply_manifest_runner_defaults_sets_planner_model_when_configured() -> None:
    env = {}
    entry = {
        "runner_defaults": {
            "planner_model_name": "codellama:34b",
        }
    }
    args = Namespace(
        strict_llm_planning=False,
        planner_model_name="",
        planner_attempt_timeout_seconds=0,
        llm_timeout_seconds=0,
    )

    merged = _apply_manifest_runner_defaults(env, entry=entry, args=args)

    assert merged["BIO_HARNESS_MODEL_HEAVY"] == "codellama:34b"


def test_apply_manifest_runner_defaults_does_not_override_explicit_model_overrides() -> None:
    env = {
        "BIO_HARNESS_MODEL_HEAVY": "gemma4:26b",
        "BIO_HARNESS_MODEL": "gemma4:26b",
    }
    entry = {
        "runner_defaults": {
            "planner_model_name": "qwen3-coder-next:latest",
            "executor_model_name": "qwen3-coder-next:latest",
        }
    }
    args = Namespace(
        strict_llm_planning=False,
        planner_model_name="gemma4:26b",
        executor_model_name="gemma4:26b",
        planner_attempt_timeout_seconds=0,
        llm_timeout_seconds=0,
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
    )

    merged = _apply_manifest_runner_defaults(env, entry=entry, args=args)

    assert merged["BIO_HARNESS_MODEL_HEAVY"] == "gemma4:26b"
    assert merged["BIO_HARNESS_MODEL"] == "gemma4:26b"


def test_summarize_official_run_reports_generic_fallback_bucket(tmp_path: Path) -> None:
    selected_dir = tmp_path / "attempt1"
    result_obj = {
        "status": "completed",
        "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
        "run_dir": str(tmp_path / "run_artifacts"),
        "result_json": str(selected_dir / "result.json"),
        "assistance_manifest_file": str(tmp_path / "run_artifacts" / "assistance_manifest.json"),
        "assistance_manifest": {
            "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
            "generic_template_fallback_used": True,
            "generic_template_fallback": {"selected_pipeline_id": "generic_pipeline"},
            "protocol_template_fallback_used": False,
            "forbidden_benchmark_sources_visible": False,
            "forbidden_benchmark_sources": [],
            "leakage_guard_active": True,
        },
    }
    entry = {"task_id": "demo", "task_name": "Demo Task", "validator_script": "scripts/validate_demo.py"}

    row = summarize_official_run(
        entry=entry,
        selected_dir=selected_dir,
        result_obj=result_obj,
        harness_exit_code=0,
        validator_exit_code=0,
        validator_stdout="ok",
    )

    assert row["generic_template_fallback_used"] is True
    assert row["generic_template_fallback_pipeline_id"] == "generic_pipeline"
    assert row["official_report_bucket"] == "official_blind_with_generic_fallback"
    assert row["validation_passed"] is True


def test_extract_model_config_reads_run_manifest_and_planner_trace(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    planner_dir = run_dir / "planner"
    planner_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "model_name": "qwen3-coder-next",
                "llm_backend": "ollama",
                "host": "http://127.0.0.1:11434",
            }
        ),
        encoding="utf-8",
    )
    (planner_dir / "0001_planner_start.json").write_text(
        json.dumps(
            {
                "model_name": "qwen3-coder-next",
                "payload": {
                    "planning_model": "codellama:latest",
                    "fast_model": "qwen3-coder-next",
                },
            }
        ),
        encoding="utf-8",
    )

    config = extract_model_config({"run_dir": str(run_dir)})

    assert config["executor_model_name"] == "qwen3-coder-next"
    assert config["planner_model_name"] == "codellama:latest"
    assert config["llm_backend"] == "ollama"
    assert config["host"] == "http://127.0.0.1:11434"
    assert config["dual_model_active"] is True


def test_build_official_scoreboard_aggregates_rates_and_model_configs() -> None:
    rows = [
        {
            "task_id": "evolution",
            "task_name": "Evolution",
            "official_report_bucket": "official_blind_clean",
            "validation_passed": True,
            "validation_configured": True,
            "executor_model_name": "qwen3-coder-next",
            "planner_model_name": "codellama:latest",
            "llm_backend": "ollama",
            "host": "http://127.0.0.1:11434",
            "selected_dir": "/tmp/evolution_r1",
        },
        {
            "task_id": "evolution",
            "task_name": "Evolution",
            "official_report_bucket": "invalid_for_official_reporting",
            "validation_passed": False,
            "validation_configured": True,
            "executor_model_name": "qwen3-coder-next",
            "planner_model_name": "codellama:latest",
            "llm_backend": "ollama",
            "host": "http://127.0.0.1:11434",
            "selected_dir": "/tmp/evolution_r2",
        },
        {
            "task_id": "giab",
            "task_name": "GIAB",
            "official_report_bucket": "official_blind_clean",
            "validation_passed": True,
            "validation_configured": True,
            "executor_model_name": "qwen3-coder-next",
            "planner_model_name": "qwen3-coder-next",
            "llm_backend": "ollama",
            "host": "http://127.0.0.1:11434",
            "selected_dir": "/tmp/giab_r1",
        },
    ]

    scoreboard = build_official_scoreboard(rows)

    assert scoreboard["attempt_count"] == 3
    assert scoreboard["overall"]["official_blind_clean_count"] == 2
    assert scoreboard["overall"]["official_blind_clean_rate"] == 0.6667
    assert scoreboard["overall"]["invalid_for_official_reporting_count"] == 1
    assert scoreboard["overall"]["invalid_for_official_reporting_rate"] == 0.3333
    assert scoreboard["overall"]["validator_backed_scientific_pass_count"] == 2
    assert scoreboard["overall"]["validator_backed_scientific_pass_rate"] == 0.6667
    assert len(scoreboard["model_configs"]) == 2
    assert scoreboard["per_task"][0]["task_id"] == "evolution"
    assert scoreboard["per_task"][0]["attempt_count"] == 2
    assert scoreboard["per_task"][0]["official_blind_clean_count"] == 1


def test_render_official_scoreboard_markdown_includes_model_config_and_rates() -> None:
    scoreboard = {
        "overall": {
            "official_blind_clean_count": 2,
            "official_blind_clean_rate": 1.0,
            "invalid_for_official_reporting_count": 0,
            "invalid_for_official_reporting_rate": 0.0,
            "validator_backed_scientific_pass_count": 2,
            "validator_backed_scientific_pass_rate": 1.0,
        },
        "model_configs": [
            {
                "executor_model_name": "qwen3-coder-next",
                "planner_model_name": "codellama:latest",
                "llm_backend": "ollama",
                "attempt_count": 2,
                "official_blind_clean_count": 2,
                "invalid_for_official_reporting_count": 0,
                "validator_backed_scientific_pass_count": 2,
            }
        ],
        "per_task": [
            {
                "task_id": "evolution",
                "attempt_count": 2,
                "official_blind_clean_count": 2,
                "official_blind_clean_rate": 1.0,
                "invalid_for_official_reporting_count": 0,
                "invalid_for_official_reporting_rate": 0.0,
                "validator_backed_scientific_pass_count": 2,
                "validator_backed_scientific_pass_rate": 1.0,
            }
        ],
    }

    markdown = render_official_scoreboard_markdown(scoreboard)

    assert "Official blind clean" in markdown
    assert "qwen3-coder-next" in markdown
    assert "codellama:latest" in markdown
    assert "evolution" in markdown


def test_build_official_scoreboard_backfills_model_config_from_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    planner_dir = run_dir / "planner"
    planner_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "model_name": "qwen3-coder-next",
                "llm_backend": "ollama",
                "host": "http://127.0.0.1:11434",
            }
        ),
        encoding="utf-8",
    )
    (planner_dir / "0001_planner_start.json").write_text(
        json.dumps(
            {
                "payload": {
                    "planning_model": "codellama:latest",
                    "fast_model": "qwen3-coder-next",
                }
            }
        ),
        encoding="utf-8",
    )
    scoreboard = build_official_scoreboard(
        [
            {
                "task_id": "evolution",
                "task_name": "Evolution",
                "official_report_bucket": "official_blind_clean",
                "validation_passed": True,
                "validation_configured": True,
                "selected_dir": "/tmp/evolution_r1",
                "run_dir": str(run_dir),
            }
        ]
    )

    assert scoreboard["model_configs"][0]["executor_model_name"] == "qwen3-coder-next"
    assert scoreboard["model_configs"][0]["planner_model_name"] == "codellama:latest"


def test_official_report_bucket_marks_leakage_invalid() -> None:
    result_obj = {
        "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
        "assistance_manifest": {
            "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
            "generic_template_fallback_used": False,
            "forbidden_benchmark_sources_visible": True,
            "forbidden_benchmark_sources": ["/tmp/results/truth.csv"],
        },
    }

    bucket = official_report_bucket(result_obj)

    assert bucket == "invalid_for_official_reporting"


def test_official_report_bucket_marks_harness_failure_invalid() -> None:
    result_obj = {
        "status": "failed",
        "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
        "assistance_manifest": {
            "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
            "generic_template_fallback_used": False,
            "protocol_template_fallback_used": False,
            "forbidden_benchmark_sources_visible": False,
            "forbidden_benchmark_sources": [],
            "leakage_guard_active": True,
        },
    }

    bucket = official_report_bucket(result_obj, validation_configured=True, validation_passed=False)

    assert bucket == "invalid_for_official_reporting"


def test_official_report_bucket_marks_validation_failure_invalid() -> None:
    result_obj = {
        "status": "completed",
        "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
        "assistance_manifest": {
            "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
            "generic_template_fallback_used": False,
            "protocol_template_fallback_used": False,
            "forbidden_benchmark_sources_visible": False,
            "forbidden_benchmark_sources": [],
            "leakage_guard_active": True,
        },
    }

    bucket = official_report_bucket(result_obj, validation_configured=True, validation_passed=False)

    assert bucket == "invalid_for_official_reporting"


def test_official_report_bucket_marks_protocol_fallback_invalid() -> None:
    result_obj = {
        "status": "completed",
        "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
        "assistance_manifest": {
            "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
            "generic_template_fallback_used": False,
            "protocol_template_fallback_used": True,
            "forbidden_benchmark_sources_visible": False,
            "forbidden_benchmark_sources": [],
            "leakage_guard_active": True,
        },
    }

    bucket = official_report_bucket(result_obj, validation_configured=False, validation_passed=None)

    assert bucket == "invalid_for_official_reporting"


def test_build_harness_env_applies_planner_overrides(monkeypatch) -> None:
    monkeypatch.setenv("UNCHANGED_SENTINEL", "keep")
    args = Namespace(
        strict_llm_planning=True,
        planner_attempt_timeout_seconds=240,
        llm_timeout_seconds=180,
    )

    env = _build_harness_env(args)

    assert env["UNCHANGED_SENTINEL"] == "keep"
    assert env["BIO_HARNESS_STRICT_LLM_PLANNING"] == "1"
    assert env["BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS"] == "240"
    assert env["BIO_HARNESS_LLM_TIMEOUT_SECONDS"] == "180"


def test_manifest_runner_defaults_apply_when_cli_does_not_override(monkeypatch) -> None:
    monkeypatch.delenv("BIO_HARNESS_STRICT_LLM_PLANNING", raising=False)
    monkeypatch.delenv("BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("BIO_HARNESS_LLM_TIMEOUT_SECONDS", raising=False)
    args = Namespace(
        strict_llm_planning=False,
        planner_attempt_timeout_seconds=0,
        llm_timeout_seconds=0,
    )
    entry = {
        "runner_defaults": {
            "strict_llm_planning": True,
            "planner_attempt_timeout_seconds": 210,
            "llm_timeout_seconds": 150,
        }
    }

    env = _apply_manifest_runner_defaults(_build_harness_env(args), entry=entry, args=args)

    assert env["BIO_HARNESS_STRICT_LLM_PLANNING"] == "1"
    assert env["BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS"] == "210"
    assert env["BIO_HARNESS_LLM_TIMEOUT_SECONDS"] == "150"


def test_manifest_runner_defaults_do_not_override_cli_values(monkeypatch) -> None:
    monkeypatch.delenv("BIO_HARNESS_STRICT_LLM_PLANNING", raising=False)
    monkeypatch.delenv("BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("BIO_HARNESS_LLM_TIMEOUT_SECONDS", raising=False)
    args = Namespace(
        strict_llm_planning=True,
        planner_attempt_timeout_seconds=240,
        llm_timeout_seconds=180,
    )
    entry = {
        "runner_defaults": {
            "strict_llm_planning": False,
            "planner_attempt_timeout_seconds": 120,
            "llm_timeout_seconds": 90,
        }
    }

    env = _apply_manifest_runner_defaults(_build_harness_env(args), entry=entry, args=args)

    assert env["BIO_HARNESS_STRICT_LLM_PLANNING"] == "1"
    assert env["BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS"] == "240"
    assert env["BIO_HARNESS_LLM_TIMEOUT_SECONDS"] == "180"


def test_manifest_runner_defaults_apply_official_timeout_defaults(monkeypatch) -> None:
    monkeypatch.delenv("BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("BIO_HARNESS_LLM_TIMEOUT_SECONDS", raising=False)
    args = Namespace(
        strict_llm_planning=False,
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
        planner_attempt_timeout_seconds=0,
        llm_timeout_seconds=0,
    )

    env = _apply_manifest_runner_defaults(_build_harness_env(args), entry={"runner_defaults": {}}, args=args)

    assert env["BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS"] == "180"
    assert env["BIO_HARNESS_LLM_TIMEOUT_SECONDS"] == "120"


def test_task_timeout_seconds_uses_manifest_when_cli_disabled() -> None:
    entry = {"runner_defaults": {"task_timeout_seconds": 2700}}
    args = Namespace(task_timeout_seconds=0)

    timeout_seconds = _task_timeout_seconds(entry, args)

    assert timeout_seconds == 2700


def test_run_harness_subprocess_terminates_after_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import scripts.run_bioagentbench_official as official_mod

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.terminated = 0
            self.killed = 0

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.terminated += 1
            self.returncode = -15

        def wait(self, timeout=None) -> int:
            return int(self.returncode or -15)

        def kill(self) -> None:
            self.killed += 1
            self.returncode = -9

    fake_proc = _FakeProc()
    clock = {"now": 0.0}

    def _fake_monotonic() -> float:
        value = clock["now"]
        clock["now"] += 0.6
        return value

    monkeypatch.setattr(official_mod.subprocess, "Popen", lambda *args, **kwargs: fake_proc)
    monkeypatch.setattr(official_mod.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(official_mod.time, "sleep", lambda _seconds: None)

    returncode, timed_out = _run_harness_subprocess(
        cmd=["python3", "scripts/run_agent_e2e.py"],
        env={},
        log_path=tmp_path / "harness.log",
        timeout_seconds=1,
    )

    assert timed_out is True
    assert returncode == -15
    assert fake_proc.terminated == 1
    assert fake_proc.killed == 0
    assert "Task watchdog exceeded 1s" in (tmp_path / "harness.log").read_text(encoding="utf-8")
