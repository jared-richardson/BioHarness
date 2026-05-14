from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bio_harness.core.harness_doctor import assess_harness_doctor


def test_doctor_cli_help_succeeds_on_clean_interpreter() -> None:
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "doctor_bioharness.py"

    completed = subprocess.run(
        [sys.executable, "-S", str(script_path), "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert completed.returncode == 0
    assert "Run a deterministic Bio-Harness self-check" in completed.stdout


def test_assess_harness_doctor_reports_launcher_and_reference_state(monkeypatch, tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "genome.fa").write_text(">chr1\nACGT\n", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.core.harness_doctor.build_tool_installation_plan",
        lambda tool_names, skill_names: {
            "requested_tools": ["prokka", "samtools"],
            "pixi_installable_missing_tools": ["samtools"],
            "isolated_recipe_missing_tools": ["prokka"],
            "manual_install_required_tools": [],
        },
    )
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor.load_tool_launchers",
        lambda: {"prokka": {"argv": ["/tmp/prokka"]}},
    )
    monkeypatch.setattr("bio_harness.core.harness_doctor.tool_launcher_available", lambda name: name == "prokka")
    monkeypatch.setattr("bio_harness.core.harness_doctor.requirement_available", lambda name: name == "prokka")
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor.assess_resource_preflight",
        lambda skill_names, selected_dir, min_free_disk_gb: {"ok": True, "warnings": []},
    )
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor._check_command_version",
        lambda argv: {"available": argv[0] == "docker", "path": f"/usr/bin/{argv[0]}", "version": "ok"},
    )

    payload = assess_harness_doctor(
        tool_names=["prokka", "samtools"],
        selected_dir=tmp_path,
        reference_root=refs,
    )

    assert payload["tool_launchers"]["prokka"]["available"] is True
    assert payload["reference_materialization_plan"]["primary_fasta"] == str(refs / "genome.fa")
    assert any("pixi installable tools missing but pixi is unavailable on PATH" in warning for warning in payload["warnings"])
    assert any("isolated launcher setup available" in warning for warning in payload["warnings"])


def test_assess_harness_doctor_can_include_llm_backend_probe(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor.build_tool_installation_plan",
        lambda tool_names, skill_names: {
            "requested_tools": [],
            "pixi_installable_missing_tools": [],
            "isolated_recipe_missing_tools": [],
            "manual_install_required_tools": [],
        },
    )
    monkeypatch.setattr("bio_harness.core.harness_doctor.load_tool_launchers", lambda: {})
    monkeypatch.setattr("bio_harness.core.harness_doctor.requirement_available", lambda _name: True)
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor.assess_resource_preflight",
        lambda skill_names, selected_dir, min_free_disk_gb: {"ok": True, "warnings": []},
    )
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor._check_command_version",
        lambda argv: {"available": True, "path": f"/usr/bin/{argv[0]}", "version": "ok"},
    )
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor.probe_llm_backend",
        lambda **kwargs: {"available": True, "message": "ok", "diagnostics": {"backend": "ollama"}},
    )

    payload = assess_harness_doctor(
        selected_dir=tmp_path,
        probe_llm_backend_status=True,
    )

    assert payload["llm_backend"]["available"] is True
    assert payload["ready"] is True


def test_assess_harness_doctor_reports_structured_llm_dependency_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor.build_tool_installation_plan",
        lambda tool_names, skill_names: {
            "requested_tools": [],
            "pixi_installable_missing_tools": [],
            "isolated_recipe_missing_tools": [],
            "manual_install_required_tools": [],
        },
    )
    monkeypatch.setattr("bio_harness.core.harness_doctor.load_tool_launchers", lambda: {})
    monkeypatch.setattr("bio_harness.core.harness_doctor.requirement_available", lambda _name: True)
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor.assess_resource_preflight",
        lambda skill_names, selected_dir, min_free_disk_gb: {"ok": True, "warnings": []},
    )
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor._check_command_version",
        lambda argv: {"available": True, "path": f"/usr/bin/{argv[0]}", "version": "ok"},
    )

    def _missing_llm_import() -> type[object]:
        raise ModuleNotFoundError("No module named 'httpx'")

    monkeypatch.setattr("bio_harness.core.llm_backend_probe._load_bio_llm", _missing_llm_import)

    payload = assess_harness_doctor(
        selected_dir=tmp_path,
        probe_llm_backend_status=True,
    )

    assert payload["llm_backend"]["available"] is False
    assert payload["llm_backend"]["exception_class"] == "ModuleNotFoundError"
    assert "llm runtime dependencies unavailable" in payload["llm_backend"]["message"]
    assert any("llm backend unavailable" in warning for warning in payload["warnings"])
