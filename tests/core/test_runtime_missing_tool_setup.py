from __future__ import annotations

from pathlib import Path

import bio_harness.core.environment_bootstrap as environment_bootstrap
from bio_harness.harness.config import HarnessConfig
from scripts.run_agent_e2e import AgentE2EHarness


def _cfg(tmp_path: Path, *, auto_install: bool = False, auto_setup: bool = False) -> HarnessConfig:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    return HarnessConfig(
        prompt="test missing tool remediation",
        selected_dir=selected_dir,
        data_root=data_root,
        workspace_root=selected_dir,
        max_repairs=0,
        heartbeat_seconds=15,
        stall_timeout_seconds=45,
        live_process_grace_seconds=900,
        model_name=None,
        host=None,
        auto_install_missing_tools=auto_install,
        allow_replan=True,
        allow_canonicalize=True,
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
        auto_setup_isolated_tools=auto_setup,
    )


def test_tool_missing_can_use_isolated_tool_setup(tmp_path, monkeypatch):
    harness = AgentE2EHarness(_cfg(tmp_path, auto_setup=True))
    harness._init_run()
    harness.run["missing_tools_detected"] = ["cnvkit.py"]

    monkeypatch.setattr(harness, "_try_auto_setup_isolated_tools", lambda: (True, "isolated_tool_setup_completed"))

    repaired, action, details = harness._apply_repair_action("tool_missing")

    assert repaired is True
    assert action == "isolated_tool_setup_completed"
    assert details["tools"] == ["cnvkit.py"]


def test_tool_missing_falls_back_to_isolated_setup_after_failed_auto_install(tmp_path, monkeypatch):
    harness = AgentE2EHarness(_cfg(tmp_path, auto_install=True, auto_setup=True))
    harness._init_run()
    harness.run["missing_tools_detected"] = ["cnvkit.py"]

    monkeypatch.setattr(harness, "_try_auto_install_tools", lambda: (False, "tool_install_requires_manual_steps"))
    monkeypatch.setattr(harness, "_try_auto_setup_isolated_tools", lambda: (True, "isolated_tool_setup_completed"))

    repaired, action, details = harness._apply_repair_action("tool_missing")

    assert repaired is True
    assert action == "isolated_tool_setup_completed"
    assert details["tools"] == ["cnvkit.py"]


def test_try_auto_install_tools_returns_requires_pixi_when_pixi_missing(tmp_path, monkeypatch):
    harness = AgentE2EHarness(_cfg(tmp_path, auto_install=True, auto_setup=False))
    harness._init_run()
    harness.run["missing_tools_detected"] = ["bowtie2"]

    monkeypatch.setattr(
        environment_bootstrap,
        "bootstrap_bioharness_environment",
        lambda **kwargs: {
            "success": False,
            "pixi_command_missing": True,
            "install_plan": {
                "manual_install_required_tools": [],
                "pixi_installable_missing_tools": ["bowtie2"],
            },
            "commands": [],
            "warnings": ["pixi command unavailable"],
        },
    )

    ok, action = harness._try_auto_install_tools()

    assert ok is False
    assert action == "tool_install_requires_pixi"
    assert harness.run["auto_install_report"]["pixi_command_missing"] is True


def test_tool_missing_still_falls_back_to_isolated_setup_when_pixi_missing(tmp_path, monkeypatch):
    harness = AgentE2EHarness(_cfg(tmp_path, auto_install=True, auto_setup=True))
    harness._init_run()
    harness.run["missing_tools_detected"] = ["cnvkit.py"]

    monkeypatch.setattr(
        environment_bootstrap,
        "bootstrap_bioharness_environment",
        lambda **kwargs: {
            "success": False,
            "pixi_command_missing": True,
            "install_plan": {
                "manual_install_required_tools": [],
                "pixi_installable_missing_tools": [],
                "isolated_recipe_missing_tools": ["cnvkit.py"],
            },
            "commands": [],
            "warnings": ["pixi command unavailable"],
        },
    )
    monkeypatch.setattr(harness, "_try_auto_setup_isolated_tools", lambda: (True, "isolated_tool_setup_completed"))

    repaired, action, details = harness._apply_repair_action("tool_missing")

    assert repaired is True
    assert action == "isolated_tool_setup_completed"
    assert details["tools"] == ["cnvkit.py"]
