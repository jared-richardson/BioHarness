"""Fast-signal fixture and replay helpers for harness regression gates.

The fast-signal ladder uses small, versioned fixtures to replay observed LLM
emissions and stepwise candidate states before expensive benchmark runs. This
module intentionally stays deterministic: it parses stored artifacts, extracts
plan-shape idioms, and checks for silent corruption without making live LLM
calls or replacing scientific plans.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bio_harness.core.failure_classes import resolve_failure_class
from bio_harness.core.hierarchical_planning import normalize_workflow_spec

FIXTURE_SCHEMA_VERSION = 1
FIXTURE_KINDS = frozenset({"planner_shape", "candidate_gate"})
PLAN_STEP_KEYS = ("workflow", "plan", "steps", "plan_outline")


@dataclass(frozen=True)
class ReplayFixture:
    """One fast-signal replay fixture.

    Attributes:
        schema_version: Fixture schema version.
        id: Stable fixture identifier.
        kind: Replay kind, such as ``planner_shape`` or ``candidate_gate``.
        source_run: Source run or study artifact identifier.
        model: Model that produced the raw emission, when known.
        captured_against_model_digest: Backend-resolved model digest.
        backend_version: LLM backend version.
        temperature: Planner temperature, when known.
        analysis_family: Broad analysis family used for relevance checks.
        analysis_type: Analysis type or benchmark case family.
        failure_class_id: Registry-backed failure class.
        raw_emission: Raw planner emission or trace payload.
        prefix_state: Stepwise accepted-prefix state for candidate gates.
        candidate: Candidate step or one-step plan for candidate gates.
        expected_outcome: Expected replay outcome payload.
        failure_class: Historical failure class this fixture covers.
        covers_fix: Historical fix IDs covered by this fixture.
        fixture_signature_hash: Stable signature used for fixture deduplication.
        tags: Relevance tags for release-gate checks.
        metadata: Extra deterministic test/support data.
    """

    schema_version: int
    id: str
    kind: str
    source_run: str = ""
    model: str = ""
    captured_against_model_digest: str = ""
    backend_version: str = ""
    temperature: float | None = None
    analysis_family: str = ""
    analysis_type: str = ""
    failure_class_id: str = ""
    raw_emission: Any = None
    prefix_state: dict[str, Any] = field(default_factory=dict)
    candidate: dict[str, Any] = field(default_factory=dict)
    expected_outcome: dict[str, Any] = field(default_factory=dict)
    failure_class: str = ""
    covers_fix: list[str] = field(default_factory=list)
    fixture_signature_hash: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Populate derived fixture metadata."""
        if not self.analysis_family:
            object.__setattr__(
                self,
                "analysis_family",
                analysis_family_for_type(self.analysis_type),
            )
        resolution = resolve_failure_class(self.failure_class_id or self.failure_class)
        object.__setattr__(self, "failure_class_id", resolution.failure_class_id)
        if not self.fixture_signature_hash:
            object.__setattr__(
                self,
                "fixture_signature_hash",
                fixture_signature_hash(self),
            )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> ReplayFixture:
        """Build a fixture from a decoded mapping.

        Args:
            payload: JSON-compatible fixture payload.

        Returns:
            Parsed replay fixture.

        Raises:
            ValueError: If required fields or known enum values are invalid.
        """
        fixture_id = str(payload.get("id", "") or "").strip()
        kind = str(payload.get("kind", "") or "").strip()
        if not fixture_id:
            raise ValueError("Replay fixture is missing required field: id")
        if kind not in FIXTURE_KINDS:
            raise ValueError(
                f"Replay fixture {fixture_id!r} has invalid kind {kind!r}; "
                f"expected one of {sorted(FIXTURE_KINDS)}"
            )
        schema_version = int(payload.get("schema_version", FIXTURE_SCHEMA_VERSION) or 0)
        if schema_version != FIXTURE_SCHEMA_VERSION:
            raise ValueError(
                f"Replay fixture {fixture_id!r} uses schema_version="
                f"{schema_version}; expected {FIXTURE_SCHEMA_VERSION}"
            )
        return cls(
            schema_version=schema_version,
            id=fixture_id,
            kind=kind,
            source_run=str(payload.get("source_run", "") or ""),
            model=str(payload.get("model", "") or ""),
            captured_against_model_digest=str(
                payload.get("captured_against_model_digest", "") or ""
            ),
            backend_version=str(payload.get("backend_version", "") or ""),
            temperature=_optional_float(payload.get("temperature")),
            analysis_family=str(payload.get("analysis_family", "") or ""),
            analysis_type=str(payload.get("analysis_type", "") or ""),
            failure_class_id=str(payload.get("failure_class_id", "") or ""),
            raw_emission=payload.get("raw_emission"),
            prefix_state=_dict_or_empty(payload.get("prefix_state")),
            candidate=_dict_or_empty(payload.get("candidate")),
            expected_outcome=_dict_or_empty(payload.get("expected_outcome")),
            failure_class=str(payload.get("failure_class", "") or ""),
            covers_fix=[str(item) for item in payload.get("covers_fix", []) or []],
            fixture_signature_hash=str(payload.get("fixture_signature_hash", "") or ""),
            tags=[str(item) for item in payload.get("tags", []) or []],
            metadata=_dict_or_empty(payload.get("metadata")),
        )

    def to_mapping(self) -> dict[str, Any]:
        """Return a JSON-compatible fixture payload."""
        return asdict(self)


@dataclass(frozen=True)
class ReplayResult:
    """Outcome from replaying one fast-signal fixture.

    Attributes:
        fixture_id: Stable fixture identifier.
        kind: Fixture kind.
        passed: Whether observed replay behavior matched expectation.
        observed: Observed replay payload.
        expected: Expected replay payload.
        reason: Human-readable failure reason.
    """

    fixture_id: str
    kind: str
    passed: bool
    observed: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def load_replay_fixture(path: Path | str) -> ReplayFixture:
    """Load one replay fixture from disk.

    Args:
        path: Path to a fixture JSON file.

    Returns:
        Parsed replay fixture.
    """
    fixture_path = Path(path).expanduser().resolve(strict=False)
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Replay fixture {fixture_path} must decode to an object")
    return ReplayFixture.from_mapping(payload)


def load_replay_fixtures(root: Path | str) -> list[ReplayFixture]:
    """Load replay fixtures from a file or directory.

    Args:
        root: Fixture JSON file or directory containing JSON fixtures.

    Returns:
        Fixtures sorted by stable identifier.
    """
    root_path = Path(root).expanduser().resolve(strict=False)
    paths = [root_path] if root_path.is_file() else sorted(root_path.rglob("*.json"))
    fixtures = [load_replay_fixture(path) for path in paths]
    return sorted(fixtures, key=lambda item: item.id)


def write_replay_fixture(fixture: ReplayFixture, path: Path | str) -> None:
    """Write one replay fixture as stable JSON.

    Args:
        fixture: Fixture to serialize.
        path: Destination JSON file.
    """
    fixture_path = Path(path).expanduser().resolve(strict=False)
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(
        json.dumps(fixture.to_mapping(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def fixture_signature_hash(fixture: ReplayFixture) -> str:
    """Return a stable deduplication hash for a replay fixture.

    Args:
        fixture: Fixture to summarize.

    Returns:
        SHA-256 hex digest for the fixture's behavior-relevant shape.
    """
    payload = {
        "kind": fixture.kind,
        "analysis_family": fixture.analysis_family
        or analysis_family_for_type(fixture.analysis_type),
        "analysis_type": fixture.analysis_type,
        "failure_class_id": resolve_failure_class(
            fixture.failure_class_id or fixture.failure_class
        ).failure_class_id,
        "raw_shape": _raw_emission_shape(fixture.raw_emission),
        "prefix_shape": _prefix_state_shape(fixture.prefix_state),
        "candidate_shape": _candidate_shape(fixture.candidate),
        "expected_outcome": fixture.expected_outcome,
        "covers_fix": sorted(fixture.covers_fix),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def analysis_family_for_type(analysis_type: str) -> str:
    """Return the broad fixture relevance family for an analysis type.

    Args:
        analysis_type: Specific analysis type from an analysis spec.

    Returns:
        Broad analysis family label.
    """
    lowered = analysis_type.strip().lower()
    if any(token in lowered for token in ("evolution", "shared_variant")):
        return "evolution"
    if any(token in lowered for token in ("germline", "variant_call")):
        return "germline_vc"
    if any(token in lowered for token in ("differential", "rna", "deseq", "de_")):
        return "de"
    return lowered or "unknown"


def raw_response_text(trace_payload: dict[str, Any], *, run_dir: Path | None = None) -> str:
    """Return raw planner text from a trace payload.

    Args:
        trace_payload: Decoded ``*_raw_response.json`` trace payload.
        run_dir: Optional run directory used to resolve relative raw text paths.

    Returns:
        Raw response text when available, otherwise the trace excerpt.
    """
    raw_file = str(trace_payload.get("raw_content_file", "") or "").strip()
    if raw_file:
        raw_path = Path(raw_file).expanduser()
        if not raw_path.is_absolute() and run_dir is not None:
            raw_path = run_dir / raw_path
        try:
            if raw_path.is_file():
                return raw_path.read_text(encoding="utf-8")
        except OSError:
            pass
    return str(trace_payload.get("raw_excerpt", "") or "")


def parse_raw_emission(raw_emission: Any) -> dict[str, Any]:
    """Parse a raw fixture emission into a planner payload.

    Args:
        raw_emission: Raw emission stored as a dict or JSON-like text.

    Returns:
        Parsed planner payload. Unparseable emissions return an empty dict.
    """
    if isinstance(raw_emission, dict):
        if "raw_content" in raw_emission:
            return parse_raw_emission(raw_emission.get("raw_content"))
        if "raw_excerpt" in raw_emission and not _contains_plan_steps(raw_emission):
            parsed = parse_raw_emission(raw_emission.get("raw_excerpt"))
            return parsed or raw_emission
        return raw_emission
    if not isinstance(raw_emission, str):
        return {}
    text = raw_emission.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    snippet = _first_balanced_json_object(text)
    if not snippet:
        return {}
    try:
        parsed = json.loads(snippet)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def plan_step_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the first planner step list found in a payload.

    Args:
        payload: Planner payload.

    Returns:
        List of step dictionaries.
    """
    for key in PLAN_STEP_KEYS:
        steps = payload.get(key)
        if isinstance(steps, list):
            return [dict(step) for step in steps if isinstance(step, dict)]
        if isinstance(steps, dict):
            for nested_key in PLAN_STEP_KEYS:
                nested_steps = steps.get(nested_key)
                if isinstance(nested_steps, list):
                    return [dict(step) for step in nested_steps if isinstance(step, dict)]
    return []


def plan_idiom_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Summarize shape idioms in a planner payload.

    Args:
        payload: Planner payload.

    Returns:
        JSON-compatible idiom summary.
    """
    steps = plan_step_list(payload)
    top_level_step_key = ""
    for key in PLAN_STEP_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            top_level_step_key = key
            break
        if isinstance(value, dict) and plan_step_list({key: value}):
            top_level_step_key = key
            break
    tool_names: list[str] = []
    path_styles = {"absolute": 0, "relative": 0, "bare": 0}
    argument_forms = {"arguments": 0, "parameter_hints": 0, "top_level": 0}
    branch_styles = {"branch_id": 0, "sample_name": 0, "objective_only": 0}
    duplicate_tools = 0
    seen_tools: set[str] = set()
    for step in steps:
        tool_name = str(step.get("tool_name") or step.get("tool") or "").strip()
        if tool_name:
            duplicate_tools += int(tool_name in seen_tools)
            seen_tools.add(tool_name)
            tool_names.append(tool_name)
        args = _dict_or_empty(step.get("arguments"))
        hints = _dict_or_empty(step.get("parameter_hints"))
        if args:
            argument_forms["arguments"] += 1
        if hints:
            argument_forms["parameter_hints"] += 1
        if not args and not hints:
            argument_forms["top_level"] += 1
        if str(step.get("branch_id", "") or "").strip():
            branch_styles["branch_id"] += 1
        elif str(args.get("sample_name", "") or hints.get("sample_name", "") or "").strip():
            branch_styles["sample_name"] += 1
        elif str(step.get("objective", "") or "").strip():
            branch_styles["objective_only"] += 1
        for value in list(args.values()) + list(hints.values()):
            if isinstance(value, str) and _looks_path_like(value):
                path_styles[_path_style(value)] += 1
    return {
        "top_level_keys": sorted(str(key) for key in payload),
        "top_level_step_key": top_level_step_key,
        "step_count": len(steps),
        "tool_names": tool_names,
        "unique_tool_count": len(set(tool_names)),
        "duplicate_tool_count": duplicate_tools,
        "path_styles": path_styles,
        "argument_forms": argument_forms,
        "branch_styles": branch_styles,
    }


def plan_idiom_key(summary: dict[str, Any]) -> str:
    """Return a stable bucket key for one plan-idiom summary.

    Args:
        summary: Output from :func:`plan_idiom_summary`.

    Returns:
        Pipe-delimited shape key used by corpus summaries and deduplication.
    """
    path_styles = summary.get("path_styles", {})
    argument_forms = summary.get("argument_forms", {})
    branch_styles = summary.get("branch_styles", {})
    return "|".join(
        [
            f"step_key={summary.get('top_level_step_key', '')}",
            f"steps={summary.get('step_count', 0)}",
            f"abs={path_styles.get('absolute', 0)}",
            f"rel={path_styles.get('relative', 0)}",
            f"bare={path_styles.get('bare', 0)}",
            f"args={argument_forms.get('arguments', 0)}",
            f"hints={argument_forms.get('parameter_hints', 0)}",
            f"branch={branch_styles.get('branch_id', 0)}",
            f"sample={branch_styles.get('sample_name', 0)}",
        ]
    )


def run_planner_shape_replay(fixture: ReplayFixture) -> ReplayResult:
    """Replay one planner-shape fixture through the normalizer.

    Args:
        fixture: Planner-shape fixture.

    Returns:
        Replay result with observed normalized shape and expectation checks.
    """
    if fixture.kind != "planner_shape":
        return ReplayResult(
            fixture_id=fixture.id,
            kind=fixture.kind,
            passed=False,
            expected=fixture.expected_outcome,
            reason=f"Unsupported fixture kind for planner replay: {fixture.kind}",
        )
    raw_payload = parse_raw_emission(fixture.raw_emission)
    raw_steps = plan_step_list(raw_payload)
    normalized = normalize_workflow_spec(raw_payload)
    normalized_steps = plan_step_list(normalized)
    observed = {
        "parsed_ok": bool(raw_payload),
        "raw_step_count": len(raw_steps),
        "normalized_step_count": len(normalized_steps),
        "idioms": plan_idiom_summary(raw_payload),
        "normalized_tool_names": [
            str(step.get("tool_name", "") or "").strip()
            for step in normalized_steps
            if str(step.get("tool_name", "") or "").strip()
        ],
    }
    expected = fixture.expected_outcome
    failures = _planner_replay_failures(observed=observed, expected=expected)
    return ReplayResult(
        fixture_id=fixture.id,
        kind=fixture.kind,
        passed=not failures,
        observed=observed,
        expected=expected,
        reason="; ".join(failures),
    )


def build_planner_shape_fixture(
    *,
    fixture_id: str,
    source_run: str,
    trace_payload: dict[str, Any],
    run_dir: Path | None = None,
    analysis_type: str = "",
    analysis_family: str = "",
    failure_class: str = "",
    covers_fix: list[str] | None = None,
    tags: list[str] | None = None,
) -> ReplayFixture:
    """Build a planner-shape fixture from one raw-response trace.

    Args:
        fixture_id: Stable fixture identifier.
        source_run: Source run identifier.
        trace_payload: Decoded raw-response trace payload.
        run_dir: Optional run directory for resolving raw text paths.
        analysis_type: Analysis type label.
        analysis_family: Broad analysis family label.
        failure_class: Historical failure class.
        covers_fix: Fix IDs covered by the fixture.
        tags: Relevance tags for gate decisions.

    Returns:
        Planner-shape replay fixture.
    """
    raw_text = raw_response_text(trace_payload, run_dir=run_dir)
    parsed = parse_raw_emission(raw_text)
    idioms = plan_idiom_summary(parsed) if parsed else {}
    return ReplayFixture(
        schema_version=FIXTURE_SCHEMA_VERSION,
        id=fixture_id,
        kind="planner_shape",
        source_run=source_run,
        model=str(trace_payload.get("model_name", "") or ""),
        captured_against_model_digest=str(trace_payload.get("model_digest", "") or ""),
        backend_version=str(trace_payload.get("backend_version", "") or ""),
        temperature=_optional_float(_nested_get(trace_payload, ("payload", "temperature"))),
        analysis_family=analysis_family or analysis_family_for_type(analysis_type),
        analysis_type=analysis_type,
        raw_emission=raw_text,
        expected_outcome={
            "passed": True,
            "min_steps": 1,
            "forbid_silent_corruption": True,
        },
        failure_class=failure_class,
        covers_fix=list(covers_fix or []),
        tags=list(tags or []),
        metadata={
            "raw_response_trace": str(trace_payload.get("raw_content_file", "") or ""),
            "idiom_summary": idioms,
        },
    )


def _raw_emission_shape(raw_emission: Any) -> dict[str, Any]:
    payload = parse_raw_emission(raw_emission)
    if payload:
        return plan_idiom_summary(payload)
    if isinstance(raw_emission, str):
        return {"text_sha256": hashlib.sha256(raw_emission.encode("utf-8")).hexdigest()}
    return {"type": type(raw_emission).__name__}


def _prefix_state_shape(prefix_state: dict[str, Any]) -> dict[str, Any]:
    plan = _dict_or_empty(prefix_state.get("plan"))
    steps = plan_step_list(plan)
    return {
        "step_count": len(steps),
        "tool_names": [
            str(step.get("tool_name", "") or "").strip()
            for step in steps
            if str(step.get("tool_name", "") or "").strip()
        ],
    }


def _candidate_shape(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "steps": [
            {
                "tool_name": str(step.get("tool_name", "") or "").strip(),
                "branch_id": str(step.get("branch_id", "") or "").strip(),
                "argument_keys": sorted(_dict_or_empty(step.get("arguments")).keys()),
            }
            for step in plan_step_list(candidate)
        ],
    }


def _planner_replay_failures(
    *,
    observed: dict[str, Any],
    expected: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    if expected.get("expect_parse_failure", False) and observed.get("parsed_ok", False):
        failures.append("expected parse failure but raw emission parsed")
    if expected.get("expect_parse_success", False) and not observed.get("parsed_ok", False):
        failures.append("expected parse success but raw emission did not parse")
    min_steps = int(expected.get("min_steps", 0) or 0)
    if min_steps and int(observed.get("normalized_step_count", 0) or 0) < min_steps:
        failures.append(
            f"normalized step count {observed.get('normalized_step_count')} < expected {min_steps}"
        )
    min_bare_paths = int(expected.get("min_bare_paths", 0) or 0)
    if min_bare_paths:
        path_styles = (observed.get("idioms", {}) or {}).get("path_styles", {})
        observed_bare = int(path_styles.get("bare", 0) or 0)
        if observed_bare < min_bare_paths:
            failures.append(f"bare path count {observed_bare} < expected {min_bare_paths}")
    if (
        expected.get("forbid_silent_corruption", False)
        and observed.get("raw_step_count", 0)
        and not observed.get("normalized_step_count", 0)
    ):
        failures.append("raw plan had steps but normalized plan is empty")
    required_tools = {str(item) for item in expected.get("required_tools", []) or []}
    if required_tools:
        observed_tools = set(observed.get("normalized_tool_names", []) or [])
        missing = sorted(required_tools - observed_tools)
        if missing:
            failures.append(f"missing required tools after normalization: {missing}")
    forbidden_tools = {str(item) for item in expected.get("forbidden_tools", []) or []}
    if forbidden_tools:
        observed_tools = set(observed.get("normalized_tool_names", []) or [])
        present = sorted(forbidden_tools & observed_tools)
        if present:
            failures.append(f"forbidden tools present after normalization: {present}")
    return failures


def _first_balanced_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def _contains_plan_steps(payload: dict[str, Any]) -> bool:
    return any(isinstance(payload.get(key), list) for key in PLAN_STEP_KEYS)


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _looks_path_like(value: str) -> bool:
    return "/" in value or "." in Path(value).name


def _path_style(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return "absolute"
    if "/" in value:
        return "relative"
    return "bare"


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested_get(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
