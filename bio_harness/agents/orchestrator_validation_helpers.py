from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


def manual_hint(tool: str) -> str:
    if not tool:
        return ""
    candidates = [
        [tool, "--help"],
        [tool, "-h"],
        ["man", tool],
    ]
    for cmd in candidates:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            out = (proc.stdout or proc.stderr or "").strip()
            if out:
                return out[:400]
        except Exception:
            continue
    return ""


def regenerate_splicing_lists(cwd: str | None) -> dict[str, Any]:
    base = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    out_base = base / "outputs" / "splicing_auto"
    manifest = out_base / "fastq_manifest.txt"
    control = out_base / "control_r1.txt"
    treatment = out_base / "treatment_r1.txt"
    if not manifest.exists():
        return {"ok": False, "reason": f"missing_manifest:{manifest}"}

    try:
        lines = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception as exc:
        return {"ok": False, "reason": f"read_manifest_failed:{exc}"}

    control_hits = [
        line
        for line in lines
        if re.search(r"_S1(?:_[^/]+)?_R1_001\.f(ast)?q(\.gz)?$", line, flags=re.IGNORECASE)
        or re.search(r"_S1_R1_001\.f(ast)?q(\.gz)?$", line, flags=re.IGNORECASE)
    ]
    treatment_hits = [
        line
        for line in lines
        if re.search(r"_S6(?:_[^/]+)?_R1_001\.f(ast)?q(\.gz)?$", line, flags=re.IGNORECASE)
        or re.search(r"_S6_R1_001\.f(ast)?q(\.gz)?$", line, flags=re.IGNORECASE)
    ]

    out_base.mkdir(parents=True, exist_ok=True)
    control.write_text("\n".join(control_hits) + ("\n" if control_hits else ""), encoding="utf-8")
    treatment.write_text("\n".join(treatment_hits) + ("\n" if treatment_hits else ""), encoding="utf-8")
    return {
        "ok": bool(control_hits and treatment_hits),
        "reason": "rewritten_lists",
        "control_count": len(control_hits),
        "treatment_count": len(treatment_hits),
    }


def steps_cover_same_capability(
    failed_tool: str,
    next_tool: str,
    failed_cmd: str = "",
) -> bool:
    """Return True when *next_tool* covers the same analytical capability."""
    failed_tool = failed_tool.lower().strip()
    next_tool = next_tool.lower().strip()

    if failed_tool == next_tool:
        return True

    r_skills = frozenset(
        {
            "deseq2_run",
            "edger_run",
            "limma_voom_run",
            "dexseq_run",
            "rmats_run",
            "seurat_rscript_workflow",
        }
    )
    if failed_tool == "bash_run" and next_tool in r_skills:
        cmd = failed_cmd.lower()
        is_inline_r = (
            "rscript -e" in cmd
            or "rscript --vanilla -e" in cmd
            or ("library(" in cmd and ("r -e" in cmd or "r --no-save" in cmd or "rscript -e" in cmd))
            or (("rscript" in cmd or "r --no-save" in cmd) and ("<<" in cmd or ("library(" in cmd and ";" in cmd)))
        )
        if is_inline_r:
            return True

    py_skills = frozenset({"scanpy_workflow", "sc_count_and_cluster"})
    if failed_tool == "bash_run" and next_tool in py_skills:
        cmd = failed_cmd.lower()
        if "python" in cmd and "import " in cmd:
            return True

    bash_to_skill = {
        "salmon": "salmon_quant",
        "kallisto": "kallisto_quant",
        "featurecounts": "featurecounts_run",
        "subread": "featurecounts_run",
        "gatk haplotypecaller": "gatk_haplotypecaller",
        "star ": "star_align",
        "bwa ": "bwa_mem_align",
    }
    if failed_tool == "bash_run":
        cmd = failed_cmd.lower()
        for keyword, skill in bash_to_skill.items():
            if keyword in cmd and next_tool == skill:
                return True

    return False
