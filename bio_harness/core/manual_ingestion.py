"""Deterministic documentation ingestion for tool onboarding.

This module turns help output, README text, markdown manuals, and normalized
web hits into compact structured guidance that can be attached to tool cards.
The implementation is intentionally deterministic so onboarding remains usable
without requiring an LLM or network access at runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

_COMMON_OUTPUT_SUFFIXES = (
    ".bam",
    ".bai",
    ".sam",
    ".vcf",
    ".bcf",
    ".gtf",
    ".gff",
    ".gff3",
    ".bed",
    ".bedgraph",
    ".bigwig",
    ".bw",
    ".tsv",
    ".csv",
    ".json",
    ".html",
    ".txt",
    ".fastq",
    ".fq",
    ".fasta",
    ".fa",
    ".h5ad",
    ".loom",
    ".mtx",
)
_DANGEROUS_FLAG_TOKENS = (
    "--force",
    "--overwrite",
    "--delete",
    "--remove",
    "--clobber",
    "--replace",
    "--clean",
    "--rm",
)
_WHEN_NOT_TO_USE_MARKERS = (
    "do not use",
    "don't use",
    "not for",
    "avoid using",
    "should not be used",
)
_OUTPUT_HINT_MARKERS = ("output", "outputs", "writes", "produces", "generated")
_EXAMPLE_HINT_MARKERS = ("example", "examples")
_ERROR_HINT_MARKERS = ("error", "failed", "missing", "invalid", "cannot", "unable")


@dataclass(frozen=True)
class DocumentationSource:
    """One documentation source used during onboarding.

    Attributes:
        source: Stable source identifier or URL.
        kind: Source type such as `help_text`, `readme`, or `web`.
        title: Optional short title.
        text: Extracted text content used for parsing.
    """

    source: str
    kind: str
    title: str
    text: str


@dataclass(frozen=True)
class ManualIngestionResult:
    """Structured summary derived from documentation sources.

    Attributes:
        when_to_use: Short intended-use statement inferred from docs.
        when_not_to_use: Short avoidance statement if present.
        canonical_outputs: Expected output files or formats mentioned in docs.
        dangerous_flags: Flags that can overwrite, remove, or mutate results.
        common_errors: Structured error snippets with lightweight guidance.
        example_invocations: Representative command examples.
        source_documents: Source identifiers included in the summary.
    """

    when_to_use: str
    when_not_to_use: str
    canonical_outputs: tuple[str, ...]
    dangerous_flags: tuple[str, ...]
    common_errors: tuple[dict[str, str], ...]
    example_invocations: tuple[str, ...]
    source_documents: tuple[str, ...]


def collect_documentation_sources(
    tool_name: str,
    *,
    help_text: str = "",
    readme_text: str = "",
    markdown_text: str = "",
    web_hits: Iterable[Mapping[str, Any]] = (),
    source_meta: Mapping[str, Any] | None = None,
    librarian: Any | None = None,
    max_web_results: int = 3,
) -> tuple[DocumentationSource, ...]:
    """Collect and normalize documentation sources for one tool.

    Args:
        tool_name: Tool name being onboarded.
        help_text: Raw CLI help text.
        readme_text: Local README-style text.
        markdown_text: Additional markdown manual text.
        web_hits: Pre-fetched normalized or semi-normalized web-search hits.
        source_meta: Optional onboarding source metadata.
        librarian: Optional `Librarian` instance for web search fallback.
        max_web_results: Maximum number of web hits to keep.

    Returns:
        Normalized documentation sources.
    """

    sources: list[DocumentationSource] = []
    if help_text.strip():
        sources.append(
            DocumentationSource(
                source=f"help_text:{tool_name}",
                kind="help_text",
                title=f"{tool_name} --help",
                text=help_text.strip(),
            )
        )
    if readme_text.strip():
        sources.append(
            DocumentationSource(
                source=f"readme:{tool_name}",
                kind="readme",
                title=f"{tool_name} README",
                text=readme_text.strip(),
            )
        )
    if markdown_text.strip():
        sources.append(
            DocumentationSource(
                source=f"markdown:{tool_name}",
                kind="markdown",
                title=f"{tool_name} manual",
                text=markdown_text.strip(),
            )
        )

    normalized_hits = list(_normalize_web_hits(web_hits))
    if not normalized_hits and librarian is not None:
        normalized_hits = _fetch_librarian_hits(
            tool_name=tool_name,
            source_meta=source_meta,
            librarian=librarian,
            max_web_results=max_web_results,
        )
    for hit in normalized_hits[:max_web_results]:
        sources.append(
            DocumentationSource(
                source=hit["source"],
                kind="web",
                title=hit["title"],
                text=hit["text"],
            )
        )
    return tuple(sources)


def ingest_tool_documentation(
    tool_name: str,
    *,
    help_text: str = "",
    readme_text: str = "",
    markdown_text: str = "",
    web_hits: Iterable[Mapping[str, Any]] = (),
    source_meta: Mapping[str, Any] | None = None,
    librarian: Any | None = None,
    max_web_results: int = 3,
) -> ManualIngestionResult:
    """Create a deterministic onboarding summary from documentation sources.

    Args:
        tool_name: Tool name being onboarded.
        help_text: Raw CLI help text.
        readme_text: Local README-style text.
        markdown_text: Additional markdown or manual text.
        web_hits: Optional pre-fetched web hits.
        source_meta: Optional onboarding source metadata.
        librarian: Optional search helper used only when no web hits are given.
        max_web_results: Maximum number of web hits to keep.

    Returns:
        Structured documentation summary suitable for tool cards.
    """

    sources = collect_documentation_sources(
        tool_name,
        help_text=help_text,
        readme_text=readme_text,
        markdown_text=markdown_text,
        web_hits=web_hits,
        source_meta=source_meta,
        librarian=librarian,
        max_web_results=max_web_results,
    )
    texts = [source.text for source in sources if source.text.strip()]
    source_documents = tuple(dict.fromkeys(source.source for source in sources if source.source.strip()))
    return ManualIngestionResult(
        when_to_use=_infer_when_to_use(tool_name, texts),
        when_not_to_use=_infer_when_not_to_use(texts),
        canonical_outputs=tuple(_extract_canonical_outputs(texts)),
        dangerous_flags=tuple(_extract_dangerous_flags(texts)),
        common_errors=tuple(_extract_common_errors(texts)),
        example_invocations=tuple(_extract_example_invocations(tool_name, texts)),
        source_documents=source_documents,
    )


def render_manual_ingestion_result(result: ManualIngestionResult) -> dict[str, Any]:
    """Render a manual-ingestion result into a JSON-ready mapping."""

    payload = asdict(result)
    payload["canonical_outputs"] = list(result.canonical_outputs)
    payload["dangerous_flags"] = list(result.dangerous_flags)
    payload["common_errors"] = [dict(value) for value in result.common_errors]
    payload["example_invocations"] = list(result.example_invocations)
    payload["source_documents"] = list(result.source_documents)
    return payload


def _fetch_librarian_hits(
    *,
    tool_name: str,
    source_meta: Mapping[str, Any] | None,
    librarian: Any,
    max_web_results: int,
) -> list[dict[str, str]]:
    """Fetch normalized web hits from a librarian if one is available."""

    allowed_domains = _allowed_domains_from_source_meta(source_meta)
    try:
        raw_hits = librarian.web_search(
            f"{tool_name} documentation usage outputs examples",
            max_results=max_web_results,
            allowed_domains=allowed_domains,
        )
    except Exception:
        return []
    return list(_normalize_web_hits(raw_hits))


def _allowed_domains_from_source_meta(
    source_meta: Mapping[str, Any] | None,
) -> list[str] | None:
    """Infer an allow-list from onboarding source metadata when possible."""

    if not isinstance(source_meta, Mapping):
        return None
    source = str(source_meta.get("source", "")).strip()
    if not source:
        return None
    try:
        host = (urlparse(source).hostname or "").lower()
    except Exception:
        return None
    return [host] if host else None


def _normalize_web_hits(
    web_hits: Iterable[Mapping[str, Any]],
) -> Iterable[dict[str, str]]:
    """Normalize heterogeneous web-hit mappings into a stable shape."""

    for raw_hit in web_hits:
        if not isinstance(raw_hit, Mapping):
            continue
        source = str(
            raw_hit.get("href", "")
            or raw_hit.get("url", "")
            or raw_hit.get("source", "")
        ).strip()
        title = str(raw_hit.get("title", "") or raw_hit.get("name", "")).strip()
        text = str(
            raw_hit.get("body", "")
            or raw_hit.get("snippet", "")
            or raw_hit.get("abstract", "")
            or raw_hit.get("text", "")
        ).strip()
        if not source and not title and not text:
            continue
        yield {
            "source": source or f"web:{title[:40]}",
            "title": title or source,
            "text": text or title,
        }


def _infer_when_to_use(tool_name: str, texts: Iterable[str]) -> str:
    """Infer a short intended-use sentence from documentation text."""

    candidates = _iter_candidate_sentences(texts)
    for sentence in candidates:
        lowered = sentence.lower()
        if "usage:" in lowered or sentence.startswith("-"):
            continue
        if tool_name.lower() in lowered or any(marker in lowered for marker in ("use", "align", "quant", "call", "analy")):
            return sentence
    return f"Use `{tool_name}` when its documented command-line workflow fits the analysis."


def _infer_when_not_to_use(texts: Iterable[str]) -> str:
    """Infer a short avoidance sentence from documentation text."""

    for text in texts:
        for line in text.splitlines():
            cleaned = " ".join(line.strip().split())
            lowered = cleaned.lower()
            if cleaned and any(marker in lowered for marker in _WHEN_NOT_TO_USE_MARKERS):
                return cleaned
    for sentence in _iter_candidate_sentences(texts):
        lowered = sentence.lower()
        if any(marker in lowered for marker in _WHEN_NOT_TO_USE_MARKERS):
            return sentence
    return ""


def _extract_canonical_outputs(texts: Iterable[str]) -> list[str]:
    """Extract likely canonical outputs from documentation text."""

    outputs: list[str] = []
    seen: set[str] = set()
    for text in texts:
        capture_output_block = False
        for line in text.splitlines():
            stripped = line.strip()
            lowered = stripped.lower()
            if not stripped:
                capture_output_block = False
                continue
            if lowered.startswith("usage:") or lowered.startswith("example:"):
                capture_output_block = False
                continue
            if any(marker in lowered for marker in _OUTPUT_HINT_MARKERS):
                capture_output_block = True
            elif capture_output_block and stripped.endswith(":"):
                capture_output_block = False
                continue
            elif not capture_output_block:
                continue
            for token in re.findall(r"[\w./-]+", line):
                normalized = token.strip("`'\".,:;()[]{}")
                if not normalized:
                    continue
                token_lower = normalized.lower()
                if "input" in token_lower:
                    continue
                if any(token_lower.endswith(suffix) for suffix in _COMMON_OUTPUT_SUFFIXES):
                    if token_lower not in seen:
                        seen.add(token_lower)
                        outputs.append(normalized)
    return outputs


def _extract_dangerous_flags(texts: Iterable[str]) -> list[str]:
    """Extract overwrite or mutating flags from documentation text."""

    flags: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for token in re.findall(r"--[\w-]+", text):
            lowered = token.lower()
            if lowered in _DANGEROUS_FLAG_TOKENS and lowered not in seen:
                seen.add(lowered)
                flags.append(lowered)
    return flags


def _extract_common_errors(texts: Iterable[str]) -> list[dict[str, str]]:
    """Extract lightweight structured error hints from documentation text."""

    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    for text in texts:
        for line in text.splitlines():
            cleaned = " ".join(line.strip().split())
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if not any(marker in lowered for marker in _ERROR_HINT_MARKERS):
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            errors.append(
                {
                    "pattern": cleaned[:240],
                    "cause": _extract_error_cause(cleaned),
                    "fix": _extract_error_fix(cleaned),
                }
            )
            if len(errors) >= 8:
                return errors
    return errors


def _extract_example_invocations(tool_name: str, texts: Iterable[str]) -> list[str]:
    """Extract representative command examples for one tool."""

    examples: list[str] = []
    seen: set[str] = set()
    prefix = tool_name.lower()
    for text in texts:
        for line in text.splitlines():
            cleaned = " ".join(line.strip().split())
            lowered = cleaned.lower()
            if not cleaned:
                continue
            if (lowered.startswith(prefix + " ") or lowered == prefix) and _looks_like_command_example(
                cleaned,
                tool_name=tool_name,
            ):
                if cleaned not in seen:
                    seen.add(cleaned)
                    examples.append(cleaned)
                    continue
            if any(marker in lowered for marker in _EXAMPLE_HINT_MARKERS):
                for token_line in text.splitlines():
                    candidate = " ".join(token_line.strip().split())
                    if (
                        candidate.lower().startswith(prefix + " ")
                        and _looks_like_command_example(candidate, tool_name=tool_name)
                        and candidate not in seen
                    ):
                        seen.add(candidate)
                        examples.append(candidate)
            if len(examples) >= 5:
                return examples
    return examples


def _iter_candidate_sentences(texts: Iterable[str]) -> Iterable[str]:
    """Yield short candidate sentences from documentation text."""

    for text in texts:
        normalized = re.sub(r"\s+", " ", text).strip()
        for sentence in re.split(r"(?<=[.!?])\s+", normalized):
            candidate = sentence.strip()
            if 20 <= len(candidate) <= 220:
                yield candidate


def _looks_like_command_example(candidate: str, *, tool_name: str) -> bool:
    """Return whether a line looks like a real shell command example."""

    lowered = candidate.lower()
    if not lowered.startswith(tool_name.lower()):
        return False
    tokens = candidate.split()
    if len(tokens) < 2:
        return False
    return any(
        token.startswith("-")
        or any(token.lower().endswith(suffix) for suffix in _COMMON_OUTPUT_SUFFIXES)
        for token in tokens[1:]
    )


def _extract_error_cause(line: str) -> str:
    """Extract a lightweight cause hint from an error line."""

    lowered = line.lower()
    if "missing" in lowered:
        return "required input or dependency is missing"
    if "invalid" in lowered:
        return "an argument or file format is invalid"
    if "cannot" in lowered or "unable" in lowered:
        return "the tool could not access or parse a required resource"
    return ""


def _extract_error_fix(line: str) -> str:
    """Extract a lightweight fix hint from an error line."""

    lowered = line.lower()
    if "--help" in lowered:
        return "recheck the documented command syntax"
    if "missing" in lowered:
        return "verify input paths and required indices before rerunning"
    if "invalid" in lowered:
        return "recheck flags and input file formats before rerunning"
    return ""


__all__ = [
    "DocumentationSource",
    "ManualIngestionResult",
    "collect_documentation_sources",
    "ingest_tool_documentation",
    "render_manual_ingestion_result",
]
