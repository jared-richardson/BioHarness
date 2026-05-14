"""Deterministic workflow template builders and script export helpers."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from bio_harness.workflows.template_io_support import DEFAULT_STAR_INDEX_CACHE_ROOT, script_command


def build_bootstrap_execution_plan(data_root: str) -> dict:
    """Return the deterministic bootstrap plan used for lightweight discovery."""

    manifest = "outputs/auto_bootstrap/fastq_manifest.txt"
    return {
        "thought_process": "Bootstrap execution plan to start concrete work and gather file context.",
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": script_command("fastq_manifest.sh", data_root, manifest),
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "if [ -s outputs/auto_bootstrap/fastq_manifest.txt ]; then "
                        "head -n 4 outputs/auto_bootstrap/fastq_manifest.txt "
                        "| xargs -I{} fastqc -o outputs/auto_bootstrap -t 2 \"{}\"; "
                        "else echo '__NO_FASTQ_FOUND__'; fi"
                    ),
                },
                "step_id": 2,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "echo '__BOOTSTRAP_COMPLETE__'",
                },
                "step_id": 3,
            },
        ],
    }


def build_splicing_execution_plan(
    data_root: str,
    gtf_path: str,
    fasta_path: str,
    control_tag: str = "S1",
    treatment_tag: str = "S6",
    use_test_subset: bool = True,
    test_reads_per_fastq: int = 1000000,
) -> dict:
    """Return the deterministic reusable splicing execution template."""

    out_base = "outputs/splicing_auto"
    manifest = f"{out_base}/fastq_manifest.txt"
    ctl_r1 = f"{out_base}/control_r1.txt"
    trt_r1 = f"{out_base}/treatment_r1.txt"
    ctl_bams = f"{out_base}/control_bams.txt"
    trt_bams = f"{out_base}/treatment_bams.txt"
    ctl_r1_active = ctl_r1
    trt_r1_active = trt_r1
    ctl_r1_test = f"{out_base}/control_r1_test.txt"
    trt_r1_test = f"{out_base}/treatment_r1_test.txt"
    subset_dir = f"{out_base}/test_subset"
    star_idx = f"{out_base}/star_index"
    star_out = f"{out_base}/star"
    qc_out = f"{out_base}/fastqc"
    rmats_out = f"{out_base}/rmats"
    rmats_tmp = f"{out_base}/rmats_tmp"
    fastqc_sentinel = f"{out_base}/.fastqc_done"
    star_cache_root = DEFAULT_STAR_INDEX_CACHE_ROOT

    gtf_q = shlex.quote(gtf_path)
    fasta_q = shlex.quote(fasta_path)

    steps = [
        {
            "tool_name": "bash_run",
            "arguments": {
                "command": script_command("fastq_manifest.sh", data_root, manifest),
            },
            "step_id": 1,
        },
        {
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    f"{script_command('select_sample_r1.sh', manifest, control_tag, ctl_r1, 'CONTROL')}; "
                    f"{script_command('select_sample_r1.sh', manifest, treatment_tag, trt_r1, 'TREATMENT')}"
                ),
            },
            "step_id": 2,
        },
    ]

    if use_test_subset:
        steps.append(
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": script_command(
                        "create_test_subset_from_r1_lists.sh",
                        ctl_r1,
                        trt_r1,
                        subset_dir,
                        ctl_r1_test,
                        trt_r1_test,
                        str(int(test_reads_per_fastq)),
                        "control",
                        "treatment",
                    ),
                },
                "step_id": 3,
            }
        )
        ctl_r1_active = ctl_r1_test
        trt_r1_active = trt_r1_test

    steps.extend(
        [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": script_command(
                        "run_fastqc_if_needed.sh",
                        ctl_r1_active,
                        trt_r1_active,
                        qc_out,
                        fastqc_sentinel,
                    ),
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": script_command("check_required_tools.sh", "star", "fastqc", "samtools", "rmats"),
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"if [ ! -f {fasta_q} ]; then echo '__MISSING_REFERENCE__:fasta'; fi; "
                        f"if [ ! -f {gtf_q} ]; then echo '__MISSING_REFERENCE__:gtf'; fi"
                    ),
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": script_command(
                        "build_star_index.sh",
                        star_idx,
                        fasta_path,
                        gtf_path,
                        "2",
                        star_cache_root,
                        "149",
                    ),
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": script_command(
                        "align_r1_list_with_star.sh",
                        ctl_r1_active,
                        star_idx,
                        star_out,
                        ctl_bams,
                        "control",
                        "2",
                    ),
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": script_command(
                        "align_r1_list_with_star.sh",
                        trt_r1_active,
                        star_idx,
                        star_out,
                        trt_bams,
                        "treatment",
                        "2",
                    ),
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": script_command(
                        "run_rmats_if_needed.sh",
                        ctl_bams,
                        trt_bams,
                        gtf_path,
                        rmats_out,
                        rmats_tmp,
                        "150",
                        "2",
                    ),
                },
            },
        ]
    )

    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx

    return {
        "thought_process": (
            "Deterministic splicing workflow using reusable pipeline scripts "
            "(FASTQ manifest, sample selection, optional test subset, FastQC, STAR, rMATS)."
        ),
        "plan": steps,
        "canonical_template": "splicing_execution_v2",
        "execution_options": {
            "use_test_subset": bool(use_test_subset),
            "test_reads_per_fastq": int(test_reads_per_fastq),
            "star_index_cache_root": star_cache_root,
        },
    }


def export_plan_run_scripts(
    plan_json: dict,
    run_dir: Path,
    selected_dir: Path,
    script_set_name: str,
) -> dict[str, str]:
    """Export executable bash scripts for each bash-backed step in one plan."""

    scripts_root = run_dir / "scripts" / script_set_name
    steps_root = scripts_root / "steps"
    steps_root.mkdir(parents=True, exist_ok=True)

    plan_path = scripts_root / "plan.json"
    plan_path.write_text(json.dumps(plan_json, indent=2), encoding="utf-8")

    rendered_steps: list[dict[str, str]] = []
    steps = plan_json.get("plan", []) if isinstance(plan_json, dict) else []
    for idx, raw_step in enumerate(steps, start=1):
        step = raw_step if isinstance(raw_step, dict) else {}
        step_id = int(step.get("step_id", idx))
        tool_name = str(step.get("tool_name", "unknown"))
        cmd = ""
        if tool_name == "bash_run":
            cmd = str((step.get("arguments") or {}).get("command", "")).strip()
        if not cmd:
            cmd = (
                "echo 'Step has no executable bash command in this exported script.' >&2\n"
                "exit 2"
            )

        step_script = steps_root / f"step_{step_id:02d}_{tool_name}.sh"
        step_script.write_text(
            (
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f"cd {shlex.quote(str(selected_dir))}\n"
                f"{cmd}\n"
            ),
            encoding="utf-8",
        )
        step_script.chmod(0o755)
        rendered_steps.append(
            {
                "step_id": str(step_id),
                "tool_name": tool_name,
                "script_path": str(step_script),
            }
        )

    run_all_path = scripts_root / "run_all.sh"
    run_all_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        f"echo '[run-script] script set: {script_set_name}'",
    ]
    for item in rendered_steps:
        rel_step = Path(item["script_path"]).relative_to(scripts_root)
        run_all_lines.append(f"echo '[run-script] step {item['step_id']} ({item['tool_name']})'")
        run_all_lines.append(f"bash \"$SCRIPT_DIR/{rel_step.as_posix()}\"")
    run_all_lines.append("echo '[run-script] completed'")
    run_all_path.write_text("\n".join(run_all_lines) + "\n", encoding="utf-8")
    run_all_path.chmod(0o755)

    manifest_path = scripts_root / "scripts_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "script_set": script_set_name,
                "selected_dir": str(selected_dir),
                "plan_json": str(plan_path),
                "run_all": str(run_all_path),
                "steps": rendered_steps,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "script_set_dir": str(scripts_root),
        "run_all": str(run_all_path),
        "plan_json": str(plan_path),
        "scripts_manifest": str(manifest_path),
    }
