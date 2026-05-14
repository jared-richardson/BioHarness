"""Shared Pydantic schemas for runtime artifacts and planner payloads."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ARTIFACT_SCHEMA_VERSION = 2


class PlanStepSchema(BaseModel):
    """Schema for one executable plan step."""

    step_id: int
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    deliverables: List[str] = Field(default_factory=list)
    expected_files: List[str] = Field(default_factory=list)
    validation_method: str = ""

    model_config = ConfigDict(extra="allow")


class PlanPayloadSchema(BaseModel):
    """Schema for an executable plan payload."""

    thought_process: str = ""
    plan: List[PlanStepSchema] = Field(default_factory=list)
    final_deliverables: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class PlanContractSchema(BaseModel):
    """Schema for the persisted plan contract."""

    explicit_tool_hints: List[str] = Field(default_factory=list)
    must_include_capabilities: List[str] = Field(default_factory=list)
    required_tool_hints: List[str] = Field(default_factory=list)
    required_output_paths: List[str] = Field(default_factory=list)
    blocked_tool_hints: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class ContractValidationSchema(BaseModel):
    """Schema for contract validation results."""

    passed: bool = False
    missing_capabilities: List[str] = Field(default_factory=list)
    missing_tool_hints: List[str] = Field(default_factory=list)
    missing_required_tool_hints: List[str] = Field(default_factory=list)
    issues: List[Dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class ProtocolGroundingCommandSchema(BaseModel):
    """Schema for one protocol-grounding command or postprocess item."""

    command: str = ""
    description: str = ""
    tool_name: str = ""

    model_config = ConfigDict(extra="allow")


class ProtocolGroundingSchema(BaseModel):
    """Schema for protocol-grounding payloads."""

    grounded: bool = False
    task_name: str = ""
    analysis_family: str = ""
    input_mode: str = ""
    execution_mode: str = ""
    output_path: str = ""
    required_tools: List[str] = Field(default_factory=list)
    compatible_tools: List[str] = Field(default_factory=list)
    required_signals: List[str] = Field(default_factory=list)
    source_files: List[str] = Field(default_factory=list)
    postprocess: List[ProtocolGroundingCommandSchema] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class AnalysisSpecPayloadSchema(BaseModel):
    """Schema for persisted analysis-spec payloads used by the planner."""

    analysis_type: str = ""
    benchmark_policy: str = ""
    protocol_grounding: ProtocolGroundingSchema = Field(default_factory=ProtocolGroundingSchema)
    deterministic_warnings: Dict[str, Any] = Field(default_factory=dict)
    execution_contract: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class DeliverableMetadataSchema(BaseModel):
    """Schema for deliverable materialization metadata rows."""

    why: str = ""
    analysis_type: str = ""
    output_path: str = ""
    source_path: str = ""
    source_kind: str = ""
    nonfatal: bool = False
    row_count: int = 0
    cluster_assignments: str = ""
    marker_genes: str = ""
    raw_counts: str = ""

    model_config = ConfigDict(extra="allow")


class RepairAuditEntrySchema(BaseModel):
    """Schema for persisted repair audit history entries."""

    ts: str
    run_id: str
    failure_class: str = ""
    attempt: int = 0
    action: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)
    patch_audit: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class PlannerResultSchema(BaseModel):
    """Schema for ``planner/result.json``."""

    schema_version: int = ARTIFACT_SCHEMA_VERSION
    user_request: str = ""
    benchmark_policy: str = ""
    plan: PlanPayloadSchema = Field(default_factory=PlanPayloadSchema)
    plan_contract: PlanContractSchema = Field(default_factory=PlanContractSchema)
    contract_validation: ContractValidationSchema = Field(default_factory=ContractValidationSchema)
    analysis_spec: AnalysisSpecPayloadSchema = Field(default_factory=AnalysisSpecPayloadSchema)
    protocol_validation: Dict[str, Any] = Field(default_factory=dict)
    semantic_validation: Dict[str, Any] = Field(default_factory=dict)
    protocol_normalization_meta: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


PlannerStatusLiteral = Literal[
    "planning",
    "planned",
    "planning_failed",
    "planning_timed_out",
]


class PlannerStatusSchema(BaseModel):
    """Schema for ``planner/status.json``."""

    schema_version: int = ARTIFACT_SCHEMA_VERSION
    run_id: str
    status: PlannerStatusLiteral
    started_at: str = ""
    updated_at: str = ""
    error: str = ""
    timeout_seconds: int = 0
    result_ready: bool = False
    finished_at: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class RunManifestSchema(BaseModel):
    """Schema for ``manifest.json``."""

    schema_version: int = ARTIFACT_SCHEMA_VERSION
    run_id: str
    created_at: str = ""
    plan_id: str | int | None = ""
    plan_kind: str = ""
    user_request: str = ""
    workspace_root: str = ""
    selected_dir: str = ""
    requested_data_root: str = ""
    execution_options: Dict[str, Any] = Field(default_factory=dict)
    benchmark_policy: str = ""
    chat_session_id: str = ""
    planning_started_at: str = ""
    planning_finished_at: str = ""
    canonicalization: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class RunExitSchema(BaseModel):
    """Schema for ``exit.json``."""

    schema_version: int = ARTIFACT_SCHEMA_VERSION
    run_id: str
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: str = ""

    model_config = ConfigDict(extra="allow")


class RunEventSchema(BaseModel):
    """Schema for one ``events.jsonl`` entry."""

    schema_version: int = ARTIFACT_SCHEMA_VERSION
    ts: str
    run_id: str
    step_id: Optional[int] = None
    agent: str = ""
    event_type: str = ""
    severity: str = "info"
    payload: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class ExecutorRuntimeSchema(BaseModel):
    """Schema for durable executor runtime tracking."""

    schema_version: int = ARTIFACT_SCHEMA_VERSION
    run_id: str
    pid: int
    status: str = "running"
    started_at: str = ""
    updated_at: str = ""
    finished_at: Optional[str] = None
    error: str = ""
    last_event_type: str = ""
    last_step_id: Optional[int] = None
    last_tool_name: str = ""

    model_config = ConfigDict(extra="allow")


class RunStateSchema(BaseModel):
    """Schema for ``state.json``."""

    schema_version: int = ARTIFACT_SCHEMA_VERSION
    run_id: str
    status: str = "initialized"
    chat_session_id: str = ""
    error: str = ""
    next_step_idx: int = 0
    step_statuses: List[str] = Field(default_factory=list)
    planner_status: str = ""
    planning_started_at: str = ""
    planning_finished_at: str = ""
    planner_error: str = ""
    requested_data_root: str = ""
    selected_dir: str = ""
    updated_at: str = ""
    benchmark_policy: str = ""
    execution_options: Dict[str, Any] = Field(default_factory=dict)
    auto_repair_attempts: Dict[str, Any] = Field(default_factory=dict)
    auto_repair_last_class: str = ""
    auto_repair_history: List[RepairAuditEntrySchema] = Field(default_factory=list)
    auto_repair_promotions: List[Dict[str, Any]] = Field(default_factory=list)
    plan_contract: PlanContractSchema = Field(default_factory=PlanContractSchema)
    contract_validation: ContractValidationSchema = Field(default_factory=ContractValidationSchema)

    recovery_verification_required: bool = False
    policy_block_detected: bool = False
    validation_block_detected: bool = False
    stale_tmp_cache_detected: bool = False
    format_input_error_detected: bool = False

    model_config = ConfigDict(extra="allow")


TERMINAL_RUN_STATUSES: frozenset[str] = frozenset(
    {
        "completed",
        "failed",
        "blocked_missing_tools",
        "blocked_input",
        "planning_failed",
        "planning_timed_out",
    }
)

TRANSIENT_RUN_STATUSES: frozenset[str] = frozenset(
    {
        "initialized",
        "draft",
        "planning",
        "planned",
        "running",
        "repairing",
        "remediating_tools",
    }
)


def is_terminal_status(status: str) -> bool:
    """Return whether *status* is terminal."""

    return str(status).strip().lower() in TERMINAL_RUN_STATUSES


def is_transient_status(status: str) -> bool:
    """Return whether *status* is transient."""

    return str(status).strip().lower() in TRANSIENT_RUN_STATUSES


def safe_parse_planner_status(raw: Dict[str, Any]) -> PlannerStatusSchema | None:
    """Parse planner status payloads safely."""

    try:
        return PlannerStatusSchema.model_validate(raw)
    except Exception:
        return None


def safe_parse_planner_result(raw: Dict[str, Any]) -> PlannerResultSchema | None:
    """Parse planner result payloads safely."""

    try:
        return PlannerResultSchema.model_validate(raw)
    except Exception:
        return None


def safe_parse_run_state(raw: Dict[str, Any]) -> RunStateSchema | None:
    """Parse run state payloads safely."""

    try:
        return RunStateSchema.model_validate(raw)
    except Exception:
        return None


def safe_parse_manifest(raw: Dict[str, Any]) -> RunManifestSchema | None:
    """Parse run manifest payloads safely."""

    try:
        return RunManifestSchema.model_validate(raw)
    except Exception:
        return None


def safe_parse_run_exit(raw: Dict[str, Any]) -> RunExitSchema | None:
    """Parse run exit payloads safely."""

    try:
        return RunExitSchema.model_validate(raw)
    except Exception:
        return None


def safe_parse_run_event(raw: Dict[str, Any]) -> RunEventSchema | None:
    """Parse one event payload safely."""

    try:
        return RunEventSchema.model_validate(raw)
    except Exception:
        return None


def safe_parse_executor_runtime(raw: Dict[str, Any]) -> ExecutorRuntimeSchema | None:
    """Parse executor runtime payloads safely."""

    try:
        return ExecutorRuntimeSchema.model_validate(raw)
    except Exception:
        return None


def safe_parse_plan_payload(raw: Dict[str, Any]) -> PlanPayloadSchema | None:
    """Parse plan payloads safely."""

    try:
        return PlanPayloadSchema.model_validate(raw)
    except Exception:
        return None


def safe_parse_plan_contract(raw: Dict[str, Any]) -> PlanContractSchema | None:
    """Parse plan-contract payloads safely."""

    try:
        return PlanContractSchema.model_validate(raw)
    except Exception:
        return None


def safe_parse_protocol_grounding(raw: Dict[str, Any]) -> ProtocolGroundingSchema | None:
    """Parse protocol-grounding payloads safely."""

    try:
        return ProtocolGroundingSchema.model_validate(raw)
    except Exception:
        return None


def safe_parse_deliverable_metadata(raw: Dict[str, Any]) -> DeliverableMetadataSchema | None:
    """Parse deliverable metadata payloads safely."""

    try:
        return DeliverableMetadataSchema.model_validate(raw)
    except Exception:
        return None


def safe_parse_repair_audit_entry(raw: Dict[str, Any]) -> RepairAuditEntrySchema | None:
    """Parse repair-audit payloads safely."""

    try:
        return RepairAuditEntrySchema.model_validate(raw)
    except Exception:
        return None
