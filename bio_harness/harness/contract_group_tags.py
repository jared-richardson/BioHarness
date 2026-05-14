"""Group and sample tag parsing helpers for contract utilities."""

from __future__ import annotations

import re


def _extract_sample_tags_from_plan(plan: dict[str, object]) -> tuple[str, str]:
    control_tag = "S1"
    treatment_tag = "S6"
    for step in plan.get("plan", []) if isinstance(plan, dict) else []:
        if not isinstance(step, dict):
            continue
        if step.get("tool_name") != "bash_run":
            continue
        args = step.get("arguments", {})
        cmd = str(args.get("command", "")) if isinstance(args, dict) else ""
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


def _extract_group_tags_from_request_text(request_text: str) -> tuple[list[str], list[str]]:
    text = str(request_text or "")

    def _dedup_keep_order(values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            token = str(value or "").strip()
            if not token:
                continue
            lowered = token.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(token)
        return out

    def _extract_tags(chunk: str) -> list[str]:
        tags = re.findall(r"\b[A-Za-z]{1,4}\d+\b", chunk)
        if tags:
            return _dedup_keep_order(tags)
        raw_tokens = re.split(r"[\s,;/]+", chunk)
        candidates: list[str] = []
        for token in raw_tokens:
            candidate = str(token or "").strip().strip("()[]{}.")
            if not candidate:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.-]{2,24}", candidate):
                continue
            lowered = candidate.lower()
            if lowered in {"and", "the", "as", "sample", "samples", "paired", "pair", "end", "control", "treatment"}:
                continue
            candidates.append(candidate)
        return _dedup_keep_order(candidates)

    control_tags: list[str] = []
    treatment_tags: list[str] = []

    control_match = re.search(
        r"(?P<tags>.+?)\s+(?:paired[\s-]?end\s+)?sample[s]?\s+as\s+the\s+control\b",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if control_match:
        control_tags = _extract_tags(str(control_match.group("tags") or ""))

    treatment_scope = text[control_match.end() :] if control_match else text
    treatment_match = re.search(
        r"(?P<tags>.+?)\s+(?:paired[\s-]?end\s+)?sample[s]?\s+as\s+the\s+treatment\b",
        treatment_scope,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if treatment_match:
        treatment_tags = _extract_tags(str(treatment_match.group("tags") or ""))

    return control_tags, treatment_tags
