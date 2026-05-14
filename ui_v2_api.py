"""Bio-Harness v2 API — FastAPI backend for the React UI.

Run with:  python3 ui_v2_api.py
Serves on: http://127.0.0.1:8000 by default.

Set BIO_HARNESS_UI_HOST=0.0.0.0 only when intentionally exposing the API to a
trusted local network.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import psutil
import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
WORKSPACE = PROJECT_ROOT / "workspace"
SKILLS_INDEX = PROJECT_ROOT / "bio_harness" / "skills" / "definitions" / "index.json"
OLLAMA_BASE = os.getenv("OLLAMA_HOST", "http://localhost:11434")
RUNS_DIR = WORKSPACE / "runs"
DEFAULT_UI_HOST = "127.0.0.1"
DEFAULT_UI_PORT = 8000
MAX_TERMINAL_TIMEOUT_SECONDS = 60.0
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
)

TERMINAL_BLOCK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\brm\b(?=[^;&|]*\s-[A-Za-z]*r)(?=[^;&|]*\s-[A-Za-z]*f)", re.IGNORECASE),
        "recursive forced deletion is not allowed from the UI terminal",
    ),
    (
        re.compile(r"\brm\b[^;&|]*(?:--recursive|-R)\b[^;&|]*--force\b", re.IGNORECASE),
        "recursive forced deletion is not allowed from the UI terminal",
    ),
    (
        re.compile(r":\s*\(\s*\)\s*\{"),
        "fork-bomb shell functions are not allowed",
    ),
    (
        re.compile(
            r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:/usr/bin/|/bin/)?(?:bash|sh|zsh|python3?|perl|ruby)\b",
            re.IGNORECASE,
        ),
        "piping downloaded content into an interpreter is not allowed",
    ),
    (
        re.compile(r">\s*/dev/(?:sd|disk|rdisk|nvme|mapper)", re.IGNORECASE),
        "writing directly to block devices is not allowed",
    ),
)

TERMINAL_BLOCKED_EXECUTABLES = {
    "dd",
    "halt",
    "mkfs",
    "poweroff",
    "reboot",
    "shutdown",
    "su",
    "sudo",
}

TERMINAL_UPLOAD_FLAGS = {"--upload-file", "--form", "-F", "-T"}

logger = logging.getLogger("ui_v2_api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

EXTENSION_KIND_MAP: dict[str, str] = {
    ".csv": "table",
    ".tsv": "table",
    ".txt": "text",
    ".log": "text",
    ".json": "json",
    ".jsonl": "json",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".svg": "image",
    ".gif": "image",
    ".pdf": "document",
    ".html": "document",
    ".htm": "document",
    ".md": "text",
    ".py": "text",
    ".r": "text",
    ".R": "text",
    ".sh": "text",
    ".yml": "text",
    ".yaml": "text",
    ".vcf": "table",
    ".gff": "table",
    ".gtf": "table",
    ".bed": "table",
    ".sam": "text",
    ".bam": "binary",
    ".fasta": "text",
    ".fa": "text",
    ".fq": "text",
    ".fastq": "text",
}


def _cors_origins_from_env() -> list[str]:
    """Return allowed browser origins for the local UI API."""
    raw = os.getenv("BIO_HARNESS_UI_CORS_ORIGINS", "")
    if not raw.strip():
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _infer_kind(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return EXTENSION_KIND_MAP.get(suffix, "other")


def _safe_resolve(requested: str) -> Path:
    """Resolve a path and ensure it lives under PROJECT_ROOT."""
    resolved = (PROJECT_ROOT / requested).resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Path traversal denied") from exc
    return resolved


def _env_bool(name: str, default: bool) -> bool:
    """Return a boolean environment setting."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _server_host_from_env() -> str:
    """Return the configured API bind host."""
    return os.getenv("BIO_HARNESS_UI_HOST", DEFAULT_UI_HOST).strip() or DEFAULT_UI_HOST


def _server_port_from_env() -> int:
    """Return the configured API port."""
    raw = os.getenv("BIO_HARNESS_UI_PORT")
    if raw is None or not raw.strip():
        return DEFAULT_UI_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("BIO_HARNESS_UI_PORT must be an integer") from exc
    if port < 1 or port > 65535:
        raise ValueError("BIO_HARNESS_UI_PORT must be between 1 and 65535")
    return port


def _blocked_terminal_reason(command: str) -> str | None:
    """Return a human-readable block reason for unsafe UI terminal commands."""
    stripped = command.strip()
    if not stripped:
        return "empty commands are not allowed"

    for pattern, reason in TERMINAL_BLOCK_PATTERNS:
        if pattern.search(stripped):
            return reason

    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        return "command could not be parsed safely"

    lowered = [token.lower() for token in tokens]
    for index, token in enumerate(lowered):
        executable = Path(token).name
        if executable in TERMINAL_BLOCKED_EXECUTABLES or executable.startswith("mkfs."):
            return f"'{tokens[index]}' is not allowed from the UI terminal"
        if (
            executable == "kill"
            and {"-9", "-kill"}.intersection(lowered[index + 1 :])
            and "-1" in lowered[index + 1 :]
        ):
            return "mass process termination is not allowed"
        if executable in {"curl", "wget"} and TERMINAL_UPLOAD_FLAGS.intersection(
            tokens[index + 1 :]
        ):
            return "network upload commands are not allowed from the UI terminal"

    if "chmod" in lowered and "777" in lowered and any(token in {"-r", "-R"} for token in tokens):
        return "recursive world-writable chmod is not allowed"

    return None


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Bio-Harness v2 API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins_from_env(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store for background runs
_active_runs: dict[str, dict[str, Any]] = {}
_background_tasks: set[asyncio.Task[None]] = set()
_setup_jobs: dict[str, dict[str, Any]] = {}


def _relative(p: Path) -> str:
    """Return path relative to PROJECT_ROOT."""
    try:
        return str(p.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


async def _ollama_get(path: str, timeout: float = 5.0) -> httpx.Response | None:
    """Best-effort GET to the Ollama API."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(f"{OLLAMA_BASE}{path}")
    except Exception:
        return None


async def _ollama_post(
    path: str,
    payload: dict[str, Any],
    timeout: float = 120.0,
) -> httpx.Response | None:
    """POST to the Ollama API."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(f"{OLLAMA_BASE}{path}", json=payload)
    except Exception:
        return None


def _mock_plan() -> dict[str, Any]:
    """Return a realistic mock plan for UI testing when Ollama is unavailable."""
    return {
        "analysis_type": "rna_seq_differential_expression",
        "reasoning": (
            "The user wants to perform differential expression analysis. "
            "We will align reads with STAR, quantify with featureCounts, "
            "and run DESeq2 for statistical testing."
        ),
        "steps": [
            {
                "step_number": 1,
                "tool_name": "star_align",
                "description": "Align RNA-seq reads to the reference genome using STAR",
                "parameters": {
                    "reference_fasta": "workspace/reference/genome.fa",
                    "input_fastq": "workspace/data/sample1_R1.fq.gz",
                    "output_dir": "workspace/outputs/star_align",
                },
            },
            {
                "step_number": 2,
                "tool_name": "featurecounts_count",
                "description": "Quantify gene-level read counts from aligned BAMs",
                "parameters": {
                    "input_bam": "workspace/outputs/star_align/Aligned.out.bam",
                    "annotation_gtf": "workspace/reference/genes.gtf",
                    "output_counts": "workspace/outputs/featurecounts/counts.txt",
                },
            },
            {
                "step_number": 3,
                "tool_name": "deseq2_run",
                "description": "Run DESeq2 for differential expression testing",
                "parameters": {
                    "counts_matrix": "workspace/outputs/featurecounts/counts.txt",
                    "metadata_table": "workspace/data/metadata.tsv",
                    "output_dir": "workspace/outputs/deseq2",
                    "contrast": "condition_treatment_vs_control",
                },
            },
        ],
    }


# ---------------------------------------------------------------------------
# 1. GET /api/health
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, bool | str]:
    """Return API and local model-backend health."""
    resp = await _ollama_get("/api/tags")
    ollama_ok = resp is not None and resp.status_code == 200
    return {"status": "ok", "ollama": ollama_ok}


# ---------------------------------------------------------------------------
# 2. GET /api/models
# ---------------------------------------------------------------------------


@app.get("/api/models")
async def list_models() -> dict[str, Any]:
    """List models available from the configured local Ollama backend."""
    resp = await _ollama_get("/api/tags")
    if resp is None or resp.status_code != 200:
        return {"models": [], "error": "Ollama not reachable"}
    data = resp.json()
    models = []
    for m in data.get("models", []):
        models.append(
            {
                "name": m.get("name", ""),
                "size": m.get("size", 0),
                "modified_at": m.get("modified_at", ""),
                "parameter_size": m.get("details", {}).get("parameter_size", ""),
                "family": m.get("details", {}).get("family", ""),
            }
        )
    return {"models": models}


# ---------------------------------------------------------------------------
# 2a. GET /api/setup/models
# ---------------------------------------------------------------------------


class SetupActionRequest(BaseModel):
    """First-run setup action request from the React UI."""

    action_id: str
    model_name: str = ""
    host: str = ""


def _model_rows_for_setup_catalog(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert UI model rows into first-run setup model rows."""
    rows: list[dict[str, Any]] = []
    for model in models:
        size_bytes = float(model.get("size", 0) or 0)
        rows.append(
            {
                "name": str(model.get("name", "") or "").strip(),
                "family": str(model.get("family", "") or "").strip(),
                "parameter_size": str(model.get("parameter_size", "") or "").strip(),
                "size_gb": round(size_bytes / (1024**3), 2) if size_bytes > 0 else 0.0,
            }
        )
    return [row for row in rows if row["name"]]


def _setup_resource_snapshot() -> dict[str, float | int | None]:
    """Return system resources used by the setup assistant."""
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(PROJECT_ROOT))
    return {
        "cpu_count": psutil.cpu_count(logical=True),
        "ram_total_gb": round(vm.total / (1024**3), 2),
        "available_ram_gb": round(vm.available / (1024**3), 2),
        "disk_free_gb": round(disk.free / (1024**3), 2),
    }


@app.get("/api/setup/models")
async def setup_models() -> dict[str, Any]:
    """Return installed and recommended setup models for the first-run UI."""
    from bio_harness.core.model_catalog import build_model_setup_options

    resources = _setup_resource_snapshot()
    model_payload = await list_models()
    installed_rows = _model_rows_for_setup_catalog(list(model_payload.get("models", []) or []))
    options = build_model_setup_options(
        installed_models=installed_rows,
        free_disk_gb=float(resources["disk_free_gb"] or 0.0),
        available_ram_gb=float(resources["available_ram_gb"] or 0.0),
    )
    return {
        **options,
        "resources": resources,
        "backend_error": model_payload.get("error", ""),
    }


@app.get("/api/setup/status")
async def setup_status() -> dict[str, Any]:
    """Return first-run setup status for the React setup wizard."""
    from bio_harness.core.first_run_setup import build_first_run_setup_status
    from bio_harness.core.harness_doctor import assess_harness_doctor
    from bio_harness.core.llm_setup_support import build_llm_setup_report

    resources = _setup_resource_snapshot()
    doctor_report: dict[str, Any]
    try:
        doctor_report = assess_harness_doctor(
            selected_dir=WORKSPACE,
            probe_llm_backend_status=False,
        )
    except Exception as exc:
        doctor_report = {
            "ready": False,
            "warnings": [f"doctor check failed: {exc}"],
            "exception_class": exc.__class__.__name__,
        }
    llm_report = build_llm_setup_report(
        llm_backend=os.getenv("BIO_HARNESS_LLM_BACKEND", "ollama"),
        model_name=os.getenv("BIO_HARNESS_MODEL", "qwen3-coder-next:latest"),
        host=os.getenv("BIO_HARNESS_OLLAMA_HOST", ""),
        pull_if_missing=False,
    )
    status = build_first_run_setup_status(
        doctor_report=doctor_report,
        llm_setup_report=llm_report,
        free_disk_gb=float(resources["disk_free_gb"] or 0.0),
        available_ram_gb=float(resources["available_ram_gb"] or 0.0),
    )
    return {
        **status,
        "resources": resources,
        "doctor_report": doctor_report,
        "llm_setup_report": llm_report,
    }


def _new_setup_job(
    *,
    action_id: str,
    model_name: str = "",
    host: str = "",
    command: list[str] | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Create an in-memory setup job record."""
    resolved_job_id = job_id or str(uuid.uuid4())
    job = {
        "job_id": resolved_job_id,
        "action_id": action_id,
        "model_name": model_name,
        "host": host,
        "command": command or [],
        "status": "queued",
        "cancel_requested": False,
        "pid": None,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "events": [],
        "result": None,
        "error": "",
    }
    _setup_jobs[resolved_job_id] = job
    return job


def _update_setup_job(
    job_id: str,
    *,
    status: str | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Update one setup job record if it still exists."""
    job = _setup_jobs.get(job_id)
    if not job:
        return
    if status is not None:
        job["status"] = status
    if result is not None:
        job["result"] = result
    if error is not None:
        job["error"] = error
    job["updated_at"] = datetime.utcnow().isoformat() + "Z"


def _append_setup_job_event(job_id: str, event: dict[str, Any]) -> None:
    """Append one bounded progress event to a setup job."""
    job = _setup_jobs.get(job_id)
    if not job:
        return
    events = list(job.get("events", []) or [])
    events.append(event)
    job["events"] = events[-20:]
    job["updated_at"] = datetime.utcnow().isoformat() + "Z"


def _setup_job_paths(job_id: str, stem: str) -> dict[str, Path]:
    """Return receipt and log paths for one setup job."""
    root = WORKSPACE / "setup_reports"
    root.mkdir(parents=True, exist_ok=True)
    return {
        "stdout": root / f"{job_id}_{stem}.stdout.log",
        "stderr": root / f"{job_id}_{stem}.stderr.log",
        "output_json": root / f"{job_id}_{stem}.json",
    }


def _parse_json_file(path: Path) -> dict[str, Any]:
    """Load a JSON object from a path, returning an empty object on failure."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _setup_job_cancel_requested(job_id: str | None) -> bool:
    """Return whether a setup job has been asked to cancel."""
    if not job_id:
        return False
    return bool((_setup_jobs.get(job_id) or {}).get("cancel_requested", False))


def _terminate_process(proc: subprocess.Popen[Any]) -> None:
    """Terminate a setup subprocess, escalating to kill if needed."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _run_known_setup_command(
    *,
    command: list[str],
    output_json: Path,
    stdout_log: Path,
    stderr_log: Path,
    timeout_seconds: int,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Run one approved setup command and return a structured result."""
    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.utcnow().isoformat() + "Z"
    try:
        with (
            stdout_log.open("w", encoding="utf-8") as stdout_handle,
            stderr_log.open(
                "w",
                encoding="utf-8",
            ) as stderr_handle,
        ):
            proc = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=stdout_handle,
                stderr=stderr_handle,
            )
            if job_id:
                _setup_jobs[job_id]["pid"] = int(proc.pid)
            deadline = time.monotonic() + timeout_seconds
            returncode: int | None = None
            error = ""
            while returncode is None:
                returncode = proc.poll()
                if returncode is not None:
                    break
                if _setup_job_cancel_requested(job_id):
                    _terminate_process(proc)
                    returncode = 130
                    error = "setup job canceled"
                    break
                if time.monotonic() > deadline:
                    _terminate_process(proc)
                    returncode = 124
                    error = f"setup command timed out after {timeout_seconds} seconds"
                    break
                time.sleep(0.5)
        if not error:
            error = "" if returncode == 0 else f"setup command exited with code {returncode}"
    except OSError as exc:
        returncode = 1
        error = str(exc)
    return {
        "attempted": True,
        "succeeded": returncode == 0,
        "canceled": returncode == 130,
        "returncode": returncode,
        "started_at": started,
        "finished_at": datetime.utcnow().isoformat() + "Z",
        "command": command,
        "output_json": str(output_json),
        "stdout_log": str(stdout_log),
        "stderr_log": str(stderr_log),
        "payload": _parse_json_file(output_json),
        "error": error,
    }


async def _run_known_setup_command_job(
    *,
    job_id: str,
    command: list[str],
    output_json: Path,
    stdout_log: Path,
    stderr_log: Path,
    timeout_seconds: int,
) -> None:
    """Run a known setup command in the background and update job state."""
    _update_setup_job(job_id, status="running")
    _append_setup_job_event(
        job_id,
        {
            "status": "running",
            "command": " ".join(shlex.quote(part) for part in command),
        },
    )
    result = await asyncio.to_thread(
        _run_known_setup_command,
        command=command,
        output_json=output_json,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        timeout_seconds=timeout_seconds,
        job_id=job_id,
    )
    _append_setup_job_event(
        job_id,
        {
            "status": _setup_job_terminal_status(result),
            "returncode": result.get("returncode"),
            "output_json": result.get("output_json"),
        },
    )
    _update_setup_job(
        job_id,
        status=_setup_job_terminal_status(result),
        result=result,
        error=str(result.get("error", "") or ""),
    )


def _setup_job_terminal_status(result: dict[str, Any]) -> str:
    """Return the terminal job status for one setup result."""
    if result.get("canceled"):
        return "canceled"
    return "completed" if result.get("succeeded") else "failed"


async def _run_pull_model_setup_job(
    *,
    job_id: str,
    model_name: str,
    host: str,
) -> None:
    """Run a background Ollama model pull and update the setup job record."""
    from bio_harness.core.ollama_setup import OllamaPullCancelled, pull_ollama_model

    job = _setup_jobs[job_id]
    job["status"] = "running"
    job["updated_at"] = datetime.utcnow().isoformat() + "Z"
    progress_path = WORKSPACE / "setup_reports" / f"{job_id}_ollama_pull.jsonl"

    def progress_callback(event: dict[str, Any]) -> None:
        if _setup_job_cancel_requested(job_id):
            raise OllamaPullCancelled("setup job canceled")
        _append_setup_job_event(job_id, event)

    result = await asyncio.to_thread(
        pull_ollama_model,
        model_name=model_name,
        host=host,
        progress_path=progress_path,
        timeout_seconds=None,
        progress_callback=progress_callback,
    )
    job["result"] = result
    job["status"] = _setup_job_terminal_status(result)
    job["error"] = str(result.get("error", "") or "")
    job["updated_at"] = datetime.utcnow().isoformat() + "Z"


def _setup_command_job_for_action(
    *,
    action_id: str,
    model_name: str,
    host: str,
) -> dict[str, Any]:
    """Build a background job for one approved setup command."""
    job_id = str(uuid.uuid4())
    if action_id == "run_environment_setup":
        paths = _setup_job_paths(job_id, "bootstrap")
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "bootstrap_bioharness.py"),
            "--output-json",
            str(paths["output_json"]),
        ]
        return _new_setup_command_job(
            job_id=job_id,
            action_id=action_id,
            command=command,
            paths=paths,
            timeout_seconds=1800,
        )
    if action_id == "verify_model":
        paths = _setup_job_paths(job_id, "verify_model")
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "first_run_setup.py"),
            "--skip-bootstrap",
            "--json",
            "--output-json",
            str(paths["output_json"]),
            "--model-name",
            model_name,
            "--host",
            host,
        ]
        return _new_setup_command_job(
            job_id=job_id,
            action_id=action_id,
            model_name=model_name,
            host=host,
            command=command,
            paths=paths,
            timeout_seconds=120,
        )
    if action_id == "run_mini_preflight":
        paths = _setup_job_paths(job_id, "mini_preflight")
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_fast_model_preflight.py"),
            "--suite",
            "mini",
            "--model",
            model_name,
            "--output-json",
            str(paths["output_json"]),
        ]
        return _new_setup_command_job(
            job_id=job_id,
            action_id=action_id,
            model_name=model_name,
            host=host,
            command=command,
            paths=paths,
            timeout_seconds=1800,
        )
    raise HTTPException(status_code=400, detail=f"Unsupported setup action: {action_id}")


def _new_setup_command_job(
    *,
    job_id: str,
    action_id: str,
    command: list[str],
    paths: dict[str, Path],
    timeout_seconds: int,
    model_name: str = "",
    host: str = "",
) -> dict[str, Any]:
    """Create a setup command job with log and receipt paths."""
    job = _new_setup_job(
        action_id=action_id,
        model_name=model_name,
        host=host,
        command=command,
        job_id=job_id,
    )
    job["stdout_log"] = str(paths["stdout"])
    job["stderr_log"] = str(paths["stderr"])
    job["output_json"] = str(paths["output_json"])
    job["timeout_seconds"] = timeout_seconds
    return job


@app.post("/api/setup/actions")
async def setup_action(req: SetupActionRequest) -> dict[str, Any]:
    """Run a safe first-run setup action."""
    from bio_harness.core.ollama_setup import (
        DEFAULT_OLLAMA_HOST,
        normalize_ollama_host,
        start_ollama_server,
        validate_ollama_model_name,
    )

    action_id = str(req.action_id or "").strip()
    host = normalize_ollama_host(
        req.host or os.getenv("BIO_HARNESS_OLLAMA_HOST", "") or OLLAMA_BASE
    )
    model_name = ""
    model_required = action_id in {"pull_model", "verify_model", "run_mini_preflight"}
    raw_model_name = req.model_name or os.getenv("BIO_HARNESS_MODEL", "")
    if model_required and not raw_model_name:
        raw_model_name = "qwen3-coder-next:latest"
    if model_required or req.model_name:
        try:
            model_name = validate_ollama_model_name(raw_model_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if action_id == "start_ollama":
        result = start_ollama_server(host=host)
        return {"action_id": action_id, "result": result}

    if action_id == "pull_model":
        if not model_name:
            raise HTTPException(status_code=400, detail="A model name is required.")
        job = _new_setup_job(
            action_id=action_id,
            model_name=model_name,
            host=host or DEFAULT_OLLAMA_HOST,
        )
        task = asyncio.create_task(
            _run_pull_model_setup_job(
                job_id=str(job["job_id"]),
                model_name=model_name,
                host=str(job["host"]),
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return {"action_id": action_id, "job": job}

    if action_id in {"run_environment_setup", "verify_model", "run_mini_preflight"}:
        job = _setup_command_job_for_action(
            action_id=action_id,
            model_name=model_name or "qwen3-coder-next:latest",
            host=host,
        )
        task = asyncio.create_task(
            _run_known_setup_command_job(
                job_id=str(job["job_id"]),
                command=list(job["command"]),
                output_json=Path(str(job["output_json"])),
                stdout_log=Path(str(job["stdout_log"])),
                stderr_log=Path(str(job["stderr_log"])),
                timeout_seconds=int(job["timeout_seconds"]),
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return {"action_id": action_id, "job": job}

    raise HTTPException(status_code=400, detail=f"Unsupported setup action: {action_id}")


@app.get("/api/setup/jobs/{job_id}")
async def setup_job(job_id: str) -> dict[str, Any]:
    """Return status for a background setup job."""
    job = _setup_jobs.get(str(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Setup job not found")
    return job


@app.post("/api/setup/jobs/{job_id}/cancel")
async def cancel_setup_job(job_id: str) -> dict[str, Any]:
    """Request cancellation for a background setup job."""
    job = _setup_jobs.get(str(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Setup job not found")
    if str(job.get("status", "")) in {"completed", "failed", "canceled"}:
        return job
    job["cancel_requested"] = True
    job["status"] = "cancel_requested"
    job["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _append_setup_job_event(str(job_id), {"status": "cancel_requested"})
    return job


# ---------------------------------------------------------------------------
# 3. GET /api/skills
# ---------------------------------------------------------------------------


@app.get("/api/skills")
async def get_skills() -> Any:
    """Return the packaged Bio-Harness skill index."""
    if not SKILLS_INDEX.exists():
        raise HTTPException(status_code=404, detail="Skills index not found")
    try:
        data = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return data


# ---------------------------------------------------------------------------
# 4. GET /api/workspace/tree
# ---------------------------------------------------------------------------


@app.get("/api/workspace/tree")
async def workspace_tree(path: str = Query(default="workspace")) -> list[dict[str, Any]]:
    """Return a bounded file tree for a workspace directory."""
    target = _safe_resolve(path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    entries: list[dict[str, Any]] = []
    try:
        for child in sorted(target.iterdir()):
            if child.name.startswith("."):
                continue
            entry: dict[str, Any] = {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "path": _relative(child),
            }
            if child.is_file():
                try:
                    entry["size"] = child.stat().st_size
                except OSError:
                    entry["size"] = 0
            else:
                entry["size"] = 0
            entries.append(entry)
            if len(entries) >= 200:
                break
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied") from exc
    return entries


# ---------------------------------------------------------------------------
# 5. GET /api/workspace/dirs
# ---------------------------------------------------------------------------


@app.get("/api/workspace/dirs")
async def workspace_dirs() -> dict[str, list[dict[str, str]]]:
    """Return likely output directories for the artifact browser."""
    outputs = WORKSPACE / "outputs"
    if not outputs.exists():
        return {"dirs": []}
    dirs = [{"name": "outputs (root)", "path": _relative(outputs)}]
    try:
        for child in sorted(outputs.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                dirs.append(
                    {
                        "name": child.name,
                        "path": _relative(child),
                    }
                )
    except PermissionError:
        pass
    return {"dirs": dirs}


# ---------------------------------------------------------------------------
# 6. GET /api/workspace/artifacts
# ---------------------------------------------------------------------------


@app.get("/api/workspace/artifacts")
async def workspace_artifacts(path: str = Query(...)) -> list[dict[str, Any]]:
    """Return artifact metadata for one workspace directory."""
    target = _safe_resolve(path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    artifacts: list[dict[str, Any]] = []
    try:
        for child in sorted(target.iterdir()):
            if child.is_dir() or child.name.startswith("."):
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            artifacts.append(
                {
                    "name": child.name,
                    "path": _relative(child),
                    "size": stat.st_size,
                    "kind": _infer_kind(child.name),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied") from exc
    return artifacts


# ---------------------------------------------------------------------------
# 7. GET /api/workspace/file
# ---------------------------------------------------------------------------

_TABLE_EXTENSIONS = {".csv", ".tsv", ".vcf", ".bed", ".gff", ".gtf"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf"}
_MAX_TEXT_SIZE = 2 * 1024 * 1024  # 2 MB


@app.get("/api/workspace/file")
async def workspace_file(path: str = Query(...)) -> Any:
    """Return a preview payload for a workspace file."""
    target = _safe_resolve(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    suffix = target.suffix.lower()

    # Images / PDF — serve directly
    if suffix in _IMAGE_EXTENSIONS:
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".svg": "image/svg+xml",
            ".pdf": "application/pdf",
        }
        return FileResponse(target, media_type=media_types.get(suffix, "application/octet-stream"))

    # Tabular — parse and return as JSON
    if suffix in _TABLE_EXTENSIONS:
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            # Detect delimiter
            delimiter = "\t" if suffix in {".tsv", ".vcf", ".bed", ".gff", ".gtf"} else ","
            # Skip VCF/GFF header lines
            lines = text.splitlines()
            data_lines = []
            header_comments: list[str] = []
            for line in lines:
                if line.startswith("##"):
                    header_comments.append(line)
                    continue
                data_lines.append(line)

            if not data_lines:
                return {"columns": [], "rows": [], "total_rows": 0}

            reader = csv.reader(data_lines, delimiter=delimiter)
            rows_out = []
            columns = []
            for i, row in enumerate(reader):
                if i == 0:
                    # Use first row as header; strip leading '#'
                    columns = [c.lstrip("#").strip() for c in row]
                    continue
                rows_out.append(row)
                if len(rows_out) >= 500:
                    break

            return {
                "columns": columns,
                "rows": rows_out,
                "total_rows": len(data_lines) - 1,
                "truncated": len(data_lines) - 1 > 500,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Error reading table: {exc}") from exc

    # Text / JSON / other
    try:
        size = target.stat().st_size
        if size > _MAX_TEXT_SIZE:
            raise HTTPException(status_code=413, detail="File too large for text preview")
        text = target.read_text(encoding="utf-8", errors="replace")

        if suffix == ".json":
            with contextlib.suppress(json.JSONDecodeError):
                return {"content_type": "json", "data": json.loads(text)}

        return {"content_type": "text", "content": text, "size": size}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 8. POST /api/chat
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Chat planning request sent from the React UI."""

    message: str
    session_id: str = ""
    model: str = ""
    data_root: str = ""
    output_dir: str = ""


@app.post("/api/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    """Ask the local model for a workflow plan or return a mock plan."""
    model = req.model or os.getenv("BIO_HARNESS_MODEL", "qwen3-coder-next:latest")

    # Check if Ollama is available
    resp = await _ollama_get("/api/tags")
    ollama_ok = resp is not None and resp.status_code == 200

    if not ollama_ok:
        # Return mock data so the UI is fully testable without Ollama
        return {
            "response": (
                "[Mock — Ollama not running] I would analyze your request and create "
                "a bioinformatics workflow plan. Here is a sample plan for demonstration:\n\n"
                "**Analysis type**: RNA-seq Differential Expression\n\n"
                "1. Align reads with STAR\n"
                "2. Quantify with featureCounts\n"
                "3. Run DESeq2 for differential expression\n\n"
                "Start Ollama to get real LLM responses."
            ),
            "plan": _mock_plan(),
            "mock": True,
        }

    # Call Ollama /api/chat
    messages = [
        {
            "role": "system",
            "content": (
                "You are Bio-Harness, a bioinformatics workflow planner. "
                "Given a user request, produce a JSON plan with fields: "
                "analysis_type (string), reasoning (string), and steps (list of objects "
                "with step_number, tool_name, description, parameters). "
                "Respond ONLY with valid JSON."
            ),
        },
        {"role": "user", "content": req.message},
    ]

    ollama_resp = await _ollama_post(
        "/api/chat",
        {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 4096},
        },
        timeout=300.0,
    )

    if ollama_resp is None or ollama_resp.status_code != 200:
        return {
            "response": "Error: could not reach Ollama model. Is the model pulled?",
            "plan": None,
            "error": True,
        }

    data = ollama_resp.json()
    content = data.get("message", {}).get("content", "")

    # Try to extract JSON plan from the response
    plan = None
    try:
        # Try direct parse
        plan = json.loads(content)
    except json.JSONDecodeError:
        # Try to find JSON block in the response
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if json_match:
            with contextlib.suppress(json.JSONDecodeError):
                plan = json.loads(json_match.group(1))

    return {
        "response": content,
        "plan": plan,
        "mock": False,
        "model": model,
        "eval_count": data.get("eval_count"),
        "eval_duration_ns": data.get("eval_duration"),
    }


# ---------------------------------------------------------------------------
# 9. POST /api/run
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    """Workflow execution request sent from the React UI."""

    plan: dict[str, Any]
    output_dir: str = ""


@app.post("/api/run")
async def start_run(req: RunRequest) -> dict[str, str]:
    """Queue a UI run and stream simulated progress events."""
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Persist the plan
    (run_dir / "plan.json").write_text(json.dumps(req.plan, indent=2), encoding="utf-8")

    # Write initial state
    state = {
        "run_id": run_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "plan": req.plan,
        "output_dir": req.output_dir or str(WORKSPACE / "outputs" / run_id),
        "steps_completed": 0,
        "steps_total": len(req.plan.get("steps", [])),
        "current_step": None,
        "error": None,
    }
    (run_dir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    _active_runs[run_id] = {"state": state, "run_dir": str(run_dir)}

    # TODO: Wire in actual execution via Orchestrator in background thread
    # For now, simulate progress so the UI can be developed
    task = asyncio.create_task(_simulate_run(run_id, req.plan))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"run_id": run_id, "status": "queued", "run_dir": _relative(run_dir)}


async def _simulate_run(run_id: str, plan: dict[str, Any]) -> None:
    """Simulate execution progress for UI development."""
    run_dir = RUNS_DIR / run_id
    events_file = run_dir / "events.jsonl"
    steps = plan.get("steps", [])

    def _write_state(state: dict[str, Any]) -> None:
        (run_dir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _append_event(event: dict[str, Any]) -> None:
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    state["status"] = "running"
    state["started_at"] = datetime.now().isoformat()
    _write_state(state)
    _append_event({"type": "run_started", "run_id": run_id, "ts": datetime.now().isoformat()})

    for i, step in enumerate(steps):
        state["current_step"] = step.get("tool_name", f"step_{i + 1}")
        state["steps_completed"] = i
        _write_state(state)

        _append_event(
            {
                "type": "step_started",
                "step_number": i + 1,
                "tool_name": step.get("tool_name", ""),
                "description": step.get("description", ""),
                "ts": datetime.now().isoformat(),
            }
        )

        # Simulate work
        await asyncio.sleep(2.0)

        _append_event(
            {
                "type": "step_completed",
                "step_number": i + 1,
                "tool_name": step.get("tool_name", ""),
                "exit_code": 0,
                "duration_s": 2.0,
                "ts": datetime.now().isoformat(),
            }
        )

    state["status"] = "completed"
    state["steps_completed"] = len(steps)
    state["current_step"] = None
    state["completed_at"] = datetime.now().isoformat()
    _write_state(state)
    _append_event({"type": "run_completed", "run_id": run_id, "ts": datetime.now().isoformat()})


# ---------------------------------------------------------------------------
# 10. GET /api/run/{run_id}
# ---------------------------------------------------------------------------


@app.get("/api/run/{run_id}")
async def get_run(run_id: str) -> Any:
    """Return persisted state for one UI run."""
    state_path = RUNS_DIR / run_id / "state.json"
    if not state_path.exists():
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 11. WebSocket /ws/run/{run_id}
# ---------------------------------------------------------------------------


@app.websocket("/ws/run/{run_id}")
async def ws_run(websocket: WebSocket, run_id: str) -> None:
    """Stream JSONL run events to a browser websocket."""
    await websocket.accept()
    events_path = RUNS_DIR / run_id / "events.jsonl"
    offset = 0

    try:
        while True:
            if events_path.exists():
                text = events_path.read_text(encoding="utf-8")
                lines = text.splitlines()
                new_lines = lines[offset:]
                for line in new_lines:
                    line = line.strip()
                    if line:
                        await websocket.send_text(line)
                offset = len(lines)

                # Check if run is done
                try:
                    last_event = json.loads(lines[-1]) if lines else {}
                    if last_event.get("type") in ("run_completed", "run_failed"):
                        break
                except (json.JSONDecodeError, IndexError):
                    pass

            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WebSocket error for run %s: %s", run_id, exc)


# ---------------------------------------------------------------------------
# 12. GET /api/system
# ---------------------------------------------------------------------------


@app.get("/api/system")
async def system_info() -> dict[str, float | int | None]:
    """Return basic local system resources for the UI status panel."""
    try:
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage(str(PROJECT_ROOT))
        return {
            "cpu_count": psutil.cpu_count(logical=True),
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "ram_total_gb": round(vm.total / (1024**3), 2),
            "ram_used_gb": round(vm.used / (1024**3), 2),
            "ram_percent": vm.percent,
            "disk_free_gb": round(disk.free / (1024**3), 2),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 13. POST /api/terminal/exec
# ---------------------------------------------------------------------------


class TerminalRequest(BaseModel):
    """Terminal execution request constrained to the project workspace."""

    command: str
    timeout: float = 30.0


@app.post("/api/terminal/exec")
async def terminal_exec(req: TerminalRequest) -> dict[str, int | str]:
    """Execute a shell command in the project workspace."""
    blocked_reason = _blocked_terminal_reason(req.command)
    if blocked_reason:
        return {"stdout": "", "stderr": f"Blocked: {blocked_reason}", "exit_code": 1}

    try:
        result = subprocess.run(
            req.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=min(req.timeout, MAX_TERMINAL_TIMEOUT_SECONDS),
            cwd=str(PROJECT_ROOT),
            env={
                **os.environ,
                "PATH": (
                    f"{PROJECT_ROOT / '.pixi' / 'envs' / 'default' / 'bin'}:"
                    f"{os.environ.get('PATH', '')}"
                ),
            },
        )
        return {
            "stdout": result.stdout[-10000:] if len(result.stdout) > 10000 else result.stdout,
            "stderr": result.stderr[-5000:] if len(result.stderr) > 5000 else result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Command timed out after {req.timeout}s", "exit_code": 124}
    except Exception as exc:
        return {"stdout": "", "stderr": str(exc), "exit_code": 1}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "ui_v2_api:app",
        host=_server_host_from_env(),
        port=_server_port_from_env(),
        reload=_env_bool("BIO_HARNESS_UI_RELOAD", True),
        log_level="info",
    )
