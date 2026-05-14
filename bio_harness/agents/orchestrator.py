import logging
import json
import queue
import threading
import importlib
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, TypedDict, Optional, Callable
from pydantic import ValidationError

# Bio-Harness Core Components
from bio_harness.core.analysis_spec import deterministic_analysis_spec, discover_data_files, normalize_analysis_spec, should_generate_analysis_review
from bio_harness.agents import orchestrator_interactive_context as _orchestrator_interactive_context
from bio_harness.agents import orchestrator_parameter_patch as _orchestrator_parameter_patch
from bio_harness.agents import orchestrator_plan_helpers as _orchestrator_plan_helpers
from bio_harness.agents import orchestrator_sessions as _orchestrator_sessions
from bio_harness.agents import orchestrator_skill_loading as _orchestrator_skill_loading
from bio_harness.agents import orchestrator_skill_retrieval as _orchestrator_skill_retrieval
from bio_harness.agents.execution_markers import detect_failure_marker
from bio_harness.core.benchmark_policy import SCIENTIFIC_HARNESS_POLICY, normalize_benchmark_policy
from bio_harness.core.bcftools_shell_semantics import (
    inspect_bcftools_expression_command,
    inspect_bcftools_isec_command,
    repair_bcftools_expression_command,
    repair_bcftools_isec_command,
)
from bio_harness.core.file_manifest import FileManifest
from bio_harness.core.harness_help_context import build_harness_help_context, looks_like_harness_help_query
from bio_harness.core.llm import BioLLM, BioHarnessError, LLMOutputSchema
from bio_harness.core.llm_setup_support import (
    build_llm_setup_report,
    looks_like_llm_setup_query,
    render_llm_setup_text,
)
from bio_harness.core.protocol_grounding import analysis_patch_from_protocol, extract_protocol_grounding
from bio_harness.agents import orchestrator_shell_validation as _orchestrator_shell_validation
from bio_harness.agents import orchestrator_skill_availability as _orchestrator_skill_availability
from bio_harness.agents import orchestrator_validation_helpers as _orchestrator_validation_helpers
from bio_harness.skills.registry import SkillRegistry
from bio_harness.core.runner import CommandRunner
from bio_harness.core.execution_policy import inspect_execution_command
from bio_harness.core.executor_runtime import (
    finish_executor_runtime,
    heartbeat_executor_runtime,
    start_executor_runtime,
)
from bio_harness.core.de_wrapper_semantics import (
    validate_and_repair_de_wrapper_arguments,
)
from bio_harness.core.skill_argument_policy import (
    normalize_execution_arguments,
    normalize_non_bash_run_arguments,
    resolve_execution_working_directory,
    sanitize_harness_managed_arguments,
    validate_non_bash_run_arguments,
)
from bio_harness.core.plan_validation import validate_plan
from bio_harness.core.system import recommend_aligner # For ExecutorNode
from bio_harness.core.tool_env import ensure_pixi_tooling_on_path, requirement_available
from bio_harness.tools.librarian import Librarian # For ResearcherNode
from bio_harness.tools.reader import Reader # For ResearcherNode

logger = logging.getLogger(__name__)

ensure_pixi_tooling_on_path()


def _ordered_tool_names(values: Any) -> list[str]:
    """Return normalized tool names while preserving first-seen order.

    Args:
        values: Sequence-like collection of raw tool-name values.

    Returns:
        Ordered list of lowercase tool names without duplicates.
    """

    if not isinstance(values, list):
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value).strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _trim_planner_skill_selection(
    selected: list[dict[str, Any]],
    *,
    scored_rows: list[tuple[int, str, dict[str, Any]]],
    budget: int,
    protected_names: set[str],
    preferred_tool_order: list[str],
    retrieval_protected: set[str],
    stage_aware_names: set[str],
) -> list[dict[str, Any]]:
    """Trim planner skills without dropping late-appended protected tools.

    Args:
        selected: Candidate selected skills after essential/preferred appends.
        scored_rows: Full scored planner-skill rows in rank order.
        budget: Maximum selected-skill budget.
        protected_names: Names that must be considered before ordinary rows.
        preferred_tool_order: Preferred-tool ordering from the analysis spec.
        retrieval_protected: Retrieval-protected tool names.
        stage_aware_names: Preferred tools with runtime stage metadata.

    Returns:
        Trimmed planner skills honoring protection priority.
    """

    if len(selected) <= budget:
        return list(selected)

    selected_names_in_order = [
        str(skill.get("name", "")).strip().lower()
        for skill in selected
        if isinstance(skill, dict) and str(skill.get("name", "")).strip()
    ]
    selected_name_set = set(selected_names_in_order)
    skill_by_name = {
        str(skill.get("name", "")).strip().lower(): skill
        for skill in selected
        if isinstance(skill, dict) and str(skill.get("name", "")).strip()
    }
    score_rank = {name: index for index, (_, name, _) in enumerate(scored_rows)}
    selected_rank = {name: index for index, name in enumerate(selected_names_in_order)}
    preferred_rank = {name: index for index, name in enumerate(preferred_tool_order)}

    def _priority(name: str) -> tuple[int, int, int, int, str]:
        if name in retrieval_protected:
            return (
                0,
                preferred_rank.get(name, len(preferred_rank)),
                score_rank.get(name, len(score_rank)),
                selected_rank.get(name, len(selected_rank)),
                name,
            )
        if name in preferred_rank:
            return (
                1 if name in stage_aware_names else 2,
                preferred_rank[name],
                score_rank.get(name, len(score_rank)),
                selected_rank.get(name, len(selected_rank)),
                name,
            )
        if name == "bash_run":
            return (
                3,
                preferred_rank.get(name, len(preferred_rank)),
                score_rank.get(name, len(score_rank)),
                selected_rank.get(name, len(selected_rank)),
                name,
            )
        return (
            4,
            preferred_rank.get(name, len(preferred_rank)),
            score_rank.get(name, len(score_rank)),
            selected_rank.get(name, len(selected_rank)),
            name,
        )

    trimmed_names: list[str] = []
    protected_selected = sorted(
        (name for name in selected_names_in_order if name in protected_names),
        key=_priority,
    )
    for name in protected_selected:
        if name in trimmed_names:
            continue
        trimmed_names.append(name)
        if len(trimmed_names) >= budget:
            break

    if len(trimmed_names) < budget:
        for _, name, _ in scored_rows:
            if name not in selected_name_set or name in trimmed_names:
                continue
            trimmed_names.append(name)
            if len(trimmed_names) >= budget:
                break

    if len(trimmed_names) < budget:
        for name in selected_names_in_order:
            if name in trimmed_names:
                continue
            trimmed_names.append(name)
            if len(trimmed_names) >= budget:
                break

    return [
        dict(skill_by_name[name])
        for name in trimmed_names
        if name in skill_by_name
    ]


def _capability_hints_from_discovered_files(
    discovered_files: List[Dict[str, Any]] | None,
) -> List[str]:
    """Infer stable capability hints from discovered input filenames."""

    names = {
        str(item.get("name", "")).strip().lower()
        for item in (discovered_files or [])
        if isinstance(item, dict)
    }
    hints: list[str] = []

    has_proteomics_matrix = any(
        name in {
            "abundance_matrix.csv",
            "abundance_matrix.tsv",
            "protein_abundance.csv",
            "protein_abundance.tsv",
            "intensity_matrix.csv",
            "intensity_matrix.tsv",
        }
        for name in names
    )
    has_metadata = any(
        name in {
            "metadata.csv",
            "metadata.tsv",
            "sample_metadata.csv",
            "sample_metadata.tsv",
        }
        for name in names
    )
    if has_proteomics_matrix and has_metadata:
        hints.extend(["proteomics", "differential_analysis", "group_comparison"])

    has_metabolomics_matrix = any(
        name in {
            "feature_table.csv",
            "feature_table.tsv",
            "peak_table.csv",
            "peak_table.tsv",
            "metabolite_abundance.csv",
            "metabolite_abundance.tsv",
        }
        for name in names
    )
    if has_metabolomics_matrix and has_metadata:
        hints.extend(["metabolomics", "differential_analysis", "group_comparison"])

    has_spatial_h5ad = any(name.endswith(".h5ad") for name in names) and any(
        token in name for name in names for token in ("spatial", "visium", "spot", "domain")
    )
    if has_spatial_h5ad:
        hints.extend(["spatial_transcriptomics", "single_cell_analysis"])

    ordered: list[str] = []
    seen: set[str] = set()
    for hint in hints:
        token = str(hint).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _discover_data_and_reference_files(data_root: str | Path) -> List[Dict[str, str]]:
    """Return discovered task data plus sibling reference assets.

    Many benchmark tasks keep reads/counts under ``data/`` and reference FASTA
    or GFF/GTF files under a sibling ``references/`` directory. The file
    manifest must include both locations so planners and binders can use real
    reference paths instead of inventing placeholders.
    """

    root = Path(data_root).expanduser().resolve(strict=False)
    discovered: List[Dict[str, str]] = []
    seen_paths: set[str] = set()

    def _extend_from(search_root: Path) -> None:
        for item in discover_data_files(search_root):
            path = str(item.get("path", "") or "").strip()
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            discovered.append(item)

    _extend_from(root)
    for reference_root in (root.parent / "references", root.parent.parent / "references"):
        if reference_root.is_dir():
            _extend_from(reference_root)
    return discovered


def _merge_contract_capability_hints(
    contract: Dict[str, Any] | None,
    discovered_files: List[Dict[str, Any]] | None,
) -> Dict[str, Any]:
    """Merge discovered-file capability hints into one contract mapping."""

    merged = dict(contract) if isinstance(contract, dict) else {}
    existing = [
        str(item).strip()
        for item in (merged.get("must_include_capabilities", []) or [])
        if str(item).strip()
    ]
    for hint in _capability_hints_from_discovered_files(discovered_files):
        if hint not in existing:
            existing.append(hint)
    if existing:
        merged["must_include_capabilities"] = existing
    return merged

# --- Agent State Definition ---
class AgentState(TypedDict):
    """
    Represent's the state of the agent during its execution loop.
    """
    user_query: str
    tools_context: str # Full content of SKILL.md files for LLM
    available_skills_metadata: List[Dict] # Metadata from SkillRegistry
    plan: Optional[LLMOutputSchema] # The parsed plan from BioLLM.think
    current_step_idx: int # Index of the current step in the plan (for execution)
    execution_log: List[str] # For storing all log messages from execution
    research_results: List[Dict] # For results from ResearcherNode
    error_message: Optional[str]
    analysis_spec: Optional[Dict[str, Any]]
    planner_mode: str
    seed_plan: Optional[Dict[str, Any]]
    # log_queue is passed directly to execute_plan, not stored in state


class OrchestratorSession(TypedDict):
    session_id: str
    messages: List[Dict[str, str]]
    compact_memory: str
    compactions: int
    last_context: Dict[str, Any]


# --- Orchestrator Class ---

class Orchestrator:
    """Manages the agent, orchestrating planning, research, and execution of bioinformatics tasks.

    Coordinates the full lifecycle: skill loading, LLM-driven planning via BioLLM,
    sandboxed command execution via CommandRunner, and optional research via Librarian/Reader.
    """

    def __init__(
        self,
        skills_dir: Path,
        skill_library_dir: Path,
        email: str = "your.email@example.com",
        model_name: str | None = None,
        host: str | None = None,
        llm_backend: str | None = None,
        planner_trace_dir: str | Path | None = None,
        planner_trace_context: Dict[str, Any] | None = None,
        tool_cards_dir: str | Path | None = None,
    ):
        self.skill_registry = SkillRegistry(skills_dir)
        self.biollm = BioLLM(
            model_name=model_name,
            host=host,
            llm_backend=llm_backend,
            planner_trace_dir=planner_trace_dir,
            planner_trace_context=planner_trace_context,
        )
        self.command_runner = CommandRunner()
        self.email = email
        self.model_name = model_name
        self.host = host
        self.llm_backend = llm_backend
        self._planner_trace_dir = Path(planner_trace_dir).expanduser().resolve() if planner_trace_dir else None
        self._planner_trace_context = dict(planner_trace_context or {})
        env_tool_cards_dir = str(os.getenv("BIO_HARNESS_TOOL_CARDS_DIR", "") or "").strip()
        selected_tool_cards_dir = tool_cards_dir or env_tool_cards_dir
        self.tool_cards_dir = (
            Path(selected_tool_cards_dir).expanduser().resolve(strict=False)
            if str(selected_tool_cards_dir).strip()
            else None
        )
        self.librarian: Optional[Librarian] = None
        self.reader: Optional[Reader] = None
        self.skill_library_dir = skill_library_dir

        self.tools_context = self._load_tools_context()
        self._loaded_skill_functions: Dict[str, Any] = {}
        self._load_skill_functions()
        self._sessions: Dict[str, OrchestratorSession] = {}
        self._context_limit_tokens = 8192
        self._compact_ratio = 0.70

    def configure_planner_trace(
        self,
        planner_trace_dir: str | Path | None,
        planner_trace_context: Dict[str, Any] | None = None,
    ) -> None:
        self._planner_trace_dir = Path(planner_trace_dir).expanduser().resolve() if planner_trace_dir else None
        self._planner_trace_context = dict(planner_trace_context or {})
        self.biollm.configure_planner_trace(planner_trace_dir, planner_trace_context)

    def _planner_trace(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self._planner_trace_dir is None:
            return
        try:
            self._planner_trace_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
            path = self._planner_trace_dir / f"{stamp}_{os.getpid()}_{event_type.lower()}.json"
            event = {
                "event_type": event_type,
                "ts": datetime.now().isoformat(),
                "pid": os.getpid(),
                "trace_context": dict(self._planner_trace_context),
                "payload": payload,
            }
            path.write_text(json.dumps(event, indent=2, ensure_ascii=True), encoding="utf-8")
        except Exception:
            logger.exception("Failed to write planner trace event.")

    def _get_librarian(self) -> Optional[Librarian]:
        if self.librarian is not None:
            return self.librarian
        try:
            self.librarian = Librarian(email=self.email)
            return self.librarian
        except Exception as e:
            logger.error(f"Failed to initialize Librarian: {e}")
            return None

    def _get_reader(self) -> Optional[Reader]:
        if self.reader is not None:
            return self.reader
        try:
            self.reader = Reader(model_name=self.model_name, host=self.host, llm_backend=self.llm_backend)
            return self.reader
        except Exception as e:
            logger.error(f"Failed to initialize Reader: {e}")
            return None

    def _load_tools_context(self) -> str:
        return _orchestrator_skill_loading.load_tools_context(self.skill_registry._skills, logger=logger)

    def _interactive_harness_help_context(self, user_message: str) -> str:
        """Return deterministic harness help context for help-oriented questions."""
        if not looks_like_harness_help_query(user_message):
            return ""
        return build_harness_help_context(
            self.skill_registry._skills,
            compact=True,
            retrieval_query=user_message,
        )

    def _interactive_deterministic_help_answer(self, user_message: str) -> str:
        """Return a deterministic answer for selected help questions.

        Args:
            user_message: Raw user question.

        Returns:
            A deterministic answer string when the question is better handled
            without the model, otherwise an empty string.
        """
        if not looks_like_llm_setup_query(user_message):
            return ""
        report = build_llm_setup_report(
            llm_backend=self.llm_backend or getattr(self.biollm, "backend_name", None),
            model_name=self.model_name or getattr(self.biollm, "model_name", None),
            host=self.host or getattr(self.biollm, "host", None),
            pull_if_missing=False,
        )
        return render_llm_setup_text(report)

    def _load_skill_functions(self) -> None:
        loaded = _orchestrator_skill_loading.load_skill_functions(
            list(self.skill_registry._skills.keys()),
            self.skill_library_dir,
            logger=logger,
        )
        self._loaded_skill_functions.update(loaded)

    def _planner_skill_budget(self) -> int:
        raw = os.getenv("BIO_HARNESS_PLANNER_SKILL_BUDGET", "8")
        return _orchestrator_skill_availability.planner_skill_budget_from_env(raw)

    def _tool_binary_available(self, tool_name: str) -> bool:
        return _orchestrator_skill_availability.tool_binary_available(
            tool_name,
            requirement_checker=requirement_available,
        )

    def _skill_tools_available(self, skill: Dict[str, Any]) -> bool:
        return _orchestrator_skill_availability.skill_tools_available(
            skill,
            tool_available=self._tool_binary_available,
            find_spec=importlib.util.find_spec,
        )

    def _available_skill_metadata(self) -> List[Dict[str, Any]]:
        return _orchestrator_skill_availability.available_skill_metadata(
            self.skill_registry._skills,
            skill_available=self._skill_tools_available,
        )

    def _select_planner_skill_metadata(
        self,
        user_query: str,
        available_skills_metadata: List[Dict[str, Any]],
        analysis_spec: Dict[str, Any] | None = None,
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        skills = [dict(s) for s in (available_skills_metadata or []) if isinstance(s, dict)]
        total = len(skills)
        if total <= 1:
            retrieval_meta = {
                "retrieval_enabled": False,
                "retrieval_profile": "not_needed",
                "retrieval_limit": total,
                "retrieval_selected_skill_names": [],
                "retrieval_protected_skill_names": [],
                "retrieval_matches": [],
            }
            return skills, {
                "total_skills": total,
                "selected_skills": total,
                "budget": total,
                "selection_mode": "all",
                **retrieval_meta,
            }

        budget = min(self._planner_skill_budget(), total)
        retrieval_boosts, retrieval_protected, retrieval_meta = (
            _orchestrator_skill_retrieval.planner_skill_retrieval_boosts(
                user_query,
                skills,
                analysis_spec=analysis_spec,
                model_name=getattr(self, "model_name", None),
                budget=budget,
                tool_cards_dir=getattr(self, "tool_cards_dir", None),
            )
        )
        if budget >= total:
            analysis_preferred = sorted(
                {
                    str(x).strip().lower()
                    for x in ((analysis_spec or {}).get("preferred_tools", []) if isinstance(analysis_spec, dict) else [])
                    if str(x).strip()
                }
            )
            analysis_discouraged = sorted(
                {
                    str(x).strip().lower()
                    for x in ((analysis_spec or {}).get("discouraged_tools", []) if isinstance(analysis_spec, dict) else [])
                    if str(x).strip()
                }
            )
            return skills, {
                "total_skills": total,
                "selected_skills": total,
                "budget": budget,
                "selection_mode": "all",
                "selected_skill_names": [str(s.get("name", "")).strip() for s in skills],
                "analysis_preferred_tools": analysis_preferred,
                "analysis_discouraged_tools": analysis_discouraged,
                **retrieval_meta,
            }
        query_l = str(user_query or "").lower()
        query_tokens = {
            tok
            for tok in re.findall(r"[a-z0-9_]{3,}", query_l)
            if tok not in {"with", "from", "this", "that", "please", "reads", "read", "sample", "samples"}
        }
        essential = {"bash_run"}
        grounding = (
            (analysis_spec or {}).get("protocol_grounding", {})
            if isinstance((analysis_spec or {}).get("protocol_grounding", {}), dict)
            else {}
        )
        grounded_required = {
            str(name).strip().lower()
            for name in (
                list(grounding.get("required_tools", []) or [])
                + list(grounding.get("required_plan_signals", []) or [])
            )
            if str(name).strip()
        }
        essential.update(grounded_required)
        wants_evolution_variant_workflow = (
            ("evolution" in query_l or "evolved" in query_l or "ancestor" in query_l)
            and not any(term in query_l for term in ("differential expression", "splicing", "rna-seq", "transcript"))
        )
        intent_boosts: list[tuple[set[str], tuple[str, ...]]] = [
            ({"subread_align", "featurecounts_run"}, ("subread", "subjunc", "subread-align")),
            ({"deseq2_run", "featurecounts_run", "edger_run", "limma_voom_run"}, ("differential expression", "differential", "deseq2", "edger", "limma")),
            ({"bcftools_call", "gatk_haplotypecaller", "varscan_call", "freebayes_call", "gatk_mutect2_call"}, ("variant", "variant calling", "snv", "indel", "vcf")),
            ({"rmats_run", "dexseq_run", "majiq_run", "bash_run"}, ("splicing", "alternative splicing", "rmats", "dexseq", "majiq")),
            ({"star_align", "hisat2_align", "subread_align", "bwa_mem_align", "bowtie2_align"}, ("alignment", "align", "mapping")),
            ({"spades_assemble", "flye_assemble"}, ("assemble", "assembly", "scaffolds", "contigs")),
            ({"prokka_annotate", "prodigal_annotate", "snpeff_annotate", "vep_annotate"}, ("annotate", "annotation", "impact", "effect", "severity")),
        ]
        wants_splicing = any(phrase in query_l for phrase in ("splicing", "alternative splicing", "rmats", "dexseq", "majiq"))
        preferred_tool_order = _ordered_tool_names(
            ((analysis_spec or {}).get("preferred_tools", []) if isinstance(analysis_spec, dict) else [])
        )
        preferred_tools = set(preferred_tool_order)
        discouraged_tools = {
            str(x).strip().lower()
            for x in ((analysis_spec or {}).get("discouraged_tools", []) if isinstance(analysis_spec, dict) else [])
            if str(x).strip()
        }
        chosen_method = str((analysis_spec or {}).get("chosen_method", "") if isinstance(analysis_spec, dict) else "").strip().lower()
        chosen_parts = {part.strip() for part in chosen_method.split("+") if part.strip()}
        try:
            from bio_harness.core.tool_registry import default_tool_registry

            registry = default_tool_registry()
        except Exception:
            registry = None
        scored: List[tuple[int, str, Dict[str, Any]]] = []
        for skill in skills:
            name = str(skill.get("name", "")).strip()
            name_l = name.lower()
            desc_l = str(skill.get("description", "")).lower()
            score = 0
            if name_l in essential:
                score += 100
            if name_l == "fallback_skill_builder" and not any(tok in query_l for tok in ("fallback", "template", "coverage")):
                score -= 80
            if name_l and name_l in query_l:
                score += 40
            for token in query_tokens:
                if token in name_l:
                    score += 8
                elif token in desc_l:
                    score += 3
            for name_part in [p for p in re.split(r"[^a-z0-9]+", name_l) if len(p) >= 3]:
                if name_part in query_tokens:
                    score += 4
            for boosted_names, phrases in intent_boosts:
                if name_l not in boosted_names:
                    continue
                if any(phrase in query_l for phrase in phrases):
                    score += 26
            if name_l in preferred_tools:
                score += 34
            if name_l in chosen_parts:
                score += 42
            if name_l in discouraged_tools:
                score -= 52
            score += retrieval_boosts.get(name_l, 0)
            if name_l in preferred_tools and retrieval_boosts.get(name_l, 0):
                score += 12
            if registry is not None:
                meta = registry.get(name_l)
                alternative_tools = {
                    str(item).strip().lower()
                    for item in ((meta.alternative_tools if meta is not None else []) or [])
                    if str(item).strip()
                }
                if name_l not in preferred_tools and alternative_tools & preferred_tools:
                    score -= 20
            if any(phrase in query_l for phrase in ("differential expression", "differential")):
                if name_l in {"deseq2_run", "featurecounts_run"}:
                    score += 18
                elif name_l in {"edger_run", "limma_voom_run"}:
                    score += 6
            if "variant" in query_l or "variant calling" in query_l:
                if name_l in {"bcftools_call", "gatk_haplotypecaller", "varscan_call"}:
                    score += 18
                elif name_l in {"freebayes_call", "gatk_mutect2_call"}:
                    score += 6
            if wants_evolution_variant_workflow:
                if name_l == "spades_assemble":
                    score += 48
                elif name_l in {"bwa_mem_align", "prodigal_annotate", "snpeff_annotate", "bcftools_call"}:
                    score += 38
                elif name_l in {"flye_assemble", "vep_annotate", "prokka_annotate"}:
                    score += 12
                elif name_l in {"fastqc_run"}:
                    score += 4
                elif name_l in {"gatk_haplotypecaller", "gatk_mutect2_call"}:
                    score -= 18
                elif name_l == "varscan_call":
                    score -= 8
                elif name_l == "freebayes_call":
                    score += 18
                elif name_l in {
                    "star_align",
                    "star_2pass_align",
                    "subread_align",
                    "hisat2_align",
                    "featurecounts_run",
                    "deseq2_run",
                    "edger_run",
                    "limma_voom_run",
                    "rmats_run",
                    "dexseq_run",
                    "majiq_run",
                    "star_solo_count",
                    "salmon_quant",
                    "kallisto_quant",
                }:
                    score -= 24
            if wants_splicing:
                if name_l == "rmats_run":
                    score += 36
                elif name_l in {"dexseq_run", "majiq_run"}:
                    score += 30
                elif name_l in {"limma_voom_run", "deseq2_run", "edger_run"}:
                    score -= 4
            # Enriched metadata scoring: analysis_categories + input_types
            analysis_type = str((analysis_spec or {}).get("analysis_type", "") if isinstance(analysis_spec, dict) else "").strip().lower()
            skill_categories = [str(c).lower() for c in (skill.get("analysis_categories") or [])]
            if analysis_type and skill_categories:
                if analysis_type in skill_categories:
                    score += 50
                elif any(cat in analysis_type or analysis_type in cat for cat in skill_categories):
                    score += 20
            skill_input_types = set(str(t).lower() for t in (skill.get("input_types") or []))
            if skill_input_types and analysis_spec and isinstance(analysis_spec, dict):
                discovered = analysis_spec.get("discovered_data_files") or []
                if isinstance(discovered, list):
                    data_exts = set()
                    for df in discovered:
                        dname = str(df.get("name", "") if isinstance(df, dict) else df).lower()
                        if any(dname.endswith(ext) for ext in (".fastq", ".fq", ".fastq.gz", ".fq.gz")):
                            data_exts.add("fastq")
                        elif any(dname.endswith(ext) for ext in (".fa", ".fasta", ".fa.gz", ".fasta.gz")):
                            data_exts.add("fasta_reference")
                        elif any(dname.endswith(ext) for ext in (".vcf", ".vcf.gz")):
                            data_exts.add("vcf")
                        elif dname.endswith((".gff", ".gff3", ".gff.gz")):
                            data_exts.add("gff")
                        elif dname.endswith((".gtf", ".gtf.gz")):
                            data_exts.add("gtf")
                        elif dname.endswith((".bam", ".cram")):
                            data_exts.add("bam")
                        elif dname.endswith(".h5ad"):
                            data_exts.add("h5ad")
                    if data_exts and skill_input_types & data_exts:
                        score += 30
            scored.append((score, name_l, skill))

        scored.sort(key=lambda x: (-x[0], x[1]))
        selected: List[Dict[str, Any]] = [row[2] for row in scored[:budget]]
        selected_names = {str(s.get("name", "")).strip().lower() for s in selected}
        for skill in skills:
            name_l = str(skill.get("name", "")).strip().lower()
            if name_l in essential and name_l not in selected_names:
                selected.append(skill)
                selected_names.add(name_l)
        if preferred_tool_order:
            skills_by_name = {
                str(skill.get("name", "")).strip().lower(): skill
                for skill in skills
                if str(skill.get("name", "")).strip()
            }
            for name_l in preferred_tool_order:
                skill = skills_by_name.get(name_l)
                if skill is None or name_l in selected_names:
                    continue
                selected.append(skill)
                selected_names.add(name_l)
        if wants_splicing and not any(name in selected_names for name in {"rmats_run", "dexseq_run", "majiq_run"}):
            for preferred_name in ("rmats_run", "dexseq_run", "majiq_run"):
                for skill in skills:
                    name_l = str(skill.get("name", "")).strip().lower()
                    if name_l == preferred_name and name_l not in selected_names:
                        selected.append(skill)
                        selected_names.add(name_l)
                        break
                if any(name in selected_names for name in {"rmats_run", "dexseq_run", "majiq_run"}):
                    break
        if len(selected) > budget:
            protected = {"bash_run"}
            protected.update(retrieval_protected)
            protected.update(preferred_tools)
            if wants_splicing:
                protected.update({"rmats_run", "dexseq_run", "majiq_run"})
            stage_aware_names = {
                name
                for name in preferred_tools
                if registry is not None
                and (meta := registry.get(name)) is not None
                and (list(getattr(meta, "consumes_stages", []) or []) or list(getattr(meta, "produces_stages", []) or []))
            }
            selected = _trim_planner_skill_selection(
                selected,
                scored_rows=scored,
                budget=budget,
                protected_names=protected,
                preferred_tool_order=preferred_tool_order,
                retrieval_protected=retrieval_protected,
                stage_aware_names=stage_aware_names,
            )

        return selected, {
            "total_skills": total,
            "selected_skills": len(selected),
            "budget": budget,
            "selection_mode": "query_weighted_subset",
            "selected_skill_names": [str(s.get("name", "")).strip() for s in selected],
            "analysis_preferred_tools": sorted(preferred_tools),
            "analysis_discouraged_tools": sorted(discouraged_tools),
            **retrieval_meta,
        }

    # --- Node Logic (encapsulated in Orchestrator methods) ---
    def _planner_node(self, state: AgentState) -> AgentState:
        """
        Logic for the planner node. Generates an execution plan based on the user query.
        """
        logger.info("Entering PlannerNode...")
        user_query = state["user_query"]
        available_skills_metadata = state["available_skills_metadata"]
        analysis_spec = state.get("analysis_spec", None)
        planner_skills, selection_meta = self._select_planner_skill_metadata(user_query, available_skills_metadata, analysis_spec=analysis_spec)
        logger.info(
            "Planner skill selection: mode=%s selected=%s/%s budget=%s",
            selection_meta.get("selection_mode", ""),
            selection_meta.get("selected_skills", 0),
            selection_meta.get("total_skills", 0),
            selection_meta.get("budget", 0),
        )
        self._planner_trace(
            "SKILL_SELECTION",
            {
                **selection_meta,
                "user_query": user_query,
                "selected_skill_names": [str(s.get("name", "")).strip() for s in planner_skills],
            },
        )
        
        planner_mode = str(state.get("planner_mode", "auto") or "auto")
        seed_plan = state.get("seed_plan", None)
        planner_model_override = state.get("model_override", None)
        try:
            plan_output = self.biollm.think(
                user_query,
                planner_skills,
                analysis_spec=analysis_spec,
                planner_mode=planner_mode,
                seed_plan=seed_plan if isinstance(seed_plan, dict) else None,
                model_override=planner_model_override,
            )
            # Post-plan parameter patching
            plan_output = self._post_plan_parameter_patch(plan_output, analysis_spec)
            return {**state, "plan": plan_output, "current_step_idx": 0, "error_message": None, "analysis_spec": analysis_spec}
        except BioHarnessError as e:
            logger.error(f"PlannerNode failed: {e}")
            return {**state, "error_message": str(e), "plan": None}

    _PARAMETER_KNOWLEDGE_BASE = _orchestrator_parameter_patch.PARAMETER_KNOWLEDGE_BASE

    def _post_plan_parameter_patch(
        self,
        plan: Dict[str, Any],
        analysis_spec: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        return _orchestrator_parameter_patch.post_plan_parameter_patch(
            plan,
            analysis_spec,
            knowledge_base=self._PARAMETER_KNOWLEDGE_BASE,
        )

    def build_analysis_spec(
        self,
        user_query: str,
        contract: Dict[str, Any] | None = None,
        *,
        selected_dir: str | None = None,
        data_root: str | None = None,
        project_root: str | None = None,
        benchmark_policy: str = SCIENTIFIC_HARNESS_POLICY,
        analysis_type_override: str | None = None,
    ) -> Dict[str, Any]:
        """Build a normalized analysis specification for a user request.

        Args:
            user_query: User-facing task request.
            contract: Optional request contract inferred from the prompt.
            selected_dir: Optional run output directory for grounding.
            data_root: Optional input-data root for grounding.
            project_root: Optional project root for protocol discovery.
            benchmark_policy: Active benchmark-assistance policy.
            analysis_type_override: Optional explicit analysis type supplied by
                a trusted caller, such as a benchmark manifest. The override
                controls analysis-family routing without replacing the
                scientific plan.

        Returns:
            Normalized analysis-spec payload.
        """

        benchmark_policy = normalize_benchmark_policy(benchmark_policy)
        available_skills = self._available_skill_metadata()
        available_skill_names = [str(skill.get("name", "")).strip() for skill in available_skills if isinstance(skill, dict)]
        deterministic_warnings: list[dict[str, str]] = []

        def _record_deterministic_warning(subsystem: str, exc: Exception) -> None:
            message = str(exc).strip() or exc.__class__.__name__
            deterministic_warnings.append(
                {
                    "subsystem": subsystem,
                    "exception_class": exc.__class__.__name__,
                    "message": message,
                }
            )

        discovered_files: list[dict[str, Any]] = []
        if data_root:
            try:
                discovered_files = _discover_data_and_reference_files(data_root)
            except Exception as exc:
                _record_deterministic_warning("data_discovery", exc)
                discovered_files = []
        hinted_contract = _merge_contract_capability_hints(contract, discovered_files)
        fallback = deterministic_analysis_spec(
            user_query,
            contract=hinted_contract,
            available_skill_names=available_skill_names,
            discovered_data_files=discovered_files,
        )
        requested_analysis_type = str(analysis_type_override or "").strip()
        if requested_analysis_type:
            fallback = normalize_analysis_spec(
                {**fallback, "analysis_type": requested_analysis_type},
                user_query=user_query,
                contract=hinted_contract,
                available_skill_names=available_skill_names,
                benchmark_policy=benchmark_policy,
                discovered_data_files=discovered_files,
            )
        fallback["benchmark_policy"] = benchmark_policy
        if data_root:
            fallback["discovered_data_files"] = discovered_files

        protocol_grounding = {}
        if selected_dir and data_root and project_root:
            try:
                protocol_grounding = extract_protocol_grounding(
                    user_query=user_query,
                    analysis_type=str(fallback.get("analysis_type", "") or "").strip(),
                    selected_dir=Path(selected_dir),
                    data_root=Path(data_root),
                    project_root=Path(project_root),
                    available_skill_names=available_skill_names,
                    benchmark_policy=benchmark_policy,
                    analysis_spec=fallback,
                    contract=hinted_contract,
                )
            except Exception as exc:
                _record_deterministic_warning("protocol_grounding", exc)
                protocol_grounding = {}
        patch: Dict[str, Any] = {}
        if protocol_grounding:
            patch = analysis_patch_from_protocol(
                protocol_grounding,
                available_skill_names=available_skill_names,
                analysis_spec=fallback,
            )
        fallback = normalize_analysis_spec(
            {**fallback, **patch},
            user_query=user_query,
            contract=hinted_contract,
            available_skill_names=available_skill_names,
            benchmark_policy=benchmark_policy,
            discovered_data_files=discovered_files,
        )
        fallback["deterministic_warnings"] = list(deterministic_warnings)
        if selected_dir:
            fallback["selected_dir"] = str(Path(selected_dir).resolve(strict=False))
        if data_root:
            try:
                fallback["discovered_data_files"] = discovered_files
                # Build FileManifest for role-based path resolution
                analysis_type = str(fallback.get("analysis_type", "") or "").strip()
                manifest = FileManifest.from_discovered_files(
                    discovered_files,
                    analysis_type=analysis_type,
                    output_dir=str(fallback.get("selected_dir", "") or ""),
                )
                fallback["file_manifest"] = manifest
                # Graph-based pipeline suggestion
                try:
                    from bio_harness.core.capability_graph import CapabilityGraph
                    graph = CapabilityGraph.default()
                    available_file_types = list(manifest.file_types())
                    preferred = [str(x).strip() for x in (fallback.get("preferred_tools", []) or []) if str(x).strip()]
                    pipeline = graph.trace_pipeline_for_analysis(analysis_type, available_file_types)
                    if pipeline:
                        fallback["graph_pipeline_text"] = graph.format_pipeline_for_prompt(pipeline, preferred_tools=preferred)
                        fallback["graph_pipeline_skeleton"] = graph.suggest_plan_skeleton(analysis_type, available_file_types, preferred_tools=preferred)
                except Exception as exc:
                    _record_deterministic_warning("capability_graph", exc)
            except Exception as exc:
                if not any(row["subsystem"] == "data_discovery" for row in deterministic_warnings):
                    _record_deterministic_warning("data_discovery", exc)
            fallback["deterministic_warnings"] = list(deterministic_warnings)
        if not should_generate_analysis_review(user_query, hinted_contract):
            return fallback
        fallback_analysis_type = str(fallback.get("analysis_type", "") or "").strip()
        fallback_method = str(fallback.get("chosen_method", "") or "").strip()
        # Deterministic-first policy: if the seeded analysis brief already
        # resolved to a concrete non-generic method, keep the harness-owned
        # brief and avoid an additional LLM review hop.
        if fallback_analysis_type and fallback_analysis_type != "generic_analysis" and fallback_method:
            return fallback
        backend_reachable = getattr(self.biollm, "backend_reachable", None)
        if callable(backend_reachable) and not backend_reachable(timeout_seconds=0.5):
            return fallback
        try:
            reviewed = self.biollm.design_analysis(
                user_query,
                available_skills,
                contract=hinted_contract,
                fallback_spec=fallback,
            )
            reviewed_base = normalize_analysis_spec(
                {**fallback, **(reviewed if isinstance(reviewed, dict) else {})},
                user_query=user_query,
                contract=hinted_contract,
                available_skill_names=available_skill_names,
                benchmark_policy=benchmark_policy,
                discovered_data_files=discovered_files,
            )
            # If the LLM returned a different analysis_type than the fallback,
            # the protocol_grounding (built from fallback.analysis_type) may be
            # for the wrong analysis type.  Recompute grounding using the
            # LLM's analysis_type to avoid cross-contamination (e.g. germline
            # grounding blocking a variant_annotation template).
            reviewed_at = str((reviewed_base or {}).get("analysis_type", "") or "").strip()
            fallback_at = str(fallback.get("analysis_type", "") or "").strip()
            if reviewed_at and reviewed_at != fallback_at and selected_dir and data_root and project_root:
                try:
                    protocol_grounding = extract_protocol_grounding(
                        user_query=user_query,
                        analysis_type=reviewed_at,
                        selected_dir=Path(selected_dir),
                        data_root=Path(data_root),
                        project_root=Path(project_root),
                        available_skill_names=available_skill_names,
                        benchmark_policy=benchmark_policy,
                        analysis_spec=reviewed_base,
                        contract=hinted_contract,
                    )
                except Exception as exc:
                    _record_deterministic_warning("review_protocol_grounding", exc)
            if protocol_grounding:
                patch = analysis_patch_from_protocol(
                    protocol_grounding,
                    available_skill_names=available_skill_names,
                    analysis_spec=reviewed_base,
                )
                reviewed_base = normalize_analysis_spec(
                    {**reviewed_base, **patch},
                    user_query=user_query,
                    contract=hinted_contract,
                    available_skill_names=available_skill_names,
                    benchmark_policy=benchmark_policy,
                    discovered_data_files=reviewed_base.get("discovered_data_files", discovered_files),
                )
            if data_root and "discovered_data_files" not in reviewed_base:
                reviewed_base["discovered_data_files"] = fallback.get("discovered_data_files", [])
            if "file_manifest" not in reviewed_base and "file_manifest" in fallback:
                reviewed_base["file_manifest"] = fallback["file_manifest"]
            if selected_dir:
                reviewed_base["selected_dir"] = str(Path(selected_dir).resolve(strict=False))
            reviewed_base["benchmark_policy"] = benchmark_policy
            reviewed_base["deterministic_warnings"] = list(deterministic_warnings)
            return reviewed_base
        except Exception as exc:
            _record_deterministic_warning("analysis_review", exc)
            fallback["deterministic_warnings"] = list(deterministic_warnings)
            return fallback

    def _researcher_node(self, state: AgentState) -> AgentState:
        """
        Logic for the researcher node. Performs research based on the user query or agent's need for info.
        """
        logger.info("Entering ResearcherNode...")
        user_query = state["user_query"].lower()
        new_research_results = []
        librarian = self._get_librarian()
        reader = self._get_reader()
        if "pubmed" in user_query or "paper" in user_query or "article" in user_query:
            if librarian is None:
                return {**state, "research_results": [{"source": "Researcher", "result": "Librarian tool unavailable."}]}
            query = user_query.replace("pubmed", "").replace("paper", "").replace("article", "").strip()
            pubmed_res = librarian.pubmed_search(query)
            new_research_results.extend([{"source": "PubMed", "result": res} for res in pubmed_res])
            logger.info(f"Performed PubMed research for: {query}")
        elif "highly cited" in user_query or "citation" in user_query:
            if librarian is None:
                return {**state, "research_results": [{"source": "Researcher", "result": "Librarian tool unavailable."}]}
            query = user_query.replace("highly cited", "").replace("citation", "").strip()
            citation_res = librarian.citation_search(query)
            new_research_results.extend([{"source": "SemanticScholar", "result": res} for res in citation_res])
            logger.info(f"Performed Semantic Scholar research for: {query}")
        elif "tool" in user_query or "software" in user_query:
            if librarian is None:
                return {**state, "research_results": [{"source": "Researcher", "result": "Librarian tool unavailable."}]}
            query = user_query.replace("tool", "").replace("software", "").strip()
            tool_res = librarian.tool_search(query)
            new_research_results.extend([{"source": "DuckDuckGoTools", "result": res} for res in tool_res])
            logger.info(f"Performed Tool research for: {query}")
        elif any(k in user_query for k in ("reference genome", "gtf", "fasta", "encode", "ensembl", "ncbi")):
            if librarian is None:
                return {**state, "research_results": [{"source": "Researcher", "result": "Librarian tool unavailable."}]}
            query = (
                user_query.replace("reference genome", "")
                .replace("gtf", "")
                .replace("fasta", "")
                .strip()
            )
            ref_res = librarian.web_search(query, allowed_domains=librarian.default_reference_domains)
            new_research_results.extend([{"source": "ReferenceSearch", "result": res} for res in ref_res])
            logger.info(f"Performed reference-domain research for: {query}")
        elif "read pdf" in user_query or "parse pdf" in user_query:
            # This would typically require a file path from the user, not just keywords
            if reader is None:
                new_research_results.append({"source": "Reader", "result": "Reader tool unavailable."})
            else:
                new_research_results.append({"source": "Reader", "result": "PDF parsing requires a file path from the user."})
            logger.warning("PDF parsing requested, but no file path provided in simplified ResearcherNode.")
        else:
            new_research_results.append({"source": "Researcher", "result": "No specific research action triggered by query."})
        
        return {**state, "research_results": state.get("research_results", []) + new_research_results}

    def _executor_node(
        self,
        state: AgentState,
        log_queue: queue.Queue,
        cwd: Optional[str] = None,
        allowed_root: Optional[str] = None,
        step_contracts: Optional[Dict[int, Dict[str, Any]]] = None,
        event_writer: Optional[Callable[[Dict[str, Any]], None]] = None,
        run_id: Optional[str] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> AgentState:
        """
        Logic for the executor node. Executes a plan generated by the LLM.
        Crucially, it checks core.system.recommend_aligner before running memory-heavy tools.
        """
        logger.info("Entering ExecutorNode...")
        plan = state["plan"]
        current_step_idx = state["current_step_idx"]
        execution_log = state.get("execution_log", [])

        if not plan or not plan.plan:
            execution_log.append("Error: No plan available for execution.\n")
            log_queue.put("Error: No plan available for execution.\n")
            log_queue.put(None)
            return {**state, "execution_log": execution_log, "error_message": "No plan available."}

        try:
            if isinstance(plan, LLMOutputSchema):
                validated_plan = plan
            else:
                validated_plan = LLMOutputSchema(**plan)
        except ValidationError as e:
            execution_log.append(f"Error: Invalid plan JSON received: {e}\n")
            log_queue.put(f"Error: Invalid plan JSON received: {e}\n")
            log_queue.put(None)
            return {**state, "execution_log": execution_log, "error_message": f"Invalid plan: {e}"}

        # Execute steps from current_step_idx onwards
        for i in range(current_step_idx, len(validated_plan.plan)):
            if stop_event is not None and stop_event.is_set():
                error_msg = "Execution cancelled by supervisor before step start.\n"
                execution_log.append(error_msg)
                log_queue.put(error_msg)
                log_queue.put(None)
                return {**state, "execution_log": execution_log, "error_message": error_msg, "current_step_idx": i}
            step = validated_plan.plan[i]
            tool_name = step.tool_name
            arguments = dict(step.arguments or {})
            step_id = step.step_id
            current_run_id = run_id or "unknown"

            log_queue.put(
                f"[Step {step_id} Output] [status] phase=validating tool={tool_name}\n"
            )

            # Per-step validation agent before execution.
            validation = self._step_validation_agent(step, cwd)
            if event_writer is not None:
                event_writer(
                    {
                        "ts": datetime.now().isoformat(),
                        "run_id": current_run_id,
                        "step_id": step_id,
                        "agent": "StepValidationAgent",
                        "event_type": "STEP_HEARTBEAT",
                        "severity": "info",
                        "payload": {
                            "validation_passed": validation.get("passed", False),
                            "issues": validation.get("issues", []),
                            "fixes": validation.get("fixes", []),
                        },
                    }
                )
            if not validation.get("passed", False):
                issues = validation.get("issues", [])
                # Only skip when (a) next step covers the same capability AND
                # (b) the blocking issues are "soft" (syntax errors, not missing
                # inputs/tools which the next step also needs).
                _HARD_ISSUE_PREFIXES = ("missing_input", "missing_tool", "empty_input")
                has_hard_issues = any(
                    i.startswith(_HARD_ISSUE_PREFIXES) for i in issues
                )
                can_skip_validation = False
                if not has_hard_issues and i + 1 < len(validated_plan.plan):
                    next_step = validated_plan.plan[i + 1]
                    next_tool = str(
                        next_step.get("tool_name", "")
                        if isinstance(next_step, dict)
                        else getattr(next_step, "tool_name", "")
                    ).lower()
                    can_skip_validation = self._steps_cover_same_capability(
                        tool_name, next_tool, str(arguments.get("command", "")),
                    )
                if can_skip_validation:
                    skip_msg = (
                        f"[WARN] Step {step_id} ({tool_name}) blocked by validation "
                        f"({', '.join(issues)}) but next step covers same capability — skipping.\n"
                    )
                    execution_log.append(skip_msg)
                    log_queue.put(skip_msg)
                    if event_writer is not None:
                        event_writer(
                            {
                                "ts": datetime.now().isoformat(),
                                "run_id": current_run_id,
                                "step_id": step_id,
                                "agent": "StepValidationAgent",
                                "event_type": "STEP_SKIPPED",
                                "severity": "warning",
                                "payload": {
                                    "tool_name": tool_name,
                                    "skip_reason": "next_step_covers_capability",
                                    "issues": issues,
                                },
                            }
                        )
                    continue  # skip to next step

                marker = "__VALIDATION_BLOCK__:" + "|".join([str(x) for x in issues]) if issues else "__VALIDATION_BLOCK__"
                error_msg = (
                    f"Step {step_id} blocked by validation agent. "
                    f"Issues: {', '.join(issues)}\n"
                )
                execution_log.append(error_msg)
                log_queue.put(error_msg)
                log_queue.put(f"[Step {step_id} Output] [stderr] {marker}\n")
                log_queue.put("[exit_code=125]\n")
                if event_writer is not None:
                    event_writer(
                        {
                            "ts": datetime.now().isoformat(),
                            "run_id": current_run_id,
                            "step_id": step_id,
                            "agent": "StepValidationAgent",
                            "event_type": "STEP_BLOCKED",
                            "severity": "error",
                            "payload": {
                                "failure_class": "validation_block",
                                "exit_code": 125,
                                "status": "failed",
                                "issues": issues,
                                "fixes": validation.get("fixes", []),
                            },
                        }
                    )
                    event_writer(
                        {
                            "ts": datetime.now().isoformat(),
                            "run_id": current_run_id,
                            "step_id": step_id,
                            "agent": "ExecutorAgent",
                            "event_type": "STEP_FINISHED",
                            "severity": "error",
                            "payload": {
                                "tool_name": tool_name,
                                "exit_code": 125,
                                "failure_class": "validation_block",
                            },
                        }
                    )
                log_queue.put(None)
                return {**state, "execution_log": execution_log, "error_message": error_msg, "current_step_idx": i}

            arguments = validation.get("arguments", arguments)

            if event_writer is not None:
                event_writer(
                    {
                        "ts": datetime.now().isoformat(),
                        "run_id": current_run_id,
                        "step_id": step_id,
                        "agent": "ExecutorAgent",
                        "event_type": "STEP_STARTED",
                        "severity": "info",
                        "payload": {"tool_name": tool_name},
                    }
                )

            execution_log.append(f"--- Executing Step {step_id}: {tool_name} ---\n")
            log_queue.put(f"--- Executing Step {step_id}: {tool_name} ---\n")
            
            # --- Smart Recommendation for memory-heavy tools ---
            # For simplicity, hardcode check for fastqc_run as an example
            # A more robust system would involve skill metadata to mark memory-intensive tools.
            if tool_name == "fastqc_run":
                # This check is more relevant for aligners like STAR/BWA.
                dummy_genome_size_gb = 3.2 # Assume human genome size for recommendation check
                aligner_recommendation = recommend_aligner(dummy_genome_size_gb)
                
                if "Insufficient RAM" in aligner_recommendation:
                    error_msg = f"Pre-execution check failed: {aligner_recommendation}. Cannot proceed with {tool_name}."
                    execution_log.append(error_msg + "\n")
                    log_queue.put(error_msg + "\n")
                    log_queue.put(None)
                    return {**state, "execution_log": execution_log, "error_message": error_msg, "current_step_idx": i}


            if tool_name not in self._loaded_skill_functions:
                error_msg = f"Error: Unknown tool '{tool_name}'. Skipping step {step_id}.\n"
                execution_log.append(error_msg)
                log_queue.put(error_msg)
                if event_writer is not None:
                    event_writer(
                        {
                            "ts": datetime.now().isoformat(),
                            "run_id": current_run_id,
                            "step_id": step_id,
                            "agent": "ExecutorAgent",
                            "event_type": "TOOL_MISSING",
                            "severity": "error",
                            "payload": {"tool_name": tool_name, "reason": "unknown_tool"},
                        }
                    )
                log_queue.put(None)
                return {**state, "execution_log": execution_log, "error_message": error_msg, "current_step_idx": i}

            try:
                skill_func = self._loaded_skill_functions[tool_name]

                # Defensive: strip step-level keys that may have leaked into
                # the arguments dict due to plan merging or serialisation edge
                # cases.  These keys belong on the step envelope, not inside
                # the tool's arguments.
                _STEP_ENVELOPE_KEYS = frozenset({
                    "step_id", "tool_name", "purpose", "step_purpose",
                    "deliverables", "expected_files", "validation_method",
                    "success_criteria", "canonicalized_to",
                })
                for _sek in _STEP_ENVELOPE_KEYS:
                    arguments.pop(_sek, None)

                skill_arguments = normalize_execution_arguments(tool_name, arguments, cwd=cwd)
                if tool_name == "minimap2_align" and cwd and "execution_cwd" not in skill_arguments:
                    skill_arguments["execution_cwd"] = str(cwd)
                command_cwd = resolve_execution_working_directory(
                    tool_name,
                    skill_arguments,
                    cwd=cwd,
                )
                if tool_name == "bash_run" and command_cwd and allowed_root:
                    command_cwd_path = Path(command_cwd).expanduser().resolve(strict=False)
                    root_path = Path(allowed_root).expanduser().resolve(strict=False)
                    try:
                        command_cwd_path.relative_to(root_path)
                    except ValueError:
                        pass
                    else:
                        command_cwd_path.mkdir(parents=True, exist_ok=True)

                # Call the skill function to get the command string
                log_queue.put(
                    f"[Step {step_id} Output] [status] phase=rendering_command tool={tool_name}\n"
                )
                command_to_execute = skill_func(**skill_arguments)

                # Use CommandRunner to execute the command asynchronously
                temp_log_queue = queue.Queue() # Queue specific to this command
                command_cancel_event = threading.Event()
                log_queue.put(
                    f"[Step {step_id} Output] [status] phase=launching_runner tool={tool_name}\n"
                )
                command_thread = threading.Thread(
                    target=self.command_runner.run_command,
                    args=(command_to_execute, temp_log_queue, command_cwd, allowed_root),
                    kwargs={"cancel_event": command_cancel_event},
                )
                command_thread.daemon = True # Ensure thread doesn't prevent program exit
                command_thread.start()

                step_exit_code = None
                policy_blocked = False
                policy_block_reason = ""
                semantic_failure_marker = ""
                while True:
                    if stop_event is not None and stop_event.is_set():
                        command_cancel_event.set()
                    try:
                        log_line = temp_log_queue.get(timeout=1.0)
                    except queue.Empty:
                        if command_thread.is_alive():
                            continue
                        break
                    if log_line is None:
                        break
                    stripped = log_line.strip()
                    if stripped.startswith("[exit_code=") and stripped.endswith("]"):
                        try:
                            step_exit_code = int(stripped.removeprefix("[exit_code=").removesuffix("]"))
                        except ValueError:
                            step_exit_code = None
                    if "[status] running" in stripped and event_writer is not None:
                        event_writer(
                            {
                                "ts": datetime.now().isoformat(),
                                "run_id": current_run_id,
                                "step_id": step_id,
                                "agent": "ExecutorAgent",
                                "event_type": "STEP_HEARTBEAT",
                                "severity": "info",
                                "payload": {"status_line": stripped},
                            }
                        )
                    if "__NO_FASTQ_FOUND__" in stripped and event_writer is not None:
                        event_writer(
                            {
                                "ts": datetime.now().isoformat(),
                                "run_id": current_run_id,
                                "step_id": step_id,
                                "agent": "InputResolverAgent",
                                "event_type": "INPUT_SCOPE_EMPTY",
                                "severity": "warning",
                                "payload": {"marker": "__NO_FASTQ_FOUND__"},
                            }
                        )
                    failure_marker = detect_failure_marker(stripped)
                    if failure_marker:
                        semantic_failure_marker = semantic_failure_marker or failure_marker
                        if event_writer is not None:
                            event_writer(
                                {
                                    "ts": datetime.now().isoformat(),
                                    "run_id": current_run_id,
                                    "step_id": step_id,
                                    "agent": "ExecutorAgent",
                                    "event_type": "STEP_BLOCKED",
                                    "severity": "error",
                                    "payload": {
                                        "failure_class": "semantic_step_failure",
                                        "marker": failure_marker,
                                    },
                                }
                            )
                    if "__POLICY_BLOCK__:" in stripped:
                        policy_blocked = True
                        policy_block_reason = stripped.split("__POLICY_BLOCK__:", 1)[-1].strip()
                        if event_writer is not None:
                            event_writer(
                                {
                                    "ts": datetime.now().isoformat(),
                                    "run_id": current_run_id,
                                    "step_id": step_id,
                                    "agent": "PolicyAgent",
                                    "event_type": "STEP_BLOCKED",
                                    "severity": "error",
                                    "payload": {
                                        "failure_class": "policy_block",
                                        "reason": policy_block_reason,
                                    },
                                }
                            )
                    execution_log.append(f"[Step {step_id} Output] {log_line}")
                    log_queue.put(f"[Step {step_id} Output] {log_line}")

                command_thread.join() # Wait for the command to finish
                if semantic_failure_marker and step_exit_code in {None, 0}:
                    step_exit_code = 65
                if stop_event is not None and stop_event.is_set():
                    try:
                        from bio_harness.core.artifact_inspectors import (
                            _extract_expected_outputs,
                        )
                        from bio_harness.core.step_completion import (
                            write_step_completion_manifest,
                        )

                        write_step_completion_manifest(
                            tool_name=tool_name,
                            step_arguments=arguments,
                            cwd=Path(cwd) if cwd else None,
                            outputs=_extract_expected_outputs(
                                {"tool_name": tool_name, "arguments": arguments}
                            ),
                            exit_code=step_exit_code if step_exit_code is not None else 1,
                            success=False,
                            error="supervisor_stop_event",
                            metadata={"writer": "executor", "failure_class": "cancelled"},
                        )
                    except Exception:
                        logger.debug("Failed to persist cancellation completion manifest.", exc_info=True)
                    error_msg = f"Step {step_id} ({tool_name}) cancelled by supervisor.\n"
                    execution_log.append(error_msg)
                    log_queue.put(error_msg)
                    if event_writer is not None:
                        event_writer(
                            {
                                "ts": datetime.now().isoformat(),
                                "run_id": current_run_id,
                                "step_id": step_id,
                                "agent": "ExecutorAgent",
                                "event_type": "STEP_FINISHED",
                                "severity": "error",
                                "payload": {
                                    "tool_name": tool_name,
                                    "failure_class": "cancelled",
                                    "reason": "supervisor_stop_event",
                                },
                            }
                        )
                    log_queue.put(None)
                    return {**state, "execution_log": execution_log, "error_message": error_msg, "current_step_idx": i}

                if step_exit_code is not None and step_exit_code != 0:
                    expected_outputs: list[str] = []
                    manifest_check = None
                    try:
                        from bio_harness.core.artifact_inspectors import (
                            _extract_expected_outputs,
                        )
                        from bio_harness.core.step_completion import (
                            check_completion_manifest,
                            find_completion_manifest,
                            write_step_completion_manifest,
                        )

                        step_dict = {"tool_name": tool_name, "arguments": arguments}
                        expected_outputs = _extract_expected_outputs(step_dict)
                        manifest_path = find_completion_manifest(
                            arguments,
                            tool_name=tool_name,
                            cwd=Path(cwd) if cwd else None,
                        )
                        if manifest_path:
                            manifest_check = check_completion_manifest(
                                manifest_path.parent,
                                tool_name,
                                expected_outputs=expected_outputs,
                            )
                    except Exception:
                        manifest_check = None

                    completed_via_manifest = bool(
                        manifest_check is not None and manifest_check.completed and not policy_blocked
                    )
                    if completed_via_manifest:
                        warn_msg = (
                            f"Step {step_id} ({tool_name}) returned exit code {step_exit_code} "
                            "but the completion manifest marked it successful. Continuing.\n"
                        )
                        execution_log.append(warn_msg)
                        log_queue.put(warn_msg)
                        if event_writer is not None:
                            event_writer(
                                {
                                    "ts": datetime.now().isoformat(),
                                    "run_id": current_run_id,
                                    "step_id": step_id,
                                    "agent": "ExecutorAgent",
                                    "event_type": "STEP_FINISHED",
                                    "severity": "warning",
                                    "payload": {
                                        "tool_name": tool_name,
                                        "exit_code": step_exit_code,
                                        "status": "completed_with_warnings",
                                        "completion_manifest": True,
                                    },
                                }
                            )
                        step_exit_code = (
                            int(manifest_check.exit_code)
                            if manifest_check is not None and manifest_check.exit_code is not None
                            else 0
                        )
                    elif policy_blocked:
                        try:
                            write_step_completion_manifest(
                                tool_name=tool_name,
                                step_arguments=arguments,
                                cwd=Path(cwd) if cwd else None,
                                outputs=expected_outputs,
                                exit_code=step_exit_code,
                                success=False,
                                error=policy_block_reason or "policy blocked command",
                                metadata={"writer": "executor", "failure_class": "policy_block"},
                            )
                        except Exception:
                            logger.debug("Failed to persist policy-block completion manifest.", exc_info=True)
                        detail = policy_block_reason or "policy blocked command"
                        error_msg = (
                            f"Step {step_id} ({tool_name}) blocked by policy with exit code {step_exit_code}. "
                            f"Reason: {detail}\n"
                        )
                        execution_log.append(error_msg)
                        log_queue.put(error_msg)
                        if event_writer is not None:
                            event_writer(
                                {
                                    "ts": datetime.now().isoformat(),
                                    "run_id": current_run_id,
                                    "step_id": step_id,
                                    "agent": "ExecutorAgent",
                                    "event_type": "STEP_FINISHED",
                                    "severity": "error",
                                    "payload": {
                                        "tool_name": tool_name,
                                        "exit_code": step_exit_code,
                                        "failure_class": "policy_block",
                                        "reason": policy_block_reason,
                                    },
                                }
                        )
                        log_queue.put(None)
                        return {**state, "execution_log": execution_log, "error_message": error_msg, "current_step_idx": i}
                    else:
                        try:
                            write_step_completion_manifest(
                                tool_name=tool_name,
                                step_arguments=arguments,
                                cwd=Path(cwd) if cwd else None,
                                outputs=expected_outputs,
                                exit_code=step_exit_code,
                                success=False,
                                error=(
                                    manifest_check.error
                                    if manifest_check is not None and manifest_check.error
                                    else (
                                        f"semantic failure marker: {semantic_failure_marker}"
                                        if semantic_failure_marker
                                        else "non-zero exit without success manifest"
                                    )
                                ),
                                metadata={
                                    "writer": "executor",
                                    "failure_class": (
                                        "semantic_step_failure"
                                        if semantic_failure_marker
                                        else "runtime_step_failure"
                                    ),
                                    "marker": semantic_failure_marker or None,
                                },
                            )
                        except Exception:
                            logger.debug("Failed to persist failure completion manifest.", exc_info=True)
                        error_msg = (
                            f"Step {step_id} ({tool_name}) failed with exit code {step_exit_code}. "
                            + (
                                f"Detected semantic failure marker {semantic_failure_marker}. "
                                if semantic_failure_marker
                                else ""
                            )
                            + "No success completion manifest was available. Review step output for details.\n"
                        )
                        execution_log.append(error_msg)
                        log_queue.put(error_msg)
                        if event_writer is not None:
                            event_writer(
                                {
                                    "ts": datetime.now().isoformat(),
                                    "run_id": current_run_id,
                                    "step_id": step_id,
                                    "agent": "ExecutorAgent",
                                    "event_type": "STEP_FINISHED",
                                    "severity": "error",
                                "payload": {
                                    "tool_name": tool_name,
                                    "exit_code": step_exit_code,
                                        "failure_class": (
                                            "semantic_step_failure"
                                            if semantic_failure_marker
                                            else "runtime_step_failure"
                                        ),
                                        "marker": semantic_failure_marker or None,
                                    },
                                }
                            )
                        log_queue.put(None)
                        return {**state, "execution_log": execution_log, "error_message": error_msg, "current_step_idx": i}

                contract = (step_contracts or {}).get(step_id, {})
                deliverable_check = self._validate_deliverables(contract, cwd)
                if contract and not deliverable_check["passed"]:
                    try:
                        from bio_harness.core.artifact_inspectors import (
                            _extract_expected_outputs,
                        )
                        from bio_harness.core.step_completion import (
                            write_step_completion_manifest,
                        )

                        write_step_completion_manifest(
                            tool_name=tool_name,
                            step_arguments=arguments,
                            cwd=Path(cwd) if cwd else None,
                            outputs=_extract_expected_outputs(
                                {"tool_name": tool_name, "arguments": arguments}
                            ),
                            exit_code=step_exit_code if step_exit_code is not None else 0,
                            success=False,
                            error=str(deliverable_check.get("reason", "") or "deliverable_validation_failed"),
                            metadata={"writer": "executor", "failure_class": "deliverable_validation_failed"},
                        )
                    except Exception:
                        logger.debug("Failed to persist deliverable-failure manifest.", exc_info=True)
                    error_msg = (
                        f"Step {step_id} ({tool_name}) exited 0 but deliverable validation failed: "
                        f"{deliverable_check['reason']}\n"
                    )
                    execution_log.append(error_msg)
                    log_queue.put(error_msg)
                    if event_writer is not None:
                        event_writer(
                            {
                                "ts": datetime.now().isoformat(),
                                "run_id": current_run_id,
                                "step_id": step_id,
                                "agent": "ExecutorAgent",
                                "event_type": "DELIVERABLE_CHECK_FAILED",
                                "severity": "error",
                                "payload": deliverable_check,
                            }
                        )
                    log_queue.put(None)
                    return {**state, "execution_log": execution_log, "error_message": error_msg, "current_step_idx": i}
                if contract and deliverable_check["passed"] and event_writer is not None:
                    event_writer(
                        {
                            "ts": datetime.now().isoformat(),
                            "run_id": current_run_id,
                            "step_id": step_id,
                            "agent": "ExecutorAgent",
                            "event_type": "DELIVERABLE_CHECK_PASSED",
                            "severity": "info",
                            "payload": deliverable_check,
                        }
                    )

                execution_log.append(f"--- Step {step_id} ({tool_name}) finished ---\n")
                log_queue.put(f"--- Step {step_id} ({tool_name}) finished ---\n")
                try:
                    from bio_harness.core.artifact_inspectors import (
                        _extract_expected_outputs,
                    )
                    from bio_harness.core.step_completion import (
                        find_completion_manifest,
                        write_step_completion_manifest,
                    )

                    manifest_path = find_completion_manifest(
                        arguments,
                        tool_name=tool_name,
                        cwd=Path(cwd) if cwd else None,
                    )
                    if manifest_path is None:
                        write_step_completion_manifest(
                            tool_name=tool_name,
                            step_arguments=arguments,
                            cwd=Path(cwd) if cwd else None,
                            outputs=_extract_expected_outputs(
                                {"tool_name": tool_name, "arguments": arguments}
                            ),
                            exit_code=step_exit_code if step_exit_code is not None else 0,
                            success=True,
                            metadata={"writer": "executor"},
                        )
                except Exception:
                    logger.debug("Failed to persist success completion manifest.", exc_info=True)
                if event_writer is not None:
                    event_writer(
                        {
                            "ts": datetime.now().isoformat(),
                            "run_id": current_run_id,
                            "step_id": step_id,
                            "agent": "ExecutorAgent",
                            "event_type": "STEP_FINISHED",
                            "severity": "info",
                            "payload": {"tool_name": tool_name, "exit_code": step_exit_code if step_exit_code is not None else 0},
                        }
                    )

            except Exception as e:
                try:
                    from bio_harness.core.artifact_inspectors import (
                        _extract_expected_outputs,
                    )
                    from bio_harness.core.step_completion import (
                        write_step_completion_manifest,
                    )

                    write_step_completion_manifest(
                        tool_name=tool_name,
                        step_arguments=arguments if isinstance(arguments, dict) else {},
                        cwd=Path(cwd) if cwd else None,
                        outputs=_extract_expected_outputs(
                            {
                                "tool_name": tool_name,
                                "arguments": arguments if isinstance(arguments, dict) else {},
                            }
                        ),
                        exit_code=step_exit_code if step_exit_code is not None else 1,
                        success=False,
                        error=str(e),
                        metadata={"writer": "executor", "failure_class": "exception"},
                    )
                except Exception:
                    logger.debug("Failed to persist exception completion manifest.", exc_info=True)
                error_msg = f"Error executing step {step_id} ({tool_name}): {e}\n"
                execution_log.append(error_msg)
                log_queue.put(error_msg)
                if event_writer is not None:
                    event_writer(
                        {
                            "ts": datetime.now().isoformat(),
                            "run_id": current_run_id,
                            "step_id": step_id,
                            "agent": "ExecutorAgent",
                            "event_type": "STEP_FINISHED",
                            "severity": "error",
                            "payload": {"tool_name": tool_name, "exception": str(e)},
                        }
                    )
                log_queue.put(None)
                return {**state, "execution_log": execution_log, "error_message": error_msg, "current_step_idx": i}
        
        execution_log.append("Plan execution completed.\n")
        log_queue.put("Plan execution completed.\n")
        log_queue.put(None) # Signal end of stream
        return {**state, "execution_log": execution_log, "current_step_idx": len(validated_plan.plan), "error_message": None}


    # --- Public Orchestrator Interface Methods ---
    def get_or_create_session(self, session_id: str = "default") -> OrchestratorSession:
        session = self._sessions.get(session_id)
        if session is None:
            session = _orchestrator_sessions.new_session(session_id)
            self._sessions[session_id] = session
        return session

    def _estimate_tokens(self, text: str) -> int:
        return _orchestrator_sessions.estimate_tokens(text)

    def _session_token_load(self, session: OrchestratorSession) -> int:
        return _orchestrator_sessions.session_token_load(session)

    def _normalize_plan_json(self, plan_json: Dict[str, Any]) -> Dict[str, Any]:
        return _orchestrator_plan_helpers.normalize_plan_json(plan_json)

    def _extract_step_contracts(self, plan_json: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
        return _orchestrator_plan_helpers.extract_step_contracts(plan_json)

    def _validate_deliverables(self, contract: Dict[str, Any], cwd: Optional[str]) -> Dict[str, Any]:
        return _orchestrator_plan_helpers.validate_deliverables(contract, cwd)

    def _split_shell_segments(self, command: str) -> List[str]:
        return _orchestrator_shell_validation.split_shell_segments_for_validation(command)

    def _is_shell_assignment(self, token: str) -> bool:
        return _orchestrator_shell_validation.is_shell_assignment_token(token)

    def _should_ignore_command_token(self, token: str) -> bool:
        return _orchestrator_shell_validation.should_ignore_command_token_for_validation(token)

    def _extract_segment_command(self, tokens: List[str]) -> Optional[str]:
        return _orchestrator_shell_validation.extract_segment_command_for_validation(tokens)

    def _extract_step_requirements(self, command: str, cwd: Optional[str]) -> Dict[str, Any]:
        return _orchestrator_shell_validation.extract_step_requirements(command, cwd)

    def _find_stdin_blocking_commands(self, command: str) -> List[str]:
        return _orchestrator_shell_validation.find_stdin_blocking_commands(command)

    def _find_disallowed_git_commands(self, command: str) -> List[str]:
        return _orchestrator_shell_validation.find_disallowed_git_commands(command)

    def _find_inline_interpreter_commands(self, command: str) -> List[str]:
        return _orchestrator_shell_validation.find_inline_interpreter_commands(command)

    def _manual_hint(self, tool: str) -> str:
        return _orchestrator_validation_helpers.manual_hint(tool)

    def _regenerate_splicing_lists(self, cwd: Optional[str]) -> Dict[str, Any]:
        return _orchestrator_validation_helpers.regenerate_splicing_lists(cwd)

    @staticmethod
    def _steps_cover_same_capability(
        failed_tool: str, next_tool: str, failed_cmd: str = "",
    ) -> bool:
        return _orchestrator_validation_helpers.steps_cover_same_capability(
            failed_tool,
            next_tool,
            failed_cmd,
        )

    def _step_validation_agent(
        self,
        step: Any,
        cwd: Optional[str],
    ) -> Dict[str, Any]:
        tool_name = step.tool_name
        arguments = normalize_execution_arguments(tool_name, dict(step.arguments or {}), cwd=cwd)
        if tool_name != "bash_run":
            skill_registry = getattr(self, "skill_registry", None)
            skill_metadata = skill_registry.get_skill(tool_name) if skill_registry is not None else None
            original_arguments = dict(arguments)
            arguments = sanitize_harness_managed_arguments(tool_name, arguments, skill_metadata)
            arguments = normalize_non_bash_run_arguments(tool_name, arguments, cwd=cwd)
            issues = validate_non_bash_run_arguments(tool_name, arguments, skill_metadata)
            fixes: list[str] = []
            stripped_managed = sorted(set(original_arguments) - set(arguments))
            for key in stripped_managed:
                fixes.append(f"stripped_harness_managed:{key}")

            blocking_issues = [
                issue
                for issue in issues
                if not issue.startswith("undocumented_argument:")
            ]
            undocumented_issues = [
                issue
                for issue in issues
                if issue.startswith("undocumented_argument:")
            ]
            for issue in undocumented_issues:
                arg_name = issue.split(":", 1)[-1]
                if arg_name and arg_name != "<empty>":
                    arguments.pop(arg_name, None)
                    fixes.append(f"stripped_undocumented:{arg_name}")
            arguments, semantic_issues, semantic_fixes = validate_and_repair_de_wrapper_arguments(
                tool_name=tool_name,
                arguments=arguments,
            )
            blocking_issues.extend(semantic_issues)
            blocking_issues.extend(
                self._validate_non_bash_existing_inputs(
                    tool_name=tool_name,
                    arguments=arguments,
                    cwd=cwd,
                )
            )
            fixes.extend(semantic_fixes)
            if blocking_issues:
                fixes.append(
                    "non-bash_run steps must use only documented parameters and cannot carry raw command overrides."
                )
            return {
                "passed": len(blocking_issues) == 0,
                "arguments": arguments,
                "issues": blocking_issues,
                "fixes": fixes,
            }

        command = str(arguments.get("command", "")).strip()
        req = self._extract_step_requirements(command, cwd)
        issues: List[str] = []
        fixes: List[str] = []

        # Map of tool binaries to equivalent binaries for runtime availability checks.
        #
        # The entries below the blank-line separator (Fix #12) resolve the
        # case where the LLM puts a harness *wrapper* name (e.g.
        # ``prokka_annotate``) into a bash_run command. Wrapper names are
        # not real binaries — ``shutil.which`` won't find them and the
        # downstream ``man <wrapper>`` probe fails with "No manual entry",
        # which the validator then reports as ``missing_tool`` and blocks
        # the step. If the wrapper's underlying binary is available, this
        # map rewrites the command to invoke it directly instead of
        # rejecting the step. That unblocks plans that try to call
        # annotation / variant-calling wrappers from bash, which in turn
        # gives the stepwise planner a working annotation path instead of
        # livelocking on ``spades_assemble`` retries.
        _TOOL_BINARY_EQUIVALENCES: dict[str, list[str]] = {
            "vcffilter": ["bcftools"],
            "bwa": ["bwa-mem2"],
            "rmats": ["rmats.py"],
            "star": ["STAR"],
            "STAR": ["star"],
            "trimmomatic": ["TrimmomaticPE", "trimmomatic-0.39.jar"],
            "megahit": ["megahit_core"],

            # Wrapper-name → real-binary fallbacks (Fix #12 / #15).
            # prokka_annotate falls through to prodigal_annotate and then
            # prodigal itself: per the project memory, these two annotators
            # are "interchangeable" for downstream SnpEff/VCF work, and
            # many installs carry only prodigal. Without this chain, the
            # validator rejects every bash_run calling prokka_annotate
            # with missing_tool:prokka and the planner gets stuck rewriting
            # earlier steps. (Fix #15, 2026-04-23.)
            "prokka_annotate": ["prokka", "prodigal_annotate", "prodigal"],
            "prokka": ["prodigal"],
            "prodigal_annotate": ["prodigal"],
            "snpeff_annotate": ["snpEff", "snpeff"],
            "spades_assemble": ["spades.py", "spades"],
            "bwa_mem_align": ["bwa", "bwa-mem2"],
            "freebayes_call": ["freebayes"],
            "bcftools_filter": ["bcftools"],
            "bcftools_isec": ["bcftools"],
            "bcftools_norm": ["bcftools"],
            "bcftools_concat": ["bcftools"],
            "samtools_sort": ["samtools"],
            "samtools_index": ["samtools"],
            "fastp_trim": ["fastp"],
            "kraken2_classify": ["kraken2"],
            "featurecounts_quantify": ["featureCounts"],
            "star_align": ["STAR", "star"],
            "deseq2_run": ["Rscript"],
            "iqtree_run": ["iqtree", "iqtree2"],
        }

        for tool in req["tools"]:
            if shutil.which(tool):
                continue
            # Check binary equivalences before blocking
            equivalents = _TOOL_BINARY_EQUIVALENCES.get(tool, [])
            found_equiv = None
            for equiv in equivalents:
                if shutil.which(equiv):
                    found_equiv = equiv
                    break
            if found_equiv:
                if tool != found_equiv:
                    # If the command already contains a structured fallback using the
                    # equivalent tool as a *whole token* (e.g.
                    # "if command -v vcffilter ... else bcftools filter ...")
                    # skip the naive replacement to avoid breaking the fallback logic.
                    #
                    # Fix #12: must use \b word-boundary match, not raw
                    # substring. "prokka" is a substring of
                    # "prokka_annotate", so the old substring check
                    # skipped rewriting `prokka_annotate` → `prokka` and
                    # left the command pointing at a non-existent binary.
                    if re.search(rf"\b{re.escape(found_equiv)}\b", command):
                        fixes.append(f"skipped_replace_{tool}_already_has_{found_equiv}_fallback")
                    else:
                        command = re.sub(rf"\b{re.escape(tool)}\b", found_equiv, command)
                        fixes.append(f"replaced {tool} with {found_equiv}")
                continue
            hint = self._manual_hint(tool)
            issues.append(f"missing_tool:{tool}" + (f":hint={hint}" if hint else ""))

        # Re-extract after command rewrite.
        req = self._extract_step_requirements(command, cwd)

        for p in req["input_paths"]:
            if not p.exists():
                issues.append(self._missing_path_issue(p))
        for p in req["must_be_nonempty"]:
            if p.exists() and p.is_file() and p.stat().st_size == 0:
                # targeted auto-recovery for splicing list files
                if p.name in {"control_r1.txt", "treatment_r1.txt", "control_bams.txt", "treatment_bams.txt"}:
                    rec = self._regenerate_splicing_lists(cwd)
                    fixes.append(f"regen_lists:{rec}")
                else:
                    issues.append(f"empty_input:{p}")

        # Re-check nonempty after attempted list recovery.
        req = self._extract_step_requirements(command, cwd)
        for p in req["must_be_nonempty"]:
            if not p.exists():
                issues.append(self._missing_path_issue(p))
            elif p.is_file() and p.stat().st_size == 0:
                issues.append(f"empty_input:{p}")

        for p in req["gtf_paths"]:
            if p.exists() and not re.search(r"\.gtf(\.gz)?$", p.name, flags=re.IGNORECASE):
                issues.append(f"suspect_gtf_format:{p}")
        for p in req["fasta_paths"]:
            if p.exists() and not re.search(r"\.(fa|fasta|fna)(\.gz)?$", p.name, flags=re.IGNORECASE):
                issues.append(f"suspect_fasta_format:{p}")

        blocking_cmds = self._find_stdin_blocking_commands(command)
        if blocking_cmds:
            issues.extend([f"stdin_block:{c}" for c in blocking_cmds])
            fixes.append(
                "detected stdin-blocking command without input; provide file args "
                "or use a pipeline/redirection for head/tail."
            )

        git_cmds = self._find_disallowed_git_commands(command)
        if git_cmds:
            issues.extend([f"disallowed_git:{c}" for c in git_cmds])
            fixes.append(
                "detected runtime git command; use preinstalled local tools/skills "
                "instead of cloning/pulling repositories during execution."
            )

        inline_interpreters = self._find_inline_interpreter_commands(command)
        if inline_interpreters:
            issues.extend([f"inline_interpreter:{entry}" for entry in inline_interpreters])
            fixes.append(
                "detected inline interpreter scripting; use a deterministic skill, checked-in helper script, "
                "or an explicit user-approved workspace helper instead."
            )

        policy = inspect_execution_command(command)
        issues.extend(policy.get("blocking", []))
        issues.extend(policy.get("audits", []))
        if policy.get("audits"):
            fixes.append(
                "runtime network/install behavior audited; prefer librarian research, trusted download helpers, "
                "or deterministic bootstrap/setup paths instead of ad hoc fetch/install commands."
            )

        # Bash syntax check: catch unmatched quotes, missing operators, etc.
        try:
            syntax_check = subprocess.run(
                ["bash", "-n", "-c", command],
                capture_output=True, text=True, timeout=5,
            )
            if syntax_check.returncode != 0:
                stderr_msg = (syntax_check.stderr or "").strip()
                # Truncate long messages
                if len(stderr_msg) > 200:
                    stderr_msg = stderr_msg[:200] + "..."
                issues.append(f"bash_syntax_error:{stderr_msg}")
        except Exception:
            pass  # Don't block on syntax-check failures (timeout, missing bash, etc.)

        repaired_bcftools_command, bcftools_repairs = repair_bcftools_expression_command(command, cwd=cwd)
        if repaired_bcftools_command != command:
            command = repaired_bcftools_command
            fixes.extend(
                f"qualified_bcftools_expression_tag:{repair.get('tag', '')}->"
                f"{repair.get('preferred_namespace', 'INFO')}/{repair.get('tag', '')}"
                for repair in bcftools_repairs
                if str(repair.get("tag", "")).strip()
            )
        repaired_isec_command, isec_repairs = repair_bcftools_isec_command(command)
        if repaired_isec_command != command:
            command = repaired_isec_command
            fixes.extend(
                f"repaired_bcftools_isec_export:{repair.get('reason', '')}"
                for repair in isec_repairs
                if str(repair.get("reason", "")).strip()
            )
        for repair_issue in inspect_bcftools_expression_command(command, cwd=cwd):
            tag = str(repair_issue.get("tag", "")).strip() or "<unknown>"
            issue_name = str(repair_issue.get("issue", "")).strip()
            if issue_name == "missing_bcftools_expression_namespace_field":
                namespace = str(repair_issue.get("missing_namespace", "")).strip() or "<unknown>"
                issues.append(f"missing_bcftools_expression_namespace_field:{namespace}:{tag}")
            else:
                issues.append(f"ambiguous_bcftools_expression_namespace:{tag}")
            reason = str(repair_issue.get("reason", "")).strip()
            if reason:
                fixes.append(
                    "bcftools expression must qualify ambiguous header tags "
                    f"for {tag}: {reason}"
                )
        for isec_issue in inspect_bcftools_isec_command(command):
            reason = str(isec_issue.get("reason", "")).strip() or "invalid_isec_output_mode"
            issues.append(f"invalid_bcftools_isec_output_mode:{reason}")
            fixes.append(
                "bcftools isec with -p must export a concrete file from the prefix directory "
                "instead of assuming stdout or -o materializes the named output."
            )

        arguments["command"] = command
        blocking_prefixes = (
            "missing_tool", "missing_input", "placeholder_token_in_path", "empty_input",
            "stdin_block", "disallowed_git", "bash_syntax_error",
            "inline_interpreter", "execution_policy_block",
            "ambiguous_bcftools_expression_namespace",
            "missing_bcftools_expression_namespace_field",
            "invalid_bcftools_isec_output_mode",
        )
        return {
            "passed": len([i for i in issues if i.startswith(blocking_prefixes)]) == 0,
            "arguments": arguments,
            "issues": issues,
            "fixes": fixes,
        }

    def _missing_path_issue(self, candidate: Path | str) -> str:
        """Return the stable issue string for one missing path candidate.

        Args:
            candidate: Candidate path text identified during step validation.

        Returns:
            ``placeholder_token_in_path:...`` when the candidate still contains
            unresolved template syntax, otherwise ``missing_input:...``.
        """

        path_text = str(candidate or "").strip()
        if "<" in path_text or ">" in path_text:
            return f"placeholder_token_in_path:{path_text}"
        return f"missing_input:{path_text}"

    def _validate_non_bash_existing_inputs(
        self,
        *,
        tool_name: str,
        arguments: Dict[str, Any],
        cwd: Optional[str],
    ) -> List[str]:
        """Return missing-input issues for structured tools just before launch."""

        if not cwd:
            return []

        from bio_harness.core.tool_registry import default_tool_registry

        registry = default_tool_registry()
        meta = registry.get(tool_name)
        if meta is None:
            return []

        base_dir = Path(cwd).expanduser().resolve(strict=False)
        issues: List[str] = []
        seen: set[str] = set()
        for key in meta.input_path_keys:
            raw_value = arguments.get(key)
            values: List[str]
            if isinstance(raw_value, (list, tuple, set)):
                values = [str(item).strip() for item in raw_value if str(item).strip()]
            else:
                text = str(raw_value or "").strip()
                values = [text] if text else []
            for value in values:
                candidate = Path(value).expanduser()
                if not candidate.is_absolute():
                    candidate = (base_dir / candidate).resolve(strict=False)
                if candidate.exists():
                    continue
                issue = self._missing_path_issue(candidate)
                if issue in seen:
                    continue
                seen.add(issue)
                issues.append(issue)
        return issues

    def _compact_session_if_needed(self, session: OrchestratorSession) -> None:
        _orchestrator_sessions.compact_session_if_needed(
            session,
            context_limit_tokens=self._context_limit_tokens,
            compact_ratio=self._compact_ratio,
            summarize_text=self.biollm.summarize_text,
        )

    def _subagent_dataset_scout(
        self,
        data_root: Optional[str],
        include_subdirs: bool = False,
        limit: int = 200,
    ) -> Dict[str, Any]:
        return _orchestrator_interactive_context.subagent_dataset_scout(
            data_root,
            include_subdirs=include_subdirs,
            limit=limit,
        )

    def _subagent_requirements(self, user_text: str) -> List[str]:
        return _orchestrator_interactive_context.subagent_requirements(user_text)

    def _infer_autonomy_mode(self, user_text: str) -> bool:
        return _orchestrator_interactive_context.infer_autonomy_mode(user_text)

    def _detect_context_completeness(self, user_text: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return _orchestrator_interactive_context.detect_context_completeness(user_text, context)

    def interactive_turn(
        self,
        session_id: str,
        user_message: str,
        data_root: Optional[str] = None,
        include_subdirs: bool = False,
        policy_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Interactive orchestration turn with continued session memory and compaction.
        Uses lightweight subagents to collect context and drive clarifying dialogue.
        """
        session = self.get_or_create_session(session_id)
        session["messages"].append({"role": "user", "content": user_message})

        data_context = self._subagent_dataset_scout(data_root=data_root, include_subdirs=include_subdirs, limit=300)
        requirements = self._subagent_requirements(user_message)
        autonomy_mode = self._infer_autonomy_mode(user_message)
        completeness = self._detect_context_completeness(
            user_message,
            {"data_context": data_context, "requirements": requirements},
        )
        followup_limit = 0 if autonomy_mode else 1
        session["last_context"] = {
            "data_context": data_context,
            "requirements": requirements,
            "autonomy_mode": autonomy_mode,
            "completeness": completeness,
            "policy_context": policy_context or {},
            "followup_question_limit": followup_limit,
        }

        compact_mem = session.get("compact_memory", "")
        recent_msgs = session.get("messages", [])[-10:]
        recent_text = "\n".join([f"{m['role']}: {m['content']}" for m in recent_msgs])
        context_blob = json.dumps(session["last_context"], indent=2)
        harness_help_context = self._interactive_harness_help_context(user_message)
        deterministic_help_answer = self._interactive_deterministic_help_answer(user_message)

        if deterministic_help_answer:
            session["messages"].append({"role": "assistant", "content": deterministic_help_answer})
            self._compact_session_if_needed(session)
            return {
                "assistant_message": deterministic_help_answer,
                "context": session["last_context"],
                "compactions": session.get("compactions", 0),
                "token_load_estimate": self._session_token_load(session),
            }

        if harness_help_context:
            system_prompt = (
                "You are answering a question about the local Bio-Harness repository in this workspace. "
                "Use only the deterministic harness help context and recent conversation. "
                "Do not substitute information from any unrelated project with a similar name. "
                "Answer directly instead of describing future research steps. "
                "Prefer wrapped skills and wrapped tool families over catalog-only references, "
                "and do not invent unsupported commands or extension workflows."
            )
            user_prompt = (
                f"Harness help context:\n{harness_help_context}\n\n"
                f"Compacted memory:\n{compact_mem}\n\n"
                f"Recent conversation:\n{recent_text}\n\n"
                f"User question:\n{user_message}\n\n"
                "Respond with concise sections:\n"
                "1) Direct answer\n"
                "2) Relevant commands or files\n"
                "3) Important support-tier or limitation notes\n"
                "4) Blocking question (only if truly required; otherwise say none)\n"
                "Do not say you will inspect docs or take future actions. Answer from the provided context now.\n"
            )
        else:
            system_prompt = (
                "You are an interactive bioinformatics orchestrator. "
                "Prioritize autonomous progress over repeated questioning. "
                "If the user asks you to infer/figure out missing details, proceed with reasonable defaults "
                "and state assumptions explicitly. Ask at most one blocking question per turn, and only when "
                "execution cannot continue safely. Never repeat the same missing-input question from prior turns."
            )
            user_prompt = (
                f"Compacted memory:\n{compact_mem}\n\n"
                f"Recent conversation:\n{recent_text}\n\n"
                f"Subagent context:\n{context_blob}\n\n"
                "Respond with concise sections:\n"
                "1) What I understood\n"
                "2) Autonomous next actions I will take now\n"
                "3) Assumptions/defaults used (if any)\n"
                "4) Blocking question (only if required by followup_question_limit)\n"
                "If followup_question_limit is 0, do not ask questions and proceed with defaults.\n"
            )
        try:
            assistant_message = self.biollm.generate_text(system_prompt, user_prompt, num_ctx=self._context_limit_tokens)
        except Exception as e:
            raise BioHarnessError(
                f"Interactive turn failed to reach model '{self.biollm.model_name}' "
                f"at host '{self.biollm.host or 'default'}': {e}"
            ) from e
        session["messages"].append({"role": "assistant", "content": assistant_message})

        self._compact_session_if_needed(session)
        return {
            "assistant_message": assistant_message,
            "context": session["last_context"],
            "compactions": session.get("compactions", 0),
            "token_load_estimate": self._session_token_load(session),
        }

    def session_snapshot(self, session_id: str) -> Dict[str, Any]:
        session = self.get_or_create_session(session_id)
        return {
            "session_id": session.get("session_id", session_id),
            "compactions": session.get("compactions", 0),
            "token_load_estimate": self._session_token_load(session),
            "compact_memory": session.get("compact_memory", ""),
            "messages": session.get("messages", []),
            "last_context": session.get("last_context", {}),
        }

    def think(
        self,
        user_query: str,
        analysis_spec: Dict[str, Any] | None = None,
        *,
        planner_mode: str = "auto",
        seed_plan: Dict[str, Any] | None = None,
        model_override: str | None = None,
        available_skills_metadata_override: List[Dict[str, Any]] | None = None,
    ) -> Dict:
        """
        Generates an execution plan based on the user query using the agent's planner.
        This method will conceptually run the planning node and return the generated plan.

        Args:
            user_query: The user's query or task description.
            model_override: If set, forces this model for planning (e.g., fast model for repairs).
            available_skills_metadata_override: Optional planner-skill metadata
                override used when the caller already selected the planner tool
                subset and wants the planner context to match it exactly.

        Returns:
            A dictionary representing the validated plan generated by the LLM.

        Raises:
            BioHarnessError: If the planning fails.
        """
        initial_state: AgentState = {
            "user_query": user_query,
            "tools_context": self.tools_context,
            "available_skills_metadata": (
                [dict(item) for item in available_skills_metadata_override]
                if isinstance(available_skills_metadata_override, list)
                else self._available_skill_metadata()
            ),
            "plan": None,
            "current_step_idx": 0,
            "execution_log": [],
            "research_results": [],
            "error_message": None,
            "analysis_spec": analysis_spec,
        }
        initial_state["planner_mode"] = planner_mode
        initial_state["seed_plan"] = seed_plan if isinstance(seed_plan, dict) else None
        initial_state["model_override"] = model_override
        
        # Run the planner node logic directly
        result_state = self._planner_node(initial_state)
        
        if result_state.get("error_message"):
            raise BioHarnessError(result_state["error_message"])
        if result_state.get("plan"):
            return result_state["plan"]
        raise BioHarnessError("Planner failed to generate a plan without an explicit error message.")

    def execute_plan(
        self,
        plan_json: Dict,
        log_queue: queue.Queue,
        cwd: Optional[str] = None,
        allowed_root: Optional[str] = None,
        run_artifacts: Optional[Dict[str, str]] = None,
        stop_event: Optional[threading.Event] = None,
        current_step_idx: int = 0,
    ) -> None:
        """
        Executes a pre-approved plan using the agent's executor.

        Args:
            plan_json: The pre-approved plan in JSON format.
            log_queue: A queue.Queue instance to stream execution logs to.
        """
        if run_artifacts:
            try:
                start_executor_runtime(
                    run_artifacts,
                    run_id=str(run_artifacts.get("run_id", "") or ""),
                    pid=os.getpid(),
                )
            except Exception:
                logger.exception("Failed to record executor start runtime state.")

        def _event_writer(event: Dict[str, Any]) -> None:
            if not run_artifacts:
                return
            events_path = run_artifacts.get("events")
            if not events_path:
                return
            try:
                with Path(events_path).open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=True) + "\n")
                heartbeat_executor_runtime(
                    run_artifacts,
                    run_id=str(run_artifacts.get("run_id", "") or ""),
                    event_type=str(event.get("event_type", "") or ""),
                    step_id=event.get("step_id"),
                    tool_name=str((event.get("payload", {}) or {}).get("tool_name", "") or ""),
                )
            except Exception:
                logger.exception("Failed to append event.")

        def _emit_executor_status(phase: str, *, tool_name: str = "") -> None:
            """Emit a queue-visible executor phase marker before step start."""

            payload: Dict[str, Any] = {"phase": str(phase).strip()}
            line = f"[status] phase={payload['phase']}"
            if str(tool_name).strip():
                payload["tool_name"] = str(tool_name).strip()
                line = f"{line} tool={payload['tool_name']}"
            log_queue.put(f"{line}\n")
            _event_writer(
                {
                    "ts": datetime.now().isoformat(),
                    "run_id": str((run_artifacts or {}).get("run_id", "") or ""),
                    "agent": "ExecutorAgent",
                    "event_type": "EXECUTOR_PHASE",
                    "severity": "info",
                    "payload": payload,
                }
            )

        _emit_executor_status("executor_preflight")

        # Pre-execution plan validation: catch empty plans, unknown tools,
        # and missing arguments before entering the execution loop.
        from bio_harness.core.tool_registry import default_tool_registry

        skill_registry = getattr(self, "skill_registry", None)
        known_skills = {"bash_run"}
        if skill_registry is not None:
            skills = getattr(skill_registry, "_skills", {})
            if isinstance(skills, dict):
                known_skills |= {str(name).strip() for name in skills.keys() if str(name).strip()}
        plan_for_validation = plan_json if isinstance(plan_json, dict) else {}
        _emit_executor_status("pre_execution_validation")
        validation_result = validate_plan(
            plan_for_validation,
            registry=default_tool_registry(),
            known_skill_names=known_skills,
        )
        if not validation_result.passed:
            error_msg = "; ".join(e.message for e in validation_result.errors)
            logger.error("Pre-execution validation failed: %s", error_msg)
            log_queue.put(f"Pre-execution validation failed: {error_msg}\n")
            _event_writer(
                {
                    "ts": datetime.now().isoformat(),
                    "run_id": str((run_artifacts or {}).get("run_id", "") or ""),
                    "agent": "ExecutorAgent",
                    "event_type": "PRE_EXECUTION_VALIDATION_FAILED",
                    "severity": "error",
                    "payload": {"error": error_msg},
                }
            )
            if run_artifacts:
                try:
                    finish_executor_runtime(
                        run_artifacts,
                        run_id=str(run_artifacts.get("run_id", "") or ""),
                        status="failed",
                        error=error_msg,
                    )
                except Exception:
                    logger.exception("Failed to record pre-execution validation failure state.")
            log_queue.put(None)
            return

        _emit_executor_status("executor_state_init")
        initial_state: AgentState = {
            "user_query": "Executing pre-approved plan", # Dummy query for execution context
            "tools_context": self.tools_context,
            # Planner skill availability is not consulted on the direct executor
            # path. Avoid rebuilding it here so the supervisor can observe
            # progress before step-level execution begins.
            "available_skills_metadata": [],
            "plan": LLMOutputSchema(**self._normalize_plan_json(plan_json)), # Ensure the plan is a Pydantic model
            "current_step_idx": max(0, int(current_step_idx)),
            "execution_log": [],
            "research_results": [],
            "error_message": None,
        }

        step_contracts = self._extract_step_contracts(plan_json if isinstance(plan_json, dict) else {})
        _emit_executor_status("executor_dispatch")
        final_state = self._executor_node(
            initial_state,
            log_queue,
            cwd=cwd,
            allowed_root=allowed_root,
            step_contracts=step_contracts,
            event_writer=_event_writer if run_artifacts else None,
            run_id=(run_artifacts or {}).get("run_id"),
            stop_event=stop_event,
        )

        if final_state.get("error_message"):
            logger.error(f"Plan execution failed: {final_state['error_message']}")
        if run_artifacts:
            try:
                finish_executor_runtime(
                    run_artifacts,
                    run_id=str(run_artifacts.get("run_id", "") or ""),
                    status="failed" if final_state.get("error_message") else "completed",
                    error=str(final_state.get("error_message", "") or ""),
                )
            except Exception:
                logger.exception("Failed to record executor runtime completion state.")

    def export_plan_script(self, plan_json: Dict, output_path: Path) -> Path:
        """
        Export a validated plan as a runnable shell script for repeatable execution
        without agent planning.
        """
        if isinstance(plan_json, LLMOutputSchema):
            validated_plan = plan_json
        else:
            validated_plan = LLMOutputSchema(**self._normalize_plan_json(plan_json))

        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            "# Generated by BioHarness",
        ]

        for step in validated_plan.plan:
            tool_name = step.tool_name
            if tool_name not in self._loaded_skill_functions:
                raise ValueError(f"Cannot export unknown tool '{tool_name}'")
            command = self._loaded_skill_functions[tool_name](
                **normalize_execution_arguments(tool_name, step.arguments, cwd=output_path.parent)
            )
            lines.append(f"# Step {step.step_id}: {tool_name}")
            lines.append(command)
            lines.append("")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        output_path.chmod(0o755)
        return output_path
