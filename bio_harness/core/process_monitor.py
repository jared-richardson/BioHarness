from __future__ import annotations

import re
import shlex
import os
from pathlib import Path
from typing import Any

import psutil

SHELL_WRAPPERS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
LANG_WRAPPERS = {"python", "python3", "perl", "ruby", "rscript", "java", "node"}
GENERIC_WRAPPERS = SHELL_WRAPPERS | LANG_WRAPPERS | {"env", "time", "command", "nohup"}
SHELL_KEYWORDS = {"if", "then", "else", "fi", "for", "do", "done", "while", "case", "esac"}
SEGMENT_SPLIT_RE = re.compile(r"(?:\|\||&&|[|;\n])")
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
DEFAULT_OUTPUT_EXCLUDE_DIRS = {"runs", "inputs_readonly", ".git", "__pycache__"}


def _safe_split(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except Exception:
        return raw.split()


def _normalize_candidate(token: str) -> str:
    raw = str(token or "").strip()
    if not raw:
        return ""
    base = Path(raw).name.strip().lower()
    if not base:
        return ""
    if base.startswith("-"):
        return ""
    if base in SHELL_KEYWORDS:
        return ""
    if base in {"(", ")", "{", "}"}:
        return ""
    if base.endswith((".py", ".pl", ".rb", ".sh", ".jar")):
        stem = Path(base).stem.lower()
        if stem in GENERIC_WRAPPERS or not stem:
            return ""
        return stem
    if base in GENERIC_WRAPPERS:
        return ""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.+-]*", base):
        return ""
    return base


def _strip_segment_prefix(tokens: list[str]) -> list[str]:
    idx = 0
    while idx < len(tokens):
        token = str(tokens[idx]).strip()
        low = Path(token).name.lower()
        if ASSIGNMENT_RE.match(token):
            idx += 1
            continue
        if low in SHELL_KEYWORDS or low in {"(", ")", "{", "}"}:
            idx += 1
            continue
        break
    return tokens[idx:]


def _extract_candidates_from_tokens(tokens: list[str]) -> list[str]:
    clean = _strip_segment_prefix(tokens)
    if not clean:
        return []

    head = Path(clean[0]).name.lower()

    if head in {"env", "time", "command", "nohup"}:
        return _extract_candidates_from_tokens(clean[1:])

    if head in SHELL_WRAPPERS:
        if len(clean) >= 3 and clean[1] in {"-c", "-lc"}:
            return _extract_candidates_from_text(clean[2])
        return []

    if head == "java":
        try:
            idx = clean.index("-jar")
        except ValueError:
            return []
        if idx + 1 < len(clean):
            cand = _normalize_candidate(clean[idx + 1])
            return [cand] if cand else []
        return []

    if head in {"python", "python3", "perl", "ruby", "rscript", "node"}:
        if len(clean) >= 2 and not str(clean[1]).startswith("-"):
            cand = _normalize_candidate(clean[1])
            if cand:
                return [cand]
        return []

    cand = _normalize_candidate(clean[0])
    return [cand] if cand else []


def _extract_candidates_from_text(text: str) -> list[str]:
    candidates: list[str] = []
    for segment in SEGMENT_SPLIT_RE.split(str(text or "")):
        part = segment.strip()
        if not part:
            continue
        tokens = _safe_split(part)
        if not tokens:
            continue
        candidates.extend(_extract_candidates_from_tokens(tokens))
    return candidates


def infer_process_label(command_hint: str = "", runtime_cmdlines: list[str] | None = None) -> str:
    seen: set[str] = set()
    ordered: list[str] = []

    for source in (runtime_cmdlines or []):
        for cand in _extract_candidates_from_text(source):
            if cand and cand not in seen:
                seen.add(cand)
                ordered.append(cand)

    for cand in _extract_candidates_from_text(command_hint):
        if cand and cand not in seen:
            seen.add(cand)
            ordered.append(cand)

    return ordered[0] if ordered else "unknown"


def collect_process_snapshot(
    pid: int | None,
    *,
    command_hint: str = "",
    max_processes: int = 6,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "pid": int(pid) if isinstance(pid, int) and pid > 0 else None,
        "alive": False,
        "live_process_count": 0,
        "inferred_tool": "unknown",
        "tree_cpu_seconds": 0.0,
        "tree_rss_mb": 0.0,
        "top_processes": [],
    }
    if not isinstance(pid, int) or pid <= 0:
        return snapshot

    try:
        root = psutil.Process(pid)
        process_tree = [root] + root.children(recursive=True)
    except Exception:
        return snapshot

    live: list[dict[str, Any]] = []
    cmdlines: list[str] = []
    total_cpu = 0.0
    total_rss = 0.0
    for proc in process_tree:
        try:
            if not proc.is_running():
                continue
            status = str(proc.status()).lower()
            if status in {"zombie", "dead"}:
                continue
            cmd = " ".join(proc.cmdline()).strip()
            if not cmd:
                cmd = proc.name()
            if cmd:
                cmdlines.append(cmd)
            cpu_times = proc.cpu_times()
            cpu_total = float(getattr(cpu_times, "user", 0.0) + getattr(cpu_times, "system", 0.0))
            rss_bytes = int(getattr(proc.memory_info(), "rss", 0))
            rss_mb = round(rss_bytes / (1024 * 1024), 1)
            total_cpu += cpu_total
            total_rss += max(0.0, float(rss_mb))
            live.append(
                {
                    "pid": proc.pid,
                    "name": proc.name(),
                    "status": status,
                    "cpu_seconds": round(cpu_total, 2),
                    "rss_mb": rss_mb,
                    "cmdline": cmd,
                }
            )
        except Exception:
            continue

    live_sorted = sorted(live, key=lambda x: (float(x.get("rss_mb", 0.0)), float(x.get("cpu_seconds", 0.0))), reverse=True)
    top = []
    for item in live_sorted[: max(1, int(max_processes))]:
        top.append(
            {
                "pid": item["pid"],
                "name": item["name"],
                "status": item["status"],
                "cpu_seconds": item["cpu_seconds"],
                "rss_mb": item["rss_mb"],
                "cmdline_head": str(item["cmdline"])[:180],
            }
        )

    snapshot.update(
        {
            "alive": bool(live),
            "live_process_count": len(live),
            "inferred_tool": infer_process_label(command_hint=command_hint, runtime_cmdlines=cmdlines),
            "tree_cpu_seconds": round(total_cpu, 2),
            "tree_rss_mb": round(total_rss, 1),
            "top_processes": top,
        }
    )
    return snapshot


def collect_recent_outputs(
    root_dir: str | Path,
    *,
    since_ts: float,
    max_files: int = 8,
    max_scan: int = 25000,
    exclude_dirs: set[str] | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).expanduser()
    if not root.exists() or not root.is_dir():
        return {"latest_mtime": 0.0, "recent_files": [], "scanned_files": 0}

    excludes = set(DEFAULT_OUTPUT_EXCLUDE_DIRS)
    if exclude_dirs:
        excludes.update(str(x) for x in exclude_dirs)

    scanned = 0
    recent: list[dict[str, Any]] = []
    latest_mtime = 0.0
    root_resolved = root.resolve()

    for current_root, dirs, files in os.walk(root_resolved, topdown=True):
        dirs[:] = [d for d in dirs if d not in excludes]
        for file_name in files:
            scanned += 1
            if scanned > max_scan:
                break
            fp = Path(current_root) / file_name
            try:
                st = fp.stat()
            except Exception:
                continue
            mtime = float(getattr(st, "st_mtime", 0.0))
            if mtime <= 0:
                continue
            if mtime > latest_mtime:
                latest_mtime = mtime
            if mtime < since_ts:
                continue
            try:
                rel = fp.relative_to(root_resolved)
                ptxt = str(rel)
            except Exception:
                ptxt = str(fp)
            recent.append(
                {
                    "path": ptxt,
                    "size_bytes": int(getattr(st, "st_size", 0)),
                    "mtime": mtime,
                }
            )
        if scanned > max_scan:
            break

    recent_sorted = sorted(recent, key=lambda x: float(x.get("mtime", 0.0)), reverse=True)[: max(1, int(max_files))]
    for item in recent_sorted:
        item["mtime_epoch"] = round(float(item["mtime"]), 3)
        item.pop("mtime", None)

    return {
        "latest_mtime": latest_mtime,
        "recent_files": recent_sorted,
        "scanned_files": scanned,
    }
