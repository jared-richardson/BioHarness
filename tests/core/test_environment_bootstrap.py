from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bio_harness.core import environment_bootstrap
from bio_harness.core import tool_env
from bio_harness.core.harness_doctor import assess_harness_doctor
from bio_harness.core.install_receipts import write_install_receipt


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_bootstrap_cli_help_succeeds_on_clean_interpreter() -> None:
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "bootstrap_bioharness.py"

    completed = subprocess.run(
        [sys.executable, "-S", str(script_path), "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert completed.returncode == 0
    assert "Bootstrap a local Bio-Harness" in completed.stdout


def test_required_tools_for_skills_uses_skill_index_rows(monkeypatch):
    monkeypatch.setattr(
        environment_bootstrap,
        "_load_skill_rows",
        lambda: [
            {"name": "star_align", "tools_required": ["star", "samtools"]},
            {"name": "freebayes_call", "tools_required": ["freebayes", "samtools"]},
        ],
    )

    tools = environment_bootstrap.required_tools_for_skills(["star_align", "freebayes_call"])

    assert tools == ["star", "samtools", "freebayes"]


def test_build_tool_installation_plan_distinguishes_available_pixi_and_manual(monkeypatch):
    monkeypatch.setattr(
        environment_bootstrap,
        "requirement_available",
        lambda name: name == "scanpy",
    )

    plan = environment_bootstrap.build_tool_installation_plan(
        tool_names=["bowtie2", "cellranger", "scanpy", "majiq"],
    )

    assert plan["already_available_tools"] == ["scanpy"]
    assert plan["pixi_installable_missing_tools"] == ["bowtie2"]
    assert plan["pixi_environments"] == ["alignment-extra"]
    assert plan["manual_install_required_tools"] == ["cellranger", "majiq"]


def test_build_tool_installation_plan_splits_r_environments(monkeypatch):
    monkeypatch.setattr(
        environment_bootstrap,
        "requirement_available",
        lambda _name: False,
    )

    plan = environment_bootstrap.build_tool_installation_plan(
        tool_names=["edger", "dexseq", "seurat"],
    )

    assert plan["pixi_installable_missing_tools"] == ["dexseq", "edger", "seurat"]
    assert plan["pixi_environments"] == ["r-bulk", "r-splicing", "r-singlecell"]


def test_build_tool_installation_plan_routes_freebayes_skill_to_variant_extra(monkeypatch):
    monkeypatch.setattr(
        environment_bootstrap,
        "requirement_available",
        lambda _name: False,
    )
    monkeypatch.setattr(
        environment_bootstrap,
        "_load_skill_rows",
        lambda: [
            {"name": "freebayes_call", "tools_required": ["freebayes", "samtools"]},
        ],
    )

    plan = environment_bootstrap.build_tool_installation_plan(skill_names=["freebayes_call"])

    assert plan["requested_tools"] == ["freebayes", "samtools"]
    assert plan["pixi_installable_missing_tools"] == ["freebayes", "samtools"]
    assert plan["pixi_environments"] == ["variant-extra"]


def test_build_tool_installation_plan_separates_isolated_recipe_tools(monkeypatch):
    monkeypatch.setattr(
        environment_bootstrap,
        "requirement_available",
        lambda _name: False,
    )

    plan = environment_bootstrap.build_tool_installation_plan(
        tool_names=["cnvkit.py", "prokka", "STAR-Fusion", "cellranger"],
    )

    assert plan["isolated_recipe_missing_tools"] == ["STAR-Fusion", "cnvkit.py", "prokka"]
    assert plan["manual_install_required_tools"] == ["cellranger"]
    assert plan["pixi_installable_missing_tools"] == []


def test_bootstrap_commands_include_venv_and_requested_pixi_envs(tmp_path):
    commands = environment_bootstrap.bootstrap_commands(
        project_root=tmp_path,
        python_bin="/usr/bin/python3",
        venv_path=".venv",
        install_python=True,
        install_pixi=True,
        pixi_environments=["reports", "specialty-annotation"],
    )

    labels = [command.label for command in commands]
    assert labels == [
        "create_venv",
        "upgrade_pip",
        "install_venv_requirements",
        "install_editable_package",
        "install_pixi_default",
        "install_pixi_reports",
        "install_pixi_specialty-annotation",
    ]
    assert str(Path(commands[0].argv[-1]).name) == ".venv"


def test_run_bootstrap_commands_records_missing_pixi(tmp_path, monkeypatch) -> None:
    def _missing_pixi(*args, **kwargs):
        raise FileNotFoundError("pixi")

    monkeypatch.setattr(environment_bootstrap.subprocess, "run", _missing_pixi)

    rows = environment_bootstrap.run_bootstrap_commands(
        [
            environment_bootstrap.BootstrapCommand(
                label="install_pixi_default",
                argv=("pixi", "install", "--manifest-path", str(tmp_path / "pixi.toml")),
            )
        ],
        cwd=tmp_path,
        dry_run=False,
    )

    assert rows == [
        {
            "label": "install_pixi_default",
            "argv": ["pixi", "install", "--manifest-path", str(tmp_path / "pixi.toml")],
            "returncode": 127,
            "stdout_tail": "",
            "stderr_tail": (
                "pixi command not found on PATH; install pixi or rerun with "
                "--skip-pixi for a Python-only bootstrap."
            ),
            "status": "command_not_found",
            "missing_command": "pixi",
            "exception_class": "FileNotFoundError",
        }
    ]


def test_bootstrap_bioharness_environment_dry_run_returns_success(tmp_path, monkeypatch):
    monkeypatch.setattr(
        environment_bootstrap,
        "run_bootstrap_commands",
        lambda commands, *, cwd, dry_run=False: [
            {"label": command.label, "argv": list(command.argv), "returncode": 0, "dry_run": dry_run}
            for command in commands
        ],
    )
    monkeypatch.setattr(
        environment_bootstrap,
        "requirement_available",
        lambda _name: False,
    )

    report = environment_bootstrap.bootstrap_bioharness_environment(
        project_root=tmp_path,
        python_bin="/usr/bin/python3",
        venv_path=".venv",
        tool_names=["bowtie2", "multiqc"],
        install_all_known_pixi_envs=False,
        dry_run=True,
    )

    assert report["success"] is True
    assert report["install_plan"]["pixi_environments"] == ["reports", "alignment-extra"]


def test_bootstrap_bioharness_environment_reports_missing_pixi(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(environment_bootstrap, "requirement_available", lambda _name: False)
    monkeypatch.setattr(environment_bootstrap, "_pixi_command_available", lambda: False)

    def _fake_run(argv, cwd, capture_output, text, check):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(environment_bootstrap.subprocess, "run", _fake_run)

    report = environment_bootstrap.bootstrap_bioharness_environment(
        project_root=tmp_path,
        python_bin="/usr/bin/python3",
        venv_path=".venv",
        install_python=False,
        install_pixi=True,
        install_isolated=False,
        tool_names=["bowtie2"],
        dry_run=False,
    )

    assert report["success"] is False
    assert report["pixi_command_available"] is False
    assert report["pixi_command_missing"] is True
    assert report["commands"][0]["status"] == "command_not_found"
    assert report["commands"][0]["missing_command"] == "pixi"
    assert any("pixi command unavailable" in warning for warning in report["warnings"])


def test_bootstrap_environment_reports_remaining_missing_tools_after_install(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        environment_bootstrap,
        "run_bootstrap_commands",
        lambda commands, *, cwd, dry_run=False: [
            {"label": command.label, "argv": list(command.argv), "returncode": 0, "dry_run": dry_run}
            for command in commands
        ],
    )
    monkeypatch.setattr(environment_bootstrap, "requirement_available", lambda _name: False)

    report = environment_bootstrap.bootstrap_bioharness_environment(
        project_root=tmp_path,
        python_bin="/usr/bin/python3",
        venv_path=".venv",
        tool_names=["bowtie2"],
        install_python=False,
        install_pixi=True,
        install_isolated=False,
        dry_run=False,
    )

    assert report["post_install_verification_performed"] is True
    assert report["final_tool_status"] == {"bowtie2": False}
    assert report["remaining_missing_tools"] == ["bowtie2"]
    assert report["success"] is False
    assert any("requested tools still unavailable after bootstrap" in warning for warning in report["warnings"])


def test_bootstrap_environment_runs_isolated_setup_when_needed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        environment_bootstrap,
        "run_bootstrap_commands",
        lambda commands, *, cwd, dry_run=False: [
            {"label": command.label, "argv": list(command.argv), "returncode": 0, "dry_run": dry_run}
            for command in commands
        ],
    )
    monkeypatch.setattr(
        environment_bootstrap,
        "requirement_available",
        lambda _name: False,
    )
    calls: list[tuple[list[str], bool, bool]] = []

    def _fake_setup(tool_names, *, config_path, env_root, install, dry_run):
        calls.append((list(tool_names), install, dry_run))
        return {"reports": [], "resolved_tools": list(tool_names), "unresolved_tools": [], "success": True}

    monkeypatch.setattr(environment_bootstrap, "setup_isolated_tools_for_missing", _fake_setup)

    report = environment_bootstrap.bootstrap_bioharness_environment(
        project_root=tmp_path,
        python_bin="/usr/bin/python3",
        venv_path=".venv",
        tool_names=["cnvkit.py", "prokka"],
        dry_run=True,
    )

    assert report["success"] is True
    assert report["install_plan"]["isolated_recipe_missing_tools"] == ["cnvkit.py", "prokka"]
    assert calls == [(["cnvkit.py", "prokka"], False, True)]


def test_bootstrap_environment_can_include_llm_backend_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(
        environment_bootstrap,
        "run_bootstrap_commands",
        lambda commands, *, cwd, dry_run=False: [
            {"label": command.label, "argv": list(command.argv), "returncode": 0, "dry_run": dry_run}
            for command in commands
        ],
    )
    monkeypatch.setattr(
        environment_bootstrap,
        "requirement_available",
        lambda _name: True,
    )
    monkeypatch.setattr(
        environment_bootstrap,
        "probe_llm_backend",
        lambda **kwargs: {"available": False, "message": "offline", "diagnostics": {"backend": "ollama"}},
    )

    report = environment_bootstrap.bootstrap_bioharness_environment(
        project_root=tmp_path,
        python_bin="/usr/bin/python3",
        venv_path=".venv",
        dry_run=True,
        probe_llm_backend_status=True,
    )

    assert report["success"] is True
    assert report["llm_backend"]["available"] is False
    assert any("llm backend unavailable" in warning for warning in report["warnings"])


def test_bootstrap_environment_reports_structured_llm_dependency_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        environment_bootstrap,
        "run_bootstrap_commands",
        lambda commands, *, cwd, dry_run=False: [
            {"label": command.label, "argv": list(command.argv), "returncode": 0, "dry_run": dry_run}
            for command in commands
        ],
    )
    monkeypatch.setattr(environment_bootstrap, "requirement_available", lambda _name: True)

    def _missing_llm_import() -> type[object]:
        raise ModuleNotFoundError("No module named 'httpx'")

    monkeypatch.setattr("bio_harness.core.llm_backend_probe._load_bio_llm", _missing_llm_import)

    report = environment_bootstrap.bootstrap_bioharness_environment(
        project_root=tmp_path,
        python_bin="/usr/bin/python3",
        venv_path=".venv",
        dry_run=True,
        probe_llm_backend_status=True,
    )

    assert report["llm_backend"]["available"] is False
    assert report["llm_backend"]["exception_class"] == "ModuleNotFoundError"
    assert "llm runtime dependencies unavailable" in report["llm_backend"]["message"]
    assert any("llm backend unavailable" in warning for warning in report["warnings"])


def test_write_install_receipt_persists_json_payload(tmp_path):
    target = tmp_path / "receipt.json"
    receipt = write_install_receipt({"success": True, "commands": []}, prefix="bootstrap", output_path=target)

    assert receipt == target
    payload = target.read_text(encoding="utf-8")
    assert '"success": true' in payload
    assert '"receipt_prefix": "bootstrap"' in payload


def test_bootstrap_and_doctor_agree_on_fake_sidecar_tools(monkeypatch, tmp_path):
    _make_executable(tmp_path / ".pixi" / "envs" / "reports" / "bin" / "multiqc")
    _make_executable(tmp_path / ".pixi" / "envs" / "alignment-extra" / "bin" / "bowtie2")

    monkeypatch.setattr(tool_env, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tool_env.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor._check_command_version",
        lambda argv: {"available": True, "path": f"/usr/bin/{argv[0]}", "version": "ok"},
    )
    monkeypatch.setattr(
        "bio_harness.core.harness_doctor.assess_resource_preflight",
        lambda skill_names, selected_dir, min_free_disk_gb: {"ok": True, "warnings": []},
    )

    bootstrap = environment_bootstrap.bootstrap_bioharness_environment(
        project_root=tmp_path,
        python_bin="/usr/bin/python3",
        venv_path=".venv",
        tool_names=["bowtie2", "multiqc"],
        install_python=False,
        install_pixi=False,
        install_isolated=False,
        dry_run=True,
    )
    doctor = assess_harness_doctor(
        tool_names=["bowtie2", "multiqc"],
        selected_dir=tmp_path,
        min_free_disk_gb=0.0,
    )

    assert bootstrap["install_plan"]["already_available_tools"] == ["bowtie2", "multiqc"]
    assert doctor["install_plan"]["already_available_tools"] == ["bowtie2", "multiqc"]
    assert doctor["tool_status"] == {"bowtie2": True, "multiqc": True}
