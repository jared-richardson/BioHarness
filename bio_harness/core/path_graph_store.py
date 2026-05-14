from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_DB_FILENAME = "path_graph.sqlite"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _json_loads(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def deterministic_prompt_hash(prompt: str) -> str:
    normalized = " ".join(str(prompt or "").strip().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def default_path_graph_db_path(selected_dir: Path | str) -> Path:
    root = Path(selected_dir).expanduser().resolve()
    return root / "knowledge" / DEFAULT_DB_FILENAME


class UnsafeMutationRequestError(ValueError):
    pass


class PathGraphStore:
    """SQLite-backed path graph substrate used for deterministic fallback path ranking."""

    _UNSAFE_MUTATION_PATTERNS = (
        re.compile(r"(^|\s)rm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+/(\s|$)", flags=re.IGNORECASE),
        re.compile(r"(^|\s)sudo\s+rm\b", flags=re.IGNORECASE),
        re.compile(r"(^|\s)mkfs(\.|\s)", flags=re.IGNORECASE),
        re.compile(r"(^|\s)dd\s+if=", flags=re.IGNORECASE),
        re.compile(r"(^|\s)shutdown\b", flags=re.IGNORECASE),
        re.compile(r"(^|\s)reboot\b", flags=re.IGNORECASE),
        re.compile(r":\(\)\s*\{\s*:\|:&\s*;\s*\}", flags=re.IGNORECASE),
    )

    _ALLOWED_MUTATIONS = {
        "upsert_node",
        "upsert_edge",
        "add_annotation",
        "record_path_run",
        "update_path_metrics",
        "upsert_user_preferences",
    }

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.bootstrap()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def bootstrap(self) -> None:
        ddl = [
            """
            CREATE TABLE IF NOT EXISTS nodes (
              node_id TEXT PRIMARY KEY,
              node_type TEXT NOT NULL,
              label TEXT NOT NULL,
              properties_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS edges (
              edge_id TEXT PRIMARY KEY,
              src_node_id TEXT NOT NULL,
              dst_node_id TEXT NOT NULL,
              edge_type TEXT NOT NULL,
              weight REAL NOT NULL,
              properties_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS path_runs (
              run_id TEXT PRIMARY KEY,
              path_id TEXT NOT NULL,
              prompt_hash TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              artifacts_json TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS path_metrics (
              path_id TEXT PRIMARY KEY,
              success_rate REAL NOT NULL,
              mean_runtime_sec REAL NOT NULL,
              recency_score REAL NOT NULL,
              quality_score REAL NOT NULL,
              updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
              pref_id TEXT PRIMARY KEY,
              user_key TEXT NOT NULL,
              scope TEXT NOT NULL,
              preferences_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS annotations (
              annotation_id TEXT PRIMARY KEY,
              target_type TEXT NOT NULL,
              target_id TEXT NOT NULL,
              note TEXT NOT NULL,
              tags_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_edges_src_type ON edges(src_node_id, edge_type)",
            "CREATE INDEX IF NOT EXISTS idx_edges_dst_type ON edges(dst_node_id, edge_type)",
            "CREATE INDEX IF NOT EXISTS idx_path_runs_path_status ON path_runs(path_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_path_runs_prompt_hash ON path_runs(prompt_hash)",
            "CREATE INDEX IF NOT EXISTS idx_user_preferences_scope ON user_preferences(user_key, scope, updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_annotations_target ON annotations(target_type, target_id, created_at)",
        ]
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            for stmt in ddl:
                conn.execute(stmt)
            conn.commit()

    def upsert_node(
        self,
        *,
        node_id: str,
        node_type: str,
        label: str,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        now = _now_utc_iso()
        payload = _json_dumps(dict(properties or {}))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO nodes(node_id, node_type, label, properties_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                  node_type=excluded.node_type,
                  label=excluded.label,
                  properties_json=excluded.properties_json,
                  updated_at=excluded.updated_at
                """,
                (str(node_id), str(node_type), str(label), payload, now, now),
            )
            conn.commit()

    def upsert_edge(
        self,
        *,
        edge_id: str,
        src_node_id: str,
        dst_node_id: str,
        edge_type: str,
        weight: float = 1.0,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        now = _now_utc_iso()
        payload = _json_dumps(dict(properties or {}))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO edges(edge_id, src_node_id, dst_node_id, edge_type, weight, properties_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(edge_id) DO UPDATE SET
                  src_node_id=excluded.src_node_id,
                  dst_node_id=excluded.dst_node_id,
                  edge_type=excluded.edge_type,
                  weight=excluded.weight,
                  properties_json=excluded.properties_json,
                  updated_at=excluded.updated_at
                """,
                (
                    str(edge_id),
                    str(src_node_id),
                    str(dst_node_id),
                    str(edge_type),
                    float(weight),
                    payload,
                    now,
                    now,
                ),
            )
            conn.commit()

    def add_annotation(
        self,
        *,
        target_type: str,
        target_id: str,
        note: str,
        tags: Sequence[str] | None = None,
        annotation_id: str | None = None,
    ) -> str:
        created_at = _now_utc_iso()
        tags_sorted = sorted({str(t).strip() for t in (tags or []) if str(t).strip()})
        resolved_id = str(annotation_id or "").strip()
        if not resolved_id:
            base = f"{target_type}|{target_id}|{note}|{','.join(tags_sorted)}|{created_at}"
            resolved_id = hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO annotations(annotation_id, target_type, target_id, note, tags_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (resolved_id, str(target_type), str(target_id), str(note), _json_dumps(tags_sorted), created_at),
            )
            conn.commit()
        return resolved_id

    def record_path_run(
        self,
        *,
        run_id: str,
        path_id: str,
        prompt_hash: str,
        status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        artifacts: Mapping[str, Any] | None = None,
    ) -> None:
        started = str(started_at or _now_utc_iso())
        finished = str(finished_at) if finished_at else None
        artifacts_payload = _json_dumps(dict(artifacts or {}))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO path_runs(run_id, path_id, prompt_hash, status, started_at, finished_at, artifacts_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    str(path_id),
                    str(prompt_hash),
                    str(status),
                    started,
                    finished,
                    artifacts_payload,
                ),
            )
            conn.commit()
        run_node = f"run:{str(run_id)}"
        path_node = f"path:{str(path_id)}"
        with self._connect() as conn:
            existing_path_row = conn.execute(
                "SELECT label, properties_json FROM nodes WHERE node_id=?",
                (path_node,),
            ).fetchone()
        if existing_path_row:
            merged_props = _json_loads(existing_path_row["properties_json"])
            merged_props["pipeline_id"] = str(path_id)
            self.upsert_node(
                node_id=path_node,
                node_type="path",
                label=str(existing_path_row["label"] or path_id),
                properties=merged_props,
            )
        else:
            self.upsert_node(
                node_id=path_node,
                node_type="path",
                label=str(path_id),
                properties={"pipeline_id": str(path_id)},
            )
        self.upsert_node(
            node_id=run_node,
            node_type="run_outcome",
            label=str(run_id),
            properties={
                "run_id": str(run_id),
                "path_id": str(path_id),
                "prompt_hash": str(prompt_hash),
                "status": str(status),
                "started_at": started,
                "finished_at": finished or "",
                "artifacts": dict(artifacts or {}),
            },
        )
        self.upsert_edge(
            edge_id=f"edge:path_has_run_outcome:{str(path_id)}:{str(run_id)}",
            src_node_id=path_node,
            dst_node_id=run_node,
            edge_type="path_has_run_outcome",
            weight=1.0,
            properties={"status": str(status)},
        )
        self._refresh_metrics_for_path(str(path_id))

    def update_path_metrics(
        self,
        *,
        path_id: str,
        success_rate: float,
        mean_runtime_sec: float,
        recency_score: float,
        quality_score: float,
    ) -> None:
        now = _now_utc_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO path_metrics(path_id, success_rate, mean_runtime_sec, recency_score, quality_score, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path_id) DO UPDATE SET
                  success_rate=excluded.success_rate,
                  mean_runtime_sec=excluded.mean_runtime_sec,
                  recency_score=excluded.recency_score,
                  quality_score=excluded.quality_score,
                  updated_at=excluded.updated_at
                """,
                (
                    str(path_id),
                    float(max(0.0, min(1.0, success_rate))),
                    float(max(0.0, mean_runtime_sec)),
                    float(max(0.0, min(1.0, recency_score))),
                    float(max(0.0, min(1.0, quality_score))),
                    now,
                ),
            )
            conn.commit()

    def upsert_user_preferences(
        self,
        *,
        user_key: str,
        scope: str,
        preferences: Mapping[str, Any],
        pref_id: str | None = None,
    ) -> str:
        now = _now_utc_iso()
        resolved_pref_id = str(pref_id or "").strip()
        if not resolved_pref_id:
            token = f"{user_key}|{scope}"
            resolved_pref_id = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences(pref_id, user_key, scope, preferences_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(pref_id) DO UPDATE SET
                  user_key=excluded.user_key,
                  scope=excluded.scope,
                  preferences_json=excluded.preferences_json,
                  updated_at=excluded.updated_at
                """,
                (
                    resolved_pref_id,
                    str(user_key),
                    str(scope),
                    _json_dumps(dict(preferences)),
                    now,
                ),
            )
            conn.commit()
        return resolved_pref_id

    def get_user_preferences(self, *, user_key: str, scope: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT preferences_json
                FROM user_preferences
                WHERE user_key=? AND scope=?
                ORDER BY updated_at DESC, pref_id ASC
                LIMIT 1
                """,
                (str(user_key), str(scope)),
            ).fetchone()
        return _json_loads(row["preferences_json"]) if row else {}

    def ensure_catalog_paths(self, catalog: Sequence[Mapping[str, Any]]) -> None:
        for row in catalog:
            pipeline_id = str(row.get("pipeline_id", "")).strip()
            if not pipeline_id:
                continue
            path_node = f"path:{pipeline_id}"
            skill_name = str(row.get("skill_name", "")).strip() or pipeline_id
            skill_node = f"skill:{skill_name}"
            contract_caps = sorted(
                {
                    str(x).strip()
                    for x in (row.get("contract_capabilities", []) if isinstance(row.get("contract_capabilities", []), list) else [])
                    if str(x).strip()
                }
            )
            required_tools = sorted(
                {
                    str(x).strip()
                    for x in (row.get("required_tools", []) if isinstance(row.get("required_tools", []), list) else [])
                    if str(x).strip()
                }
            )
            tool_wrappers = sorted(
                {
                    str(x).strip()
                    for x in (row.get("tool_wrappers", []) if isinstance(row.get("tool_wrappers", []), list) else [])
                    if str(x).strip()
                }
            )
            self.upsert_node(
                node_id=path_node,
                node_type="path",
                label=pipeline_id,
                properties={
                    "pipeline_id": pipeline_id,
                    "rank": int(row.get("rank", 999)),
                    "recovery_safety": str(row.get("recovery_safety", "medium")).lower(),
                    "contract_capabilities": contract_caps,
                    "required_tools": required_tools,
                    "use_case": str(row.get("use_case", "")),
                },
            )
            self.upsert_node(
                node_id=skill_node,
                node_type="skill",
                label=skill_name,
                properties={
                    "skill_name": skill_name,
                    "pipeline_id": pipeline_id,
                },
            )
            self.upsert_edge(
                edge_id=f"edge:path_uses_skill:{pipeline_id}:{skill_name}",
                src_node_id=path_node,
                dst_node_id=skill_node,
                edge_type="path_uses_skill",
                weight=1.0,
                properties={},
            )
            for cap in contract_caps:
                cap_node = f"capability:{cap}"
                self.upsert_node(
                    node_id=cap_node,
                    node_type="capability",
                    label=cap,
                    properties={"capability_id": cap},
                )
                self.upsert_edge(
                    edge_id=f"edge:path_supports_capability:{pipeline_id}:{cap}",
                    src_node_id=path_node,
                    dst_node_id=cap_node,
                    edge_type="path_supports_capability",
                    weight=1.0,
                    properties={},
                )
                self.upsert_edge(
                    edge_id=f"edge:capability_maps_to_skill:{cap}:{skill_name}",
                    src_node_id=cap_node,
                    dst_node_id=skill_node,
                    edge_type="capability_maps_to_skill",
                    weight=1.0,
                    properties={"pipeline_id": pipeline_id},
                )
            for tool in required_tools:
                tool_l = tool.lower()
                tool_node = f"tool:{tool_l}"
                self.upsert_node(
                    node_id=tool_node,
                    node_type="tool",
                    label=tool_l,
                    properties={"tool_name": tool_l},
                )
                self.upsert_edge(
                    edge_id=f"edge:path_requires_tool:{pipeline_id}:{tool_l}",
                    src_node_id=path_node,
                    dst_node_id=tool_node,
                    edge_type="path_requires_tool",
                    weight=1.0,
                    properties={},
                )
            for tool in (tool_wrappers or required_tools):
                tool_l = str(tool).strip().lower()
                if not tool_l:
                    continue
                tool_node = f"tool:{tool_l}"
                self.upsert_node(
                    node_id=tool_node,
                    node_type="tool",
                    label=tool_l,
                    properties={"tool_name": tool_l},
                )
                self.upsert_edge(
                    edge_id=f"edge:skill_uses_tool:{skill_name}:{tool_l}",
                    src_node_id=skill_node,
                    dst_node_id=tool_node,
                    edge_type="skill_uses_tool",
                    weight=1.0,
                    properties={"pipeline_id": pipeline_id},
                )

    def get_candidate_paths_for_capabilities(
        self,
        *,
        capabilities: Sequence[str],
        constraints: Mapping[str, Any] | None = None,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        requested = sorted({str(c).strip() for c in capabilities if str(c).strip()})
        if not requested:
            return []

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT node_id, label, properties_json FROM nodes WHERE node_type='path' ORDER BY node_id ASC"
            ).fetchall()

        out: list[dict[str, Any]] = []
        metrics = self._metrics_for_paths([str(r["label"]) for r in rows])
        for row in rows:
            path_id = str(row["label"])
            props = _json_loads(row["properties_json"])
            caps = sorted(
                {
                    str(x).strip()
                    for x in (props.get("contract_capabilities", []) if isinstance(props.get("contract_capabilities", []), list) else [])
                    if str(x).strip()
                }
            )
            covered = sorted(set(requested).intersection(caps))
            missing = sorted(set(requested).difference(caps))
            if not covered:
                continue
            out.append(
                {
                    "path_id": path_id,
                    "pipeline_id": path_id,
                    "covered_caps": covered,
                    "missing_caps": missing,
                    "required_tools_effective": sorted(
                        {
                            str(x).strip()
                            for x in (props.get("required_tools", []) if isinstance(props.get("required_tools", []), list) else [])
                            if str(x).strip()
                        }
                    ),
                    "recovery_safety": str(props.get("recovery_safety", "medium")).lower(),
                    "metrics": metrics.get(path_id, {}),
                }
            )

        out.sort(
            key=lambda x: (
                len(x.get("missing_caps", [])),
                -len(x.get("covered_caps", [])),
                str(x.get("path_id", "")),
            )
        )
        return out[: max(1, int(top_k))]

    def has_rank_signal(
        self,
        *,
        path_ids: Sequence[str] | None = None,
        preference_profile: Mapping[str, Any] | None = None,
    ) -> bool:
        if isinstance(preference_profile, Mapping) and preference_profile:
            return True

        ids = [str(x).strip() for x in (path_ids or []) if str(x).strip()]
        if not ids:
            return False

        with self._connect() as conn:
            ph = ",".join("?" for _ in ids)
            metrics_count = conn.execute(
                f"SELECT COUNT(*) AS n FROM path_metrics WHERE path_id IN ({ph})",
                ids,
            ).fetchone()["n"]
            if int(metrics_count or 0) > 0:
                return True
            run_count = conn.execute(
                f"SELECT COUNT(*) AS n FROM path_runs WHERE path_id IN ({ph}) AND lower(status) IN ('completed','passed','success')",
                ids,
            ).fetchone()["n"]
        return int(run_count or 0) > 0

    def rank_paths(
        self,
        *,
        paths: Sequence[Mapping[str, Any]],
        capabilities: Sequence[str],
        constraints: Mapping[str, Any] | None = None,
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = [dict(p) for p in paths if isinstance(p, Mapping)]
        if not rows:
            return []

        requested_caps = sorted({str(c).strip() for c in capabilities if str(c).strip()})
        constraints_map = dict(constraints or {})
        preference_profile = (
            dict(constraints_map.get("preference_profile", {}))
            if isinstance(constraints_map.get("preference_profile", {}), Mapping)
            else {}
        )
        reuse_ids = {
            str(x.get("path_id", "")).strip()
            for x in (constraints_map.get("reuse_candidates", []) if isinstance(constraints_map.get("reuse_candidates", []), list) else [])
            if isinstance(x, Mapping)
        }
        ids = [str(r.get("pipeline_id") or r.get("path_id") or "").strip() for r in rows]
        metrics = self._metrics_for_paths(ids)
        use_graph_signal = self.has_rank_signal(path_ids=ids, preference_profile=preference_profile)

        ranked: list[dict[str, Any]] = []
        for idx, row in enumerate(rows):
            pipeline_id = str(row.get("pipeline_id") or row.get("path_id") or "").strip()
            metric = metrics.get(pipeline_id, {})

            covered_caps = {
                str(x).strip()
                for x in (row.get("covered_caps", []) if isinstance(row.get("covered_caps", []), list) else [])
                if str(x).strip()
            }
            if not covered_caps and requested_caps:
                contract_caps = {
                    str(x).strip()
                    for x in (
                        row.get("contract_capabilities", [])
                        if isinstance(row.get("contract_capabilities", []), list)
                        else []
                    )
                    if str(x).strip()
                }
                covered_caps = set(requested_caps).intersection(contract_caps)
            capability_coverage = 1.0
            if requested_caps:
                capability_coverage = len(covered_caps) / max(1, len(requested_caps))

            success_rate = float(metric.get("success_rate", 0.0) or 0.0)
            quality_score = float(metric.get("quality_score", 0.0) or 0.0)
            recency_score = float(metric.get("recency_score", 0.0) or 0.0)
            historical_reliability = max(
                0.0,
                min(1.0, (0.55 * success_rate) + (0.3 * quality_score) + (0.15 * recency_score)),
            )

            required_tools = sorted(
                {
                    str(x).strip().lower()
                    for x in (
                        row.get("required_tools_effective", [])
                        if isinstance(row.get("required_tools_effective", []), list)
                        else []
                    )
                    if str(x).strip()
                }
            )
            missing_tools = sorted(
                {
                    str(x).strip().lower()
                    for x in (
                        row.get("missing_tools", [])
                        if isinstance(row.get("missing_tools", []), list)
                        else []
                    )
                    if str(x).strip()
                }
            )
            tool_availability = 1.0
            if required_tools:
                tool_availability = max(0.0, 1.0 - (len(missing_tools) / max(1, len(required_tools))))

            pref_alignment, pref_details = self._preference_alignment(
                pipeline_id=pipeline_id,
                requested_caps=requested_caps,
                required_tools=required_tools,
                recovery_safety=str(row.get("recovery_safety", "medium")).lower(),
                preference_profile=preference_profile,
            )

            reuse_bonus = 0.15 if pipeline_id in reuse_ids else 0.0
            if not use_graph_signal:
                graph_total = 0.0
            else:
                graph_total = (
                    0.35 * capability_coverage
                    + 0.35 * historical_reliability
                    + 0.20 * pref_alignment
                    + 0.10 * tool_availability
                    + reuse_bonus
                )

            patched = dict(row)
            patched["graph_breakdown"] = {
                "capability_coverage": round(capability_coverage, 6),
                "historical_reliability": round(historical_reliability, 6),
                "preference_alignment": round(pref_alignment, 6),
                "tool_availability": round(tool_availability, 6),
                "reuse_bonus": round(reuse_bonus, 6),
                "preference": pref_details,
                "graph_signal_enabled": bool(use_graph_signal),
            }
            patched["graph_total_score"] = round(graph_total, 6)
            patched["_graph_idx"] = idx
            ranked.append(patched)

        if use_graph_signal:
            ranked.sort(
                key=lambda x: (
                    bool((x.get("graph_breakdown", {}).get("preference", {}) or {}).get("blocked_by_blacklist", False)),
                    int(x.get("feasibility_tier", 9)),
                    -float(x.get("graph_total_score", 0.0)),
                    len(x.get("missing_caps", [])),
                    len(x.get("missing_inputs", [])),
                    len(x.get("missing_tools", [])),
                    -int(x.get("score", 0)),
                    int(x.get("rank", 9999)),
                    str(x.get("pipeline_id", "")),
                )
            )
        else:
            ranked.sort(key=lambda x: int(x.get("_graph_idx", 0)))

        for row in ranked:
            row.pop("_graph_idx", None)

        if isinstance(top_k, int) and top_k > 0:
            return ranked[:top_k]
        return ranked

    def apply_mutation_request(self, mutation_request: Mapping[str, Any]) -> dict[str, Any]:
        op = str(mutation_request.get("operation", "")).strip().lower()
        payload = mutation_request.get("payload", {})
        if op not in self._ALLOWED_MUTATIONS:
            raise UnsafeMutationRequestError(f"Unsupported graph mutation operation: {op or 'unknown'}")
        if not isinstance(payload, Mapping):
            raise UnsafeMutationRequestError("Mutation payload must be an object.")
        if self._contains_unsafe_mutation_payload(payload):
            raise UnsafeMutationRequestError("Unsafe graph mutation request rejected by policy guard.")

        if op == "upsert_node":
            self.upsert_node(
                node_id=str(payload.get("node_id", "")),
                node_type=str(payload.get("node_type", "generic")),
                label=str(payload.get("label", payload.get("node_id", ""))),
                properties=payload.get("properties") if isinstance(payload.get("properties"), Mapping) else {},
            )
            return {"ok": True, "operation": op}
        if op == "upsert_edge":
            self.upsert_edge(
                edge_id=str(payload.get("edge_id", "")),
                src_node_id=str(payload.get("src_node_id", "")),
                dst_node_id=str(payload.get("dst_node_id", "")),
                edge_type=str(payload.get("edge_type", "generic")),
                weight=float(payload.get("weight", 1.0)),
                properties=payload.get("properties") if isinstance(payload.get("properties"), Mapping) else {},
            )
            return {"ok": True, "operation": op}
        if op == "add_annotation":
            annotation_id = self.add_annotation(
                target_type=str(payload.get("target_type", "path")),
                target_id=str(payload.get("target_id", "")),
                note=str(payload.get("note", "")),
                tags=[str(x) for x in payload.get("tags", [])] if isinstance(payload.get("tags", []), list) else [],
                annotation_id=str(payload.get("annotation_id", "")).strip() or None,
            )
            return {"ok": True, "operation": op, "annotation_id": annotation_id}
        if op == "record_path_run":
            self.record_path_run(
                run_id=str(payload.get("run_id", "")),
                path_id=str(payload.get("path_id", "")),
                prompt_hash=str(payload.get("prompt_hash", "")),
                status=str(payload.get("status", "planned")),
                started_at=str(payload.get("started_at", "")).strip() or None,
                finished_at=str(payload.get("finished_at", "")).strip() or None,
                artifacts=payload.get("artifacts") if isinstance(payload.get("artifacts"), Mapping) else {},
            )
            return {"ok": True, "operation": op}
        if op == "update_path_metrics":
            self.update_path_metrics(
                path_id=str(payload.get("path_id", "")),
                success_rate=float(payload.get("success_rate", 0.0)),
                mean_runtime_sec=float(payload.get("mean_runtime_sec", 0.0)),
                recency_score=float(payload.get("recency_score", 0.0)),
                quality_score=float(payload.get("quality_score", 0.0)),
            )
            return {"ok": True, "operation": op}

        pref_id = self.upsert_user_preferences(
            user_key=str(payload.get("user_key", "default")),
            scope=str(payload.get("scope", "global")),
            preferences=payload.get("preferences") if isinstance(payload.get("preferences"), Mapping) else {},
            pref_id=str(payload.get("pref_id", "")).strip() or None,
        )
        return {"ok": True, "operation": op, "pref_id": pref_id}

    def persist_success_preferences(
        self,
        *,
        user_key: str,
        scope: str,
        path_id: str,
        requested_capabilities: Sequence[str],
    ) -> dict[str, Any]:
        prefs = self.get_user_preferences(user_key=user_key, scope=scope)
        preferred_methods = (
            dict(prefs.get("preferred_methods", {}))
            if isinstance(prefs.get("preferred_methods", {}), Mapping)
            else {}
        )
        for cap in sorted({str(x).strip() for x in requested_capabilities if str(x).strip()}):
            existing = preferred_methods.get(cap, [])
            normalized = [str(x).strip() for x in (existing if isinstance(existing, list) else []) if str(x).strip()]
            if path_id not in normalized:
                normalized.insert(0, path_id)
            preferred_methods[cap] = normalized[:8]

        prefs["preferred_methods"] = {
            str(k): [str(x) for x in v] for k, v in sorted(preferred_methods.items(), key=lambda item: str(item[0]))
        }
        self.upsert_user_preferences(user_key=user_key, scope=scope, preferences=prefs)
        return prefs

    def _contains_unsafe_mutation_payload(self, payload: Mapping[str, Any]) -> bool:
        stack: list[Any] = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, Mapping):
                stack.extend(item.values())
                continue
            if isinstance(item, (list, tuple, set)):
                stack.extend(item)
                continue
            if not isinstance(item, str):
                continue
            text = f" {item.strip().lower()} "
            for pat in self._UNSAFE_MUTATION_PATTERNS:
                if pat.search(text):
                    return True
        return False

    def _preference_alignment(
        self,
        *,
        pipeline_id: str,
        requested_caps: Sequence[str],
        required_tools: Sequence[str],
        recovery_safety: str,
        preference_profile: Mapping[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        profile = dict(preference_profile or {})
        blacklist = {
            str(x).strip().lower()
            for x in (profile.get("tool_blacklist", []) if isinstance(profile.get("tool_blacklist", []), list) else [])
            if str(x).strip()
        }
        whitelist = {
            str(x).strip().lower()
            for x in (profile.get("tool_whitelist", []) if isinstance(profile.get("tool_whitelist", []), list) else [])
            if str(x).strip()
        }
        mode = str(profile.get("mode", "")).strip().lower()
        preferred_methods = (
            dict(profile.get("preferred_methods", {}))
            if isinstance(profile.get("preferred_methods", {}), Mapping)
            else {}
        )

        blocked = bool(blacklist.intersection({t.lower() for t in required_tools}))
        score = 0.0
        if blocked:
            score -= 1.0

        if whitelist and required_tools:
            req_set = {t.lower() for t in required_tools}
            if req_set.issubset(whitelist):
                score += 0.35
            elif req_set.intersection(whitelist):
                score += 0.12
            else:
                score -= 0.15

        preferred_hits = 0
        for cap in requested_caps:
            methods = preferred_methods.get(cap, [])
            if not isinstance(methods, list):
                continue
            if pipeline_id in {str(x).strip() for x in methods if str(x).strip()}:
                preferred_hits += 1
        score += min(0.5, 0.2 * preferred_hits)

        if mode == "conservative":
            if recovery_safety == "high":
                score += 0.2
            elif recovery_safety == "medium":
                score += 0.05
            else:
                score -= 0.15
        elif mode == "aggressive":
            if recovery_safety == "low":
                score += 0.15
            elif recovery_safety == "medium":
                score += 0.05

        score = max(-1.0, min(1.0, score))
        norm = (score + 1.0) / 2.0
        return norm, {
            "blocked_by_blacklist": blocked,
            "mode": mode,
            "preferred_method_hits": preferred_hits,
            "tool_blacklist": sorted(blacklist),
            "tool_whitelist": sorted(whitelist),
        }

    def _metrics_for_paths(self, path_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        ids = [str(x).strip() for x in path_ids if str(x).strip()]
        if not ids:
            return {}
        with self._connect() as conn:
            ph = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"""
                SELECT path_id, success_rate, mean_runtime_sec, recency_score, quality_score, updated_at
                FROM path_metrics
                WHERE path_id IN ({ph})
                ORDER BY path_id ASC
                """,
                ids,
            ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            out[str(row["path_id"])] = {
                "success_rate": float(row["success_rate"]),
                "mean_runtime_sec": float(row["mean_runtime_sec"]),
                "recency_score": float(row["recency_score"]),
                "quality_score": float(row["quality_score"]),
                "updated_at": str(row["updated_at"]),
            }
        return out

    def _refresh_metrics_for_path(self, path_id: str) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, status, started_at, finished_at, artifacts_json
                FROM path_runs
                WHERE path_id=?
                ORDER BY COALESCE(finished_at, started_at) ASC, run_id ASC
                """,
                (str(path_id),),
            ).fetchall()

        total = len(rows)
        if total == 0:
            self.update_path_metrics(
                path_id=path_id,
                success_rate=0.0,
                mean_runtime_sec=0.0,
                recency_score=0.0,
                quality_score=0.0,
            )
            return

        success_statuses = {"completed", "passed", "success"}
        success_count = 0
        runtimes: list[float] = []
        statuses: list[str] = []
        quality_samples: list[float] = []
        reliability_samples: list[float] = []
        for row in rows:
            status = str(row["status"] or "").strip().lower()
            statuses.append(status)
            if status in success_statuses:
                success_count += 1
            runtime = self._runtime_seconds(str(row["started_at"] or ""), str(row["finished_at"] or ""))
            if runtime is not None:
                runtimes.append(runtime)

            artifacts = _json_loads(row["artifacts_json"])
            raw_quality = artifacts.get("quality_score")
            raw_reliability = artifacts.get("reliability_score")
            quality = None
            reliability = None
            if isinstance(raw_quality, (int, float)):
                quality = float(max(0.0, min(1.0, float(raw_quality))))
            if isinstance(raw_reliability, (int, float)):
                reliability = float(max(0.0, min(1.0, float(raw_reliability))))
            if quality is None or reliability is None:
                penalties = 0.0
                if isinstance(artifacts.get("missing_tools_detected"), list) and artifacts.get("missing_tools_detected"):
                    penalties += 0.2
                if isinstance(artifacts.get("missing_reference_detected"), list) and artifacts.get("missing_reference_detected"):
                    penalties += 0.15
                if isinstance(artifacts.get("missing_sample_groups"), list) and artifacts.get("missing_sample_groups"):
                    penalties += 0.1
                if str(artifacts.get("error", "")).strip():
                    penalties += 0.2
                status_base = 1.0 if status in success_statuses else 0.3
                derived = max(0.0, min(1.0, status_base - penalties))
                quality = quality if quality is not None else derived
                reliability = reliability if reliability is not None else max(0.0, min(1.0, (0.7 * status_base) + (0.3 * derived)))
            quality_samples.append(float(max(0.0, min(1.0, quality))))
            reliability_samples.append(float(max(0.0, min(1.0, reliability))))

        success_rate = success_count / max(1, total)
        mean_runtime = sum(runtimes) / len(runtimes) if runtimes else 0.0
        recency_score = 0.0
        for idx, status in enumerate(reversed(statuses)):
            if status in success_statuses:
                recency_score = 1.0 / (1.0 + float(idx))
                break
        avg_quality = sum(quality_samples) / len(quality_samples) if quality_samples else 0.0
        avg_reliability = sum(reliability_samples) / len(reliability_samples) if reliability_samples else 0.0
        quality_score = max(
            0.0,
            min(
                1.0,
                (0.45 * success_rate)
                + (0.30 * avg_quality)
                + (0.15 * avg_reliability)
                + (0.10 * recency_score),
            ),
        )

        self.update_path_metrics(
            path_id=path_id,
            success_rate=success_rate,
            mean_runtime_sec=mean_runtime,
            recency_score=recency_score,
            quality_score=quality_score,
        )

    @staticmethod
    def _runtime_seconds(started_at: str, finished_at: str) -> float | None:
        if not started_at or not finished_at:
            return None
        start = PathGraphStore._parse_iso(started_at)
        finish = PathGraphStore._parse_iso(finished_at)
        if start is None or finish is None:
            return None
        seconds = (finish - start).total_seconds()
        if seconds < 0:
            return None
        return float(seconds)

    @staticmethod
    def _parse_iso(raw: str) -> datetime | None:
        text = str(raw or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
