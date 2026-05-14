"""Export completed Bio-Harness runs into workflow-exchange formats."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bio_harness.reporting.run_context import final_plan_steps, render_step_command, resolve_run_context


def _slug(text: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9]+", "_", str(text or "").strip().lower()).strip("_")
    return token or "bio_harness_run"


def _wdl_escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _cwl_step_tool_text(step_name: str, command: str) -> str:
    command_json = json.dumps(f"set -euo pipefail\n{command}\nprintf 'done\\n' > {step_name}.done.txt")
    return (
        "cwlVersion: v1.2\n"
        "class: CommandLineTool\n"
        "requirements:\n"
        "  ShellCommandRequirement: {}\n"
        "baseCommand: [bash, -lc]\n"
        "inputs:\n"
        "  prev_done:\n"
        "    type: File?\n"
        "    default: null\n"
        "arguments:\n"
        f"  - {command_json}\n"
        f"stdout: {step_name}.stdout.txt\n"
        "outputs:\n"
        "  done_token:\n"
        "    type: File\n"
        "    outputBinding:\n"
        f"      glob: {step_name}.done.txt\n"
        "  stdout_log:\n"
        "    type: File\n"
        "    outputBinding:\n"
        f"      glob: {step_name}.stdout.txt\n"
    )


def _build_cwl_workflow(step_names: list[str]) -> str:
    lines = [
        "cwlVersion: v1.2",
        "class: Workflow",
        "inputs: {}",
        "outputs:",
        "  final_done:",
        "    type: File",
        f"    outputSource: {step_names[-1]}/done_token",
        "steps:",
    ]
    for index, name in enumerate(step_names):
        lines.extend(
            [
                f"  {name}:",
                f"    run: steps/{name}.cwl",
                "    in:",
            ]
        )
        if index > 0:
            lines.append(f"      prev_done: {step_names[index - 1]}/done_token")
        lines.extend(
            [
                "    out:",
                "      - done_token",
                "      - stdout_log",
            ]
        )
    return "\n".join(lines) + "\n"


def _build_wdl(step_commands: list[tuple[str, str]]) -> str:
    parts = ["version 1.0", ""]
    for step_name, command in step_commands:
        parts.extend(
            [
                f"task {step_name} {{",
                "  input {",
                "    String command",
                '    String prev_done = ""',
                "  }",
                "  command <<<",
                "    set -euo pipefail",
                "    bash -lc \"~{command}\"",
                f"    printf 'done\\n' > {step_name}.done.txt",
                "  >>>",
                "  output {",
                f'    String done = read_string("{step_name}.done.txt")',
                "  }",
                "}",
                "",
            ]
        )
    workflow_name = "bio_harness_exported_workflow"
    parts.extend([f"workflow {workflow_name} {{"])
    for index, (step_name, command) in enumerate(step_commands):
        escaped = _wdl_escape(command)
        if index == 0:
            parts.extend(
                [
                    f"  call {step_name} {{",
                    f'    input: command = "{escaped}"',
                    "  }",
                ]
            )
        else:
            prev_name = step_commands[index - 1][0]
            parts.extend(
                [
                    f"  call {step_name} {{",
                    f'    input: command = "{escaped}", prev_done = {prev_name}.done',
                    "  }",
                ]
            )
    parts.extend(["}", ""])
    return "\n".join(parts)


def _build_trs_metadata(bundle_name: str, selected_dir: str) -> dict[str, Any]:
    workflow_id = _slug(bundle_name)
    return {
        "id": workflow_id,
        "name": bundle_name,
        "organization": "Bio-Harness",
        "description": "Exported workflow metadata from a Bio-Harness run.",
        "toolclass": {"name": "Workflow"},
        "aliases": [selected_dir],
        "versions": [
            {
                "id": "1",
                "name": "exported-workflow",
                "descriptor_type": ["CWL", "WDL"],
                "verified": False,
            }
        ],
    }


def load_trs_tool_metadata(path: str | Path) -> dict[str, Any]:
    """Load exported TRS metadata."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def export_workflow_exchange_bundle(run_input: str | Path, output_dir: str | Path | None = None) -> Path:
    """Export a completed run as CWL, WDL, TRS, WES, and TES artifacts."""
    context = resolve_run_context(run_input)
    export_dir = Path(output_dir).expanduser().resolve() if output_dir else (context.selected_dir / "reports" / "workflow_exchange")
    export_dir.mkdir(parents=True, exist_ok=True)

    steps = final_plan_steps(context)
    rendered_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        command = render_step_command(step)
        rendered_steps.append(
            {
                "step_name": f"step_{index:02d}",
                "tool_name": str(step.get("tool_name", "") or "").strip(),
                "command": command,
                "arguments": dict(step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}),
            }
        )

    (export_dir / "steps").mkdir(parents=True, exist_ok=True)
    for row in rendered_steps:
        step_path = export_dir / "steps" / f"{row['step_name']}.cwl"
        step_path.write_text(_cwl_step_tool_text(row["step_name"], row["command"]), encoding="utf-8")

    cwl_path = export_dir / "workflow.cwl"
    if rendered_steps:
        cwl_path.write_text(_build_cwl_workflow([row["step_name"] for row in rendered_steps]), encoding="utf-8")
    else:
        cwl_path.write_text("cwlVersion: v1.2\nclass: Workflow\ninputs: {}\noutputs: {}\nsteps: {}\n", encoding="utf-8")

    wdl_path = export_dir / "workflow.wdl"
    wdl_path.write_text(
        _build_wdl([(row["step_name"], row["command"]) for row in rendered_steps]) if rendered_steps else "version 1.0\nworkflow bio_harness_exported_workflow {}\n",
        encoding="utf-8",
    )

    trs_path = export_dir / "trs_tool.json"
    trs_path.write_text(
        json.dumps(_build_trs_metadata(context.selected_dir.name, str(context.selected_dir)), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    wes_dir = export_dir / "wes_requests"
    wes_dir.mkdir(parents=True, exist_ok=True)
    for descriptor_type, descriptor_path, version in [
        ("CWL", cwl_path, "v1.2"),
        ("WDL", wdl_path, "1.0"),
    ]:
        payload = {
            "workflow_url": str(descriptor_path),
            "workflow_type": descriptor_type,
            "workflow_type_version": version,
            "workflow_params": {},
            "tags": {
                "source": "Bio-Harness",
                "selected_dir": str(context.selected_dir),
                "run_dir": str(context.run_dir),
            },
        }
        (wes_dir / f"{descriptor_type.lower()}_request.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    tes_payload = []
    for row in rendered_steps:
        tes_payload.append(
            {
                "name": row["step_name"],
                "description": f"Exported Bio-Harness step using {row['tool_name'] or 'unknown'}",
                "executors": [
                    {
                        "command": ["bash", "-lc", row["command"]],
                        "workdir": "/workspace",
                    }
                ],
                "outputs": [
                    {
                        "path": f"/workspace/{row['step_name']}.done.txt",
                        "type": "FILE",
                    }
                ],
            }
        )
    (export_dir / "tes_tasks.json").write_text(json.dumps(tes_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    (export_dir / "workflow_plan.json").write_text(json.dumps(context.final_plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (export_dir / "rendered_steps.json").write_text(json.dumps(rendered_steps, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return export_dir
