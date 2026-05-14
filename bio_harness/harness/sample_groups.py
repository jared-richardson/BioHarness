"""Sample group tracking and discovery utilities."""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.shell_parse import split_shell_segments
from bio_harness.harness.path_utils import (
    _extract_paths_from_argument_value,
    _extract_paths_from_command,
    _looks_like_path_token,
    _path_has_positive_group_evidence,
    _path_text_has_group_evidence,
    _resolve_candidate_path,
    _text_mentions_group_token,
)


def _normalize_group_label(label: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "_", str(label or "").strip().lower()).strip("_")
    if not raw:
        return ""
    aliases = {
        "ctl": "control",
        "ctrl": "control",
        "treated": "treatment",
        "tx": "treatment",
        "trt": "treatment",
    }
    return aliases.get(raw, raw)


def _ensure_group_tracking(run: dict[str, Any]) -> None:
    if not isinstance(run.get("missing_sample_group_signals"), list):
        run["missing_sample_group_signals"] = []
    if not isinstance(run.get("observed_sample_groups"), list):
        run["observed_sample_groups"] = []
    if not isinstance(run.get("observed_sample_group_sources"), dict):
        run["observed_sample_group_sources"] = {}
    if not isinstance(run.get("missing_sample_groups"), list):
        run["missing_sample_groups"] = []


def _reconcile_missing_sample_groups(run: dict[str, Any]) -> None:
    _ensure_group_tracking(run)
    missing = {_normalize_group_label(x) for x in run.get("missing_sample_group_signals", []) if str(x).strip()}
    observed = {_normalize_group_label(x) for x in run.get("observed_sample_groups", []) if str(x).strip()}
    unresolved = sorted([g for g in missing if g and g not in observed])
    run["missing_sample_groups"] = unresolved


def _mark_group_missing_signal(run: dict[str, Any], group_label: str) -> None:
    _ensure_group_tracking(run)
    group = _normalize_group_label(group_label)
    if not group:
        return
    signals = {str(x).strip() for x in run.get("missing_sample_group_signals", []) if str(x).strip()}
    if group not in signals:
        run["missing_sample_group_signals"] = sorted(signals.union({group}))
    _reconcile_missing_sample_groups(run)


def _note_group_observation_source(run: dict[str, Any], group: str, source: str) -> None:
    _ensure_group_tracking(run)
    grp = _normalize_group_label(group)
    src = str(source or "").strip().lower()
    if not grp or not src:
        return
    source_map = run.get("observed_sample_group_sources", {})
    if not isinstance(source_map, dict):
        source_map = {}
    existing = {str(x).strip().lower() for x in source_map.get(grp, []) if str(x).strip()}
    if src not in existing:
        source_map[grp] = sorted(existing.union({src}))
        run["observed_sample_group_sources"] = source_map


def _mark_group_observed(run: dict[str, Any], group_label: str, *, source: str = "unspecified") -> None:
    _ensure_group_tracking(run)
    group = _normalize_group_label(group_label)
    if not group:
        return
    observed = {str(x).strip() for x in run.get("observed_sample_groups", []) if str(x).strip()}
    if group not in observed:
        run["observed_sample_groups"] = sorted(observed.union({group}))
    _note_group_observation_source(run, group, source)
    _reconcile_missing_sample_groups(run)


def _group_aliases(group_label: str) -> set[str]:
    base = _normalize_group_label(group_label)
    if not base:
        return set()
    aliases = {base}
    if base == "control":
        aliases.update({"ctrl", "ctl", "untreated", "reference", "baseline", "group1", "g1"})
    elif base in {"treatment", "case"}:
        aliases.update({"treated", "tx", "trt", "experimental", "perturbed", "group2", "g2", "treatment", "case"})
    return aliases


def _path_mentions_group(path_text: str, aliases: set[str]) -> bool:
    text = str(path_text or "").strip().lower()
    if not text or not aliases:
        return False
    for alias in aliases:
        if not alias:
            continue
        pat = rf"(^|[\/_.-]){re.escape(alias)}($|[\/_.-])"
        if re.search(pat, text):
            return True
    return False


def _argument_key_mentions_group(key: str, aliases: set[str]) -> bool:
    key_text = str(key or "").strip().lower()
    if not key_text:
        return False
    return _text_mentions_group_token(key_text, aliases) or _path_mentions_group(key_text, aliases)


def _extract_group_hinted_paths_from_command(
    command: str,
    selected_dir: Path,
    group_alias_map: dict[str, set[str]],
) -> dict[str, set[Path]]:
    hinted: dict[str, set[Path]] = {group: set() for group in group_alias_map.keys()}
    for seg in split_shell_segments(command or ""):
        segment = str(seg or "").strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except Exception:
            tokens = segment.split()
        if not tokens:
            continue
        i = 0
        while i < len(tokens):
            tok = str(tokens[i]).strip()
            if not tok.startswith("-"):
                i += 1
                continue
            option_name = tok
            option_value = ""
            if "=" in tok:
                option_name, option_value = tok.split("=", 1)
            elif i + 1 < len(tokens):
                nxt = str(tokens[i + 1]).strip()
                if nxt and not nxt.startswith("-"):
                    option_value = nxt
                    i += 1
            if option_value and _looks_like_path_token(option_value):
                p = _resolve_candidate_path(option_value, selected_dir)
                for group, aliases in group_alias_map.items():
                    if _argument_key_mentions_group(option_name, aliases):
                        hinted[group].add(p)
            i += 1
    return hinted


def _infer_observed_groups_from_plan_artifacts(plan: dict[str, Any], selected_dir: Path, groups: list[str]) -> set[str]:
    if not groups:
        return set()
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list):
        return set()

    group_alias_map: dict[str, set[str]] = {}
    for raw_group in groups:
        norm_group = _normalize_group_label(raw_group)
        if not norm_group:
            continue
        group_alias_map[norm_group] = _group_aliases(norm_group)
    if not group_alias_map:
        return set()

    candidate_paths: list[Path] = []
    group_hinted_paths: dict[str, set[Path]] = {group: set() for group in group_alias_map.keys()}
    for step in steps:
        if not isinstance(step, dict):
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        cmd = str(args.get("command", "")).strip()
        if cmd:
            candidate_paths.extend(_extract_paths_from_command(cmd, selected_dir))
            cmd_hints = _extract_group_hinted_paths_from_command(cmd, selected_dir, group_alias_map)
            for group, hinted in cmd_hints.items():
                group_hinted_paths[group].update(hinted)
        for key, raw_value in args.items():
            if str(key).strip().lower() == "command":
                continue
            parsed_paths = _extract_paths_from_argument_value(raw_value, selected_dir)
            candidate_paths.extend(parsed_paths)
            key_text = str(key or "").strip()
            for group, aliases in group_alias_map.items():
                if _argument_key_mentions_group(key_text, aliases):
                    group_hinted_paths[group].update(parsed_paths)

    dedup_paths: list[Path] = []
    seen_paths: set[str] = set()
    for p in candidate_paths:
        ps = str(p)
        if ps in seen_paths:
            continue
        seen_paths.add(ps)
        dedup_paths.append(p)

    observed: set[str] = set()
    for norm_group, aliases in group_alias_map.items():
        hinted_paths = list(group_hinted_paths.get(norm_group, set()))
        for p in hinted_paths:
            if _path_has_positive_group_evidence(p):
                observed.add(norm_group)
                break
        if norm_group in observed:
            continue
        for p in dedup_paths:
            if not _path_mentions_group(str(p), aliases):
                continue
            if _path_has_positive_group_evidence(p):
                observed.add(norm_group)
                break
        if norm_group in observed:
            continue
        for p in dedup_paths:
            if not _path_has_positive_group_evidence(p):
                continue
            if _path_text_has_group_evidence(p, aliases):
                observed.add(norm_group)
                break
    return observed
