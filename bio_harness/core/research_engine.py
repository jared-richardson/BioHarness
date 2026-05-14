"""Research-driven analysis design and parameter selection.

This module encapsulates research workflows that use Librarian (web/tool/pubmed
search) and Reader (PDF extraction) to discover appropriate tools, parameters,
and protocols for unfamiliar analyses.  Results are cached to avoid redundant
lookups within a session.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class ResearchEngine:
    """First-class research capability for the agent loop.

    Wraps Librarian (search) and Reader (PDF extraction) to provide
    structured research endpoints that return actionable recommendations.
    """

    def __init__(
        self,
        librarian: Any | None = None,
        reader: Any | None = None,
        biollm: Any | None = None,
    ):
        self._librarian = librarian
        self._reader = reader
        self._biollm = biollm
        self._cache: Dict[str, Any] = {}

    def _cache_key(self, prefix: str, query: str) -> str:
        return f"{prefix}:{query.strip().lower()[:120]}"

    # ------------------------------------------------------------------
    # Research endpoints
    # ------------------------------------------------------------------

    def research_analysis_approach(
        self, query: str, *, max_results: int = 5
    ) -> Dict[str, Any]:
        """Research the best analysis approach for a user query.

        Returns:
            dict with keys:
              - tool_recommendations: list of tool name strings
              - parameter_hints: dict of tool_name -> {param: value}
              - protocol_steps: list of step description strings
              - sources: list of source descriptions
        """
        ck = self._cache_key("approach", query)
        if ck in self._cache:
            return self._cache[ck]

        tool_recommendations: List[str] = []
        parameter_hints: Dict[str, Dict[str, Any]] = {}
        protocol_steps: List[str] = []
        sources: List[str] = []

        if self._librarian:
            try:
                tool_hits = self._librarian.tool_search(query, max_results=max_results)
                for hit in (tool_hits or []):
                    name = str(hit.get("name", "") or hit.get("title", "")).strip()
                    if name:
                        tool_recommendations.append(name)
                        sources.append(f"tool_search: {name}")
            except Exception as exc:
                logger.debug("tool_search failed: %s", exc)

            try:
                web_hits = self._librarian.web_search(
                    f"bioinformatics pipeline {query} best practices",
                    max_results=max_results,
                )
                for hit in (web_hits or []):
                    snippet = str(hit.get("snippet", "") or hit.get("abstract", "")).strip()
                    if snippet:
                        protocol_steps.append(snippet[:300])
                        sources.append(str(hit.get("url", "") or hit.get("title", ""))[:200])
            except Exception as exc:
                logger.debug("web_search failed: %s", exc)

        # Synthesize if LLM is available
        if self._biollm and (tool_recommendations or protocol_steps):
            try:
                context = (
                    f"Tools found: {', '.join(tool_recommendations[:10])}\n"
                    f"Protocol info: {' | '.join(protocol_steps[:5])}"
                )
                summary = self._biollm.summarize_text(
                    context,
                    f"For the analysis '{query}', list the recommended tools and "
                    "parameters as bullet points. Be concise.",
                )
                if summary:
                    sources.append("llm_synthesis")
            except Exception as exc:
                logger.debug("LLM synthesis failed: %s", exc)

        result = {
            "tool_recommendations": tool_recommendations,
            "parameter_hints": parameter_hints,
            "protocol_steps": protocol_steps,
            "sources": sources,
        }
        self._cache[ck] = result
        return result

    def research_tool_parameters(
        self, tool_name: str, context: str = ""
    ) -> Dict[str, Any]:
        """Research recommended parameters for a specific tool.

        Returns:
            dict with keys:
              - parameters: dict of param_name -> recommended_value
              - rationale: str explaining why these parameters
              - sources: list of source descriptions
        """
        ck = self._cache_key("params", f"{tool_name}:{context}")
        if ck in self._cache:
            return self._cache[ck]

        parameters: Dict[str, Any] = {}
        rationale = ""
        sources: List[str] = []

        if self._librarian:
            try:
                search_query = f"{tool_name} {context} recommended parameters settings"
                hits = self._librarian.tool_search(search_query, max_results=3)
                for hit in (hits or []):
                    sources.append(str(hit.get("name", "") or hit.get("url", ""))[:200])
            except Exception as exc:
                logger.debug("tool parameter search failed: %s", exc)

        if self._biollm and sources:
            try:
                summary = self._biollm.summarize_text(
                    f"Tool: {tool_name}, Context: {context}, Sources: {sources}",
                    f"What are the best parameters for {tool_name} in the context "
                    f"of {context}? List key=value pairs.",
                )
                rationale = str(summary or "")[:500]
            except Exception:
                pass

        result = {
            "parameters": parameters,
            "rationale": rationale,
            "sources": sources,
        }
        self._cache[ck] = result
        return result

    def research_error_solution(
        self, error_text: str, tool_name: str = ""
    ) -> Dict[str, Any]:
        """Research a solution for an encountered error.

        Returns:
            dict with keys:
              - suggested_fix: str describing the fix
              - parameter_changes: dict of param changes to try
              - tool_substitution: alternative tool name if applicable
              - sources: list of source descriptions
        """
        ck = self._cache_key("error", f"{tool_name}:{error_text[:80]}")
        if ck in self._cache:
            return self._cache[ck]

        suggested_fix = ""
        parameter_changes: Dict[str, Any] = {}
        tool_substitution = ""
        sources: List[str] = []

        # Check tool equivalence map first (fast, no network)
        from bio_harness.core.recovery_policy import TOOL_EQUIVALENCE_MAP
        equivalents = TOOL_EQUIVALENCE_MAP.get(tool_name, [])
        if equivalents:
            tool_substitution = equivalents[0]

        if self._librarian:
            try:
                # Search for the error + tool combination
                search_query = f"{tool_name} error {error_text[:60]} solution fix"
                hits = self._librarian.web_search(search_query, max_results=3)
                for hit in (hits or []):
                    snippet = str(hit.get("snippet", "")).strip()
                    if snippet:
                        sources.append(snippet[:200])
            except Exception as exc:
                logger.debug("error solution search failed: %s", exc)

        if self._biollm and sources:
            try:
                summary = self._biollm.summarize_text(
                    f"Error in {tool_name}: {error_text[:200]}\nSearch results: {sources}",
                    "What is the most likely fix for this error? Be specific.",
                )
                suggested_fix = str(summary or "")[:500]
            except Exception:
                pass

        result = {
            "suggested_fix": suggested_fix,
            "parameter_changes": parameter_changes,
            "tool_substitution": tool_substitution,
            "sources": sources,
        }
        self._cache[ck] = result
        return result

    def clear_cache(self) -> None:
        """Clear all cached research results."""
        self._cache.clear()
