import queue
import re
import threading
import textwrap
import os
import json
import shlex
import shutil
import time
import traceback
from collections import deque
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import graphviz
import httpx
import psutil
import streamlit as st
import yaml

from bio_harness.agents.orchestrator import Orchestrator
from bio_harness.core.llm import BioLLM, BioHarnessError
from bio_harness.core.llm_backends import (
    backend_default_host,
    backend_label,
    normalize_backend_name,
)
from bio_harness.core.pathing import (
    canonical_resolve,
    discover_fastq_files_guarded,
    discover_read_roots,
    resolve_with_rejections,
)
from bio_harness.core.capability_catalog import (
    capability_index,
    infer_capabilities_from_text,
    infer_tool_hints_from_text,
    load_capability_catalog,
    normalize_capability_id,
    save_capability_catalog,
    update_capability_tool_hints,
)
from bio_harness.core.curated_tool_batches import CURATED_TOOL_BATCHES, install_curated_batch
from bio_harness.core.contracts import assess_plan_contract as assess_plan_contract_core
from bio_harness.core.executor_runtime import (
    executor_runtime_is_live,
    finish_executor_runtime,
    heartbeat_executor_runtime,
    start_executor_runtime,
)
from bio_harness.core.tool_onboarding import install_tool_onboarding_draft as install_tool_onboarding_draft_core
from bio_harness.core.run_state import (
    evaluate_existing_plan_resume,
    latest_error_event_detail,
    mark_running_items_failed,
)
from bio_harness.core.recovery_policy import (
    DEFAULT_MAX_REPAIR_ATTEMPTS,
    build_repair_audit_entry,
    can_attempt_repair,
    classify_failure,
    max_attempts_for_class,
)
from bio_harness.core.runner import CommandRunner
from bio_harness.core.tool_env import ensure_pixi_tooling_on_path
from bio_harness.core.run_artifacts import (
    append_event,
    append_line,
    write_exit,
    write_manifest,
    write_path_decisions,
)
from bio_harness.core.constants import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_LIVE_PROCESS_GRACE_SECONDS,
    DEFAULT_STALL_TIMEOUT_SECONDS,
)
from bio_harness.core.harness_help_context import build_harness_help_context
from bio_harness.core.llm_setup_support import build_llm_setup_report
from bio_harness.skills.registry import SkillRegistry
from bio_harness.tools.reader import Reader
from bio_harness.harness.contract_utils import _infer_request_contract as infer_request_contract_core
from bio_harness.ui.auto_plan import (
    is_actionable_execution_plan,
    normalize_ui_auto_plan,
)
from bio_harness.ui.bioagentbench_ui_support import (
    concretize_ui_benchmark_prompt,
    extract_ui_benchmark_data_root,
    is_ui_benchmark_prompt,
    ui_benchmark_policy,
)
from bio_harness.ui.chat_sessions import (
    build_chat_session_id,
    ensure_user_message_in_session,
    session_id_for_run,
)
from bio_harness.ui.chat_rendering import (
    maybe_append_chat_result_summary,
    render_chat_empty_state,
    render_chat_live_run_view,
    render_model_setup_block,
    render_workspace_header,
)
from bio_harness.ui.completed_run_followups import (
    build_completed_run_followup_response,
    should_route_completed_run_followup,
)
from bio_harness.ui.chat_first_shell import (
    chat_first_css,
    compact_model_name_for_rail,
    collect_run_artifacts,
    format_structured_chat_message,
    normalize_dock_view,
    preferred_chat_run,
    recent_event_rows,
    suggest_dock_view_from_request,
    status_badge,
    summarize_recent_runs,
)
from bio_harness.ui.path_text import extract_paths_from_text
from bio_harness.ui.planning_logic import build_ui_execution_plan
from bio_harness.ui.execution_plan_normalization import (
    normalize_ui_run_plan_for_execution,
)
from bio_harness.ui.planning_payloads import (
    apply_planner_payload as apply_planner_payload_ui,
    mark_planning_failure as mark_planning_failure_ui,
)
from bio_harness.ui.planning_run_state import (
    ensure_planning_run_initialized,
    launch_planner_job,
    load_planner_result,
    load_planner_status,
    planner_job_snapshot,
    planning_is_orphaned,
)
from bio_harness.ui.preflight import plan_requires_filename_group_tags
from bio_harness.ui.run_paths import resolve_effective_chat_selected_dir
from bio_harness.ui.run_data_root import resolve_effective_run_data_root
from bio_harness.ui.run_persistence import (
    init_run_files,
    load_all_events,
    merge_recent_persisted_runs,
    parse_event_epoch as _parse_event_epoch,
    persist_run_state,
    tail_items as _tail_items,
    write_terminal_artifacts_if_needed,
)
from bio_harness.ui.run_tracking import (
    append_run_log,
    append_tail,
    ensure_process,
    extract_pid_from_status_text,
    init_process_tracker,
    maybe_progress_update,
    parse_log_channel,
    run_has_live_executor_process,
    summarize_command_for_ui,
    update_process_tracker_from_log,
)
from bio_harness.ui.stall_policy import should_fail_ui_run_for_stall
from bio_harness.ui.data_root import (
    discovery_root_for_path,
    latest_path_hints_from_messages,
    select_preferred_latest_root,
)
from bio_harness.ui.deliverables import (
    capture_ui_run_final_outputs,
    materialize_ui_run_deliverables,
)
from bio_harness.ui.execution_request_context import build_execution_request_context
from bio_harness.ui.model_switch_help import build_model_switch_help
from bio_harness.ui.terminal_state import (
    drain_shell_log_queue,
    mark_shell_command_started,
    shell_output_text,
)
from bio_harness.workflows import (
    build_bootstrap_execution_plan as workflow_build_bootstrap_execution_plan,
    build_splicing_execution_plan as workflow_build_splicing_execution_plan,
    canonicalize_execution_plan,
    export_plan_run_scripts,
)

ensure_pixi_tooling_on_path()

st.set_page_config(layout="wide", page_title="BioHarness", initial_sidebar_state="collapsed")

WORKSPACE_ROOT = Path("workspace").resolve()
PROJECT_ROOT = Path(".").resolve()
READONLY_LINKS_ROOT = WORKSPACE_ROOT / "inputs_readonly"
RUNS_ROOT = WORKSPACE_ROOT / "runs"
READONLY_MANIFEST = WORKSPACE_ROOT / ".readonly_links.json"
SKILLS_DEFINITIONS = Path("bio_harness/skills/definitions").resolve()
SKILLS_LIBRARY = Path("bio_harness/skills/library").resolve()
CAPABILITIES_DIR = Path("bio_harness/capabilities").resolve()
CAPABILITY_CATALOG_PATH = CAPABILITIES_DIR / "catalog.json"
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
READONLY_LINKS_ROOT.mkdir(parents=True, exist_ok=True)
RUNS_ROOT.mkdir(parents=True, exist_ok=True)
SKILLS_DEFINITIONS.mkdir(parents=True, exist_ok=True)
CAPABILITIES_DIR.mkdir(parents=True, exist_ok=True)

# Timing and regex constants imported from bio_harness.core.constants
HEARTBEAT_INTERVAL_SECONDS = DEFAULT_HEARTBEAT_SECONDS
STALL_TIMEOUT_SECONDS = DEFAULT_STALL_TIMEOUT_SECONDS
LIVE_PROCESS_STALL_GRACE_SECONDS = DEFAULT_LIVE_PROCESS_GRACE_SECONDS
MAX_REPAIR_ATTEMPTS_BY_CLASS = DEFAULT_MAX_REPAIR_ATTEMPTS
MAX_QUEUE_LINES_PER_TICK = 400
MAX_WORKSPACE_DIR_OPTIONS = 2000
MAX_TREE_LINES = 1200
MAX_TREE_ENTRIES_PER_DIR = 200


@st.cache_resource
def get_orchestrator(model_name: str, host: Optional[str], llm_backend: str) -> Orchestrator:
    return Orchestrator(SKILLS_DEFINITIONS, SKILLS_LIBRARY, model_name=model_name, host=host, llm_backend=llm_backend)


@st.cache_resource
def get_reader(model_name: str, host: Optional[str], llm_backend: str) -> Reader:
    return Reader(model_name=model_name, host=host, llm_backend=llm_backend)


@st.cache_data(ttl=20, show_spinner=False)
def cached_llm_setup_report(llm_backend_name: str, selected_model_name: str, backend_host: str) -> dict[str, Any]:
    """Return a cached deterministic LLM-backend setup report."""
    return build_llm_setup_report(
        llm_backend=llm_backend_name,
        model_name=selected_model_name,
        host=backend_host,
        pull_if_missing=False,
    )


def init_state() -> None:
    st.session_state.setdefault("selected_dir", str(WORKSPACE_ROOT))
    st.session_state.setdefault("shell_running", False)
    st.session_state.setdefault("shell_output", [])
    st.session_state.setdefault("shell_log_queue", queue.Queue())
    st.session_state.setdefault("shell_thread", None)
    st.session_state.setdefault("plan_running", False)
    st.session_state.setdefault("plan_log_queue", queue.Queue())
    st.session_state.setdefault("plan_thread", None)
    st.session_state.setdefault("plan_execution_run_id", None)
    st.session_state.setdefault("plan_runs", [])
    st.session_state.setdefault("next_plan_id", 1)
    st.session_state.setdefault("active_plan_id", None)
    st.session_state.setdefault("last_plan", None)
    st.session_state.setdefault("plan_execution_mode", None)
    st.session_state.setdefault("plan_execution_step_idx", None)
    st.session_state.setdefault("orchestrator_session_id", "default")
    st.session_state.setdefault("model_busy", False)
    st.session_state.setdefault("model_last_activity", "")
    st.session_state.setdefault("model_last_heartbeat_note", "")
    st.session_state.setdefault("executor_last_heartbeat_ts", 0.0)
    st.session_state.setdefault("executor_last_heartbeat_note", "")
    st.session_state.setdefault("executor_last_heartbeat_event_epoch", 0.0)
    st.session_state.setdefault("executor_last_progress_ts", 0.0)
    st.session_state.setdefault("executor_last_progress_note", "")
    st.session_state.setdefault("auto_start_from_chat", True)
    st.session_state.setdefault("auto_remediate_missing_tools", True)
    st.session_state.setdefault("chat_data_root", str(WORKSPACE_ROOT))
    st.session_state.setdefault("chat_include_subdirs", False)
    st.session_state.setdefault("chat_filename_filter", "")
    st.session_state.setdefault("chat_max_files", 200)
    st.session_state.setdefault("chat_selected_files", [])
    st.session_state.setdefault("chat_use_test_subset", True)
    st.session_state.setdefault("chat_test_subset_reads", 1000000)
    st.session_state.setdefault("pending_plan_retry_run_id", None)
    st.session_state.setdefault("tool_remediation_active", False)
    st.session_state.setdefault("tool_remediation_run_id", None)
    st.session_state.setdefault("tool_remediation_tools", [])
    st.session_state.setdefault("path_resolution", {})
    st.session_state.setdefault("chat_fastq_discovery_cache", {})
    st.session_state.setdefault("capability_onboarding_draft", {})
    st.session_state.setdefault("capability_onboarding_source", {})
    st.session_state.setdefault("latest_run_badge", "Idle")
    st.session_state.setdefault("plan_last_poll_at", 0.0)
    st.session_state.setdefault("live_refresh_enabled", False)
    st.session_state.setdefault("live_refresh_last_tick", 0.0)
    st.session_state.setdefault("dock_view", "Hidden")
    st.session_state.setdefault("pending_dock_view", None)
    st.session_state.setdefault(
        "trusted_reference_domains",
        ["ncbi.nlm.nih.gov", "encodeproject.org", "ensembl.org"],
    )


@st.fragment(run_every=1)
def refresh_driver() -> None:
    if not st.session_state.get("live_refresh_enabled", False):
        return
    now_ts = time.time()
    last_tick = float(st.session_state.get("live_refresh_last_tick", 0.0) or 0.0)
    if (now_ts - last_tick) < 0.9:
        return
    st.session_state.live_refresh_last_tick = now_ts
    st.rerun()


init_state()
refresh_driver()


def mark_model_start() -> None:
    st.session_state.model_busy = True
    st.session_state.model_last_heartbeat_note = "Running inference"


def mark_model_end() -> None:
    st.session_state.model_busy = False
    st.session_state.model_last_activity = datetime.utcnow().isoformat()
    st.session_state.model_last_heartbeat_note = "Inference idle"


def run_model_call_with_timeout(callable_fn, timeout_seconds: int, timeout_context: str):
    result_q: queue.Queue = queue.Queue(maxsize=1)

    def _worker() -> None:
        try:
            result_q.put(("ok", callable_fn()))
        except Exception as exc:
            result_q.put(("err", exc))

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join(timeout=max(1, int(timeout_seconds)))
    if worker.is_alive():
        raise BioHarnessError(
            f"{timeout_context} timed out after {int(timeout_seconds)}s. "
            "Please retry with a shorter request or raise BIO_HARNESS_UI_PLAN_TIMEOUT_SECONDS."
        )
    if result_q.empty():
        raise BioHarnessError(f"{timeout_context} failed without returning a result.")
    kind, payload = result_q.get_nowait()
    if kind == "err":
        raise payload
    return payload


def should_auto_start_from_chat(user_text: str) -> bool:
    text = user_text.lower()
    triggers = (
        "proceed",
        "start",
        "run it",
        "run now",
        "execute",
        "go ahead",
        "do it",
    )
    return any(t in text for t in triggers)


def should_resume_terminal_run(user_text: str) -> bool:
    text = (user_text or "").lower()
    resume_triggers = (
        "resume this run",
        "resume run",
        "continue this run",
        "retry this run",
        "retry step",
        "resume step",
    )
    return any(t in text for t in resume_triggers)


def should_create_fresh_run_for_proceed(run: dict) -> bool:
    status = str(run.get("status", "")).strip().lower()
    if status in {"failed", "completed"}:
        return True
    if run.get("run_uid"):
        return True
    if run.get("plan"):
        return True
    if run.get("logs"):
        return True
    return False


def try_start_existing_plan_from_proceed(run: dict, orchestrator: Orchestrator, user_text: str) -> tuple[bool, str]:
    decision = evaluate_existing_plan_resume(
        run,
        plan_running=bool(st.session_state.get("plan_running", False)),
        allow_terminal_resume=should_resume_terminal_run(user_text),
    )
    if not decision.get("reusable", False):
        return False, ""

    action = str(decision.get("action", ""))
    resume_idx = int(decision.get("resume_idx", 0))
    if action == "single_step":
        start_single_step_execution(run, orchestrator, resume_idx)
    elif action == "full_plan":
        start_plan_execution(run, orchestrator)
    return True, str(decision.get("message", ""))

default_model_name = os.getenv("BIO_HARNESS_MODEL", "qwen3-coder-next:latest")
default_backend = normalize_backend_name(
    os.getenv("BIO_HARNESS_LLM_BACKEND", os.getenv("BIO_HARNESS_LLM_PROVIDER", "ollama"))
)
backend_options = ["ollama", "ollama_openai", "mlx", "vllm", "openai_compatible"]
backend_labels = {name: backend_label(name) for name in backend_options}
default_backend_index = backend_options.index(default_backend) if default_backend in backend_options else 0
default_host_env_candidates = {
    "ollama": os.getenv("BIO_HARNESS_OLLAMA_HOST", ""),
    "ollama_openai": os.getenv("BIO_HARNESS_OLLAMA_OPENAI_BASE_URL", os.getenv("BIO_HARNESS_OLLAMA_HOST", "")),
    "mlx": os.getenv("BIO_HARNESS_MLX_BASE_URL", os.getenv("BIO_HARNESS_OPENAI_BASE_URL", "")),
    "vllm": os.getenv("BIO_HARNESS_VLLM_BASE_URL", os.getenv("BIO_HARNESS_OPENAI_BASE_URL", "")),
    "openai_compatible": os.getenv("BIO_HARNESS_OPENAI_BASE_URL", ""),
}
llm_backend = st.sidebar.selectbox(
    "LLM Backend",
    options=backend_options,
    index=default_backend_index,
    format_func=lambda x: backend_labels.get(x, x),
    help="Choose the model transport first. The model name and host are interpreted relative to this backend.",
)
default_host = default_host_env_candidates.get(llm_backend, "") or backend_default_host(llm_backend)
model_switch_help = build_model_switch_help(llm_backend, st.session_state.get("model_name", default_model_name))
model_name = st.sidebar.text_input(
    "Model",
    value=default_model_name,
    key="model_name",
    help="Enter the exact model id exposed by the selected backend. The new model is used on the next request.",
)
host_label = "Backend Base URL" if llm_backend != "ollama" else "Ollama Host"
backend_host = st.sidebar.text_input(
    host_label,
    value=default_host,
    help=model_switch_help["host_help"],
)
resolved_host = backend_host.strip() or None
llm = BioLLM(model_name=model_name, host=resolved_host, llm_backend=llm_backend)
ok, status_msg = llm.is_available()
llm_setup_report = cached_llm_setup_report(llm_backend, model_name, resolved_host or "")
mem = psutil.virtual_memory()
cpu = psutil.cpu_percent(interval=None)

with st.sidebar:
    with st.expander("How To Switch Models", expanded=False):
        for idx, step in enumerate(model_switch_help["steps"], start=1):
            st.markdown(f"{idx}. {step}")
        if str(model_switch_help.get("backend_note", "")).strip():
            st.caption(str(model_switch_help["backend_note"]))
        examples = list(model_switch_help.get("examples", []) or [])
        if examples:
            st.markdown("**Examples**")
            for example in examples:
                st.markdown(f"- {example}")
        if str(model_switch_help.get("gemini_note", "")).strip():
            st.info(str(model_switch_help["gemini_note"]))
    st.markdown("### Runtime Settings")
    st.caption("Advanced connection and model controls.")
    st.write(status_msg)
    if not ok:
        st.warning(
            "Harness planning/summarization will fail until the model is available. "
            f"Check {backend_labels.get(llm_backend, llm_backend)}, host, model name, and localhost permissions."
        )
        if "loopback access" in status_msg.lower():
            st.error(
                "This runtime cannot reach the local model server over localhost. "
                "Run the UI with local network permission or outside the sandbox."
            )
    note = st.session_state.get("model_last_heartbeat_note", "").strip()
    if note:
        st.caption(f"Model note: {note}")
    with st.expander("Live machine status", expanded=False):
        st.progress(mem.percent / 100, text=f"RAM {mem.percent:.1f}% ({mem.used // 1024**3} / {mem.total // 1024**3} GB)")
        st.progress(cpu / 100, text=f"CPU {cpu:.1f}%")

# Main layout shell
st.markdown(chat_first_css(), unsafe_allow_html=True)
st.markdown(
    """
    <div class="bh-hero">
      <div class="bh-kicker">Local Bioinformatics Harness</div>
      <h1 class="bh-hero-title">BioHarness</h1>
      <div class="bh-hero-subtitle">
        Chat-first analysis with deterministic runtime scaffolding, live progress, and on-demand workspace context.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)
shell_left, shell_content = st.columns([1.0, 4.0], gap="large")


@st.cache_data(ttl=20, show_spinner=False)
def list_relative_paths(base_path: str, max_dirs: int = MAX_WORKSPACE_DIR_OPTIONS) -> list[str]:
    base = Path(base_path)
    paths = [str(base)]
    count = 1
    try:
        for root, dirs, _ in os.walk(base, topdown=True, followlinks=False):
            dirs[:] = sorted(dirs, key=lambda x: x.lower())
            for dname in dirs:
                p = Path(root) / dname
                paths.append(str(p))
                count += 1
                if count >= max_dirs:
                    return paths
    except Exception:
        return [str(base)]
    return paths

def render_tree(
    root: Path,
    depth: int = 0,
    max_depth: int = 4,
    line_budget: int = MAX_TREE_LINES,
    per_dir_cap: int = MAX_TREE_ENTRIES_PER_DIR,
) -> int:
    if depth > max_depth or line_budget <= 0:
        return line_budget
    try:
        entries = root.iterdir()
    except Exception:
        return line_budget
    shown = 0
    for entry in entries:
        if line_budget <= 0:
            break
        if shown >= per_dir_cap:
            indent = "&nbsp;" * (depth * 4)
            st.markdown(f"{indent}… _(truncated)_", unsafe_allow_html=True)
            line_budget -= 1
            break
        try:
            rel = entry.relative_to(WORKSPACE_ROOT)
        except Exception:
            rel = entry
        indent = "&nbsp;" * (depth * 4)
        if entry.is_symlink():
            st.markdown(f"{indent}🔗 `{rel}`", unsafe_allow_html=True)
            line_budget -= 1
            shown += 1
            continue
        if entry.is_dir():
            st.markdown(f"{indent}📁 `{rel}/`", unsafe_allow_html=True)
            line_budget -= 1
            line_budget = render_tree(entry, depth + 1, max_depth, line_budget, per_dir_cap)
        else:
            st.markdown(f"{indent}📄 `{rel}`", unsafe_allow_html=True)
            line_budget -= 1
        shown += 1
    return line_budget


def resolve_data_root_with_guards(initial_root: str, recent_messages: list[dict], max_files: int = 2000) -> tuple[str, int, str, list[dict]]:
    candidates: list[str] = []
    if initial_root:
        candidates.append(initial_root)
    for m in recent_messages[-10:]:
        if m.get("role") != "user":
            continue
        candidates.extend(extract_paths_from_text(m.get("content", "")))

    read_roots = discover_read_roots(WORKSPACE_ROOT, READONLY_LINKS_ROOT)
    resolved, rejected = resolve_with_rejections(
        candidates,
        read_roots=read_roots,
        write_roots=[WORKSPACE_ROOT],
        mode="read",
        readonly_root=READONLY_LINKS_ROOT,
    )

    # Highest priority: explicit paths in the most recent user message.
    latest_user_paths = latest_path_hints_from_messages(
        recent_messages,
        extractor=extract_paths_from_text,
    )
    latest_candidates: list[Path] = []
    for ptxt in latest_user_paths:
        resolved_latest, _ = canonical_resolve(
            ptxt,
            read_roots=read_roots,
            write_roots=[WORKSPACE_ROOT],
            mode="read",
            readonly_root=READONLY_LINKS_ROOT,
        )
        if resolved_latest is None:
            continue
        latest_candidates.append(resolved_latest)
    best_latest_root, best_latest_count = select_preferred_latest_root(
        latest_candidates,
        fastq_counter=lambda path: _count_fastq_in_dir(str(path), max_files=max_files),
    )
    if best_latest_root is not None:
        if best_latest_count > 0:
            return str(best_latest_root), best_latest_count, "preferred_latest_user_message_root", rejected
        return str(best_latest_root), 0, "preferred_latest_user_message_path", rejected

    # Respect the currently selected/requested data root when it is valid and non-empty.
    preferred_root = None
    preferred_count = 0
    if initial_root:
        preferred_root, _ = canonical_resolve(
            initial_root,
            read_roots=read_roots,
            write_roots=[WORKSPACE_ROOT],
            mode="read",
            readonly_root=READONLY_LINKS_ROOT,
        )
        if preferred_root is not None:
            preferred_root = discovery_root_for_path(preferred_root)
            preferred_count = _count_fastq_in_dir(str(preferred_root), max_files=max_files)
            if preferred_count > 0:
                return str(preferred_root), preferred_count, "preferred_user_selected_root", rejected

    # Score all valid candidates by FASTQ count, then pick the best.
    valid_candidates: list[Path] = []
    for candidate in candidates:
        c_resolved, c_reason = canonical_resolve(
            candidate,
            read_roots=read_roots,
            write_roots=[WORKSPACE_ROOT],
            mode="read",
            readonly_root=READONLY_LINKS_ROOT,
        )
        if c_resolved is None:
            rejected.append({"candidate": str(candidate), "reason": c_reason})
            continue
        valid_candidates.append(discovery_root_for_path(c_resolved))

    best_dir = str(discovery_root_for_path(resolved) if resolved is not None else WORKSPACE_ROOT)
    best_count = -1
    reason = "fallback_to_inputs_readonly_scan" if resolved is None else "resolved_from_candidates"
    for c in valid_candidates:
        c_count = _count_fastq_in_dir(str(c), max_files=max_files)
        if c_count > best_count:
            best_count = c_count
            best_dir = str(c)
            reason = "resolved_from_candidates_with_fastq_match"

    if best_count < 0:
        best_count = _count_fastq_in_dir(best_dir, max_files=max_files)

    if best_count <= 0:
        discovered = discover_fastq_files_guarded(
            READONLY_LINKS_ROOT,
            include_subdirs=True,
            name_filter="",
            max_files=max_files,
        )
        parent_counts: dict[str, int] = {}
        for fp in discovered:
            parent = str(Path(fp).parent)
            parent_counts[parent] = parent_counts.get(parent, 0) + 1
        if parent_counts:
            best_dir, best_count = max(parent_counts.items(), key=lambda kv: kv[1])
            reason = "auto_discovered_from_inputs_readonly_scan"
    return best_dir, best_count, reason, rejected


def is_under_readonly(path: Path) -> bool:
    try:
        path.resolve().relative_to(READONLY_LINKS_ROOT.resolve())
        return True
    except Exception:
        return False


def load_capability_catalog_data() -> dict:
    try:
        return load_capability_catalog(CAPABILITY_CATALOG_PATH)
    except Exception:
        return {"version": 1, "capabilities": [], "custom_tools": []}


def save_capability_catalog_data(catalog: dict) -> dict:
    save_capability_catalog(CAPABILITY_CATALOG_PATH, catalog)
    return load_capability_catalog_data()


def capability_specs_from_catalog(catalog: dict) -> dict[str, dict]:
    specs: dict[str, dict] = {}
    for cap_id, cap in capability_index(catalog, enabled_only=False).items():
        plan_signals = [str(x).strip().lower() for x in cap.get("plan_signals", []) if str(x).strip()]
        spec = {"plan_signals": plan_signals}
        if cap_id == "group_comparison":
            spec["group_signal_mode"] = str(cap.get("group_signal_mode", "auto")).strip().lower() or "auto"
        specs[cap_id] = spec
    return specs


def _slugify_skill_name(raw: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_]+", "_", str(raw or "").strip().lower()).strip("_")
    if not token:
        token = "custom_tool"
    if token[0].isdigit():
        token = f"tool_{token}"
    return token


def _strip_html_to_text(html: str) -> str:
    no_script = re.sub(r"<script[\s\S]*?</script>", " ", html or "", flags=re.IGNORECASE)
    no_style = re.sub(r"<style[\s\S]*?</style>", " ", no_script, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", no_style)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def load_tool_source_text(source_ref: str, *, reader: Reader | None = None) -> tuple[str, dict]:
    ref = str(source_ref or "").strip()
    if not ref:
        raise ValueError("A source URL/path is required.")

    if ref.lower().startswith(("http://", "https://")):
        with httpx.Client(timeout=45) as client:
            response = client.get(ref, follow_redirects=True)
            response.raise_for_status()
            content_type = str(response.headers.get("content-type", "")).lower()
            if "pdf" in content_type:
                docs_dir = WORKSPACE_ROOT / "docs"
                docs_dir.mkdir(parents=True, exist_ok=True)
                local_pdf = docs_dir / f"onboard_{int(time.time())}.pdf"
                local_pdf.write_bytes(response.content)
                if reader is None:
                    raise ValueError("PDF source requires Reader initialization.")
                text = reader.pdf_to_markdown(local_pdf)
                return text, {"source": ref, "mode": "url_pdf", "local_copy": str(local_pdf)}
            body = response.text
        text = _strip_html_to_text(body)
        return text, {"source": ref, "mode": "url_html"}

    path = Path(ref).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Source path not found: {path}")
    if path.suffix.lower() == ".pdf":
        if reader is None:
            raise ValueError("PDF source requires Reader initialization.")
        text = reader.pdf_to_markdown(path)
        return text, {"source": str(path), "mode": "file_pdf"}
    text = path.read_text(encoding="utf-8", errors="ignore")
    return text, {"source": str(path), "mode": "file_text"}


def build_tool_onboarding_draft(
    *,
    source_ref: str,
    source_text: str,
    tool_name_hint: str,
    active_catalog: dict,
) -> dict:
    source_excerpt = (source_text or "")[:120000]
    text_for_infer = f"{tool_name_hint}\n{source_excerpt}"
    inferred_caps = infer_capabilities_from_text(text_for_infer, active_catalog, enabled_only=False)
    inferred_hints = infer_tool_hints_from_text(text_for_infer, active_catalog, enabled_only=False)
    tool_guess = _slugify_skill_name(tool_name_hint or Path(urlparse(source_ref).path or source_ref).stem or "custom_tool")

    system_prompt = (
        "You are extracting a bioinformatics tool onboarding draft.\n"
        "Return ONLY valid JSON with keys:\n"
        "{"
        "\"skill_name\": string,"
        "\"description\": string,"
        "\"risk_level\": \"low\"|\"medium\"|\"high\","
        "\"tools_required\": [string],"
        "\"capabilities\": [string],"
        "\"parameters\": {param_name: {\"type\": \"string|path|integer|boolean\", \"description\": string, \"required\": boolean}},"
        "\"command_template\": string,"
        "\"usage_guide\": string"
        "}"
    )
    user_prompt = (
        f"Source: {source_ref}\n"
        f"Tool name hint: {tool_name_hint}\n"
        f"Known capability ids: {sorted(capability_index(active_catalog, enabled_only=False).keys())}\n"
        f"Pre-inferred capabilities: {inferred_caps}\n"
        f"Pre-inferred tool hints: {inferred_hints}\n\n"
        f"Tool/manual excerpt:\n{source_excerpt}"
    )
    draft: dict = {}
    try:
        llm_resp = llm.generate_text(system_prompt=system_prompt, user_prompt=user_prompt, num_ctx=8192)
        start = llm_resp.find("{")
        end = llm_resp.rfind("}")
        if start >= 0 and end > start:
            draft = json.loads(llm_resp[start : end + 1])
    except Exception:
        draft = {}

    skill_name = _slugify_skill_name(str(draft.get("skill_name", "")).strip() or tool_guess)
    capabilities = [
        normalize_capability_id(x)
        for x in (draft.get("capabilities", inferred_caps) if isinstance(draft.get("capabilities", inferred_caps), list) else inferred_caps)
        if normalize_capability_id(x)
    ]
    if not capabilities:
        capabilities = inferred_caps or ["alignment"]
    capabilities = list(dict.fromkeys(capabilities))

    tools_required = draft.get("tools_required", [])
    if not isinstance(tools_required, list):
        tools_required = []
    normalized_tools = [str(x).strip().lower() for x in tools_required if str(x).strip()]
    if not normalized_tools:
        normalized_tools = [skill_name]

    params = draft.get("parameters", {})
    if not isinstance(params, dict) or not params:
        params = {
            "command": {
                "type": "string",
                "description": "Tool command arguments or full command segment.",
                "required": True,
            }
        }

    risk = str(draft.get("risk_level", "medium")).strip().lower()
    if risk not in {"low", "medium", "high"}:
        risk = "medium"

    command_template = str(draft.get("command_template", "")).strip()
    usage = str(draft.get("usage_guide", "")).strip()
    if not usage:
        usage = (
            f"Source: {source_ref}\n"
            "Use this tool via generated skill function.\n"
            "Review required inputs and outputs before production runs."
        )

    return {
        "skill_name": skill_name,
        "description": str(draft.get("description", "")).strip() or f"Run {skill_name} from curated manual/source.",
        "risk_level": risk,
        "tools_required": normalized_tools,
        "capabilities": capabilities,
        "parameters": params,
        "command_template": command_template,
        "usage_guide": usage,
    }


def build_generic_skill_library_stub(skill_name: str, default_tool: str) -> str:
    tool_name = default_tool or skill_name
    tool_literal = json.dumps(str(tool_name).strip())
    return (
        "from __future__ import annotations\n\n"
        "import shlex\n\n\n"
        f"def {skill_name}(**kwargs) -> str:\n"
        "    # If caller provides a full command, trust and return it.\n"
        "    if \"command\" in kwargs and str(kwargs.get(\"command\", \"\")).strip():\n"
        "        return str(kwargs[\"command\"]).strip()\n"
        f"    tool = {tool_literal}\n"
        "    parts: list[str] = [tool]\n"
        "    for key, value in kwargs.items():\n"
        "        if key == \"command\":\n"
        "            continue\n"
        "        flag = \"--\" + str(key).strip().replace(\"_\", \"-\")\n"
        "        if isinstance(value, bool):\n"
        "            if value:\n"
        "                parts.append(flag)\n"
        "            continue\n"
        "        if value is None:\n"
        "            continue\n"
        "        parts.extend([flag, str(value)])\n"
        "    return \" \".join(shlex.quote(x) for x in parts)\n"
    )


def install_tool_onboarding_draft(draft: dict, source_meta: dict) -> tuple[bool, str]:
    ok, message = install_tool_onboarding_draft_core(
        draft,
        source_meta,
        skills_definitions_dir=SKILLS_DEFINITIONS,
        skills_library_dir=SKILLS_LIBRARY,
        capability_catalog_path=CAPABILITY_CATALOG_PATH,
        install_workflow="controlled_source_onboarding",
        record_custom_tool=True,
    )
    try:
        get_orchestrator.clear()
    except Exception:
        pass
    return ok, message


def reconcile_run_from_events(run: dict) -> None:
    events = load_all_events(run)
    if not events:
        return
    previous_status = str(run.get("status", "")).strip().lower()
    previous_error = str(run.get("error", "")).strip()
    init_process_tracker(run)
    if run.get("step_statuses"):
        for i in range(len(run["step_statuses"])):
            if run["step_statuses"][i] == "running":
                run["step_statuses"][i] = "pending"
    last_executor_event_epoch = 0.0
    for ev in events:
        sid = ev.get("step_id")
        et = ev.get("event_type", "")
        sev = ev.get("severity", "info")
        payload = ev.get("payload", {}) if isinstance(ev.get("payload", {}), dict) else {}
        ev_agent = str(ev.get("agent", ""))
        ev_epoch = _parse_event_epoch(str(ev.get("ts", "")))
        if ev_agent == "ExecutorAgent":
            last_executor_event_epoch = max(last_executor_event_epoch, ev_epoch)
        if isinstance(sid, int) and sid > 0:
            p = ensure_process(run, sid, payload.get("tool_name", "bash_run"))
            if et == "STEP_STARTED":
                p["status"] = "running"
                p["active_pid"] = None
                p["status_text"] = summarize_command_for_ui(p.get("command", "")) or "Step started"
                if run.get("step_statuses") and sid <= len(run["step_statuses"]):
                    run["step_statuses"][sid - 1] = "running"
            elif et == "STEP_FINISHED":
                if sev == "error":
                    p["status"] = "failed"
                    p["active_pid"] = None
                    p["status_text"] = f"Step failed (exit {payload.get('exit_code', '?')})"
                    if run.get("step_statuses") and sid <= len(run["step_statuses"]):
                        run["step_statuses"][sid - 1] = "failed"
                else:
                    p["status"] = "completed"
                    p["active_pid"] = None
                    p["status_text"] = "Step completed"
                    if run.get("step_statuses") and sid <= len(run["step_statuses"]):
                        run["step_statuses"][sid - 1] = "completed"
            elif et == "STEP_HEARTBEAT":
                note = payload.get("status_line", "")
                if note:
                    p["last_heartbeat_ts"] = ev_epoch if ev_epoch > 0 else time.time()
                    pid = extract_pid_from_status_text(note)
                    if pid is not None:
                        p["active_pid"] = pid
                    pretty = maybe_progress_update(p.get("command", ""), note) or note
                    p["status_text"] = pretty
                    cur_epoch = float(st.session_state.get("executor_last_heartbeat_event_epoch", 0.0) or 0.0)
                    if ev_agent == "ExecutorAgent" and ev_epoch >= cur_epoch:
                        st.session_state.executor_last_heartbeat_note = pretty
                        st.session_state.executor_last_heartbeat_ts = ev_epoch if ev_epoch > 0 else time.time()
                        st.session_state.executor_last_heartbeat_event_epoch = ev_epoch if ev_epoch > 0 else time.time()
            elif et == "RECOVERY_RESULT" and sev == "error":
                p["status"] = "failed"
                p["active_pid"] = None
                p["status_text"] = "Validation/recovery failed"
                if run.get("step_statuses") and sid <= len(run["step_statuses"]):
                    run["step_statuses"][sid - 1] = "failed"
            elif et == "STEP_BLOCKED":
                p["status"] = "failed"
                p["active_pid"] = None
                p["status_text"] = "Step blocked by validation/policy"
                if run.get("step_statuses") and sid <= len(run["step_statuses"]):
                    run["step_statuses"][sid - 1] = "failed"
                failure_class = str(payload.get("failure_class", "")).strip().lower()
                if failure_class == "policy_block":
                    run["policy_block_detected"] = True
                if failure_class == "validation_block":
                    run["validation_block_detected"] = True

    # Derive run status from event stream.
    has_error_event, latest_error_detail = latest_error_event_detail(events)
    if has_error_event:
        run["status"] = "failed"
        run["async_status"] = "failed"
        run["error"] = latest_error_detail
    elif run.get("step_statuses") and all(s == "completed" for s in run["step_statuses"]):
        run["status"] = "completed"
        run["async_status"] = "completed"
    elif any(s == "running" for s in run.get("step_statuses", [])):
        run["status"] = "running"
        run["async_status"] = "running"
    elif previous_status == "failed" and previous_error:
        run["status"] = "failed"
        run["async_status"] = "failed"
        if not run.get("error"):
            run["error"] = previous_error

    last_event_epoch = _parse_event_epoch(str(events[-1].get("ts", "")))
    run["last_event_epoch"] = last_event_epoch
    if last_executor_event_epoch > 0:
        run["last_executor_event_ts"] = last_executor_event_epoch

    # Guard against stale "running" when no active executor is present.
    has_live_process = run_has_live_executor_process(run)
    if (
        run.get("status") == "running"
        and not st.session_state.get("plan_running", False)
        and not has_live_process
        and last_event_epoch > 0
        and (time.time() - last_event_epoch) > STALL_TIMEOUT_SECONDS
    ):
        run["status"] = "failed"
        run["async_status"] = "failed"
        if not run.get("error"):
            run["error"] = (
                f"Run stalled: no executor events for >{STALL_TIMEOUT_SECONDS}s "
                "and no active execution thread/process."
            )
        for i, status in enumerate(run.get("step_statuses", [])):
            if status == "running":
                run["step_statuses"][i] = "failed"
        for key in run.get("process_order", []):
            proc = run.get("process_tracker", {}).get(key, {})
            if proc.get("status") == "running":
                proc["status"] = "failed"
                proc["status_text"] = "Stalled: executor stopped without completion signal"

    completed = 0
    for i, s in enumerate(run.get("step_statuses", []), start=1):
        if s == "completed":
            completed = i
        elif s in {"running", "failed", "pending"}:
            break
    run["next_step_idx"] = completed


def post_execution_block_message(orchestrator: Orchestrator, message: str) -> None:
    try:
        session_id = st.session_state.get("orchestrator_session_id", "default")
        session = orchestrator.get_or_create_session(session_id)
        session["messages"].append({"role": "assistant", "content": message})
    except Exception:
        return


def log_ui_exception(run: Optional[dict], where: str, exc: Exception) -> None:
    if not run:
        return
    tb = traceback.format_exc(limit=4)
    run.setdefault("logs", []).append(f"[ui-exception] {where}: {exc}\n")
    run_files = run.get("run_files", {})
    if run_files:
        append_event(
            Path(run_files["events"]),
            run_id=run.get("run_uid", ""),
            step_id=None,
            agent="Orchestrator",
            event_type="UI_EXCEPTION",
            severity="error",
            payload={"where": where, "error": str(exc), "traceback": tb},
        )


def latest_run_summary(run: dict) -> str:
    if not run:
        return "No active run."
    status = run.get("status", "unknown")
    run_id = run.get("run_uid", "") or "n/a"
    err = run.get("error", "") or "none"
    steps = run.get("step_statuses", [])
    completed = sum(1 for s in steps if s == "completed")
    total = len(steps)
    return (
        f"Latest run `{run_id}` status: `{status}`.\n"
        f"- Step progress: `{completed}/{total}`\n"
        f"- Error: `{err}`\n"
        f"- Run folder: `{run.get('run_dir', 'n/a')}`"
    )


def apply_planner_payload(run: dict, planner_payload: dict[str, Any]) -> None:
    """Apply a completed planner payload to one UI run."""
    apply_planner_payload_ui(
        run,
        planner_payload,
        session_state=st.session_state,
        fallback_benchmark_policy=ui_benchmark_policy(),
    )


def mark_planning_failure(run: dict, *, status: str, error: str) -> None:
    """Persist one planning-phase failure on a UI run."""
    mark_planning_failure_ui(run, status=status, error=error)


def reconcile_background_planning(run: dict, orchestrator: Orchestrator) -> None:
    """Advance or fail one persisted planning run from durable planner state."""
    if str(run.get("status", "")).strip().lower() not in {"planning", "planned", "planning_failed", "planning_timed_out"}:
        return
    run_files = run.get("run_files", {})
    run_uid = str(run.get("run_uid", "")).strip()
    if not run_files or not run_uid:
        return

    planner_status = load_planner_status(run_files)
    planner_snapshot = planner_job_snapshot(run_uid)
    planner_state = str(planner_status.get("status", "")).strip().lower()
    if planner_state:
        run["planner_status"] = planner_state
        run["planning_started_at"] = str(
            planner_status.get("started_at", run.get("planning_started_at", ""))
        ).strip()
        run["planning_finished_at"] = str(
            planner_status.get("finished_at", run.get("planning_finished_at", ""))
        ).strip()
        run["planner_error"] = str(planner_status.get("error", run.get("planner_error", ""))).strip()

    if planner_state == "planned":
        planner_payload = load_planner_result(run_files)
        if planner_payload and not run.get("plan"):
            append_model_trace(run, "Background planning finished; applying persisted plan.")
            apply_planner_payload(run, planner_payload)
            persist_run_state(run)
        if run.get("plan") and not st.session_state.plan_running and not st.session_state.shell_running:
            append_model_trace(run, "Starting execution from completed background planning result.")
            start_plan_execution(run, orchestrator)
        return

    if planner_state in {"planning_failed", "planning_timed_out"}:
        error_text = str(planner_status.get("error", "")).strip() or "Planning failed."
        mark_planning_failure(run, status=planner_state, error=error_text)
        persist_run_state(run)
        write_terminal_artifacts_if_needed(run)
        return

    if planning_is_orphaned(run_files, run_uid=run_uid):
        error_text = (
            "Planning was interrupted before completion. "
            "Retry the run to restart planning."
        )
        append_model_trace(run, "Detected orphaned planning state without a live planner job.")
        mark_planning_failure(run, status="planning_failed", error=error_text)
        persist_run_state(run)
        write_terminal_artifacts_if_needed(run)
        return

    if planner_snapshot.get("thread_alive", False):
        run["planner_status"] = str(planner_snapshot.get("status", "planning")).strip() or "planning"


def export_execution_scripts(run: dict, plan_json: dict, script_set_name: str) -> None:
    run_dir = str(run.get("run_dir", "")).strip()
    if not run_dir:
        return
    selected_dir_text = str(run.get("selected_dir", "") or st.session_state.selected_dir).strip()
    try:
        exported = export_plan_run_scripts(
            plan_json=plan_json,
            run_dir=Path(run_dir),
            selected_dir=Path(selected_dir_text),
            script_set_name=script_set_name,
        )
    except Exception as exc:
        run.setdefault("logs", []).append(
            f"[scripts] Failed to export script set `{script_set_name}`: {exc}\n"
        )
        return

    item = {
        "script_set": script_set_name,
        "created_at": datetime.now().isoformat(),
        **exported,
    }
    run.setdefault("script_exports", []).append(item)
    run["last_script_export"] = item
    run.setdefault("logs", []).append(
        f"[scripts] Exported run scripts `{script_set_name}` to {item.get('script_set_dir', '')}\n"
    )
    run_files = run.get("run_files", {})
    if run_files:
        append_event(
            Path(run_files["events"]),
            run_id=run.get("run_uid", ""),
            step_id=None,
            agent="PlannerAgent",
            event_type="SCRIPTS_EXPORTED",
            severity="info",
            payload={
                "script_set": script_set_name,
                "script_set_dir": item.get("script_set_dir", ""),
                "run_all": item.get("run_all", ""),
            },
        )


def verify_run_outputs(run: dict) -> tuple[bool, str]:
    if not plan_contains_splicing_steps(run.get("plan") or {}):
        return True, ""
    candidates: list[Path] = []
    selected_dir = Path(str(run.get("selected_dir", "") or "")).expanduser()
    for step in (run.get("plan") or {}).get("plan", []):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "rmats_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        output_dir = str(args.get("output_dir", "")).strip()
        if not output_dir:
            continue
        path = Path(output_dir).expanduser()
        if selected_dir and not path.is_absolute():
            path = selected_dir / path
        candidates.append(path)
    candidates.append(WORKSPACE_ROOT / "outputs" / "splicing_auto" / "rmats")
    seen: set[str] = set()
    for rmats_out in candidates:
        rendered = str(rmats_out)
        if rendered in seen:
            continue
        seen.add(rendered)
        if (rmats_out / "SE.MATS.JCEC.txt").exists() or (rmats_out / "SE.MATS.JC.txt").exists():
            return True, ""
    return False, "rMATS output tables were not produced (missing SE.MATS.JC/JCEC)."


def detect_failure_class(run: dict) -> str:
    return classify_failure(run)


def maybe_record_promotion(run: dict, failure_class: str) -> None:
    count = 0
    for r in st.session_state.get("plan_runs", []):
        history = r.get("auto_repair_history", [])
        for item in history:
            if item.get("failure_class") == failure_class:
                count += 1
    if count < 2:
        return
    promotions = list(run.get("auto_repair_promotions", []))
    note = (
        f"Repeated failure class `{failure_class}` detected across runs. "
        "Promote this repair into `bio_harness/workflows/templates.py` and "
        "`bio_harness/pipeline_scripts/SKILL.md`."
    )
    record = {"failure_class": failure_class, "note": note}
    if all(
        str(item.get("note", "")).strip() != note
        for item in promotions
        if isinstance(item, dict)
    ) and note not in promotions:
        promotions.append(record)
        run["auto_repair_promotions"] = promotions
        run.setdefault("logs", []).append(f"[auto-repair] promotion: {note}\n")


def _clean_stale_tmp_cache_paths(run: dict) -> dict:
    candidates: set[Path] = set()
    plan = run.get("plan") or {}
    for step in (plan.get("plan", []) if isinstance(plan, dict) else []):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")) != "bash_run":
            continue
        cmd = str((step.get("arguments") or {}).get("command", "")).strip()
        if not cmd:
            continue
        for token in re.findall(r"(/[^\s\"']+)", cmd):
            p = Path(token)
            name_l = p.name.lower()
            if any(k in name_l for k in ("rmats_tmp", "__startmp")):
                candidates.add(p)
        for rel in re.findall(r"(outputs/[^\s\"']+)", cmd):
            p = Path(st.session_state.selected_dir) / rel
            name_l = p.name.lower()
            if any(k in name_l for k in ("rmats_tmp", "__startmp")):
                candidates.add(p)
    candidates.add(Path(st.session_state.selected_dir) / "outputs" / "splicing_auto" / "rmats_tmp")

    removed: list[str] = []
    for path in sorted(candidates):
        try:
            resolved = path.expanduser().resolve()
        except Exception:
            continue
        try:
            resolved.relative_to(WORKSPACE_ROOT)
        except Exception:
            continue
        if resolved.exists() and resolved.is_dir():
            shutil.rmtree(resolved)
            removed.append(str(resolved))
    return {
        "changed": bool(removed),
        "removed_paths": removed,
        "diff_summary": {"removed_path_count": len(removed)},
    }


def _canonicalize_run_plan(run: dict, reason: str) -> tuple[bool, dict]:
    before_plan = run.get("plan") or {}
    data_root = st.session_state.get("chat_data_root", st.session_state.selected_dir)
    run_files = run.get("run_files") or {}
    run_output_root = str(
        Path(str(run_files.get("state", "") or "")).expanduser().resolve(strict=False).parent
        if str(run_files.get("state", "") or "").strip()
        else (Path(str(run.get("run_dir", "") or "")).expanduser().resolve(strict=False) if str(run.get("run_dir", "") or "").strip() else Path(str(run.get("selected_dir", "") or st.session_state.selected_dir)).expanduser().resolve(strict=False))
    )
    canonical_plan, meta = canonicalize_execution_plan(
        before_plan,
        data_root=str(data_root),
        selected_dir=run_output_root,
    )
    if not meta.get("changed", False):
        return False, {
            "why": reason,
            "changed": False,
            "diff_summary": meta.get("diff_summary", {}),
        }
    run["plan"] = canonical_plan
    run["step_statuses"] = ["pending"] * len((canonical_plan.get("plan", []) if isinstance(canonical_plan, dict) else []))
    run["next_step_idx"] = 0
    return True, {
        "why": reason,
        "changed": True,
        "diff_summary": meta.get("diff_summary", {}),
        "canonicalization": meta,
    }


def apply_repair_action(run: dict, failure_class: str) -> tuple[bool, str, dict]:
    details: dict = {"why": f"repair_map:{failure_class}", "diff_summary": {}}
    if failure_class == "tool_missing":
        tools = list(run.get("missing_tools_detected", []))
        if tools and st.session_state.get("auto_remediate_missing_tools", True):
            trigger_tool_auto_remediation(run, tools)
            details.update({"tools": tools, "retry_deferred": True})
            return True, "tool_auto_remediation", details
        return False, "tool_missing_no_remediation", details
    if failure_class == "missing_reference":
        missing_refs = list(run.get("missing_reference_detected", []))
        repair_res = _repair_missing_references_in_plan(
            run.get("plan") or {},
            missing_refs,
            str(run.get("user_request", "")),
        )
        if repair_res.get("changed", False):
            details.update(
                {
                    "replacements": repair_res.get("replacements", []),
                    "diff_summary": {"replacement_count": len(repair_res.get("replacements", []))},
                }
            )
            return True, "replace_missing_references", details
        return False, "missing_reference_unrepaired", details
    if failure_class == "stale_tmp_cache":
        cleaned = _clean_stale_tmp_cache_paths(run)
        if cleaned.get("changed", False):
            details.update(cleaned)
            return True, "clear_stale_tmp_cache", details
        return False, "no_stale_tmp_cache_found", details
    if failure_class in {"validation_block", "policy_block", "format_input_error"}:
        ok, canon_details = _canonicalize_run_plan(run, reason=failure_class)
        if ok:
            details.update(canon_details)
            return True, "canonicalize_plan", details
    if failure_class in {"contract_mismatch", "runtime_step_failure", "unknown_failure", "validation_block", "policy_block", "format_input_error"}:
        reason = run.get("error", "") or failure_class
        ok, action, repl_details = maybe_replan_for_failure(run, failure_class, str(reason))
        details.update(repl_details)
        return ok, action, details
    return False, "no_action", details


def maybe_trigger_auto_repair(run: dict) -> bool:
    failure_class = detect_failure_class(run)
    run["auto_repair_last_class"] = failure_class
    attempts = dict(run.get("auto_repair_attempts", {}))
    current_attempts = int(attempts.get(failure_class, 0))
    if not can_attempt_repair(attempts, failure_class, limits=MAX_REPAIR_ATTEMPTS_BY_CLASS):
        maybe_record_promotion(run, failure_class)
        return False

    can_repair, action, details = apply_repair_action(run, failure_class)
    if not can_repair:
        maybe_record_promotion(run, failure_class)
        return False

    attempts[failure_class] = current_attempts + 1
    run["auto_repair_attempts"] = attempts
    retry_deferred = bool(details.get("retry_deferred", False))
    event = build_repair_audit_entry(
        run_id=run.get("run_uid", ""),
        failure_class=failure_class,
        attempt=attempts[failure_class],
        action=action,
        details=details,
    )
    history = list(run.get("auto_repair_history", []))
    history.append(event)
    run["auto_repair_history"] = history
    run.setdefault("logs", []).append(
        f"[auto-repair] class={failure_class} attempt={attempts[failure_class]} action={action}\n"
    )
    if run.get("step_statuses") and not retry_deferred:
        run["step_statuses"] = ["pending"] * len(run["step_statuses"])
        run["next_step_idx"] = 0
    run["status"] = "remediating_tools" if retry_deferred else "planned"
    run["async_status"] = "recovering"
    run["error"] = ""
    run["recovery_verification_required"] = not retry_deferred
    st.session_state.latest_run_badge = "Recovering"
    if not retry_deferred:
        st.session_state.pending_plan_retry_run_id = run["id"]
    run_files = run.get("run_files", {})
    if run_files:
        append_event(
            Path(run_files["events"]),
            run_id=run.get("run_uid", ""),
            step_id=None,
            agent="RecoveryAgent",
            event_type="REPAIR_APPLIED",
            severity="warning",
            payload=event,
        )
    return True


def describe_step_for_message(step: dict) -> str:
    tool_name = str(step.get("tool_name", "step"))
    args = step.get("arguments", {}) if isinstance(step.get("arguments"), dict) else {}
    if tool_name == "bash_run":
        cmd = str(args.get("command", "")).strip()
        return summarize_command_for_ui(cmd)
    return tool_name


def post_step_progress_message(run: dict, step_id: int) -> None:
    posted: set[int] = set()
    for raw in run.get("step_updates_posted", []):
        try:
            posted.add(int(raw))
        except Exception:
            continue
    if step_id in posted:
        return

    steps = (run.get("plan") or {}).get("plan", [])
    total = len(steps)
    if total <= 0 or step_id <= 0:
        return

    current_label = "step"
    next_label = ""
    if step_id - 1 < len(steps):
        current_label = describe_step_for_message(steps[step_id - 1])
    if step_id < len(steps):
        next_label = describe_step_for_message(steps[step_id])

    exec_mode = st.session_state.get("plan_execution_mode")
    if step_id < total and exec_mode == "single":
        msg = (
            f"Execution update: Step {step_id}/{total} completed.\n"
            f"- Completed: {current_label}\n"
            f"- Next ready step: Step {step_id + 1}/{total} ({next_label})\n"
            "- Paused because this was a single-step run. Would you like me to proceed with the next step?"
        )
    elif step_id < total:
        msg = (
            f"Execution update: Step {step_id}/{total} completed.\n"
            f"- Completed: {current_label}\n"
            f"- Next: Step {step_id + 1}/{total} ({next_label})\n"
            "- Continuing automatically."
        )
    else:
        msg = (
            f"Execution update: Step {step_id}/{total} completed.\n"
            f"- Completed: {current_label}\n"
            "- All planned steps are complete."
        )

    try:
        session_id = st.session_state.get("orchestrator_session_id", "default")
        orchestrator = get_orchestrator(model_name, resolved_host, llm_backend)
        session = orchestrator.get_or_create_session(session_id)
        session["messages"].append({"role": "assistant", "content": msg})
    except Exception:
        return

    posted.add(int(step_id))
    run["step_updates_posted"] = sorted(posted)


def should_answer_with_run_status(text: str) -> bool:
    t = (text or "").lower()
    keys = ("what happened", "status", "why did", "failed", "failure", "error", "what went wrong")
    return any(k in t for k in keys)


def render_sidebar_runtime(run: dict) -> None:
    init_process_tracker(run)
    st.sidebar.subheader("Run")
    st.sidebar.write(f"Run status: {run.get('status', 'unknown')}")
    st.sidebar.write(f"Async: {run.get('async_status', 'idle')}")
    hb_ts = float(st.session_state.get("executor_last_heartbeat_ts", 0.0) or 0.0)
    hb_note = st.session_state.get("executor_last_heartbeat_note", "")
    if hb_ts > 0:
        elapsed = int(max(0.0, time.time() - hb_ts))
        countdown = max(0, HEARTBEAT_INTERVAL_SECONDS - elapsed)
        st.sidebar.write(f"Executor heartbeat: {elapsed}s ago")
        st.sidebar.write(f"Next heartbeat check in ~{countdown}s")
        pulse = min(1.0, max(0.0, float(elapsed) / float(max(HEARTBEAT_INTERVAL_SECONDS, 1))))
        st.sidebar.progress(pulse)
        st.sidebar.caption("Heartbeat Pulse (15s)")
        filled = int(round(pulse * 5))
        hearts = ("["
            + ("#" * filled)
            + ("." * (5 - filled))
            + "]")
        st.sidebar.caption(f"Pulse: {hearts}")
        if hb_note:
            st.sidebar.caption(hb_note)
    else:
        st.sidebar.write("Executor heartbeat: idle")
        st.sidebar.write(f"Next heartbeat check in ~{HEARTBEAT_INTERVAL_SECONDS}s")
        st.sidebar.progress(0.0)
        st.sidebar.caption("Heartbeat Pulse (15s)")
        st.sidebar.caption("Pulse: [.....]")
    current = None
    for key in run.get("process_order", []):
        p = run.get("process_tracker", {}).get(key, {})
        if p.get("status") == "running":
            current = p
            break
    if current:
        st.sidebar.write(f"Current step: {current.get('step_id')}")
        st.sidebar.caption(current.get("title", current.get("tool_name", "")))
        if current.get("status_text"):
            st.sidebar.caption(current.get("status_text"))


def maybe_copy_named_references(user_text: str) -> str:
    t = (user_text or "").lower()
    if "move" not in t and "copy" not in t:
        return ""
    if "reference" not in t:
        return ""
    needs_gtf = ("mouse_gtf" in t) or ("gtf" in t)
    needs_fasta = ("mouse_fasta" in t) or ("fasta" in t) or ("mouse_fa" in t)
    if not (needs_gtf or needs_fasta):
        return ""

    refs_dir = WORKSPACE_ROOT / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    missing: list[str] = []

    def _copy_alias(alias_name: str) -> None:
        src = READONLY_LINKS_ROOT / alias_name
        if not (src.exists() or src.is_symlink()):
            missing.append(alias_name)
            return
        try:
            resolved = src.resolve(strict=True)
        except Exception:
            missing.append(alias_name)
            return
        dest = refs_dir / resolved.name
        if resolved.is_file():
            shutil.copy2(str(resolved), str(dest))
            copied.append(f"{resolved} -> {dest}")
        else:
            missing.append(alias_name)

    if needs_gtf:
        _copy_alias("mouse_gtf")
    if needs_fasta:
        _copy_alias("mouse_fasta")

    if not copied and not missing:
        return ""
    lines = ["Reference copy action completed."]
    if copied:
        lines.append("- Copied:")
        lines.extend([f"  - {c}" for c in copied])
    if missing:
        lines.append("- Missing alias link(s) under `workspace/inputs_readonly/`: " + ", ".join(sorted(set(missing))))
    lines.append(f"- Reference directory: `{refs_dir}`")
    return "\n".join(lines)


def reference_alias_links_available() -> tuple[bool, bool]:
    gtf_alias = READONLY_LINKS_ROOT / "mouse_gtf"
    fasta_alias = READONLY_LINKS_ROOT / "mouse_fasta"
    has_gtf = bool(gtf_alias.exists() or gtf_alias.is_symlink())
    has_fasta = bool(fasta_alias.exists() or fasta_alias.is_symlink())
    return has_gtf, has_fasta


def build_post_copy_followup(run: dict) -> str:
    data_root = st.session_state.get("chat_data_root", st.session_state.selected_dir)
    if detect_splicing_intent(str(run.get("user_request", ""))):
        return (
            "I verified the copy action and references are now available under `workspace/references/`.\n"
            f"Next ready step: rerun splicing execution using Data Root `{data_root}`.\n"
            "Would you like me to proceed now?"
        )
    return (
        "I verified the copy action completed successfully.\n"
        "Would you like me to proceed to the next execution step now?"
    )


def process_logs(container=None) -> None:
    """Drain terminal output into session state and optionally render it."""
    output, running = drain_shell_log_queue(
        st.session_state.shell_log_queue,
        list(st.session_state.get("shell_output", [])),
    )
    st.session_state.shell_output = output
    if not running:
        st.session_state.shell_running = False
        st.session_state.shell_thread = None
    if container is not None:
        container.code(shell_output_text(st.session_state.get("shell_output", [])), language="bash")


def new_plan_run(initial_request: str = "") -> dict:
    run_id = st.session_state.next_plan_id
    run = {
        "id": run_id,
        "user_request": initial_request,
        "plan": None,
        "plan_kind": "executable",
        "adjustments": [],
        "status": "draft",
        "logs": [],
        "error": "",
        "next_step_idx": 0,
        "step_statuses": [],
        "eta_notes": [],
        "conversation": [],
        "context_snapshots": [],
        "model_traces": [],
        "missing_tools_detected": [],
        "remediation_attempted_tools": [],
        "no_fastq_found": False,
        "missing_reference_detected": [],
        "missing_sample_groups": [],
        "empty_bams_detected": [],
        "policy_block_detected": False,
        "validation_block_detected": False,
        "stale_tmp_cache_detected": False,
        "format_input_error_detected": False,
        "recovery_verification_required": False,
        "execution_options": {},
        "last_error_feedback": "",
        "run_uid": "",
        "run_dir": "",
        "run_files": {},
        "script_exports": [],
        "last_script_export": {},
        "step_updates_posted": [],
        "rmats_failed_detected": False,
        "auto_repair_attempts": {},
        "auto_repair_history": [],
        "auto_repair_promotions": [],
        "auto_repair_last_class": "",
        "plan_contract": {},
        "contract_validation": {},
        "live_tail": deque(maxlen=4000),
        "stdout_tail": deque(maxlen=4000),
        "stderr_tail": deque(maxlen=4000),
        "events_tail": deque(maxlen=100),
        "auto_recovered_incomplete_plan": False,
        "last_heartbeat_event_ts": 0.0,
        "process_tracker": {},
        "process_order": [],
        "async_status": "idle",
        "last_process_update_ts": 0.0,
        "last_executor_event_ts": 0.0,
        "last_queue_activity_ts": 0.0,
        "stall_event_emitted": False,
        "last_reconcile_at": 0.0,
        "last_chat_result_signature": "",
        "chat_session_id": build_chat_session_id(run_id),
    }
    st.session_state.next_plan_id += 1
    st.session_state.plan_runs.append(run)
    st.session_state.active_plan_id = run["id"]
    st.session_state.orchestrator_session_id = run["chat_session_id"]
    return run


def append_model_trace(run: dict, message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    run.setdefault("model_traces", []).append(f"[{ts}] {message}")


def get_plan_run(run_id: Optional[int]) -> Optional[dict]:
    if run_id is None:
        return None
    for run in st.session_state.plan_runs:
        if run["id"] == run_id:
            return run
    return None


def sync_recent_persisted_runs() -> None:
    """Merge recent persisted runs into the current Streamlit session."""
    merged_runs, suggested_active_id = merge_recent_persisted_runs(
        list(st.session_state.get("plan_runs", [])),
        workspace_root=WORKSPACE_ROOT,
        limit=12,
    )
    st.session_state.plan_runs = merged_runs
    st.session_state.next_plan_id = max((int(run.get("id", 0) or 0) for run in merged_runs), default=0) + 1
    active_run = get_plan_run(st.session_state.get("active_plan_id"))
    if active_run is None and suggested_active_id is not None:
        st.session_state.active_plan_id = suggested_active_id
        selected_run = get_plan_run(suggested_active_id)
        if selected_run is not None:
            st.session_state.orchestrator_session_id = session_id_for_run(selected_run)


def get_active_plan_run() -> dict:
    run = get_plan_run(st.session_state.active_plan_id)
    if run is None:
        run = new_plan_run()
    return run


def process_plan_logs() -> None:
    active_exec_id = st.session_state.plan_execution_run_id
    run = get_plan_run(active_exec_id)
    if run is None:
        # Keep UI/event reconciliation alive after reruns/session churn.
        run = get_plan_run(st.session_state.active_plan_id)
    if run is not None and st.session_state.plan_running:
        plan_thread = st.session_state.get("plan_thread")
        run_files = run.get("run_files", {})
        runtime_live = bool(run_files) and executor_runtime_is_live(run_files)
        if (
            plan_thread is not None
            and (not plan_thread.is_alive())
            and st.session_state.plan_log_queue.empty()
        ):
            if runtime_live:
                st.session_state.plan_thread = None
                run["async_status"] = "running"
            else:
                st.session_state.plan_running = False
                st.session_state.plan_thread = None
                st.session_state.plan_execution_run_id = None
                st.session_state.plan_execution_mode = None
                st.session_state.plan_execution_step_idx = None
                if run.get("status") in {"running", "planned", "draft"}:
                    run["status"] = "failed"
                    run["async_status"] = "failed"
                    run["error"] = "Execution thread exited unexpectedly before completion signal."
                    if run_files:
                        finish_executor_runtime(
                            run_files,
                            run_id=str(run.get("run_uid", "") or ""),
                            status="failed",
                            error=run["error"],
                        )
                    mark_running_items_failed(
                        run,
                        process_status_text="Executor thread ended unexpectedly",
                    )
                    if run_files:
                        append_event(
                            Path(run_files["events"]),
                            run_id=run.get("run_uid", ""),
                            step_id=None,
                            agent="Orchestrator",
                            event_type="RECOVERY_RESULT",
                            severity="error",
                            payload={"status": "failed", "reason": "thread_ended_without_sentinel"},
                        )
                        write_exit(
                            Path(run_files["exit"]),
                            {
                                "run_id": run.get("run_uid", ""),
                                "status": "failed",
                                "error": run.get("error", ""),
                                "finished_at": datetime.now().isoformat(),
                            },
                        )
                    persist_run_state(run)
                write_terminal_artifacts_if_needed(run)
    if run is not None and st.session_state.plan_running and st.session_state.plan_log_queue.empty():
        run_files = run.get("run_files", {})
        now_ts = time.time()
        last_ts = float(run.get("last_heartbeat_event_ts", 0.0) or 0.0)
        if run_files and (now_ts - last_ts) >= HEARTBEAT_INTERVAL_SECONDS:
            append_event(
                Path(run_files["events"]),
                run_id=run.get("run_uid", ""),
                step_id=None,
                agent="Orchestrator",
                event_type="UI_HEARTBEAT",
                severity="info",
                payload={"status": "plan_running", "note": "ui-level heartbeat"},
            )
            heartbeat_executor_runtime(
                run_files,
                run_id=str(run.get("run_uid", "") or ""),
                event_type="UI_HEARTBEAT",
            )
            run["last_heartbeat_event_ts"] = now_ts
            persist_run_state(run)
    if run is not None and st.session_state.plan_running:
        # Keep countdown fresh even when no new output arrives.
        if st.session_state.get("executor_last_heartbeat_ts", 0.0) == 0.0:
            st.session_state.executor_last_heartbeat_ts = time.time()
            st.session_state.executor_last_heartbeat_note = "Execution started"
            st.session_state.executor_last_heartbeat_event_epoch = st.session_state.executor_last_heartbeat_ts
        plan_thread = st.session_state.get("plan_thread")
        queue_idle = st.session_state.plan_log_queue.empty()
        last_progress_ts = float(run.get("last_executor_event_ts", 0.0) or 0.0)
        if last_progress_ts <= 0:
            last_progress_ts = float(run.get("last_queue_activity_ts", 0.0) or 0.0)
        has_live_process = run_has_live_executor_process(run)
        should_fail_stalled, stall_age = should_fail_ui_run_for_stall(
            plan_running=bool(st.session_state.plan_running),
            thread_alive=bool(plan_thread is not None and plan_thread.is_alive()),
            queue_empty=bool(queue_idle),
            last_progress_ts=float(last_progress_ts),
            now_ts=time.time(),
            timeout_seconds=STALL_TIMEOUT_SECONDS,
            stall_event_emitted=bool(run.get("stall_event_emitted", False)),
            has_live_executor_process=bool(has_live_process),
            live_process_grace_seconds=LIVE_PROCESS_STALL_GRACE_SECONDS,
        )
        if should_fail_stalled:
                run["status"] = "failed"
                run["async_status"] = "failed"
                run["error"] = (
                    f"Execution stalled for {stall_age}s without executor progress. "
                    "Worker may be hung."
                )
                run["stall_event_emitted"] = True
                if run_files:
                    finish_executor_runtime(
                        run_files,
                        run_id=str(run.get("run_uid", "") or ""),
                        status="failed",
                        error=run["error"],
                    )
                mark_running_items_failed(
                    run,
                    process_status_text="Stalled: no progress heartbeat",
                )
                run_files = run.get("run_files", {})
                if run_files:
                    append_event(
                        Path(run_files["events"]),
                        run_id=run.get("run_uid", ""),
                        step_id=None,
                        agent="Orchestrator",
                        event_type="STALL_DETECTED",
                        severity="error",
                        payload={"stall_seconds": int(stall_age), "status": "failed"},
                    )
                    write_exit(
                        Path(run_files["exit"]),
                        {
                            "run_id": run.get("run_uid", ""),
                            "status": "failed",
                            "error": run.get("error", ""),
                            "finished_at": datetime.now().isoformat(),
                        },
                    )
                st.session_state.plan_running = False
                st.session_state.plan_thread = None
                st.session_state.plan_execution_run_id = None
                st.session_state.plan_execution_mode = None
                st.session_state.plan_execution_step_idx = None
                persist_run_state(run)
    if run is not None:
        now_ts = time.time()
        run.setdefault("last_reconcile_at", 0.0)
        should_reconcile = (
            (not st.session_state.plan_running)
            or (now_ts - float(run.get("last_reconcile_at", 0.0) or 0.0) >= 10.0)
        )
        # Reconcile from persisted events at a bounded interval to keep UI responsive.
        if should_reconcile:
            try:
                reconcile_run_from_events(run)
                run["last_reconcile_at"] = now_ts
            except Exception as exc:
                log_ui_exception(run, "reconcile_run_from_events", exc)
        if run.get("status") == "running":
            hb_age = time.time() - float(st.session_state.get("executor_last_heartbeat_ts", 0.0) or 0.0)
            if hb_age >= 5:
                last_stderr = "".join(_tail_items(run.get("stderr_tail", []), 5)).strip().splitlines()
                last_stdout = "".join(_tail_items(run.get("stdout_tail", []), 5)).strip().splitlines()
                tail_line = (last_stderr[-1] if last_stderr else (last_stdout[-1] if last_stdout else "")).strip()
                if tail_line:
                    quick_note = maybe_progress_update("", tail_line) or tail_line[:180]
                    st.session_state.executor_last_heartbeat_note = quick_note
        persist_run_state(run)
        write_terminal_artifacts_if_needed(run)
    processed_lines = 0
    while not st.session_state.plan_log_queue.empty():
        if processed_lines >= MAX_QUEUE_LINES_PER_TICK:
            break
        line = st.session_state.plan_log_queue.get()
        processed_lines += 1
        if line is None:
            failed = run is not None and run.get("status") == "failed"
            exec_mode = st.session_state.plan_execution_mode
            step_idx = st.session_state.plan_execution_step_idx
            st.session_state.plan_running = False
            st.session_state.plan_thread = None
            st.session_state.plan_execution_run_id = None
            st.session_state.plan_execution_mode = None
            st.session_state.plan_execution_step_idx = None

            if run is not None:
                run_files = run.get("run_files", {})
                missing_tools = run.get("missing_tools_detected", [])
                no_fastq_found = bool(run.get("no_fastq_found", False))
                missing_refs = run.get("missing_reference_detected", [])
                missing_groups = run.get("missing_sample_groups", [])
                empty_bams = run.get("empty_bams_detected", [])
                if exec_mode == "single" and step_idx is not None and 0 <= step_idx < len(run.get("step_statuses", [])):
                    run["step_statuses"][step_idx] = "failed" if failed else "completed"
                    if not failed:
                        run["next_step_idx"] = min(step_idx + 1, len(run.get("step_statuses", [])))
                elif exec_mode == "full" and not failed:
                    run["step_statuses"] = ["completed"] * len(run.get("step_statuses", []))
                    run["next_step_idx"] = len(run.get("step_statuses", []))
                # Ensure run status does not remain stale `running` once the executor sentinel arrives.
                if failed:
                    run["status"] = "failed"
                    run["async_status"] = "failed"
                else:
                    if exec_mode == "full":
                        run["status"] = "completed"
                        run["async_status"] = "completed"
                    else:
                        run["status"] = "planned"
                        run["async_status"] = "idle"
                if missing_refs:
                    run["status"] = "failed"
                    run["error"] = (
                        "Missing reference files detected: " + ", ".join(missing_refs)
                    )
                if missing_groups:
                    run["status"] = "failed"
                    run["error"] = (
                        "Could not find required FASTQ sample groups: " + ", ".join(missing_groups)
                    )
                if empty_bams:
                    run["status"] = "failed"
                    run["error"] = (
                        "STAR produced empty BAM output(s): " + ", ".join(empty_bams[:6])
                    )
                if no_fastq_found:
                    run["status"] = "failed"
                    run["error"] = (
                        "Execution found no FASTQ files in the selected Data Root. "
                        "Data Root likely points to the wrong directory."
                    )
                    st.session_state.latest_run_badge = "Recovering"
                    run["logs"].append(
                        "[recovery] No FASTQ found. Scanning workspace/inputs_readonly recursively for FASTQ files.\n"
                    )
                    if run_files:
                        append_event(
                            Path(run_files["events"]),
                            run_id=run.get("run_uid", ""),
                            step_id=None,
                            agent="RecoveryAgent",
                            event_type="RECOVERY_ATTEMPTED",
                            severity="warning",
                            payload={"reason": "INPUT_SCOPE_EMPTY"},
                        )
                    auto_root = WORKSPACE_ROOT / "inputs_readonly"
                    candidates = discover_fastq_files(str(auto_root), True, "", 2000)
                    parent_counts: dict[str, int] = {}
                    for fp in candidates:
                        parent = str(Path(fp).parent)
                        parent_counts[parent] = parent_counts.get(parent, 0) + 1
                    if parent_counts:
                        best_parent = max(parent_counts.items(), key=lambda kv: kv[1])[0]
                        st.session_state["chat_data_root"] = best_parent
                        run["logs"].append(
                            f"[recovery] Auto-selected new Data Root: {best_parent} ({parent_counts[best_parent]} FASTQ files)\n"
                        )
                        if run_files:
                            append_event(
                                Path(run_files["events"]),
                                run_id=run.get("run_uid", ""),
                                step_id=None,
                                agent="RecoveryAgent",
                                event_type="RECOVERY_RESULT",
                                severity="info",
                                payload={"status": "success", "resolved_root": best_parent},
                            )
                        st.session_state.pending_plan_retry_run_id = run["id"]
                        session_id = st.session_state.get("orchestrator_session_id", "default")
                        # Provide explicit failure + recovery in chat, not repeated plan text.
                        try:
                            orchestrator = get_orchestrator(model_name, resolved_host, llm_backend)
                            session = orchestrator.get_or_create_session(session_id)
                            session["messages"].append(
                                {
                                    "role": "assistant",
                                    "content": (
                                        "Execution failed on input scope.\n"
                                        f"- Failure: no FASTQ found in selected root.\n"
                                        f"- Recovery: switched Data Root to `{best_parent}` "
                                        f"({parent_counts[best_parent]} FASTQ files) and retrying automatically."
                                    ),
                                }
                            )
                        except Exception:
                            pass
                    else:
                        run["logs"].append(
                            "[recovery] Could not find FASTQ files under workspace/inputs_readonly.\n"
                        )
                        st.session_state.latest_run_badge = "Blocked"
                        if run_files:
                            append_event(
                                Path(run_files["events"]),
                                run_id=run.get("run_uid", ""),
                                step_id=None,
                                agent="RecoveryAgent",
                                event_type="RECOVERY_RESULT",
                                severity="error",
                                payload={"status": "failed", "reason": "no_fastq_discovered"},
                            )
                if missing_tools and run.get("status") not in {"failed", "completed"}:
                    run["status"] = "blocked_missing_tools"
                    run["error"] = (
                        "Missing tools detected during execution. "
                        "Auto-remediation will run and then retry automatically."
                    )
                    st.session_state.latest_run_badge = "Blocked"
                    if st.session_state.get("auto_remediate_missing_tools", True):
                        trigger_tool_auto_remediation(run, missing_tools)

                contract = run.get("plan_contract", {}) if isinstance(run.get("plan_contract", {}), dict) else {}
                if is_empty_contract(contract):
                    contract = infer_contract_with_fallback(
                        primary_text=str(run.get("user_request", "")),
                        fallback_texts=[],
                        existing=contract,
                    )
                    run["plan_contract"] = contract
                if run.get("status") == "completed" and contract:
                    coverage = assess_plan_contract(run.get("plan") or {}, contract)
                    run["contract_validation"] = coverage
                    if not coverage.get("passed", False):
                        run["status"] = "failed"
                        run["error"] = (
                            "Completed execution did not satisfy request contract: "
                            f"missing capabilities={coverage.get('missing_capabilities', [])}, "
                            f"missing tool hints={coverage.get('missing_tool_hints', [])}"
                        )

                if run.get("status") == "completed":
                    deliverable_meta = materialize_ui_run_deliverables(run)
                    for exported in deliverable_meta.get("exported", []):
                        append_run_log(
                            run,
                            "[deliverable] materialized "
                            f"{exported.get('analysis_type', 'output')} -> {exported.get('output_path', '')}\n",
                        )
                        if run_files:
                            append_event(
                                Path(run_files["events"]),
                                run_id=run.get("run_uid", ""),
                                step_id=None,
                                agent="DeliverableAgent",
                                event_type="DELIVERABLE_MATERIALIZED",
                                severity="info",
                                payload=exported,
                            )
                    failures = deliverable_meta.get("failures", [])
                    if failures:
                        first_failure = failures[0]
                        run["status"] = "failed"
                        run["error"] = (
                            "Failed to materialize final deliverable: "
                            f"{first_failure.get('why', 'unknown_reason')}"
                        )
                        append_run_log(
                            run,
                            "[deliverable] failed to materialize output: "
                            f"{first_failure}\n",
                        )
                        if run_files:
                            append_event(
                                Path(run_files["events"]),
                                run_id=run.get("run_uid", ""),
                                step_id=None,
                                agent="DeliverableAgent",
                                event_type="DELIVERABLE_MATERIALIZATION_FAILED",
                                severity="error",
                                payload=first_failure,
                            )
                    captured_outputs = capture_ui_run_final_outputs(run)
                    for exported in captured_outputs.get("exported", []):
                        append_run_log(
                            run,
                            "[deliverable] captured explicit output "
                            f"{exported.get('source_path', '')} -> {exported.get('output_path', '')}\n",
                        )
                        if run_files:
                            append_event(
                                Path(run_files["events"]),
                                run_id=run.get("run_uid", ""),
                                step_id=None,
                                agent="DeliverableAgent",
                                event_type="DELIVERABLE_CAPTURED",
                                severity="info",
                                payload=exported,
                            )

                if run.get("status") == "completed":
                    verification_errors: list[str] = []
                    if run.get("recovery_verification_required", False):
                        if run.get("step_statuses") and any(s != "completed" for s in run.get("step_statuses", [])):
                            verification_errors.append("retry verification failed: not all steps are completed")
                    ok_outputs, verify_msg = verify_run_outputs(run)
                    if not ok_outputs:
                        verification_errors.append(verify_msg)
                    if verification_errors:
                        run["status"] = "failed"
                        run["error"] = verification_errors[0]
                        append_run_log(run, f"[verify] failed: {'; '.join(verification_errors)}\n")
                        if run_files:
                            append_event(
                                Path(run_files["events"]),
                                run_id=run.get("run_uid", ""),
                                step_id=None,
                                agent="VerifierAgent",
                                event_type="VERIFICATION_GATE_FAILED",
                                severity="error",
                                payload={"messages": verification_errors},
                            )
                    else:
                        if run.get("recovery_verification_required", False) and run_files:
                            append_event(
                                Path(run_files["events"]),
                                run_id=run.get("run_uid", ""),
                                step_id=None,
                                agent="VerifierAgent",
                                event_type="VERIFICATION_GATE_PASSED",
                                severity="info",
                                payload={"mode": "post_retry"},
                            )
                        run["recovery_verification_required"] = False
                if run_files:
                    finish_executor_runtime(
                        run_files,
                        run_id=str(run.get("run_uid", "") or ""),
                        status="failed" if run.get("status") == "failed" else "completed",
                        error=str(run.get("error", "") or ""),
                    )
                if run.get("status") == "completed":
                    st.session_state.latest_run_badge = "Completed"
                    run["async_status"] = "completed"
                elif run.get("status") == "failed":
                    repaired = maybe_trigger_auto_repair(run)
                    if repaired:
                        if run_files:
                            append_event(
                                Path(run_files["events"]),
                                run_id=run.get("run_uid", ""),
                                step_id=None,
                                agent="RecoveryAgent",
                                event_type="RECOVERY_ATTEMPTED",
                                severity="warning",
                                payload={
                                    "failure_class": run.get("auto_repair_last_class", ""),
                                    "attempts": run.get("auto_repair_attempts", {}),
                                },
                            )
                    else:
                        st.session_state.latest_run_badge = "Failed"
                        run["async_status"] = "failed"
                elif run.get("status") in {"blocked_missing_tools", "blocked_input"}:
                    run["async_status"] = "blocked"
                persist_run_state(run)
                write_terminal_artifacts_if_needed(run)
            break
        if run is not None:
            try:
                line_text = line if isinstance(line, str) else str(line)
                run_files = run.get("run_files", {})
                run["last_queue_activity_ts"] = time.time()
                append_run_log(run, line_text)
                update_process_tracker_from_log(run, line_text)
                step_done_match = re.match(r"--- Step (\d+) \(([^)]+)\) finished ---", line_text.strip())
                if step_done_match:
                    post_step_progress_message(run, int(step_done_match.group(1)))
                if run_files:
                    append_line(Path(run_files["exec"]), line_text)
                channel, body = parse_log_channel(line_text)
                if channel == "stdout":
                    append_tail(run["stdout_tail"], body)
                    if run_files:
                        append_line(Path(run_files["stdout"]), body)
                elif channel == "stderr":
                    append_tail(run["stderr_tail"], body)
                    if run_files:
                        append_line(Path(run_files["stderr"]), body)
                else:
                    append_tail(run["live_tail"], body)
                if line_text.startswith("[Step "):
                    run["last_executor_event_ts"] = run["last_queue_activity_ts"]
                prog_note = maybe_progress_update("", body)
                if prog_note:
                    now_ts = time.time()
                    st.session_state.executor_last_progress_note = prog_note
                    st.session_state.executor_last_progress_ts = now_ts
                    st.session_state.executor_last_heartbeat_note = prog_note
                    st.session_state.executor_last_heartbeat_ts = now_ts
                    st.session_state.executor_last_heartbeat_event_epoch = now_ts
                    run["last_executor_event_ts"] = now_ts
                marker_text = body if channel in {"stdout", "stderr"} else ""
                missing = extract_missing_tools_from_line(marker_text) if marker_text else []
                if missing:
                    existing = set(run.get("missing_tools_detected", []))
                    run["missing_tools_detected"] = sorted(existing.union(missing))
                    if run_files:
                        append_event(
                            Path(run_files["events"]),
                            run_id=run.get("run_uid", ""),
                            step_id=None,
                            agent="ExecutorAgent",
                            event_type="TOOL_MISSING",
                            severity="error",
                            payload={"tools": missing},
                        )
                if "__POLICY_BLOCK__" in marker_text or "denied command" in marker_text.lower():
                    run["policy_block_detected"] = True
                    run["status"] = "failed"
                    if not run.get("error"):
                        run["error"] = "Execution blocked by policy guard."
                if "__VALIDATION_BLOCK__" in marker_text:
                    run["validation_block_detected"] = True
                    run["status"] = "failed"
                    if not run.get("error"):
                        run["error"] = "Execution blocked by validation guard."
                if "__NO_FASTQ_FOUND__" in marker_text:
                    run["no_fastq_found"] = True
                    if run_files:
                        append_event(
                            Path(run_files["events"]),
                            run_id=run.get("run_uid", ""),
                            step_id=None,
                            agent="InputResolverAgent",
                            event_type="INPUT_SCOPE_EMPTY",
                            severity="warning",
                            payload={"line": line_text.strip()},
                        )
                if "__NO_CONTROL_FASTQ__" in marker_text:
                    run["missing_sample_groups"] = sorted(set(run.get("missing_sample_groups", []) + ["control"]))
                if "__NO_TREATMENT_FASTQ__" in marker_text:
                    run["missing_sample_groups"] = sorted(set(run.get("missing_sample_groups", []) + ["treatment"]))
                if (
                    "__MISSING_PAIR__" in marker_text
                    or "__READ_DECOMPRESS_FAILED__" in marker_text
                    or "__RMATS_INPUT_LIST_EMPTY__" in marker_text
                ):
                    run["format_input_error_detected"] = True
                eb = re.search(r"__EMPTY_BAM__:(.+)", marker_text)
                if eb:
                    empty_bams = list(run.get("empty_bams_detected", []))
                    empty_bams.append(eb.group(1).strip())
                    run["empty_bams_detected"] = sorted(set(empty_bams))
                    run["status"] = "failed"
                    run["error"] = "STAR alignment produced empty BAM output; check STAR logs and read command."
                    if run_files:
                        append_event(
                            Path(run_files["events"]),
                            run_id=run.get("run_uid", ""),
                            step_id=None,
                            agent="ExecutorAgent",
                            event_type="DELIVERABLE_CHECK_FAILED",
                            severity="error",
                            payload={"deliverable": "bam_non_empty", "path": eb.group(1).strip()},
                        )
                mr = re.search(r"__MISSING_REFERENCE__:(fasta|gtf)", marker_text)
                if mr:
                    run["missing_reference_detected"] = sorted(
                        set(run.get("missing_reference_detected", []) + [mr.group(1)])
                    )
                rmf = re.search(r"__RMATS_FAILED__:exit_code:(\d+)", marker_text)
                if rmf:
                    run["rmats_failed_detected"] = True
                    run["stale_tmp_cache_detected"] = True
                    run["status"] = "failed"
                    run["error"] = (
                        "rMATS failed during step 9. Likely cause: stale/incompatible prior tmp artifacts "
                        "or rMATS input-format issue."
                    )
                if "failed with exit code" in line_text.lower() or line_text.lower().startswith("error"):
                    run["status"] = "failed"
                if "blocked by validation agent" in line_text.lower():
                    run["validation_block_detected"] = True
                    run["status"] = "failed"
                if "blocked by policy" in line_text.lower():
                    run["policy_block_detected"] = True
                    run["status"] = "failed"
                elif "plan execution completed" in line_text.lower():
                    run["status"] = "completed"
                persist_run_state(run)
            except Exception as exc:
                log_ui_exception(run, "process_plan_logs.queue_line", exc)
                persist_run_state(run)


def load_readonly_manifest() -> dict:
    if not READONLY_MANIFEST.exists():
        return {"links": []}
    try:
        return json.loads(READONLY_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return {"links": []}


def save_readonly_manifest(manifest: dict) -> None:
    READONLY_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def attach_external_path(src_path_text: str, alias: str = "") -> Path:
    candidate_root = Path(src_path_text).expanduser().resolve(strict=False)
    read_roots = discover_read_roots(WORKSPACE_ROOT, READONLY_LINKS_ROOT) + [candidate_root]
    src_path, src_reason = canonical_resolve(
        src_path_text,
        read_roots=read_roots,
        write_roots=[WORKSPACE_ROOT],
        mode="read",
        readonly_root=READONLY_LINKS_ROOT,
    )
    if src_path is None:
        raise ValueError(f"Source path rejected: {src_reason}")
    if not src_path.exists():
        raise ValueError(f"Source path not found: {src_path}")
    link_name = alias.strip() if alias.strip() else src_path.name
    dest_link = (READONLY_LINKS_ROOT / link_name).resolve()

    try:
        dest_link.relative_to(READONLY_LINKS_ROOT)
    except ValueError as exc:
        raise ValueError("Link name must stay within workspace/inputs_readonly.") from exc

    if dest_link.exists() or dest_link.is_symlink():
        if dest_link.is_symlink():
            dest_link.unlink()
        else:
            raise ValueError(f"Link target already exists and is not a symlink: {dest_link}")

    dest_link.symlink_to(src_path, target_is_directory=src_path.is_dir())

    manifest = load_readonly_manifest()
    links = [x for x in manifest.get("links", []) if x.get("link_path") != str(dest_link)]
    links.append(
        {
            "link_path": str(dest_link),
            "source_path": str(src_path),
            "readonly": True,
            "type": "directory" if src_path.is_dir() else "file",
        }
    )
    manifest["links"] = links
    save_readonly_manifest(manifest)
    return dest_link


def detach_external_path(link_path_text: str) -> None:
    link_path, reason = canonical_resolve(
        link_path_text,
        read_roots=[WORKSPACE_ROOT, READONLY_LINKS_ROOT],
        write_roots=[WORKSPACE_ROOT],
        mode="read",
        readonly_root=READONLY_LINKS_ROOT,
    )
    if link_path is None:
        raise ValueError(f"Link path rejected: {reason}")
    try:
        link_path.relative_to(READONLY_LINKS_ROOT)
    except ValueError as exc:
        raise ValueError("Can only detach links under workspace/inputs_readonly.") from exc

    if link_path.is_symlink():
        link_path.unlink()

    manifest = load_readonly_manifest()
    manifest["links"] = [x for x in manifest.get("links", []) if x.get("link_path") != str(link_path)]
    save_readonly_manifest(manifest)


def start_command(command: str, cwd: str, allowed_root: Optional[str] = None) -> None:
    if st.session_state.shell_running:
        st.warning("A command is already running.")
        return

    mark_shell_command_started(st.session_state)
    while not st.session_state.shell_log_queue.empty():
        st.session_state.shell_log_queue.get_nowait()

    runner = CommandRunner()
    st.session_state.shell_thread = threading.Thread(
        target=runner.run_command,
        args=(command, st.session_state.shell_log_queue, cwd, allowed_root or str(WORKSPACE_ROOT)),
        daemon=True,
    )
    st.session_state.shell_thread.start()


def trigger_tool_auto_remediation(run: dict, tools: list[str]) -> None:
    normalized_tools = sorted({t.strip() for t in tools if t and t.strip()})
    if not normalized_tools:
        return

    attempted = set(run.get("remediation_attempted_tools", []))
    pending = [t for t in normalized_tools if t not in attempted]
    if not pending:
        run["logs"].append(
            f"[remediation] Already attempted auto-remediation for tools: {', '.join(normalized_tools)}\n"
        )
        return

    run["remediation_attempted_tools"] = sorted(attempted.union(pending))
    run["status"] = "remediating_tools"
    run["error"] = ""
    quoted_tools = " ".join([shlex.quote(t) for t in pending])
    remediation_cmd = (
        "set -euo pipefail; "
        f"pixi add {quoted_tools}; "
        "pixi install; "
        + " ; ".join([f"(pixi run which {shlex.quote(t)} || true)" for t in pending])
    )
    run["logs"].append(
        f"\n[remediation] Missing tools detected: {', '.join(pending)}. "
        "Auto-remediation started via pixi.\n"
    )
    if st.session_state.shell_running:
        run["logs"].append(
            "[remediation] Terminal is busy; delaying auto-install until terminal is free.\n"
        )
        return

    st.session_state.tool_remediation_active = True
    st.session_state.tool_remediation_run_id = run["id"]
    st.session_state.tool_remediation_tools = pending
    run["logs"].append(
        f"[remediation] Running command: pixi add {' '.join(pending)} && pixi install\n"
    )
    start_command(remediation_cmd, str(PROJECT_ROOT), allowed_root=str(PROJECT_ROOT))


def extract_missing_tools_from_line(line: str) -> list[str]:
    found: list[str] = []
    m1 = re.search(r"Command not found\. Ensure '([^']+)' is in your PATH", line)
    if m1:
        found.append(m1.group(1).strip())
    m2 = re.search(r"\b([A-Za-z0-9._+-]+): command not found\b", line)
    if m2:
        found.append(m2.group(1).strip())
    for m in re.findall(r"__MISSING_TOOL__:([A-Za-z0-9._+-]+)", line):
        found.append(m.strip())
    return sorted({x for x in found if x})


def finalize_tool_remediation_if_done() -> None:
    if not st.session_state.get("tool_remediation_active", False):
        return
    if st.session_state.shell_running:
        return

    run_id = st.session_state.get("tool_remediation_run_id")
    run = get_plan_run(run_id)
    success = any("[exit_code=0]" in line for line in st.session_state.shell_output)
    tools = st.session_state.get("tool_remediation_tools", [])

    if run is not None:
        if success:
            run["logs"].append(
                f"[remediation] Auto-install completed for: {', '.join(tools)}. Retrying plan automatically.\n"
            )
            run["status"] = "planned"
            run["error"] = ""
            run["recovery_verification_required"] = True
            st.session_state.pending_plan_retry_run_id = run_id
        else:
            run["logs"].append(
                f"[remediation] Auto-install failed for: {', '.join(tools)}. Manual install may be required.\n"
            )
            run["status"] = "failed"
            run["error"] = f"Auto-remediation failed for tools: {', '.join(tools)}"

    st.session_state.tool_remediation_active = False
    st.session_state.tool_remediation_run_id = None
    st.session_state.tool_remediation_tools = []


def start_plan_execution(run: dict, orchestrator: Orchestrator) -> None:
    if st.session_state.plan_running:
        st.warning("A plan is already executing.")
        return
    if run.get("plan") is None:
        st.warning("No plan available to execute.")
        return

    current_data_root = resolve_effective_run_data_root(
        session_data_root=str(st.session_state.get("chat_data_root", "")),
        run=run,
        fallback_selected_dir=str(st.session_state.selected_dir),
    )
    st.session_state["chat_data_root"] = current_data_root
    execution_options = {
        "use_test_subset": bool(st.session_state.get("chat_use_test_subset", True)),
        "test_subset_reads_per_fastq": int(st.session_state.get("chat_test_subset_reads", 1000000)),
    }
    benchmark_policy = str(
        (run.get("analysis_spec", {}) if isinstance(run.get("analysis_spec", {}), dict) else {}).get(
            "benchmark_policy",
            ui_benchmark_policy(),
        )
    ).strip() or ui_benchmark_policy()
    run["benchmark_policy"] = benchmark_policy
    run["execution_options"] = execution_options
    run_files = run.get("run_files") or init_run_files(run, WORKSPACE_ROOT)
    run_output_root = str(Path(run_files["state"]).parent.resolve(strict=False))
    existing_plan = run.get("plan") or {}
    normalized_plan, normalization_meta, _featurecounts_meta = normalize_ui_run_plan_for_execution(
        plan=existing_plan,
        analysis_spec=run.get("analysis_spec", {}),
        plan_contract=run.get("plan_contract", {}),
        user_request=str(run.get("user_request", "") or ""),
        selected_dir=run_output_root,
        data_root=str(current_data_root),
        benchmark_policy=benchmark_policy,
    )
    if normalization_meta.get("changed", False):
        run["plan"] = normalized_plan
        run["step_statuses"] = ["pending"] * len(
            (normalized_plan.get("plan", []) if isinstance(normalized_plan, dict) else [])
        )
        run["next_step_idx"] = 0
        run.setdefault("logs", []).append(
            "[normalize] applied backend execution normalization before UI execution. "
            f"meta_keys={sorted(normalization_meta.keys())}\n"
        )

    contract = run.get("plan_contract", {}) if isinstance(run.get("plan_contract", {}), dict) else {}
    if is_empty_contract(contract):
        contract = infer_contract_with_fallback(
            primary_text=str(run.get("user_request", "")),
            fallback_texts=[],
            existing=contract,
        )
        run["plan_contract"] = contract
    if contract:
        validation = assess_plan_contract(run.get("plan") or {}, contract)
        run["contract_validation"] = validation
        if not validation.get("passed", False):
            reason = (
                "Plan does not satisfy contract requirements: "
                f"missing capabilities={validation.get('missing_capabilities', [])}, "
                f"missing tool hints={validation.get('missing_tool_hints', [])}"
            )
            repaired, action, details = maybe_replan_for_failure(run, "contract_mismatch", reason)
            if repaired:
                run.setdefault("logs", []).append(
                    f"[auto-repair] contract mismatch -> {action}; retrying with replanned execution.\n"
                )
            else:
                run["status"] = "failed"
                run["error"] = reason
                run.setdefault("logs", []).append(
                    f"[auto-repair] contract mismatch unrepaired: {action} {details}\n"
                )
                st.warning(reason)
                persist_run_state(run)
                write_terminal_artifacts_if_needed(run)
                return

    preflight = preflight_execution_issues(run.get("plan") or {}, current_data_root)
    auto_repair_notes: list[str] = []

    # General preflight self-heal: recover input root and resolve broken reference paths, then re-check.
    if preflight["missing_data_root"] or preflight["missing_fastq"]:
        candidate_roots = [
            current_data_root,
            st.session_state.get("selected_dir", ""),
            str(READONLY_LINKS_ROOT),
            str(WORKSPACE_ROOT / "inputs_readonly"),
        ]
        best_root, best_count = _best_fastq_root(candidate_roots)
        if best_root and best_count > 0 and best_root != str(current_data_root):
            st.session_state["chat_data_root"] = best_root
            current_data_root = best_root
            run["requested_data_root"] = best_root
            auto_repair_notes.append(f"- Auto-repair: switched Data Root to `{best_root}` ({best_count} FASTQ files).")

    if preflight["missing_references"]:
        repair_res = _repair_missing_references_in_plan(
            run.get("plan") or {},
            preflight["missing_references"],
            str(run.get("user_request", "")),
        )
        if repair_res.get("changed", False):
            run["plan"] = run.get("plan") or {}
            reps = repair_res.get("replacements", [])
            short = ", ".join([f"{Path(x['old']).name}→{Path(x['new']).name}" for x in reps[:4]])
            auto_repair_notes.append(f"- Auto-repair: updated missing reference path(s): {short}")

    if auto_repair_notes:
        preflight = preflight_execution_issues(run.get("plan") or {}, current_data_root)
        run.setdefault("logs", []).append("[preflight-repair]\n" + "\n".join(auto_repair_notes) + "\n")

    if preflight["missing_data_root"] or preflight["missing_fastq"] or preflight["missing_references"] or preflight["missing_groups"]:
        run["status"] = "blocked_input"
        run["error"] = "Preflight blocked execution due to missing inputs/references."
        msg_lines = [
            "Execution blocked before run due to missing requirements.",
        ]
        if auto_repair_notes:
            msg_lines.extend(auto_repair_notes)
        if preflight["missing_data_root"]:
            msg_lines.append(f"- Data root not found: `{st.session_state.get('chat_data_root', '')}`")
        if preflight["missing_fastq"]:
            msg_lines.append("- No FASTQ files discovered in current Data Root.")
            msg_lines.append("- Would you like to attach/add input files now?")
        if preflight["missing_groups"]:
            msg_lines.append(f"- Missing sample groups in FASTQ naming: {', '.join(preflight['missing_groups'])}")
            msg_lines.append("- Please confirm filenames include expected tags (e.g., S1/S6 with R1/R2).")
        if preflight["missing_references"]:
            has_gtf_alias, has_fasta_alias = reference_alias_links_available()
            msg_lines.append("- Missing reference files:")
            for rp in preflight["missing_references"][:4]:
                msg_lines.append(f"  - `{rp}`")
            if has_gtf_alias and has_fasta_alias:
                msg_lines.append(
                    "- I can copy `mouse_gtf` and `mouse_fasta` from `workspace/inputs_readonly/` into `workspace/references/`."
                )
                msg_lines.append("- Would you like me to do that and then proceed with execution?")
            else:
                msg_lines.append("- Would you like me to search trusted sources and download candidate references into `workspace/references/`?")
        msg_lines.append(f"- FASTQ detected now: `{preflight['fastq_count']}`")
        message = "\n".join(msg_lines)
        run["logs"].append(message + "\n")
        st.session_state.latest_run_badge = "Blocked"
        post_execution_block_message(orchestrator, message)
        persist_run_state(run)
        write_terminal_artifacts_if_needed(run)
        return

    st.session_state.plan_running = True
    st.session_state.plan_execution_run_id = run["id"]
    st.session_state.plan_execution_mode = "full"
    st.session_state.plan_execution_step_idx = None
    st.session_state.plan_last_poll_at = 0.0
    run["status"] = "running"
    run["planner_status"] = str(run.get("planner_status", "planned")).strip() or "planned"
    run["planner_error"] = ""
    run["error"] = ""
    run["missing_tools_detected"] = []
    run["no_fastq_found"] = False
    run["missing_reference_detected"] = []
    run["missing_sample_groups"] = []
    run["policy_block_detected"] = False
    run["validation_block_detected"] = False
    run["stale_tmp_cache_detected"] = False
    run["format_input_error_detected"] = False
    run["recovery_verification_required"] = False
    run["last_heartbeat_event_ts"] = 0.0
    run["last_executor_event_ts"] = time.time()
    run["last_queue_activity_ts"] = run["last_executor_event_ts"]
    run["stall_event_emitted"] = False
    run["async_status"] = "running"
    st.session_state.executor_last_heartbeat_ts = run["last_executor_event_ts"]
    st.session_state.executor_last_heartbeat_note = "Execution started"
    st.session_state.executor_last_heartbeat_event_epoch = run["last_executor_event_ts"]
    run["logs"].append("\n=== New Execution ===\n")
    effective_selected_dir = resolve_effective_chat_selected_dir(
        run,
        session_selected_dir=str(st.session_state.selected_dir),
        benchmark_policy=str(benchmark_policy),
    )
    run["selected_dir"] = effective_selected_dir
    resolved = st.session_state.get("path_resolution", {})
    write_path_decisions(
        Path(run_files["path_decisions"]),
        user_requested_root=str(resolved.get("user_requested_root", current_data_root)),
        resolved_root=str(resolved.get("resolved_root", current_data_root)),
        resolution_reason=str(resolved.get("resolution_reason", "selected_data_root")),
        rejected_candidates=resolved.get("rejected_candidates", []),
    )
    write_manifest(
        Path(run_files["manifest"]),
        {
            "run_id": run.get("run_uid", ""),
            "plan_id": run.get("id"),
            "plan_kind": run.get("plan_kind", "executable"),
            "user_request": str(run.get("user_request", "")).strip(),
            "workspace_root": str(WORKSPACE_ROOT),
            "selected_dir": effective_selected_dir,
            "requested_data_root": str(current_data_root),
            "execution_options": execution_options,
            "benchmark_policy": benchmark_policy,
            "chat_session_id": str(run.get("chat_session_id", "")).strip(),
            "planning_started_at": str(run.get("planning_started_at", "")).strip(),
            "planning_finished_at": str(run.get("planning_finished_at", "")).strip(),
            "canonicalization": normalization_meta if normalization_meta.get("changed", False) else {},
            "created_at": datetime.now().isoformat(),
        },
    )
    export_execution_scripts(run, run.get("plan") or {}, "full_plan")
    persist_run_state(run)
    write_exit(
        Path(run_files["exit"]),
        {
            "run_id": run.get("run_uid", ""),
            "status": "running",
            "started_at": datetime.now().isoformat(),
        },
    )
    start_executor_runtime(
        run_files,
        run_id=str(run.get("run_uid", "") or ""),
    )
    append_event(
        Path(run_files["events"]),
        run_id=run.get("run_uid", ""),
        step_id=None,
        agent="PlannerAgent",
        event_type="STEP_STARTED",
        severity="info",
        payload={"message": "Execution started"},
    )
    st.session_state.latest_run_badge = "Running"

    while not st.session_state.plan_log_queue.empty():
        st.session_state.plan_log_queue.get_nowait()

    st.session_state.plan_thread = threading.Thread(
        target=orchestrator.execute_plan,
        args=(
            run["plan"],
            st.session_state.plan_log_queue,
            effective_selected_dir,
            str(WORKSPACE_ROOT),
        ),
        kwargs={"run_artifacts": run_files},
        daemon=True,
    )
    st.session_state.plan_thread.start()


def start_single_step_execution(run: dict, orchestrator: Orchestrator, step_idx: int) -> None:
    if st.session_state.plan_running:
        st.warning("A plan is already executing.")
        return
    if run.get("plan") is None or "plan" not in run["plan"]:
        st.warning("Active run is not an executable step plan.")
        return
    current_data_root = resolve_effective_run_data_root(
        session_data_root=str(st.session_state.get("chat_data_root", "")),
        run=run,
        fallback_selected_dir=str(st.session_state.selected_dir),
    )
    st.session_state["chat_data_root"] = current_data_root
    run_files = init_run_files(run, WORKSPACE_ROOT)
    run_output_root = str(Path(run_files["state"]).parent.resolve(strict=False))
    existing_plan = run.get("plan") or {}
    normalized_plan, normalization_meta, _featurecounts_meta = normalize_ui_run_plan_for_execution(
        plan=existing_plan,
        analysis_spec=run.get("analysis_spec", {}),
        plan_contract=run.get("plan_contract", {}),
        user_request=str(run.get("user_request", "") or ""),
        selected_dir=run_output_root,
        data_root=str(current_data_root),
        benchmark_policy=str(run.get("benchmark_policy", "") or ui_benchmark_policy()),
    )
    if normalization_meta.get("changed", False):
        run["plan"] = normalized_plan
        run["step_statuses"] = ["pending"] * len(
            (normalized_plan.get("plan", []) if isinstance(normalized_plan, dict) else [])
        )
        run["next_step_idx"] = 0
        run.setdefault("logs", []).append(
            "[normalize] applied backend execution normalization before UI single-step execution. "
            f"meta_keys={sorted(normalization_meta.keys())}\n"
        )
        step_idx = min(step_idx, max(0, len(run["step_statuses"]) - 1))
    steps = run["plan"].get("plan", [])
    if step_idx < 0 or step_idx >= len(steps):
        st.warning("No remaining step to run.")
        return

    preflight = preflight_execution_issues(run.get("plan") or {}, current_data_root)
    if preflight["missing_data_root"] or preflight["missing_fastq"] or preflight["missing_references"] or preflight["missing_groups"]:
        run["status"] = "blocked_input"
        run["error"] = "Preflight blocked execution due to missing inputs/references."
        msg_lines = ["Execution blocked before step run due to missing requirements."]
        if preflight["missing_data_root"]:
            msg_lines.append(f"- Data root not found: `{st.session_state.get('chat_data_root', '')}`")
        if preflight["missing_fastq"]:
            msg_lines.append("- No FASTQ files discovered in current Data Root. Would you like to attach/add them now?")
        if preflight["missing_groups"]:
            msg_lines.append(f"- Missing sample groups: {', '.join(preflight['missing_groups'])}")
        if preflight["missing_references"]:
            has_gtf_alias, has_fasta_alias = reference_alias_links_available()
            if has_gtf_alias and has_fasta_alias:
                msg_lines.append("- Missing references detected.")
                msg_lines.append("- I can copy `mouse_gtf` and `mouse_fasta` from `workspace/inputs_readonly/` into `workspace/references/`.")
                msg_lines.append("- Would you like me to do that and then proceed with step execution?")
            else:
                msg_lines.append("- Missing references detected. Should I search trusted sources and download candidates?")
        message = "\n".join(msg_lines)
        run["logs"].append(message + "\n")
        st.session_state.latest_run_badge = "Blocked"
        post_execution_block_message(orchestrator, message)
        return

    st.session_state.plan_running = True
    st.session_state.plan_execution_run_id = run["id"]
    st.session_state.plan_execution_mode = "single"
    st.session_state.plan_execution_step_idx = step_idx
    st.session_state.plan_last_poll_at = 0.0

    run["status"] = "running"
    run["error"] = ""
    run["benchmark_policy"] = str(
        (run.get("analysis_spec", {}) if isinstance(run.get("analysis_spec", {}), dict) else {}).get(
            "benchmark_policy",
            ui_benchmark_policy(),
        )
    ).strip() or ui_benchmark_policy()
    run["execution_options"] = {
        "use_test_subset": bool(st.session_state.get("chat_use_test_subset", True)),
        "test_subset_reads_per_fastq": int(st.session_state.get("chat_test_subset_reads", 1000000)),
    }
    if run.get("step_statuses") and step_idx < len(run["step_statuses"]):
        run["step_statuses"][step_idx] = "running"
    run["last_heartbeat_event_ts"] = 0.0
    run["last_executor_event_ts"] = time.time()
    run["last_queue_activity_ts"] = run["last_executor_event_ts"]
    run["stall_event_emitted"] = False
    run["async_status"] = "running"
    st.session_state.executor_last_heartbeat_ts = run["last_executor_event_ts"]
    st.session_state.executor_last_heartbeat_note = "Execution started"
    st.session_state.executor_last_heartbeat_event_epoch = run["last_executor_event_ts"]
    run["logs"].append(f"\n=== Executing Step {step_idx + 1} ===\n")
    effective_selected_dir = resolve_effective_chat_selected_dir(
        run,
        session_selected_dir=str(st.session_state.selected_dir),
        benchmark_policy=str(run.get("benchmark_policy", ui_benchmark_policy())),
    )
    run["selected_dir"] = effective_selected_dir
    run["requested_data_root"] = current_data_root
    resolved = st.session_state.get("path_resolution", {})
    write_path_decisions(
        Path(run_files["path_decisions"]),
        user_requested_root=str(resolved.get("user_requested_root", current_data_root)),
        resolved_root=str(resolved.get("resolved_root", current_data_root)),
        resolution_reason=str(resolved.get("resolution_reason", "selected_data_root")),
        rejected_candidates=resolved.get("rejected_candidates", []),
    )
    write_manifest(
        Path(run_files["manifest"]),
        {
            "run_id": run.get("run_uid", ""),
            "plan_id": run.get("id"),
            "plan_kind": "single_step",
            "workspace_root": str(WORKSPACE_ROOT),
            "selected_dir": effective_selected_dir,
            "benchmark_policy": run.get("benchmark_policy", ui_benchmark_policy()),
            "execution_options": run.get("execution_options", {}),
            "created_at": datetime.now().isoformat(),
        },
    )
    single_step_plan = {
        "thought_process": f"Single-step execution for step {step_idx + 1}",
        "plan": [steps[step_idx]],
    }
    export_execution_scripts(run, single_step_plan, f"single_step_{step_idx + 1:02d}")
    persist_run_state(run)
    start_executor_runtime(
        run_files,
        run_id=str(run.get("run_uid", "") or ""),
    )
    st.session_state.latest_run_badge = "Running"

    while not st.session_state.plan_log_queue.empty():
        st.session_state.plan_log_queue.get_nowait()

    st.session_state.plan_thread = threading.Thread(
        target=orchestrator.execute_plan,
        args=(
            single_step_plan,
            st.session_state.plan_log_queue,
            effective_selected_dir,
            str(WORKSPACE_ROOT),
        ),
        kwargs={"run_artifacts": run_files},
        daemon=True,
    )
    st.session_state.plan_thread.start()


def _discover_fastq_files_with_reason(
    root_path: str,
    include_subdirs: bool,
    name_filter: str,
    max_files: int,
) -> tuple[list[str], str | None]:
    read_roots = discover_read_roots(WORKSPACE_ROOT, READONLY_LINKS_ROOT)
    resolved, reason = canonical_resolve(
        root_path,
        read_roots=read_roots,
        write_roots=[WORKSPACE_ROOT],
        mode="read",
        readonly_root=READONLY_LINKS_ROOT,
    )
    if resolved is None:
        return [], reason
    files = discover_fastq_files_guarded(
        resolved,
        include_subdirs=include_subdirs,
        name_filter=name_filter,
        max_files=max_files,
    )
    return files, None


def discover_fastq_files(root_path: str, include_subdirs: bool, name_filter: str, max_files: int) -> list[str]:
    files, reason = _discover_fastq_files_with_reason(root_path, include_subdirs, name_filter, max_files)
    if reason:
        st.session_state["path_resolution"] = {
            "user_requested_root": root_path,
            "resolved_root": "",
            "resolution_reason": "rejected",
            "rejected_candidates": [{"candidate": root_path, "reason": reason}],
        }
    return files


@st.cache_data(ttl=20, show_spinner=False)
def discover_fastq_files_ui_cached(
    root_path: str,
    include_subdirs: bool,
    name_filter: str,
    max_files: int,
) -> list[str]:
    files, _ = _discover_fastq_files_with_reason(root_path, include_subdirs, name_filter, max_files)
    return files


def discover_fastq_files_ui(
    root_path: str,
    include_subdirs: bool,
    name_filter: str,
    max_files: int,
    *,
    force_refresh: bool = False,
) -> list[str]:
    cache_key = (root_path, include_subdirs, name_filter, int(max_files))
    cached = st.session_state.get("chat_fastq_discovery_cache", {})
    if (
        not force_refresh
        and st.session_state.get("plan_running", False)
        and cached.get("key") == cache_key
        and isinstance(cached.get("files"), list)
    ):
        return cached.get("files", [])
    files = discover_fastq_files_ui_cached(root_path, include_subdirs, name_filter, max_files)
    st.session_state["chat_fastq_discovery_cache"] = {"key": cache_key, "files": files}
    return files


def _count_fastq_in_dir(path_str: str, max_files: int = 2000) -> int:
    try:
        p = Path(path_str).expanduser().resolve()
    except Exception:
        return 0
    if not p.exists():
        return 0
    if p.is_file():
        p = p.parent
    return len(discover_fastq_files(str(p), True, "", max_files))


def resolve_best_data_root(initial_root: str, recent_messages: list[dict], max_files: int = 2000) -> tuple[str, int, str]:
    best_dir, best_count, best_reason, rejected = resolve_data_root_with_guards(
        initial_root, recent_messages, max_files=max_files
    )
    st.session_state["path_resolution"] = {
        "user_requested_root": initial_root,
        "resolved_root": best_dir,
        "resolution_reason": best_reason,
        "rejected_candidates": rejected,
    }
    return best_dir, best_count, best_reason


def estimate_fastqc_minutes(file_paths: list[str], threads: int) -> tuple[float, float]:
    total_bytes = 0
    for fp in file_paths:
        try:
            total_bytes += Path(fp).stat().st_size
        except Exception:
            pass
    total_gb = total_bytes / (1024 ** 3)
    throughput_gb_per_min_per_thread = 0.25
    eta = total_gb / max(threads * throughput_gb_per_min_per_thread, 0.1)
    return round(total_gb, 2), round(eta, 1)


def select_files_from_request(request: str, files: list[str]) -> list[str]:
    req = request.lower()
    if not files:
        return files
    # Heuristic: "first two sets/pairs" => first 4 files (R1/R2 x2)
    if re.search(r"first\s+(two|2)\s+(sets|pairs)", req):
        return files[:4]
    if re.search(r"first\s+(one|1)\s+(set|pair)", req):
        return files[:2]
    return files


def build_request_from_session(snapshot: dict, fallback_user_text: str) -> str:
    return build_execution_request_context(snapshot, fallback_user_text)


def build_bootstrap_execution_plan(data_root: str) -> dict:
    return workflow_build_bootstrap_execution_plan(data_root)


def is_actionable_executable_plan(plan: dict) -> bool:
    return is_actionable_execution_plan(plan)


def _normalize_contract_hint(token: str) -> str:
    t = (token or "").strip().lower().strip("`\"'()[]{}<>:;,")
    if not t:
        return ""
    if "/" in t or "\\" in t:
        t = Path(t).name.lower()
    if not t:
        return ""
    if re.search(r"[^a-z0-9_.-]", t):
        return ""
    if len(t) < 2:
        return ""
    stop = {
        "assuming", "and", "or", "the", "a", "an", "to", "for", "with", "without", "from",
        "then", "else", "if", "of", "on", "in", "at", "by",
        "gtf", "fasta", "fa", "mouse_gtf", "mouse_fasta", "mouse_fa",
    }
    if t in stop:
        return ""
    # Keep script/tool-like hints only.
    if t.endswith((".sh", ".py")):
        return t
    if t in {"bash", "sh", "python", "python3", "star", "rmats", "rmats.py", "samtools", "fastqc"}:
        return t
    return ""


def infer_request_contract(request_text: str) -> dict:
    return infer_request_contract_core(str(request_text or ""), load_capability_catalog_data())


def is_empty_contract(contract: dict) -> bool:
    if not isinstance(contract, dict):
        return True
    caps = contract.get("must_include_capabilities", [])
    hints = contract.get("explicit_tool_hints", [])
    return (not caps) and (not hints)


def infer_contract_with_fallback(
    primary_text: str,
    fallback_texts: list[str],
    existing: Optional[dict] = None,
) -> dict:
    if isinstance(existing, dict) and not is_empty_contract(existing):
        return existing
    c = infer_request_contract(primary_text)
    if not is_empty_contract(c):
        return c
    for text in fallback_texts:
        c2 = infer_request_contract(text)
        if not is_empty_contract(c2):
            return c2
    return c


def assess_plan_contract(plan: dict, contract: dict) -> dict:
    catalog = load_capability_catalog_data()
    specs = capability_specs_from_catalog(catalog)
    return assess_plan_contract_core(plan, contract, capability_specs=specs)


def _replan_prompt_for_failure(run: dict, failure_class: str, reason: str) -> str:
    contract = run.get("plan_contract", {})
    plan_json = run.get("plan") or {}
    stderr_tail = "".join(_tail_items(run.get("stderr_tail", []), 50))
    return (
        "You are repairing a failed executable plan.\n"
        "Return ONLY executable JSON with `thought_process` and `plan`.\n"
        "Use only supported tools: `bash_run`, `fastqc_run`.\n"
        "Do not return a single-step QC-only plan unless the request is explicitly QC-only.\n\n"
        f"Original user request:\n{run.get('user_request', '')}\n\n"
        f"Failure class: {failure_class}\n"
        f"Failure reason: {reason}\n\n"
        f"Contract requirements (must satisfy):\n{json.dumps(contract, indent=2)}\n\n"
        f"Previous plan:\n{json.dumps(plan_json, indent=2)}\n\n"
        f"Recent stderr tail:\n{stderr_tail}\n"
    )


def _extract_existing_reference_pair(plan: dict) -> tuple[str, str]:
    gtf = ""
    fasta = ""
    for rp in _extract_reference_paths_from_plan(plan):
        low = str(rp).lower()
        exists = Path(str(rp)).expanduser().exists()
        if not exists:
            continue
        if not gtf and (low.endswith(".gtf") or low.endswith(".gtf.gz")):
            gtf = str(rp)
            continue
        if not fasta and (
            low.endswith(".fa")
            or low.endswith(".fa.gz")
            or low.endswith(".fasta")
            or low.endswith(".fasta.gz")
            or low.endswith(".fna")
            or low.endswith(".fna.gz")
        ):
            fasta = str(rp)
    return gtf, fasta


def _build_contract_template_repair(
    run: dict,
    *,
    failure_class: str,
    validation: dict,
) -> tuple[dict | None, str, dict]:
    if failure_class != "contract_mismatch":
        return None, "template_not_applicable", {"why": "failure_class_not_supported"}

    missing_caps = set(validation.get("missing_capabilities", []))
    if not missing_caps:
        return None, "template_not_applicable", {"why": "no_missing_capabilities"}

    contract = run.get("plan_contract", {}) if isinstance(run.get("plan_contract", {}), dict) else {}
    requested_caps = set(contract.get("must_include_capabilities", []))
    active_caps = missing_caps.union(requested_caps)

    # Keep template fallback capability-scoped: do not route DE-only contracts into splicing template repair.
    splicing_like = "splicing_analysis" in requested_caps
    needs_two_group_differential = bool(
        active_caps.intersection({"differential_analysis", "group_comparison", "splicing_analysis"})
    )
    if not (splicing_like and needs_two_group_differential):
        return None, "template_not_applicable", {"why": "no_matching_template_for_capabilities"}

    data_root = str(st.session_state.get("chat_data_root", st.session_state.selected_dir))
    gtf_path, fasta_path = _extract_existing_reference_pair(run.get("plan") or {})
    inferred_gtf, inferred_fasta, ref_reason = resolve_reference_paths(str(run.get("user_request", "")))
    if not gtf_path:
        gtf_path = inferred_gtf
    if not fasta_path:
        fasta_path = inferred_fasta
    if not gtf_path or not fasta_path:
        return None, "template_missing_references", {
            "why": "missing_reference_inputs_for_splicing_template",
            "resolved_gtf": inferred_gtf,
            "resolved_fasta": inferred_fasta,
            "resolution_reason": ref_reason,
        }

    control_tag, treatment_tag = _extract_sample_tags_from_plan(run.get("plan") or {})
    exec_opts = run.get("execution_options", {}) if isinstance(run.get("execution_options", {}), dict) else {}
    use_subset = bool(exec_opts.get("use_test_subset", st.session_state.get("chat_use_test_subset", True)))
    try:
        test_reads = int(exec_opts.get("test_subset_reads_per_fastq", st.session_state.get("chat_test_subset_reads", 1000000)))
    except Exception:
        test_reads = int(st.session_state.get("chat_test_subset_reads", 1000000))

    candidate = build_splicing_execution_plan(
        data_root=data_root,
        gtf_path=str(gtf_path),
        fasta_path=str(fasta_path),
        control_tag=str(control_tag or "S1"),
        treatment_tag=str(treatment_tag or "S6"),
        use_test_subset=use_subset,
        test_reads_per_fastq=test_reads,
    )
    template_validation = assess_plan_contract(candidate, contract)
    if not template_validation.get("passed", False):
        return None, "template_contract_failed", {
            "why": "template_plan_failed_contract_validation",
            "contract_validation": template_validation,
        }

    return candidate, "template_splicing_contract_repair", {
        "why": "contract_guided_template_repair",
        "contract_validation": template_validation,
        "data_root": data_root,
        "gtf_path": str(gtf_path),
        "fasta_path": str(fasta_path),
        "control_tag": str(control_tag or "S1"),
        "treatment_tag": str(treatment_tag or "S6"),
        "use_test_subset": use_subset,
        "test_subset_reads_per_fastq": int(test_reads),
    }


def maybe_replan_for_failure(run: dict, failure_class: str, reason: str) -> tuple[bool, str, dict]:
    attempts = dict(run.get("auto_repair_attempts", {}))
    current_attempts = int(attempts.get(failure_class, 0))
    max_attempts = max_attempts_for_class(failure_class, limits=MAX_REPAIR_ATTEMPTS_BY_CLASS)
    if current_attempts >= max_attempts:
        return False, "attempt_limit_reached", {"why": "attempt_limit_reached"}

    orchestrator = get_orchestrator(model_name, resolved_host, llm_backend)
    before_steps = len((run.get("plan") or {}).get("plan", [])) if isinstance(run.get("plan"), dict) else 0
    contract = run.get("plan_contract", {})
    prompt_base = _replan_prompt_for_failure(run, failure_class, reason)
    prompts = [prompt_base]
    if failure_class == "contract_mismatch":
        missing_caps = []
        if isinstance(run.get("contract_validation", {}), dict):
            missing_caps = list(run.get("contract_validation", {}).get("missing_capabilities", []))
        strict_prompt = (
            prompt_base
            + "\nPrevious repair attempts missed required contract capabilities.\n"
            + f"You MUST include explicit evidence for capabilities: {missing_caps or contract.get('must_include_capabilities', [])}.\n"
            + "If group comparison is required, include two-group analysis inputs and commands.\n"
        )
        prompts.append(strict_prompt)

    replan_errors: list[dict] = []
    for idx, prompt in enumerate(prompts, start=1):
        candidate = orchestrator.think(prompt)
        benchmark_policy = ui_benchmark_policy()
        candidate, candidate_meta = normalize_ui_auto_plan(
            candidate if isinstance(candidate, dict) else {},
            orchestrator=orchestrator,
            user_request=str(run.get("user_request", "") or prompt),
            contract=contract if isinstance(contract, dict) else {},
            selected_dir=resolve_effective_chat_selected_dir(
                run,
                session_selected_dir=str(st.session_state.selected_dir),
                benchmark_policy=str(benchmark_policy),
            ),
            data_root=str(st.session_state.get("chat_data_root", st.session_state.selected_dir)),
            project_root=str(PROJECT_ROOT),
            benchmark_policy=benchmark_policy,
        )
        run["benchmark_policy"] = candidate_meta.get("benchmark_policy", benchmark_policy)
        auto_steps = candidate.get("plan", []) if isinstance(candidate, dict) else []
        if not auto_steps or not is_actionable_executable_plan(candidate):
            replan_errors.append(
                {
                    "action": "replan_invalid",
                    "details": {
                        "why": "model returned empty/non-actionable plan",
                        "model_attempt": idx,
                        "normalization_meta": candidate_meta,
                    },
                    "validation": {},
                }
            )
            continue

        validation = assess_plan_contract(candidate, contract)
        if not validation.get("passed", False):
            replan_errors.append(
                {
                    "action": "replan_contract_failed",
                    "details": {"model_attempt": idx, **validation},
                    "validation": validation,
                }
            )
            continue

        run["plan"] = candidate
        run["contract_validation"] = validation
        run["analysis_spec"] = candidate_meta.get("analysis_spec", {})
        run["protocol_validation"] = candidate_meta.get("protocol_validation", {})
        run["semantic_validation"] = candidate_meta.get("semantic_validation", {})
        run["protocol_normalization_meta"] = candidate_meta
        run["step_statuses"] = ["pending"] * len(auto_steps)
        run["next_step_idx"] = 0
        run["error"] = ""
        run["status"] = "planned"
        run["async_status"] = "recovering"
        details = {
            "why": "Replanned with failure context and contract constraints.",
            "failure_class": failure_class,
            "model_attempt": idx,
            "contract_validation": validation,
            "diff_summary": {
                "before_step_count": before_steps,
                "after_step_count": len(auto_steps),
            },
        }
        return True, "replan_with_failure_context", details

    last_validation = {}
    if replan_errors and isinstance(replan_errors[-1].get("validation", {}), dict):
        last_validation = replan_errors[-1].get("validation", {})
    if not last_validation and isinstance(run.get("contract_validation", {}), dict):
        last_validation = run.get("contract_validation", {})

    fallback_plan, fallback_action, fallback_details = _build_contract_template_repair(
        run,
        failure_class=failure_class,
        validation=last_validation,
    )
    if fallback_plan is not None:
        fallback_steps = fallback_plan.get("plan", []) if isinstance(fallback_plan, dict) else []
        validation = assess_plan_contract(fallback_plan, contract)
        run["plan"] = fallback_plan
        run["contract_validation"] = validation
        run["step_statuses"] = ["pending"] * len(fallback_steps)
        run["next_step_idx"] = 0
        run["error"] = ""
        run["status"] = "planned"
        run["async_status"] = "recovering"
        details = {
            **fallback_details,
            "failure_class": failure_class,
            "contract_validation": validation,
            "diff_summary": {
                "before_step_count": before_steps,
                "after_step_count": len(fallback_steps),
            },
            "model_replan_errors": replan_errors[-2:] if replan_errors else [],
        }
        return True, fallback_action, details

    if replan_errors:
        return False, replan_errors[-1]["action"], {
            **replan_errors[-1]["details"],
            "template_repair": {"action": fallback_action, **fallback_details},
        }
    return False, fallback_action, fallback_details


def detect_splicing_intent(text: str) -> bool:
    t = (text or "").lower()
    return ("splicing" in t) or ("rmats" in t)


def pick_reference_paths_from_text(text: str) -> tuple[str, str]:
    gtf = ""
    fasta = ""
    for p in extract_paths_from_text(text):
        pl = p.lower()
        if (pl.endswith(".gtf") or pl.endswith(".gtf.gz")) and Path(p).expanduser().exists():
            # Prefer the most recent valid path mention.
            gtf = p
        if (
            pl.endswith(".fa") or pl.endswith(".fa.gz")
            or pl.endswith(".fasta") or pl.endswith(".fasta.gz")
            or pl.endswith(".fna") or pl.endswith(".fna.gz")
        ) and Path(p).expanduser().exists():
            # Prefer the most recent valid path mention.
            fasta = p
    return gtf, fasta


def _find_reference_candidate(kind: str) -> str:
    roots = [
        WORKSPACE_ROOT / "references",
        PROJECT_ROOT / "references",
        READONLY_LINKS_ROOT,
        WORKSPACE_ROOT,
    ]
    suffixes = (".gtf", ".gtf.gz") if kind == "gtf" else (".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz")
    preferred_markers = ("mouse", "mm", "grcm", "gencode")
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not (p.is_file() or p.is_symlink()):
                continue
            pl = p.name.lower()
            target_name = ""
            try:
                target_name = p.resolve(strict=True).name.lower()
            except Exception:
                target_name = ""
            if pl.endswith(suffixes) or (target_name and target_name.endswith(suffixes)):
                candidates.append(p)
    if not candidates:
        return ""
    scored = sorted(
        candidates,
        key=lambda p: (
            0 if any(m in p.name.lower() for m in preferred_markers) else 1,
            len(p.name),
        ),
    )
    return str(scored[0])


def _find_alias_reference(kind: str, request_text: str) -> str:
    lower = (request_text or "").lower()
    alias_names = []
    if kind == "gtf":
        alias_names = ["mouse_gtf", "gtf"]
    else:
        alias_names = ["mouse_fasta", "mouse_fa", "fasta", "fa"]

    # 1) Explicit alias links under workspace/inputs_readonly.
    for alias in alias_names:
        if alias not in lower:
            continue
        p = READONLY_LINKS_ROOT / alias
        if p.exists() or p.is_symlink():
            return str(p)

    # 2) Loose alias match among attached links.
    if READONLY_LINKS_ROOT.exists():
        for entry in READONLY_LINKS_ROOT.iterdir():
            name_l = entry.name.lower()
            if kind == "gtf" and "gtf" in name_l and (entry.exists() or entry.is_symlink()):
                return str(entry)
            if kind == "fasta" and any(k in name_l for k in ("fasta", "fa", "genome")) and (entry.exists() or entry.is_symlink()):
                return str(entry)
    return ""


def resolve_reference_paths(request_text: str) -> tuple[str, str, str]:
    explicit_gtf, explicit_fasta = pick_reference_paths_from_text(request_text)
    gtf = ""
    fasta = ""
    reason_parts: list[str] = []
    lower = (request_text or "").lower()
    alias_gtf_requested = ("mouse_gtf" in lower) or bool(re.search(r"\bgtf\b", lower))
    alias_fasta_requested = ("mouse_fasta" in lower) or ("mouse_fa" in lower) or bool(re.search(r"\bfasta\b", lower))

    if alias_gtf_requested:
        gtf = _find_alias_reference("gtf", request_text) or _find_reference_candidate("gtf")
        if gtf:
            reason_parts.append("gtf_alias_or_scan")
    if alias_fasta_requested:
        fasta = _find_alias_reference("fasta", request_text) or _find_reference_candidate("fasta")
        if fasta:
            reason_parts.append("fasta_alias_or_scan")

    if not gtf and explicit_gtf:
        gtf = explicit_gtf
        reason_parts.append("gtf_explicit")
    if not fasta and explicit_fasta:
        fasta = explicit_fasta
        reason_parts.append("fasta_explicit")

    if not gtf:
        gtf = _find_reference_candidate("gtf")
        if gtf:
            reason_parts.append("gtf_local_scan")
    if not fasta:
        fasta = _find_reference_candidate("fasta")
        if fasta:
            reason_parts.append("fasta_local_scan")

    reason = ",".join(reason_parts) if reason_parts else "unresolved"
    return gtf, fasta, reason


def plan_contains_splicing_steps(plan: dict) -> bool:
    for step in (plan or {}).get("plan", []):
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool_name", "")).lower()
        cmd = str(step.get("arguments", {}).get("command", "")).lower()
        if "rmats" in tool or "rmats" in cmd or "star" in cmd:
            return True
    return False


def build_splicing_execution_plan(
    data_root: str,
    gtf_path: str,
    fasta_path: str,
    control_tag: str = "S1",
    treatment_tag: str = "S6",
    use_test_subset: bool = True,
    test_reads_per_fastq: int = 1000000,
) -> dict:
    return workflow_build_splicing_execution_plan(
        data_root=data_root,
        gtf_path=gtf_path,
        fasta_path=fasta_path,
        control_tag=control_tag,
        treatment_tag=treatment_tag,
        use_test_subset=use_test_subset,
        test_reads_per_fastq=test_reads_per_fastq,
    )


def render_human_plan(plan: dict, plan_kind: str) -> None:
    if not plan:
        st.info("No plan available.")
        return
    if plan_kind == "blueprint":
        st.markdown(f"**Workflow**: `{plan.get('workflow', 'unknown')}`")
        st.markdown(f"**Selected Files**: `{plan.get('selected_file_count', 0)}`")
        requires = plan.get("requires_user_inputs", [])
        if requires:
            st.markdown("**Required User Inputs**")
            for item in requires:
                st.write(f"- {item}")
        st.markdown("**Planned Steps**")
        for step in plan.get("steps", []):
            st.write(f"{step.get('step')}. {step.get('name')} (~{step.get('eta_min')} min)")
            st.code(step.get("bash", ""), language="bash")
        return

    steps = plan.get("plan", [])
    if not steps:
        st.info("Executable plan has no steps.")
        return
    st.markdown("**Executable Steps**")
    for step in steps:
        tool = step.get("tool_name", "unknown")
        args = step.get("arguments", {})
        st.write(f"{step.get('step_id', '?')}. `{tool}`")
        if tool == "bash_run" and "command" in args:
            st.code(args.get("command", ""), language="bash")
        else:
            st.code(json.dumps(args, indent=2), language="json")


def validate_executable_plan_paths(plan: dict) -> list[str]:
    warnings: list[str] = []
    for step in plan.get("plan", []):
        if step.get("tool_name") != "fastqc_run":
            continue
        input_text = str(step.get("arguments", {}).get("input_file", "")).strip()
        if not input_text:
            warnings.append("fastqc_run step is missing 'input_file'.")
            continue
        for token in input_text.split():
            if any(ch in token for ch in ["*", "?", "["]):
                continue
            p = Path(token).expanduser()
            if not p.exists():
                warnings.append(f"Missing input path in plan: {p}")
    return warnings


def _extract_reference_paths_from_plan(plan: dict) -> list[str]:
    refs: list[str] = []
    ext_suffixes = (".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz", ".gtf", ".gtf.gz")
    alias_names = {"mouse_fasta", "mouse_fa", "mouse_gtf"}

    def _normalize_token(token: str) -> str:
        return token.strip().strip("\"'").rstrip("];,")

    def _maybe_add_reference(token: str) -> None:
        t = _normalize_token(token)
        if not t.startswith("/"):
            return
        low = t.lower()
        base = Path(t).name.lower()
        if low.endswith(ext_suffixes) or base in alias_names:
            refs.append(t)

    for step in plan.get("plan", []):
        if step.get("tool_name") != "bash_run":
            continue
        cmd = str(step.get("arguments", {}).get("command", ""))
        if not cmd.strip():
            continue

        try:
            tokens = shlex.split(cmd, posix=True)
        except Exception:
            tokens = []

        for i, token in enumerate(tokens):
            _maybe_add_reference(token)
            if token == "-f" and i + 1 < len(tokens):
                _maybe_add_reference(tokens[i + 1])

    dedup: list[str] = []
    seen: set[str] = set()
    for r in refs:
        if r not in seen:
            seen.add(r)
            dedup.append(r)
    return dedup


def _extract_sample_tags_from_plan(plan: dict) -> tuple[str, str]:
    control_tag = "S1"
    treatment_tag = "S6"
    for step in plan.get("plan", []):
        if step.get("tool_name") != "bash_run":
            continue
        cmd = str(step.get("arguments", {}).get("command", ""))
        ctl_match = re.search(
            r"select_sample_r1\.sh\s+\S+\s+([A-Za-z0-9]+)\s+\S+\s+CONTROL\b",
            cmd,
            flags=re.IGNORECASE,
        )
        trt_match = re.search(
            r"select_sample_r1\.sh\s+\S+\s+([A-Za-z0-9]+)\s+\S+\s+TREATMENT\b",
            cmd,
            flags=re.IGNORECASE,
        )
        if ctl_match:
            control_tag = str(ctl_match.group(1))
        if trt_match:
            treatment_tag = str(trt_match.group(1))

        tags = re.findall(
            r"(?:^|[/_\\-])([A-Za-z0-9]+)_R1(?:_001)?\.(?:f(?:ast)?q)(?:\.gz)?\b",
            cmd,
            flags=re.IGNORECASE,
        )
        if tags:
            if not ctl_match and len(tags) >= 1:
                control_tag = str(tags[0])
            if not trt_match and len(tags) >= 2:
                treatment_tag = str(tags[1])
    return control_tag, treatment_tag


def preflight_execution_issues(plan: dict, data_root: str) -> dict:
    issues = {
        "missing_data_root": False,
        "missing_fastq": False,
        "missing_groups": [],
        "missing_references": [],
        "fastq_count": 0,
    }
    try:
        discovered = discover_fastq_files(data_root, True, "", 5000)
    except Exception:
        discovered = []
    issues["fastq_count"] = len(discovered)
    if not Path(data_root).exists():
        issues["missing_data_root"] = True
    if not discovered:
        issues["missing_fastq"] = True
    elif plan_requires_filename_group_tags(plan, data_root=data_root):
        control_tag, treatment_tag = _extract_sample_tags_from_plan(plan)
        lower_names = [Path(x).name.lower() for x in discovered]
        has_control = any(f"_{control_tag.lower()}_" in n and "_r1_001" in n for n in lower_names)
        has_treatment = any(f"_{treatment_tag.lower()}_" in n and "_r1_001" in n for n in lower_names)
        if not has_control:
            issues["missing_groups"].append("control")
        if not has_treatment:
            issues["missing_groups"].append("treatment")

    for rp in _extract_reference_paths_from_plan(plan):
        if not Path(rp).expanduser().exists():
            issues["missing_references"].append(rp)
    return issues


def _best_fastq_root(candidates: list[str]) -> tuple[str, int]:
    best_root = ""
    best_count = -1
    seen: set[str] = set()
    for c in candidates:
        c_norm = str(c).strip()
        if not c_norm or c_norm in seen:
            continue
        seen.add(c_norm)
        try:
            count = len(discover_fastq_files(c_norm, True, "", 5000))
        except Exception:
            count = 0
        if count > best_count:
            best_root = c_norm
            best_count = count
    return best_root, max(0, best_count)


def _repair_missing_references_in_plan(plan: dict, missing_refs: list[str], request_text: str) -> dict:
    if not missing_refs:
        return {"changed": False, "replacements": []}
    replacements: list[dict] = []
    steps = (plan or {}).get("plan", [])
    for missing in missing_refs:
        missing_l = str(missing).lower()
        kind = "fasta"
        if missing_l.endswith(".gtf") or missing_l.endswith(".gtf.gz") or "gtf" in Path(missing_l).name:
            kind = "gtf"
        candidate = _find_alias_reference(kind, request_text) or _find_reference_candidate(kind)
        if not candidate or not Path(candidate).exists():
            continue
        old = str(missing)
        new = str(candidate)
        if old == new:
            continue
        changed_any = False
        for step in steps:
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool_name", ""))
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            if tool == "bash_run":
                cmd = str(args.get("command", ""))
                if old in cmd:
                    args["command"] = cmd.replace(old, new)
                    changed_any = True
            if tool == "fastqc_run":
                # fastqc_run should not carry refs; kept for generic extensibility.
                continue
        if changed_any:
            replacements.append({"old": old, "new": new, "kind": kind})
    return {"changed": bool(replacements), "replacements": replacements}


def build_bash_blueprint(request: str, files: list[str], threads: int) -> dict:
    req_lower = request.lower()
    workflow = "generic_qc"
    steps = []
    notes = []
    requires = []

    if "alternative splicing" in req_lower or "splicing" in req_lower:
        workflow = "alternative_splicing"
        requires = [
            "Case/control sample mapping",
            "Reference genome FASTA + annotation GTF",
            "Aligner index (STAR/HISAT2)",
        ]
        steps = [
            {"step": 1, "name": "FastQC", "eta_min": 20, "bash": "fastqc ..."},
            {"step": 2, "name": "Trim/QC", "eta_min": 30, "bash": "fastp ..."},
            {"step": 3, "name": "Align RNA-Seq Reads", "eta_min": 120, "bash": "STAR ..."},
            {"step": 4, "name": "Sort/Index BAM", "eta_min": 20, "bash": "samtools sort/index ..."},
            {"step": 5, "name": "Quantify Junction/Exons", "eta_min": 25, "bash": "featureCounts ..."},
            {"step": 6, "name": "Differential Splicing", "eta_min": 15, "bash": "rMATS/DEXSeq script ..."},
            {"step": 7, "name": "Report", "eta_min": 10, "bash": "Rscript summarize_splicing.R"},
        ]
        notes.append("Controls are required for meaningful differential splicing results.")
        notes.append("Dry-run with test subset validates pipeline wiring, not biological significance.")
    elif "differential expression" in req_lower or "deseq2" in req_lower:
        workflow = "differential_expression"
        requires = [
            "Case/control sample mapping with biological replicates",
            "Reference genome + annotation",
        ]
        steps = [
            {"step": 1, "name": "FastQC", "eta_min": 20, "bash": "fastqc ..."},
            {"step": 2, "name": "Trim/QC", "eta_min": 30, "bash": "fastp ..."},
            {"step": 3, "name": "Align RNA-Seq Reads", "eta_min": 120, "bash": "STAR ..."},
            {"step": 4, "name": "Count Matrix", "eta_min": 25, "bash": "featureCounts ..."},
            {"step": 5, "name": "DE Analysis", "eta_min": 10, "bash": "Rscript run_deseq2.R"},
        ]
        notes.append("DE step depends on valid condition labels and replicate structure.")
    else:
        total_gb, eta = estimate_fastqc_minutes(files, threads)
        steps = [
            {"step": 1, "name": "FastQC", "eta_min": eta, "bash": "fastqc ..."},
        ]
        notes.append(f"Estimated input size: ~{total_gb} GB.")

    test_lines = 4000
    test_commands = [
        "mkdir -p workspace/test_subset",
        f"head -n {test_lines} <input.fastq> > workspace/test_subset/<sample>_test.fastq",
    ]

    return {
        "plan_kind": "blueprint",
        "workflow": workflow,
        "request": request,
        "selected_file_count": len(files),
        "requires_user_inputs": requires,
        "steps": steps,
        "test_subset_strategy": {
            "enabled": True,
            "lines_per_fastq": test_lines,
            "commands": test_commands,
        },
        "notes": notes,
    }


def is_safe_command(command: str) -> tuple[bool, str]:
    lowered = command.lower()
    blocked_patterns = [
        " rm -rf /",
        "sudo ",
        "shutdown",
        "reboot",
        "mkfs",
        "dd if=",
        ">:",
        "chmod -r 777 /",
        " chown -r ",
    ]
    wrapped = f" {lowered}"
    for pat in blocked_patterns:
        if pat in wrapped:
            return False, f"Blocked command pattern detected: '{pat.strip()}'"
    return True, ""


def analyze_link(url: str) -> str:
    with httpx.Client(timeout=20) as client:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
        html = response.text

    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(re.sub(r"\s+", " ", text)).strip()
    return llm.summarize_text(
        text[:120000],
        (
            "Analyze this URL for bioinformatics harness use. If it is a paper, provide summary and pipeline steps. "
            "If it is a software repo, provide purpose, install notes, trust signals, and integration ideas."
        ),
    )


def normalize_domain(domain: str) -> str:
    d = domain.strip().lower()
    if not d:
        return ""
    if d.startswith("http://") or d.startswith("https://"):
        try:
            d = (urlparse(d).hostname or "").lower()
        except Exception:
            return ""
    return d.lstrip(".")


def is_url_in_allowed_domains(url: str, allowed_domains: list[str]) -> bool:
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except Exception:
        return False
    normalized = [normalize_domain(d) for d in allowed_domains if normalize_domain(d)]
    return any(host == d or host.endswith(f".{d}") for d in normalized)


def download_reference_to_workspace(url: str, allowed_domains: list[str], dest_dir: Path) -> Path:
    if not is_url_in_allowed_domains(url, allowed_domains):
        raise PermissionError("URL domain is not in the trusted reference allow-list.")
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are supported.")
    filename = Path(parsed.path).name or "downloaded_reference.dat"
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = (dest_dir / safe_name).resolve()
    dest.relative_to(WORKSPACE_ROOT)
    if is_under_readonly(dest):
        raise PermissionError("Writes under workspace/inputs_readonly are forbidden.")
    with httpx.Client(timeout=120) as client:
        with client.stream("GET", url.strip(), follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in resp.iter_bytes():
                    if chunk:
                        fh.write(chunk)
    return dest


@st.cache_data(ttl=30, show_spinner=False)
def ui_harness_help_text() -> str:
    """Return deterministic harness-help text for the guide panel."""
    registry = SkillRegistry(SKILLS_DEFINITIONS)
    return build_harness_help_context(registry._skills, compact=True)


def _model_status_chip(model_ready: bool) -> tuple[str, str]:
    if st.session_state.get("model_busy"):
        return "Running", "live"
    if not model_ready:
        return "Unavailable", "danger"
    recent = st.session_state.get("model_last_activity", "")
    if recent:
        try:
            elapsed = int((datetime.utcnow() - datetime.fromisoformat(recent)).total_seconds())
        except Exception:
            elapsed = -1
        if 0 <= elapsed <= 30:
            return f"Recently active ({elapsed}s)", "info"
    return "Idle", "success"


def _executor_heartbeat_label() -> str:
    ts = float(st.session_state.get("executor_last_heartbeat_ts", 0.0) or 0.0)
    if ts <= 0:
        return "Idle"
    elapsed = int(max(0.0, time.time() - ts))
    return f"{elapsed}s ago"


def _step_progress_text(run: dict) -> str:
    steps = list(run.get("step_statuses", []) or [])
    if not steps:
        return "No plan yet"
    completed = sum(1 for item in steps if str(item).strip().lower() == "completed")
    total = len(steps)
    running = sum(1 for item in steps if str(item).strip().lower() == "running")
    if running:
        return f"{completed}/{total} complete, 1 active"
    return f"{completed}/{total} complete"


def render_terminal_control() -> None:
    """Render the small terminal control as an on-demand surface."""
    terminal_container = st.popover("Shell") if hasattr(st, "popover") else st.expander("Shell", expanded=False)
    with terminal_container:
        command_input = st.text_input("Command", value="pwd && ls -la", key="chat_terminal_command")
        if st.button("Run", key="chat_terminal_run"):
            ok_cmd, reason = is_safe_command(command_input)
            if not ok_cmd:
                st.error(reason)
            else:
                start_command(command_input, st.session_state.selected_dir)
        if st.session_state.get("shell_running", False):
            st.caption("Running command. Output will stream below.")
        output_container = st.empty()
        process_logs(output_container)


def render_left_rail(active_run: dict, *, model_ready: bool) -> None:
    """Render the slim left rail with only essential controls."""
    badge = status_badge(str(active_run.get("status", "")))
    recent_runs = summarize_recent_runs(
        st.session_state.get("plan_runs", []),
        active_run_id=active_run.get("id"),
        limit=6,
    )
    model_label, model_tone = _model_status_chip(model_ready)
    current_step = summarize_recent_runs([active_run], active_run_id=active_run.get("id"), limit=1)[0]["step_label"]
    if current_step == "Awaiting work":
        current_step = "Waiting"
    sample_help = (
        "Uses a reduced troubleshooting subset when the workflow supports it. "
        "For FASTQ inputs this means a smaller read count per FASTQ."
    )
    compact_model_name = compact_model_name_for_rail(model_name)
    with shell_left:
        if st.button("New chat", use_container_width=True):
            new_plan_run()
            st.rerun()
        render_terminal_control()
        st.markdown(
            (
                '<div class="bh-mini-stat-grid">'
                f'<div class="bh-mini-stat"><div class="bh-mini-stat-label">Run</div><div class="bh-mini-stat-value">{badge["label"]}</div></div>'
                f'<div class="bh-mini-stat"><div class="bh-mini-stat-label">Step</div><div class="bh-mini-stat-value">{current_step}</div></div>'
                f'<div class="bh-mini-stat"><div class="bh-mini-stat-label">Heartbeat</div><div class="bh-mini-stat-value">{_executor_heartbeat_label()}</div></div>'
                f'<div class="bh-mini-stat"><div class="bh-mini-stat-label">Model</div><div class="bh-mini-stat-value">{model_label}</div></div>'
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            (
                '<div class="bh-rail-card">'
                '<div class="bh-rail-label">Runtime</div>'
                f'<div class="bh-rail-title">{backend_label(llm_backend)}</div>'
                f'<div class="bh-rail-meta">{compact_model_name}</div>'
                f'<div class="bh-rail-meta">RAM {mem.percent:.1f}% · CPU {cpu:.1f}%</div>'
                f'<div class="bh-rail-meta"><span class="bh-badge bh-badge-{model_tone}">Model {model_label}</span></div>'
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            "<div class='bh-shell-heading' style='font-size:1.08rem; margin-bottom:0.22rem;'>Preferences</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<div class='bh-shell-caption' style='margin-bottom:0.55rem;'>Keep the defaults simple without hiding the controls you use most.</div>",
            unsafe_allow_html=True,
        )
        st.checkbox(
            "Auto-start on proceed/run",
            key="auto_start_from_chat",
            help="If enabled, messages like 'proceed' trigger plan generation and execution.",
        )
        st.checkbox(
            "Auto-remediate tools",
            key="auto_remediate_missing_tools",
            help="Automatically attempt deterministic tool setup and retry when possible.",
        )
        st.checkbox(
            "Use small-sample subset",
            key="chat_use_test_subset",
            help=sample_help,
        )
        if st.session_state.get("chat_use_test_subset", True):
            st.number_input(
                "FASTQ subset reads",
                min_value=10000,
                max_value=5000000,
                step=10000,
                key="chat_test_subset_reads",
                help="Only applies when the selected workflow is operating on FASTQ inputs.",
            )
        if recent_runs:
            with st.expander("Recent conversations", expanded=False):
                for row in recent_runs:
                    active_text = " • active" if row.get("active") else ""
                    st.markdown(
                        (
                            '<div class="bh-rail-card">'
                            f'<div class="bh-rail-label">Run {row["id"]}{active_text}</div>'
                            f'<div class="bh-rail-title">{row["request"]}</div>'
                            f'<div class="bh-rail-meta"><span class="bh-badge bh-badge-{row["badge"]["tone"]}">{row["badge"]["label"]}</span> '
                            f'{row["step_label"]}</div>'
                            "</div>"
                        ),
                        unsafe_allow_html=True,
                    )
                    if not row.get("active") and st.button(
                        f"Open run {row['id']}",
                        key=f"rail_open_{row['id']}",
                        use_container_width=True,
                    ):
                        selected_run = get_plan_run(row["id"])
                        st.session_state.active_plan_id = row["id"]
                        if selected_run is not None:
                            st.session_state.orchestrator_session_id = session_id_for_run(selected_run)
                        st.rerun()


def render_activity_panel(active_run: dict) -> None:
    """Render the contextual activity panel."""
    badge = status_badge(str(active_run.get("status", "")))
    events = recent_event_rows(active_run, limit=10)
    artifacts = collect_run_artifacts(active_run, limit=8)
    st.markdown(
        (
            '<div class="bh-shell-section">'
            '<div class="bh-shell-heading">Live activity</div>'
            '<div class="bh-shell-caption">What the harness is doing now, what changed recently, and which outputs are ready.</div>'
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    c1.metric("Run status", badge["label"])
    c2.metric("Current step", summarize_recent_runs([active_run], active_run_id=active_run.get("id"), limit=1)[0]["step_label"])
    c3, c4 = st.columns(2)
    c3.metric("Step progress", _step_progress_text(active_run))
    c4.metric("Repair attempts", str(sum(int(v or 0) for v in dict(active_run.get("auto_repair_attempts", {})).values())))
    st.markdown("#### Recent events")
    if events:
        for event in events:
            st.markdown(
                f"- `{event.get('ts', '')}` · `{event.get('event_type', '')}` · {event.get('severity', 'info')} · {event.get('message', event.get('title', 'event'))}"
            )
    else:
        st.info("No structured events yet.")
    tracker = active_run.get("process_tracker", {}) if isinstance(active_run.get("process_tracker", {}), dict) else {}
    process_order = list(active_run.get("process_order", []) or [])
    active_processes = [tracker.get(key) for key in process_order if tracker.get(key)]
    if active_processes:
        st.markdown("#### Active process detail")
        for proc in active_processes[:3]:
            title = str(proc.get("title", proc.get("tool_name", "process"))).strip() or "Process"
            proc_badge = status_badge(str(proc.get("status", "")))
            st.markdown(
                f"- **{title}** · `{proc_badge['label']}` · {str(proc.get('status_text', '')).strip() or 'No status note yet'}"
            )
    if active_run.get("live_tail") or active_run.get("stderr_tail"):
        with st.expander("Output streams", expanded=False):
            live_tab, err_tab = st.tabs(["Live", "stderr"])
            with live_tab:
                st.code("".join(active_run.get("live_tail", []))[-12000:] or "(no live output yet)", language="text")
            with err_tab:
                st.code("".join(active_run.get("stderr_tail", []))[-12000:] or "(no stderr yet)", language="text")
    st.markdown("#### Recent artifacts")
    if artifacts:
        for path in artifacts:
            st.write(f"- `{path}`")
    else:
        st.caption("No artifact files discovered yet.")

def render_guide_panel() -> None:
    """Render the deterministic capability/help guide."""
    st.markdown(
        (
            '<div class="bh-shell-section">'
            '<div class="bh-shell-heading">Guide</div>'
            '<div class="bh-shell-caption">Capabilities, wrapped tool families, and extension paths from deterministic repo metadata.</div>'
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    st.markdown(ui_harness_help_text())


def route_context_panel_for_request(user_text: str) -> None:
    """Open one contextual panel when a request clearly calls for it.

    Args:
        user_text: Raw user request text.
    """
    suggestion = suggest_dock_view_from_request(user_text)
    if suggestion:
        st.session_state.pending_dock_view = suggestion


def apply_pending_dock_view() -> None:
    """Apply any deferred dock-view update before the selector widget renders."""
    pending = normalize_dock_view(st.session_state.get("pending_dock_view", "Hidden"))
    if pending != "Hidden":
        st.session_state.dock_view = pending
    st.session_state.pending_dock_view = None


def sync_chat_runtime_state(active_run: dict, orchestrator: Orchestrator) -> None:
    """Keep background run state synchronized without rendering debug UI.

    Args:
        active_run: Currently active run mapping.
        orchestrator: Shared orchestrator instance for session feedback.
    """
    reconcile_background_planning(active_run, orchestrator)

    if active_run.get("status") in {"failed", "blocked_missing_tools", "planning_failed", "planning_timed_out"} and active_run.get("error"):
        current_err = str(active_run.get("error", "")).strip()
        if current_err and current_err != active_run.get("last_error_feedback", ""):
            session = orchestrator.get_or_create_session(st.session_state.orchestrator_session_id)
            if active_run.get("status") in {"planning_failed", "planning_timed_out"}:
                recovery_note = (
                    "Planning update:\n"
                    f"- Failure: {current_err}\n"
                    "- Recovery: retry the run to start a fresh planning job."
                )
            elif active_run.get("no_fastq_found", False):
                new_root = st.session_state.get("chat_data_root", "")
                recovery_note = (
                    "Execution update:\n"
                    f"- Failure: {current_err}\n"
                    f"- Recovery: switched Data Root to `{new_root}` and queued automatic retry."
                )
            elif active_run.get("missing_reference_detected"):
                missing_refs = ", ".join(active_run.get("missing_reference_detected", []))
                recovery_note = (
                    "Execution update:\n"
                    f"- Failure: missing reference file(s): {missing_refs}\n"
                    "- Recovery: provide valid FASTA/GTF paths, or tell me to search trusted sources and download candidates into `workspace/references/`."
                )
            elif active_run.get("missing_sample_groups"):
                missing_groups = ", ".join(active_run.get("missing_sample_groups", []))
                recovery_note = (
                    "Execution update:\n"
                    f"- Failure: could not map FASTQ files for sample group(s): {missing_groups}\n"
                    "- Recovery: verify naming contains expected sample tags (e.g., S1/S6) or provide explicit mappings."
                )
            elif active_run.get("missing_tools_detected"):
                tools = ", ".join(active_run.get("missing_tools_detected", []))
                recovery_note = (
                    "Execution update:\n"
                    f"- Failure: missing tools detected ({tools}).\n"
                    "- Recovery: auto-remediation is running and plan retry is queued."
                )
            elif active_run.get("contract_validation", {}).get("missing_capabilities") or active_run.get(
                "contract_validation", {}
            ).get("missing_tool_hints"):
                recovery_note = (
                    "Execution update:\n"
                    "- Failure: plan/result did not satisfy requested workflow contract.\n"
                    f"- Missing capabilities: {active_run.get('contract_validation', {}).get('missing_capabilities', [])}\n"
                    f"- Advisory missing hints: {active_run.get('contract_validation', {}).get('missing_tool_hints', [])}\n"
                    "- Recovery: generating a revised plan with the failure context and retrying once."
                )
            else:
                recovery_note = (
                    "Execution update:\n"
                    f"- Failure: {current_err}\n"
                    "- Recovery action: check execution log; I will attempt automatic recovery when possible."
                )
            session["messages"].append({"role": "assistant", "content": recovery_note})
            active_run["last_error_feedback"] = current_err

    pending_retry_id = st.session_state.get("pending_plan_retry_run_id")
    if pending_retry_id is not None and not st.session_state.plan_running and not st.session_state.shell_running:
        retry_run = get_plan_run(pending_retry_id)
        if retry_run is not None and retry_run.get("plan"):
            failure_class = str(retry_run.get("auto_repair_last_class", "")).strip()
            if failure_class:
                retry_run["logs"].append(
                    f"[auto-repair] Retrying plan after repair action for failure class `{failure_class}`.\n"
                )
                retry_msg = (
                    "Retrying execution after automatic repair action.\n"
                    f"- Failure class: `{failure_class}`\n"
                    "- Proceeding with one-shot retry now."
                )
            else:
                retry_run["logs"].append("[remediation] Retrying step 1 due to missing tools.\n")
                retry_msg = "Retrying step 1 due to missing tools remediation result."
            session = orchestrator.get_or_create_session(st.session_state.orchestrator_session_id)
            session["messages"].append({"role": "assistant", "content": retry_msg})
            st.session_state.pending_plan_retry_run_id = None
            start_plan_execution(retry_run, orchestrator)
            st.rerun()
        st.session_state.pending_plan_retry_run_id = None

    hb_ts = float(st.session_state.get("executor_last_heartbeat_ts", 0.0) or 0.0)
    hb_recent = hb_ts > 0 and (time.time() - hb_ts) <= (HEARTBEAT_INTERVAL_SECONDS * 2)
    runtime_live = executor_runtime_is_live(active_run.get("run_files", {}))
    should_refresh = (
        st.session_state.plan_running
        or st.session_state.shell_running
        or bool(st.session_state.get("plan_thread") and st.session_state.plan_thread.is_alive())
        or bool(st.session_state.get("tool_remediation_active", False))
        or not st.session_state.plan_log_queue.empty()
        or active_run.get("status") in {"planning", "running", "recovering"}
        or hb_recent
        or runtime_live
        or bool(planner_job_snapshot(str(active_run.get("run_uid", "")).strip()).get("thread_alive", False))
    )
    maybe_append_chat_result_summary(
        active_run,
        orchestrator,
        session_id=st.session_state.orchestrator_session_id,
    )
    st.session_state.live_refresh_enabled = bool(should_refresh)
    st.session_state.plan_last_poll_at = time.time()


sync_recent_persisted_runs()
active_run = get_active_plan_run()
process_logs()
process_plan_logs()
current_dock_view = "Hidden"
render_left_rail(active_run, model_ready=ok)
with shell_content:
    shell_main = st.container()
    shell_right = st.empty()
tab_chat = shell_main.container()
tab_workspace = shell_right.container() if current_dock_view == "Files" else st.empty()
tab_skills = shell_right.container() if current_dock_view == "Extend" else st.empty()
tab_docs = shell_right.container() if current_dock_view == "Guide" else st.empty()
tab_links = shell_right.container() if current_dock_view == "Guide" else st.empty()
tab_pipeline = shell_right.container() if current_dock_view == "Visuals" else st.empty()


if current_dock_view == "Files":
    with tab_workspace:
        st.subheader("Workspace Isolation")
        st.caption("The agent and shell execution are restricted to `workspace/` subdirectories.")
    
        dir_options = list_relative_paths(str(WORKSPACE_ROOT), max_dirs=MAX_WORKSPACE_DIR_OPTIONS)
        default_idx = dir_options.index(st.session_state.selected_dir) if st.session_state.selected_dir in dir_options else 0
        selected = st.selectbox("Active project directory", dir_options, index=default_idx)
        st.session_state.selected_dir = selected
    
        col_mk, col_upload = st.columns(2)
        with col_mk:
            new_dir = st.text_input("Create subdirectory", value="")
            if st.button("Create Directory") and new_dir.strip():
                target = (Path(st.session_state.selected_dir) / new_dir.strip()).resolve()
                try:
                    target.relative_to(WORKSPACE_ROOT)
                    if is_under_readonly(target):
                        raise ValueError("Writes under workspace/inputs_readonly are forbidden.")
                    target.mkdir(parents=True, exist_ok=True)
                    st.success(f"Created: {target}")
                except ValueError as exc:
                    st.error(str(exc))
    
        with col_upload:
            uploads = st.file_uploader(
                "Upload files into active directory (small files only)",
                accept_multiple_files=True,
            )
            if uploads:
                dest = Path(st.session_state.selected_dir)
                if is_under_readonly(dest):
                    st.error("Uploads into workspace/inputs_readonly are forbidden.")
                    uploads = []
                for up in uploads:
                    out_path = dest / up.name
                    out_path.write_bytes(up.getbuffer())
                if uploads:
                    st.success(f"Uploaded {len(uploads)} file(s) to {dest}")
    
        st.markdown("### Attach External Data (No Copy)")
        st.caption(
            "For very large files/directories, attach local paths as symlinks under "
            "`workspace/inputs_readonly/` so data is available without duplication."
        )
        col_attach, col_detach = st.columns(2)
        with col_attach:
            ext_path = st.text_input("External file or directory path", placeholder="/Volumes/data/project123")
            ext_alias = st.text_input("Alias (optional)", placeholder="project123_data")
            if st.button("Attach Path"):
                try:
                    link_path = attach_external_path(ext_path, ext_alias)
                    st.success(f"Attached as read-only link: {link_path}")
                except Exception as exc:
                    st.error(f"Attach failed: {exc}")
        with col_detach:
            manifest = load_readonly_manifest()
            options = [x.get("link_path", "") for x in manifest.get("links", []) if x.get("link_path")]
            selected_link = st.selectbox("Attached links", options=options if options else [""])
            if st.button("Detach Selected Link", disabled=not bool(options)):
                try:
                    detach_external_path(selected_link)
                    st.success(f"Detached link: {selected_link}")
                except Exception as exc:
                    st.error(f"Detach failed: {exc}")
    
        current_manifest = load_readonly_manifest()
        if current_manifest.get("links"):
            st.dataframe(current_manifest["links"], use_container_width=True)
            st.warning(
                "Read-only behavior is enforced by policy and command guards. "
                "Do not intentionally run destructive commands on attached paths."
            )
    
        st.markdown("### Directory Tree")
        show_tree = st.checkbox(
            "Render directory tree snapshot (can be expensive on large workspaces)",
            value=False,
            key="workspace_show_tree",
        )
        if show_tree:
            remaining = render_tree(WORKSPACE_ROOT, max_depth=4, line_budget=MAX_TREE_LINES)
            if remaining <= 0:
                st.caption(f"Tree truncated to {MAX_TREE_LINES} lines for responsiveness.")
        else:
            st.caption("Tree rendering is paused to keep execution/heartbeat responsive.")
    
with tab_chat:
    active_run = get_active_plan_run()
    orchestrator = get_orchestrator(model_name, resolved_host, llm_backend)
    process_logs()
    finalize_tool_remediation_if_done()
    sync_chat_runtime_state(active_run, orchestrator)
    render_workspace_header()
    session_id = st.session_state.orchestrator_session_id
    session_snapshot = orchestrator.session_snapshot(session_id)
    focus_run = preferred_chat_run(active_run, st.session_state.get("plan_runs", []))
    st.markdown(
        f"<div class='bh-subtle'>Session <code>{session_id}</code> | token load est: {session_snapshot.get('token_load_estimate', 0)} "
        f"| compactions: {session_snapshot.get('compactions', 0)}</div>",
        unsafe_allow_html=True,
    )
    render_model_setup_block(llm_setup_report)
    chat_box = st.container(height=760, border=True)
    with chat_box:
        if session_snapshot.get("messages", []):
            render_chat_live_run_view(
                focus_run,
                heartbeat_label=_executor_heartbeat_label(),
                heartbeat_note=str(st.session_state.get("executor_last_heartbeat_note", "")).strip(),
            )
        else:
            render_chat_empty_state()
        for msg in session_snapshot.get("messages", []):
            role = msg.get("role", "assistant")
            with st.chat_message("assistant" if role == "assistant" else "user"):
                content = str(msg.get("content", ""))
                st.markdown(format_structured_chat_message(content) or content)

    conv_input = st.chat_input("Ask BioHarness to analyze, execute, explain, or prepare data.")
    if conv_input and conv_input.strip():
        try:
            mark_model_start()
            user_text = conv_input.strip()
            route_context_panel_for_request(user_text)
            session = orchestrator.get_or_create_session(session_id)
            user_logged = False

            copy_request_text = user_text
            user_text_lower = user_text.lower()
            should_assume_reference_copy = (
                "copy" in user_text_lower
                and "mouse_gtf" not in user_text_lower
                and "mouse_fasta" not in user_text_lower
                and detect_splicing_intent(str(active_run.get("user_request", "")))
                and active_run.get("status") == "blocked_input"
            )
            if should_assume_reference_copy:
                copy_request_text = f"{user_text} reference mouse_gtf mouse_fasta"
            copy_msg = maybe_copy_named_references(copy_request_text)
            if copy_msg:
                followup = build_post_copy_followup(active_run)
                proceed_requested = (
                    st.session_state.get("auto_start_from_chat", True)
                    and should_auto_start_from_chat(user_text)
                    and not st.session_state.plan_running
                )
                if proceed_requested:
                    combined_msg = (
                        f"{copy_msg}\n\n"
                        "I verified the copy action and will now proceed with execution."
                    )
                else:
                    combined_msg = f"{copy_msg}\n\n{followup}"
                session["messages"].append({"role": "user", "content": user_text})
                session["messages"].append({"role": "assistant", "content": combined_msg})
                active_run["conversation"].append({"user": user_text, "assistant": combined_msg})
                user_logged = True
                if not proceed_requested:
                    st.rerun()

            is_exec_trigger = (
                st.session_state.get("auto_start_from_chat", True)
                and should_auto_start_from_chat(user_text)
                and not st.session_state.plan_running
            )
            if (not is_exec_trigger) and should_answer_with_run_status(user_text):
                summary = latest_run_summary(active_run)
                if not user_logged:
                    session["messages"].append({"role": "user", "content": user_text})
                    user_logged = True
                session["messages"].append({"role": "assistant", "content": summary})
                active_run["conversation"].append({"user": user_text, "assistant": summary})
                st.rerun()

            completed_followup_run = focus_run if isinstance(focus_run, dict) else active_run
            if (not is_exec_trigger) and should_route_completed_run_followup(
                completed_followup_run,
                user_text,
            ):
                response = build_completed_run_followup_response(
                    completed_followup_run,
                    user_text,
                )
                if response:
                    if not user_logged:
                        session["messages"].append({"role": "user", "content": user_text})
                        user_logged = True
                    session["messages"].append({"role": "assistant", "content": response})
                    completed_followup_run.setdefault("conversation", []).append(
                        {"user": user_text, "assistant": response}
                    )
                    append_model_trace(
                        completed_followup_run,
                        "Completed-run follow-up answered deterministically.",
                    )
                    st.rerun()

            if is_exec_trigger:
                append_model_trace(active_run, "Auto-start trigger detected in chat message.")
                if not user_logged:
                    session["messages"].append({"role": "user", "content": user_text})
                    user_logged = True

                reused_existing, reuse_msg = try_start_existing_plan_from_proceed(active_run, orchestrator, user_text)
                if reused_existing:
                    if reuse_msg:
                        session["messages"].append({"role": "assistant", "content": reuse_msg})
                        active_run["conversation"].append({"user": user_text, "assistant": reuse_msg})
                        append_model_trace(active_run, f"Proceed reused existing plan: {reuse_msg}")
                    st.rerun()
                elif should_create_fresh_run_for_proceed(active_run):
                    active_run = new_plan_run(initial_request=user_text)
                    append_model_trace(
                        active_run,
                        "Created a fresh run for proceed-triggered execution planning.",
                    )
                    session_id = st.session_state.orchestrator_session_id
                    session = orchestrator.get_or_create_session(session_id)
                    ensure_user_message_in_session(session, user_text)
                    user_logged = True

                snapshot_after_turn = orchestrator.session_snapshot(session_id)
                chat_data_root = st.session_state.get("chat_data_root", st.session_state.selected_dir)
                chat_include_subdirs = bool(st.session_state.get("chat_include_subdirs", False))
                chat_filter = st.session_state.get("chat_filename_filter", "").strip()
                chat_max_files = int(st.session_state.get("chat_max_files", 200))

                benchmark_policy = ui_benchmark_policy()
                benchmark_prompt_active = is_ui_benchmark_prompt(user_text)
                benchmark_data_root = (
                    extract_ui_benchmark_data_root(user_text) if benchmark_prompt_active else None
                )
                if benchmark_data_root:
                    resolved_root = benchmark_data_root
                    resolved_count = _count_fastq_in_dir(resolved_root, max_files=max(chat_max_files, 2000))
                    resolved_reason = "benchmark_prompt_data_root"
                    st.session_state["path_resolution"] = {
                        "user_requested_root": chat_data_root,
                        "resolved_root": resolved_root,
                        "resolution_reason": resolved_reason,
                        "rejected_candidates": [],
                    }
                else:
                    resolved_root, resolved_count, resolved_reason = resolve_best_data_root(
                        chat_data_root,
                        snapshot_after_turn.get("messages", []),
                        max_files=max(chat_max_files, 2000),
                    )
                st.session_state["chat_data_root"] = resolved_root
                active_run["requested_data_root"] = str(resolved_root)
                exec_note = (
                    "Execution requested. Starting now.\n"
                    f"- Data root: `{resolved_root}`\n"
                    f"- FASTQ detected: `{resolved_count}` ({resolved_reason})"
                )
                session["messages"].append({"role": "assistant", "content": exec_note})
                active_run["conversation"].append({"user": user_text, "assistant": exec_note})
                active_run["status"] = "planning"
                active_run["error"] = ""
                planning_run_files = ensure_planning_run_initialized(
                    active_run,
                    workspace_root=WORKSPACE_ROOT,
                    selected_dir=str(st.session_state.selected_dir),
                    requested_data_root=str(resolved_root),
                    execution_options={
                        "use_test_subset": bool(st.session_state.get("chat_use_test_subset", True)),
                        "test_subset_reads_per_fastq": int(st.session_state.get("chat_test_subset_reads", 1000000)),
                    },
                    benchmark_policy=ui_benchmark_policy(),
                )
                effective_selected_dir = resolve_effective_chat_selected_dir(
                    active_run,
                    session_selected_dir=str(st.session_state.selected_dir),
                    benchmark_policy=ui_benchmark_policy(),
                )
                if effective_selected_dir != str(st.session_state.selected_dir):
                    planning_run_files = ensure_planning_run_initialized(
                        active_run,
                        workspace_root=WORKSPACE_ROOT,
                        selected_dir=effective_selected_dir,
                        requested_data_root=str(resolved_root),
                        execution_options={
                            "use_test_subset": bool(st.session_state.get("chat_use_test_subset", True)),
                            "test_subset_reads_per_fastq": int(st.session_state.get("chat_test_subset_reads", 1000000)),
                        },
                        benchmark_policy=ui_benchmark_policy(),
                    )
                active_run["selected_dir"] = effective_selected_dir
                persist_run_state(active_run)
                st.session_state.latest_run_badge = "Planning"
                append_model_trace(
                    active_run,
                    f"Persisted planning run artifacts in {planning_run_files.get('run_dir', '')}.",
                )

                discovered_files_auto = discover_fastq_files(
                    resolved_root,
                    chat_include_subdirs,
                    chat_filter,
                    chat_max_files,
                )
                selected_files_auto = st.session_state.get("chat_selected_files", [])
                files_for_plan_auto = selected_files_auto if selected_files_auto else discovered_files_auto

                if benchmark_prompt_active:
                    request_with_scope = concretize_ui_benchmark_prompt(
                        user_text,
                        selected_dir=effective_selected_dir,
                        benchmark_policy=benchmark_policy,
                    )
                    recent_user_msgs = [request_with_scope]
                else:
                    request_with_scope = build_request_from_session(snapshot_after_turn, user_text)
                    if files_for_plan_auto:
                        request_with_scope += (
                            "\n\nUse ONLY these input files (do not recurse beyond this list):\n"
                            + "\n".join(files_for_plan_auto)
                        )
                    recent_user_msgs = [
                        str(m.get("content", ""))
                        for m in reversed(snapshot_after_turn.get("messages", []))
                        if str(m.get("role", "")) == "user"
                    ]
                request_with_scope = str(request_with_scope or "").strip()
                if not request_with_scope:
                    raise BioHarnessError(
                        "Cannot start execution planning from an empty chat request. "
                        "Please retry with a concrete instruction."
                    )

                direct_request_contract = infer_contract_with_fallback(
                    primary_text=request_with_scope if benchmark_prompt_active else user_text,
                    fallback_texts=[],
                    existing={},
                )
                scoped_contract = infer_contract_with_fallback(
                    primary_text=request_with_scope,
                    fallback_texts=[] if benchmark_prompt_active else [request_with_scope] + recent_user_msgs[:6],
                    existing=active_run.get("plan_contract", {}),
                )
                ui_timeout = int(os.getenv("BIO_HARNESS_UI_PLAN_TIMEOUT_SECONDS", "45"))
                active_run["planning_timeout_seconds"] = ui_timeout
                active_run["user_request"] = request_with_scope
                active_run["planner_status"] = "planning"
                active_run["planning_started_at"] = datetime.now().isoformat()
                planning_run_files = ensure_planning_run_initialized(
                    active_run,
                    workspace_root=WORKSPACE_ROOT,
                    selected_dir=effective_selected_dir,
                    requested_data_root=str(resolved_root),
                    execution_options={
                        "use_test_subset": bool(st.session_state.get("chat_use_test_subset", True)),
                        "test_subset_reads_per_fastq": int(st.session_state.get("chat_test_subset_reads", 1000000)),
                    },
                    benchmark_policy=ui_benchmark_policy(),
                )
                persist_run_state(active_run)
                _plan_cancel = threading.Event()
                planning_started = launch_planner_job(
                    active_run,
                    planning_fn=lambda: build_ui_execution_plan(
                        planner_call=orchestrator.think,
                        orchestrator=orchestrator,
                        user_text=user_text,
                        request_with_scope=request_with_scope,
                        benchmark_prompt_active=benchmark_prompt_active,
                        benchmark_policy=benchmark_policy,
                        direct_request_contract=direct_request_contract,
                        scoped_contract=scoped_contract,
                        selected_dir=effective_selected_dir,
                        data_root=str(resolved_root),
                        project_root=str(PROJECT_ROOT),
                        timeout_seconds=ui_timeout,
                        cancel_event=_plan_cancel,
                    ),
                    timeout_seconds=ui_timeout,
                    cancel_event=_plan_cancel,
                )
                if planning_started:
                    append_model_trace(active_run, "Background planning job launched.")
                else:
                    append_model_trace(active_run, "Planning request reused an existing live planner job.")
            else:
                append_model_trace(active_run, "Interactive turn started.")
                with st.spinner("Orchestrator is thinking..."):
                    turn = orchestrator.interactive_turn(
                        session_id=session_id,
                        user_message=user_text,
                        data_root=st.session_state.get("chat_data_root", st.session_state.selected_dir),
                        include_subdirs=bool(st.session_state.get("chat_include_subdirs", False)),
                        policy_context={
                            "trusted_reference_domains": st.session_state.get("trusted_reference_domains", []),
                        },
                    )
                active_run["conversation"].append({"user": user_text, "assistant": turn["assistant_message"]})
                active_run["context_snapshots"].append(turn.get("context", {}))
                append_model_trace(
                    active_run,
                    f"Interactive turn finished (token_load={turn.get('token_load_estimate')}, compactions={turn.get('compactions')}).",
                )
            st.rerun()
        except BioHarnessError as exc:
            append_model_trace(active_run, f"Interactive turn failed: {exc}")
            if active_run.get("run_files"):
                active_run["status"] = "failed"
                active_run["error"] = str(exc)
                persist_run_state(active_run)
                write_terminal_artifacts_if_needed(active_run)
            st.error(str(exc))
            st.info(
                "Check the selected backend, host, model name, and localhost permissions. "
                "For Ollama, verify the model is pulled (for example `qwen3-coder-next`)."
            )
        except Exception as exc:
            append_model_trace(active_run, f"Interactive turn failed: {exc}")
            st.error(f"Conversation turn failed: {exc}")
        finally:
            mark_model_end()

    st.caption(
        "Ask in chat to plan, execute, inspect results, or recover runs. "
        "The contextual panel will open when it helps."
    )
    st.session_state.plan_last_poll_at = time.time()

if current_dock_view == "Extend":
    with tab_skills:
        st.subheader("Skill Library")
        st.caption("Create and manage reusable SKILL.md definitions without using the model.")
    
        st.markdown("### Capability Registry")
        catalog = load_capability_catalog_data()
        cap_query = st.text_input(
            "Search capabilities",
            value="",
            key="capability_search_query",
            placeholder="e.g., differential, variant, single-cell",
        )
        cap_rows = catalog.get("capabilities", []) if isinstance(catalog.get("capabilities", []), list) else []
        filtered_caps = []
        q = cap_query.strip().lower()
        for cap in cap_rows:
            cap_id = str(cap.get("id", ""))
            name = str(cap.get("name", ""))
            desc = str(cap.get("description", ""))
            if q and q not in cap_id.lower() and q not in name.lower() and q not in desc.lower():
                continue
            filtered_caps.append(cap)
        if not filtered_caps:
            st.info("No capabilities matched the filter.")
        for cap in filtered_caps:
            cap_id = normalize_capability_id(cap.get("id", ""))
            label = f"{cap.get('name', cap_id)} (`{cap_id}`)"
            key = f"cap_enabled_{cap_id}"
            st.checkbox(
                label,
                value=bool(cap.get("enabled", True)),
                key=key,
                help=str(cap.get("description", "")),
            )
        cap_col1, cap_col2 = st.columns(2)
        with cap_col1:
            if st.button("Save Capability Settings"):
                updated = load_capability_catalog_data()
                for cap in updated.get("capabilities", []):
                    cap_id = normalize_capability_id(cap.get("id", ""))
                    key = f"cap_enabled_{cap_id}"
                    if key in st.session_state:
                        cap["enabled"] = bool(st.session_state.get(key))
                save_capability_catalog_data(updated)
                st.success("Capability settings saved.")
        with cap_col2:
            if st.button("Reset Capability Catalog to Defaults"):
                if CAPABILITY_CATALOG_PATH.exists():
                    CAPABILITY_CATALOG_PATH.unlink()
                load_capability_catalog_data()
                st.success("Capability catalog reset to defaults.")
    
        st.markdown("#### Add Custom Capability")
        with st.form("add_custom_capability_form"):
            new_cap_id = st.text_input("Capability ID", placeholder="e.g., fusion_detection")
            new_cap_name = st.text_input("Capability Name", placeholder="e.g., Fusion Detection")
            new_cap_desc = st.text_area("Description", placeholder="What this capability means operationally.")
            new_cap_keywords = st.text_input("Keywords (comma-separated)", placeholder="fusion,star-fusion,arriba")
            new_cap_signals = st.text_input("Plan Signals (comma-separated)", placeholder="star-fusion,arriba,fusion")
            new_cap_hints = st.text_input("Tool Hints (comma-separated)", placeholder="star-fusion,arriba")
            new_cap_enabled = st.checkbox("Enabled", value=True)
            add_cap = st.form_submit_button("Add Capability")
        if add_cap:
            cid = normalize_capability_id(new_cap_id)
            if not cid:
                st.error("Capability ID is required.")
            else:
                cat = load_capability_catalog_data()
                existing = capability_index(cat, enabled_only=False)
                if cid in existing:
                    st.error(f"Capability `{cid}` already exists.")
                else:
                    cap_item = {
                        "id": cid,
                        "name": new_cap_name.strip() or cid.replace("_", " ").title(),
                        "description": new_cap_desc.strip(),
                        "enabled": bool(new_cap_enabled),
                        "keywords": [x.strip().lower() for x in new_cap_keywords.split(",") if x.strip()],
                        "plan_signals": [x.strip().lower() for x in new_cap_signals.split(",") if x.strip()],
                        "tool_hints": [x.strip().lower() for x in new_cap_hints.split(",") if x.strip()],
                    }
                    cat_caps = cat.get("capabilities", []) if isinstance(cat.get("capabilities", []), list) else []
                    cat_caps.append(cap_item)
                    cat["capabilities"] = cat_caps
                    save_capability_catalog_data(cat)
                    st.success(f"Added capability `{cid}`.")
    
        registry = SkillRegistry(SKILLS_DEFINITIONS)
        col_idx, col_reload = st.columns(2)
        with col_idx:
            if st.button("Refresh Skill Index"):
                index_path = registry.generate_index()
                st.success(f"Generated {index_path}")
        with col_reload:
            if st.button("Reload Skills"):
                registry.load_skills()
                try:
                    get_orchestrator.clear()
                except Exception:
                    pass
                st.success("Skills reloaded.")
    
        st.markdown("### Existing Skills")
        skill_rows = []
        for skill_name, data in sorted(registry._skills.items()):
            skill_rows.append(
                {
                    "name": skill_name,
                    "risk_level": data.get("risk_level", "unknown"),
                    "description": data.get("description", ""),
                    "file_path": data.get("file_path", ""),
                }
            )
        st.dataframe(skill_rows, use_container_width=True)
    
        st.markdown("### Create New Skill")
        with st.form("new_skill_form"):
            name = st.text_input("Skill Name", placeholder="e.g., star_align")
            description = st.text_area("Description", placeholder="What does this skill do?")
            risk_level = st.selectbox("Risk Level", ["low", "medium", "high"], index=1)
            tools_required = st.text_input("Tools Required (comma-separated)", placeholder="star,samtools")
            capability_tags = st.text_input(
                "Capability Tags (comma-separated)",
                placeholder="alignment,differential_analysis",
            )
            params_yaml = st.text_area(
                "Parameters (YAML mapping)",
                value=textwrap.dedent(
                    """\
                    input_fastq:
                      type: path
                      description: Input FASTQ file
                      required: true
                    output_dir:
                      type: path
                      description: Output directory
                      required: true
                    """
                ),
            )
            usage_guide = st.text_area("Usage Guide", value="Describe common usage and pitfalls.")
            submitted = st.form_submit_button("Create Skill")
    
        if submitted:
            skill_name = name.strip()
            if not skill_name:
                st.error("Skill name is required.")
            else:
                try:
                    parsed_params = yaml.safe_load(params_yaml) or {}
                    if not isinstance(parsed_params, dict):
                        raise ValueError("Parameters must parse to a YAML mapping/object.")
    
                    metadata = {
                        "name": skill_name,
                        "description": description.strip(),
                        "risk_level": risk_level,
                        "tools_required": [t.strip() for t in tools_required.split(",") if t.strip()],
                        "capabilities": [normalize_capability_id(c) for c in capability_tags.split(",") if normalize_capability_id(c)],
                        "parameters": parsed_params,
                    }
                    valid, errors = registry.validate_skill_metadata(metadata)
                    if not valid:
                        st.error("Invalid skill metadata: " + "; ".join(errors))
                    else:
                        frontmatter_doc = "---\n"
                        frontmatter_doc += yaml.safe_dump(metadata, sort_keys=False)
                        frontmatter_doc += "---\n"
                        frontmatter_doc += usage_guide.strip() + "\n"
    
                        out_path = SKILLS_DEFINITIONS / f"{skill_name}.md"
                        out_path.write_text(frontmatter_doc, encoding="utf-8")
                        library_path = SKILLS_LIBRARY / f"{skill_name}.py"
                        if not library_path.exists():
                            default_tool = metadata["tools_required"][0] if metadata["tools_required"] else skill_name
                            library_path.write_text(
                                build_generic_skill_library_stub(skill_name, default_tool),
                                encoding="utf-8",
                            )
                        if metadata.get("capabilities"):
                            cap_catalog = load_capability_catalog_data()
                            cap_catalog = update_capability_tool_hints(
                                cap_catalog,
                                capability_ids=metadata["capabilities"],
                                tool_hints=metadata["tools_required"] or [skill_name],
                                plan_signals=[skill_name],
                            )
                            save_capability_catalog_data(cap_catalog)
                        registry.load_skills()
                        registry.generate_index()
                        try:
                            get_orchestrator.clear()
                        except Exception:
                            pass
                        st.success(f"Created skill file: {out_path} and executable stub: {library_path}")
                except Exception as exc:
                    st.error(f"Could not create skill: {exc}")
    
        st.markdown("### Controlled Tool Onboarding")
        st.caption(
            "Controlled flow: source analyze -> draft review -> explicit approval/install. "
            "Use this to add custom tool capabilities from a GitHub/manual/PDF source."
        )
        st.markdown("#### Curated Batch Onboarding")
        curated_labels = [
            f"{batch.get('priority', '?')}. {batch.get('title', batch.get('id', 'batch'))} ({batch.get('id', '')})"
            for batch in CURATED_TOOL_BATCHES
        ]
        selected_batch_label = st.selectbox(
            "Curated batch",
            options=curated_labels,
            index=0 if curated_labels else None,
            key="curated_batch_select",
        )
        selected_batch = None
        if selected_batch_label:
            for batch in CURATED_TOOL_BATCHES:
                label = f"{batch.get('priority', '?')}. {batch.get('title', batch.get('id', 'batch'))} ({batch.get('id', '')})"
                if label == selected_batch_label:
                    selected_batch = batch
                    break
        if selected_batch:
            st.caption(str(selected_batch.get("description", "")))
            curated_preview = []
            for tool in selected_batch.get("tools", []):
                if not isinstance(tool, dict):
                    continue
                draft_item = tool.get("draft", {}) if isinstance(tool.get("draft", {}), dict) else {}
                curated_preview.append(
                    {
                        "skill_name": str(draft_item.get("skill_name", "")),
                        "capabilities": ",".join([str(c) for c in draft_item.get("capabilities", [])]),
                        "risk_level": str(draft_item.get("risk_level", "medium")),
                        "source": str((tool.get("source_meta", {}) or {}).get("source", "")),
                    }
                )
            if curated_preview:
                st.dataframe(curated_preview, use_container_width=True)
            batch_install_key = f"install_curated_batch_{selected_batch.get('id', 'unknown')}"
            if st.button("Install Selected Curated Batch", key=batch_install_key):
                try:
                    report = install_curated_batch(
                        str(selected_batch.get("id", "")),
                        skills_definitions_dir=SKILLS_DEFINITIONS,
                        skills_library_dir=SKILLS_LIBRARY,
                        capability_catalog_path=CAPABILITY_CATALOG_PATH,
                        record_custom_tool=True,
                    )
                    installed_count = len(report.get("installed", []))
                    failed_count = len(report.get("failed", []))
                    if installed_count:
                        st.success(f"Installed {installed_count} skill(s) from batch `{selected_batch.get('id', '')}`.")
                    if failed_count:
                        st.error(f"{failed_count} skill(s) failed validation or install.")
                        st.json(report.get("failed", []))
                    try:
                        get_orchestrator.clear()
                    except Exception:
                        pass
                except Exception as exc:
                    st.error(f"Curated batch install failed: {exc}")
    
        st.markdown("#### Source-Driven Draft Onboarding")
        source_ref = st.text_input(
            "Tool source URL or local path",
            key="tool_onboard_source_ref",
            placeholder="https://github.com/... or /path/to/manual.pdf",
        )
        tool_name_hint = st.text_input(
            "Tool name hint (optional)",
            key="tool_onboard_name_hint",
            placeholder="e.g., deseq2, salmon, custom_tool",
        )
        if st.button("Analyze Source and Create Draft"):
            try:
                reader = get_reader(model_name, resolved_host, llm_backend)
                mark_model_start()
                with st.spinner("Analyzing source and drafting capability/skill..."):
                    source_text, source_meta = load_tool_source_text(source_ref, reader=reader)
                    draft = build_tool_onboarding_draft(
                        source_ref=source_ref,
                        source_text=source_text,
                        tool_name_hint=tool_name_hint,
                        active_catalog=load_capability_catalog_data(),
                    )
                    st.session_state.capability_onboarding_draft = draft
                    st.session_state.capability_onboarding_source = source_meta
                st.success("Draft created. Review and approve below.")
            except Exception as exc:
                st.error(f"Tool onboarding analysis failed: {exc}")
            finally:
                mark_model_end()
    
        draft = st.session_state.get("capability_onboarding_draft", {})
        source_meta = st.session_state.get("capability_onboarding_source", {})
        if isinstance(draft, dict) and draft:
            st.markdown("#### Draft Review")
            with st.form("tool_onboarding_review_form"):
                d_skill_name = st.text_input("Skill name", value=str(draft.get("skill_name", "")))
                d_description = st.text_area("Description", value=str(draft.get("description", "")))
                d_risk = st.selectbox(
                    "Risk level",
                    ["low", "medium", "high"],
                    index=["low", "medium", "high"].index(str(draft.get("risk_level", "medium")).lower())
                    if str(draft.get("risk_level", "medium")).lower() in {"low", "medium", "high"}
                    else 1,
                )
                d_tools_required = st.text_input(
                    "Tools required (comma-separated)",
                    value=",".join([str(x) for x in draft.get("tools_required", [])]),
                )
                cap_options = sorted(capability_index(load_capability_catalog_data(), enabled_only=False).keys())
                d_caps = st.multiselect(
                    "Capability tags",
                    options=cap_options,
                    default=[normalize_capability_id(x) for x in draft.get("capabilities", []) if normalize_capability_id(x)],
                )
                d_params_yaml = st.text_area(
                    "Parameters (YAML mapping)",
                    value=yaml.safe_dump(draft.get("parameters", {}), sort_keys=False),
                    height=200,
                )
                d_command_template = st.text_area(
                    "Command template (optional, Python format placeholders allowed)",
                    value=str(draft.get("command_template", "")),
                )
                d_usage = st.text_area("Usage guide", value=str(draft.get("usage_guide", "")), height=200)
                approve = st.form_submit_button("Approve and Install Draft")
    
            if approve:
                try:
                    parsed_params = yaml.safe_load(d_params_yaml) or {}
                    if not isinstance(parsed_params, dict):
                        raise ValueError("Parameters must parse to a mapping/object.")
                    draft_payload = {
                        "skill_name": _slugify_skill_name(d_skill_name),
                        "description": d_description.strip(),
                        "risk_level": d_risk,
                        "tools_required": [x.strip() for x in d_tools_required.split(",") if x.strip()],
                        "capabilities": [normalize_capability_id(x) for x in d_caps if normalize_capability_id(x)],
                        "parameters": parsed_params,
                        "command_template": d_command_template.strip(),
                        "usage_guide": d_usage.strip(),
                    }
                    ok, msg = install_tool_onboarding_draft(draft_payload, source_meta if isinstance(source_meta, dict) else {})
                    if ok:
                        st.success(msg)
                        st.session_state.capability_onboarding_draft = {}
                        st.session_state.capability_onboarding_source = {}
                    else:
                        st.error(msg)
                except Exception as exc:
                    st.error(f"Install failed: {exc}")
    
if current_dock_view == "Guide":
    with tab_docs:
        st.subheader("Scientific PDFs")
        st.caption("Upload a paper PDF, then generate summary and extracted pipeline steps.")
        pdf_file = st.file_uploader("Upload PDF", type=["pdf"], key="pdf_uploader")
        if pdf_file is not None:
            docs_dir = Path(st.session_state.selected_dir) / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = docs_dir / pdf_file.name
            pdf_path.write_bytes(pdf_file.getbuffer())
            st.success(f"Saved: {pdf_path}")
    
            if st.button("Analyze PDF"):
                reader = get_reader(model_name, resolved_host, llm_backend)
                mark_model_start()
                with st.spinner("Extracting text and running model analysis..."):
                    try:
                        md = reader.pdf_to_markdown(pdf_path)
                        summary = reader.summarize_markdown(md)
                        steps = reader.extract_pipeline_logic(md)
                        st.markdown("### Summary")
                        st.markdown(summary)
                        st.markdown("### Pipeline Steps")
                        st.json(steps)
                    except Exception as exc:
                        st.error(f"PDF analysis failed: {exc}")
                    finally:
                        mark_model_end()
    
if current_dock_view == "Guide":
    with tab_links:
        st.subheader("Reference & Link Tools")
        st.caption("Trusted reference domains are enforced for direct downloads into workspace.")
    
        st.markdown("### Trusted Reference Domains")
        domains = st.session_state.get("trusted_reference_domains", [])
        st.code("\n".join(domains) if domains else "(none)", language="text")
        col_dom_add, col_dom_remove = st.columns(2)
        with col_dom_add:
            add_domain = st.text_input("Add domain", placeholder="example.org", key="add_trusted_domain")
            if st.button("Add Domain"):
                norm = normalize_domain(add_domain)
                if not norm:
                    st.error("Enter a valid domain.")
                elif norm in domains:
                    st.info("Domain already trusted.")
                else:
                    st.session_state.trusted_reference_domains = sorted(domains + [norm])
                    st.success(f"Added: {norm}")
                    st.rerun()
        with col_dom_remove:
            remove_domain = st.selectbox("Remove trusted domain", options=domains if domains else [""], key="remove_trusted_domain")
            if st.button("Remove Domain", disabled=not bool(domains)):
                st.session_state.trusted_reference_domains = [d for d in domains if d != remove_domain]
                st.success(f"Removed: {remove_domain}")
                st.rerun()
    
        st.markdown("### Download Reference (Trusted Domains Only)")
        ref_url = st.text_input(
            "Reference URL",
            placeholder="https://www.encodeproject.org/... or https://ftp.ensembl.org/...",
            key="reference_download_url",
        )
        if st.button("Download to workspace/references"):
            try:
                out = download_reference_to_workspace(
                    ref_url.strip(),
                    st.session_state.get("trusted_reference_domains", []),
                    WORKSPACE_ROOT / "references",
                )
                st.success(f"Downloaded: {out}")
            except Exception as exc:
                st.error(f"Download failed: {exc}")
    
        st.markdown("---")
        st.markdown("### Analyze Paper / Repo URL")
        url = st.text_input("URL", placeholder="https://... paper or GitHub repo", key="analyze_url_input")
        if st.button("Analyze URL") and url.strip():
            mark_model_start()
            with st.spinner("Fetching and analyzing link..."):
                try:
                    result = analyze_link(url.strip())
                    st.markdown(result)
                except Exception as exc:
                    st.error(f"URL analysis failed: {exc}")
                finally:
                    mark_model_end()
    
if current_dock_view == "Visuals":
    with tab_pipeline:
        with st.expander("Manual pipeline export and upload", expanded=False):
            st.caption("Keep manual pipeline controls available without making them a permanent part of the default UI.")
            graph = graphviz.Digraph(comment="Active Bioinformatics Pipeline", graph_attr={"rankdir": "LR"})
            graph.node("A", "FastQC", shape="box")
            graph.node("B", "Aligner", shape="box")
            graph.node("C", "Variant / DE Analysis", shape="box")
            graph.edge("A", "B", label="Filtered Reads")
            graph.edge("B", "C", label="BAM/Counts")
            st.graphviz_chart(graph)
            st.markdown("### Reproducible Pipeline Files")
            st.caption("Export a runnable shell script so users can run pipelines without the agent.")
    
            if st.session_state.last_plan is not None:
                if st.button("Export Last Plan as Script"):
                    try:
                        orchestrator = get_orchestrator(model_name, resolved_host, llm_backend)
                        manifest_dir = Path(st.session_state.selected_dir) / "manifests"
                        script_path = manifest_dir / "pipeline.sh"
                        out_path = orchestrator.export_plan_script(st.session_state.last_plan, script_path)
                        st.success(f"Exported script: {out_path}")
                    except Exception as exc:
                        st.error(f"Export failed: {exc}")
            else:
                st.info("No plan is currently available to export.")
    
            st.markdown("### Manual Plan Upload")
            st.caption("Upload a JSON plan and execute it directly (no planning call required).")
            uploaded_plan = st.file_uploader("Upload plan JSON", type=["json"], key="plan_upload")
            if uploaded_plan is not None:
                try:
                    plan_json = yaml.safe_load(uploaded_plan.getvalue().decode("utf-8"))
                    st.json(plan_json)
                    if st.button("Run Uploaded Plan"):
                        orchestrator = get_orchestrator(model_name, resolved_host, llm_backend)
                        uploaded_run = new_plan_run("Uploaded plan execution")
                        uploaded_run["plan"] = plan_json
                        uploaded_run["status"] = "planned"
                        st.session_state.last_plan = plan_json
                        start_plan_execution(uploaded_run, orchestrator)
                        st.rerun()
                except Exception as exc:
                    st.error(f"Invalid plan JSON: {exc}")
