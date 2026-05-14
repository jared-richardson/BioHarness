"""Automated model selection for the bio-harness.

Discovers available models from the LLM backend and selects the best
planner/executor pair based on model capabilities and task complexity.

Designed for graceful degradation: works with a single model, two models,
or many.  Explicit overrides (env vars, manifest ``runner_defaults``)
always take precedence.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Analysis types that have deterministic template compilers.
# Imported lazily to avoid circular dependencies.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _template_compiler_types() -> frozenset[str]:
    try:
        from bio_harness.core.protocol_grounding._repair import TEMPLATE_COMPILER_TYPES
        return TEMPLATE_COMPILER_TYPES
    except Exception:
        return frozenset()


# Analysis types considered high-complexity even with a template compiler.
_HIGH_COMPLEXITY_TYPES: frozenset[str] = frozenset({
    "bacterial_evolution_variant_calling",
    "rna_seq_differential_expression",
    "multi_model_dge_pathway",
})

# Preferred model families for "fast/coder" use.
_CODER_FAMILY_HINTS: frozenset[str] = frozenset({
    "qwen3next", "codellama", "starcoder", "deepseek-coder",
})


# ---------------------------------------------------------------------------
# ModelInfo
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """Metadata about an available LLM model."""

    name: str
    parameter_count_b: float = 0.0  # billions
    family: str = ""
    size_gb: float = 0.0

    @property
    def tier(self) -> str:
        """Classify model capability tier based on parameter count.

        Returns:
            ``"heavy"`` (>=30B), ``"fast"`` (>=10B), or ``"light"`` (<10B).
        """
        if self.parameter_count_b >= 30:
            return "heavy"
        if self.parameter_count_b >= 10:
            return "fast"
        return "light"

    @property
    def is_coder(self) -> bool:
        """Heuristic: is this a code-oriented model?"""
        fam = self.family.lower()
        name = self.name.lower()
        return any(hint in fam or hint in name for hint in _CODER_FAMILY_HINTS)


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------

def _parse_parameter_size(raw: str) -> float:
    """Parse Ollama ``details.parameter_size`` like ``'125.1B'`` to float."""
    raw = str(raw or "").strip().upper()
    if not raw:
        return 0.0
    m = re.match(r"([\d.]+)\s*([BKMGT]?)", raw)
    if not m:
        return 0.0
    value = float(m.group(1))
    unit = m.group(2)
    if unit == "K":
        value *= 1e-6
    elif unit == "M":
        value *= 1e-3
    elif unit in {"B", "G", ""}:
        pass  # already in billions
    elif unit == "T":
        value *= 1e3
    return value


def discover_models_from_ollama_tags(tags_response: dict[str, Any]) -> list[ModelInfo]:
    """Build ``ModelInfo`` list from the Ollama ``/api/tags`` response.

    Args:
        tags_response: The parsed JSON dict from ``GET /api/tags``.

    Returns:
        List of :class:`ModelInfo` objects with metadata populated.
    """
    models: list[ModelInfo] = []
    for row in tags_response.get("models", []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("model") or row.get("name") or "").strip()
        if not name:
            continue
        details = row.get("details", {}) if isinstance(row.get("details"), dict) else {}
        param_size = _parse_parameter_size(details.get("parameter_size", ""))
        family = str(details.get("family", "") or "").strip()
        size_gb = float(row.get("size", 0) or 0) / 1e9
        models.append(ModelInfo(
            name=name,
            parameter_count_b=param_size,
            family=family,
            size_gb=size_gb,
        ))
    return models


def discover_models(backend: Any) -> list[ModelInfo]:
    """Discover models with metadata from any supported backend.

    For Ollama backends, uses ``list_models_with_metadata()`` if available,
    falling back to ``list_models()`` with name-only heuristics.
    For other backends, only model names are available.

    Args:
        backend: An LLM backend instance (OllamaBackend, OpenAICompatibleBackend, etc.)

    Returns:
        List of :class:`ModelInfo`, possibly with only ``name`` populated
        for non-Ollama backends.
    """
    # Try rich metadata first (Ollama)
    if hasattr(backend, "list_models_with_metadata"):
        try:
            raw_list = backend.list_models_with_metadata()
            if raw_list:
                models = []
                for entry in raw_list:
                    if isinstance(entry, dict):
                        models.append(ModelInfo(
                            name=str(entry.get("name", "")),
                            parameter_count_b=float(entry.get("parameter_count_b", 0)),
                            family=str(entry.get("family", "")),
                            size_gb=float(entry.get("size_gb", 0)),
                        ))
                if models:
                    return models
        except Exception:
            pass

    # Fallback: name-only listing
    try:
        names = backend.list_models()
    except Exception:
        return []

    return [ModelInfo(name=n) for n in names if n]


# ---------------------------------------------------------------------------
# Complexity assessment
# ---------------------------------------------------------------------------

def assess_prompt_complexity(
    *,
    analysis_type: str | None = None,
) -> str:
    """Classify task complexity for model routing.

    Args:
        analysis_type: Canonical analysis type (if detected).

    Returns:
        ``"low"``, ``"medium"``, or ``"high"``.
    """
    at = str(analysis_type or "").strip().lower()
    if not at:
        return "high"  # unknown → assume hard

    compilers = _template_compiler_types()

    # High-complexity types even if template exists
    if at in _HIGH_COMPLEXITY_TYPES:
        return "high"

    # Template compiler available → low complexity (template compensates)
    if at in compilers:
        return "low"

    # Recognised canonical type but no template → medium
    try:
        from bio_harness.core.analysis_spec import CANONICAL_ANALYSIS_TYPES
        if at in CANONICAL_ANALYSIS_TYPES:
            return "medium"
    except Exception:
        pass

    # Completely unknown analysis type → high
    return "high"


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------

def _best_heavy(models: list[ModelInfo]) -> ModelInfo:
    """Pick the heaviest (highest parameter count) model."""
    return max(models, key=lambda m: m.parameter_count_b)


def _best_fast(models: list[ModelInfo]) -> ModelInfo:
    """Pick the best fast/coder model.

    Prefers coder-family models; among those, picks the largest.
    If no coder model, picks the smallest overall model.
    """
    coders = [m for m in models if m.is_coder]
    if coders:
        return max(coders, key=lambda m: m.parameter_count_b)
    # No coder model — prefer the smallest (fastest) available
    return min(models, key=lambda m: m.parameter_count_b)


def select_models(
    *,
    available_models: list[ModelInfo],
    analysis_type: str | None = None,
    has_template_compiler: bool | None = None,
    prompt_complexity: str | None = None,
) -> tuple[str, str]:
    """Select the best (planner, executor) model pair.

    Args:
        available_models: Models available on the backend.
        analysis_type: Canonical analysis type (if detected).
        has_template_compiler: Whether the analysis type has a deterministic
            template compiler.  Auto-detected from *analysis_type* if ``None``.
        prompt_complexity: Explicit complexity override (``"low"``/``"medium"``/``"high"``).
            Auto-assessed from *analysis_type* if ``None``.

    Returns:
        Tuple of ``(planner_model_name, executor_model_name)``.
    """
    if not available_models:
        # No models discovered — return empty, let caller handle
        return ("", "")

    # --- Single-model mode ---
    if len(available_models) == 1:
        name = available_models[0].name
        logger.info("Model router: single-model mode — using '%s' for both planning and execution", name)
        return (name, name)

    # --- Determine complexity ---
    if has_template_compiler is None:
        at = str(analysis_type or "").strip().lower()
        has_template_compiler = at in _template_compiler_types() if at else False

    if prompt_complexity is None:
        prompt_complexity = assess_prompt_complexity(analysis_type=analysis_type)

    heavy = _best_heavy(available_models)
    fast = _best_fast(available_models)

    # --- Routing decision ---
    if has_template_compiler and prompt_complexity != "high":
        # Template compilers produce deterministic plans; LLM quality less critical
        planner = fast.name
        executor = fast.name
        reason = "template_compiler_available"
    elif prompt_complexity == "high":
        # Complex/unknown task — use best model for planning
        planner = heavy.name
        executor = fast.name
        reason = "high_complexity"
    else:
        # Medium complexity — fast model for both
        planner = fast.name
        executor = fast.name
        reason = "medium_complexity_default"

    # Look up actual ModelInfo for selected models to log correct metadata
    planner_info = next((m for m in available_models if m.name == planner), heavy)
    executor_info = next((m for m in available_models if m.name == executor), fast)
    logger.info(
        "Model router: %s → planner='%s' (%s, %.0fB), executor='%s' (%s, %.0fB)",
        reason, planner, planner_info.tier, planner_info.parameter_count_b,
        executor, executor_info.tier, executor_info.parameter_count_b,
    )
    return (planner, executor)


def select_default_models(models: list[ModelInfo]) -> tuple[str, str]:
    """Select default planner/executor pair without task-specific info.

    Used at BioLLM init when no analysis type is known yet.
    Picks the best fast/coder model for both planning and execution.

    Args:
        models: Available models from :func:`discover_models`.

    Returns:
        ``(planner_model_name, executor_model_name)``.
    """
    if not models:
        return ("", "")
    if len(models) == 1:
        return (models[0].name, models[0].name)

    fast = _best_fast(models)
    logger.info(
        "Model router: default selection → planner='%s' (%.0fB), executor='%s' (%.0fB)",
        fast.name, fast.parameter_count_b, fast.name, fast.parameter_count_b,
    )
    return (fast.name, fast.name)
