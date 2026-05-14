from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bio_harness.core.first_run_setup import build_first_run_setup_status
from bio_harness.core.model_catalog import DEFAULT_PUBLIC_MODEL_ID
from scripts import first_run_setup as first_run_setup_cli


def test_first_run_setup_cli_help_succeeds_on_clean_interpreter() -> None:
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "first_run_setup.py"

    completed = subprocess.run(
        [sys.executable, "-S", str(script_path), "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert completed.returncode == 0
    assert "Run or preview the Bio-Harness first-run setup flow." in completed.stdout


def test_first_run_setup_creates_selected_dir_before_doctor(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    selected_dir = tmp_path / "fresh_workspace"
    output_json = tmp_path / "receipt.json"

    def fake_doctor(*, selected_dir: Path, **_kwargs):
        assert selected_dir.exists()
        return {"ready": True}

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "first_run_setup.py",
            "--skip-bootstrap",
            "--dry-run",
            "--selected-dir",
            str(selected_dir),
            "--json",
            "--output-json",
            str(output_json),
        ],
    )
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor.assess_harness_doctor",
        fake_doctor,
    )
    monkeypatch.setattr(
        "bio_harness.core.llm_setup_support.build_llm_setup_report",
        lambda **_kwargs: {
            "ready": True,
            "model_present": True,
            "available_models": [{"name": DEFAULT_PUBLIC_MODEL_ID}],
        },
    )

    assert first_run_setup_cli.main() == 0
    assert selected_dir.exists()
    assert output_json.exists()
    assert "first_run_status" in capsys.readouterr().out


def _base_llm_report(**overrides):
    report = {
        "backend_name": "ollama",
        "ready": False,
        "backend_reachable": True,
        "model_present": False,
        "ollama_command_available": True,
        "available_models": [],
    }
    report.update(overrides)
    return report


def test_first_run_status_requests_environment_setup_when_doctor_missing() -> None:
    status = build_first_run_setup_status(
        llm_setup_report=_base_llm_report(),
        free_disk_gb=200.0,
        available_ram_gb=128.0,
    )

    assert status["setup_complete"] is False
    assert status["environment_ready"] is None
    assert status["next_actions"][0]["id"] == "run_environment_setup"


def test_first_run_status_surfaces_missing_ollama_install() -> None:
    status = build_first_run_setup_status(
        doctor_report={"ready": True},
        llm_setup_report=_base_llm_report(
            backend_reachable=False,
            ollama_command_available=False,
        ),
        free_disk_gb=200.0,
        available_ram_gb=128.0,
    )

    action_ids = [row["id"] for row in status["next_actions"]]
    assert "install_ollama" in action_ids
    assert "pull_model" not in action_ids


def test_first_run_status_requests_ollama_start_before_model_pull() -> None:
    status = build_first_run_setup_status(
        doctor_report={"ready": True},
        llm_setup_report=_base_llm_report(backend_reachable=False),
        free_disk_gb=200.0,
        available_ram_gb=128.0,
    )

    assert status["next_actions"][0]["id"] == "start_ollama"


def test_first_run_status_blocks_model_pull_when_disk_is_insufficient() -> None:
    status = build_first_run_setup_status(
        doctor_report={"ready": True},
        llm_setup_report=_base_llm_report(),
        free_disk_gb=5.0,
        available_ram_gb=128.0,
    )

    assert status["recommended_model"]["model_id"] == DEFAULT_PUBLIC_MODEL_ID
    assert status["recommended_model_resource_assessment"]["disk_ok"] is False
    assert status["next_actions"][0]["id"] == "free_disk_for_model"


def test_first_run_status_requests_pull_for_missing_recommended_model() -> None:
    status = build_first_run_setup_status(
        doctor_report={"ready": True},
        llm_setup_report=_base_llm_report(),
        free_disk_gb=200.0,
        available_ram_gb=128.0,
    )

    assert status["next_actions"][0]["id"] == "pull_model"


def test_first_run_status_is_complete_when_environment_and_model_are_ready() -> None:
    status = build_first_run_setup_status(
        doctor_report={"ready": True},
        llm_setup_report=_base_llm_report(
            ready=True,
            model_present=True,
            available_models=[{"name": DEFAULT_PUBLIC_MODEL_ID, "size_gb": 51.0}],
        ),
        free_disk_gb=200.0,
        available_ram_gb=128.0,
    )

    assert status["setup_complete"] is True
    assert status["recommended_model"]["installed"] is True
    assert status["next_actions"][0]["id"] == "run_mini_preflight"
