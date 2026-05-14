from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bio_harness.core.tool_env import requirement_available
from bio_harness.workflows.fallback_guards import (
    requested_analysis_type,
    template_matches_analysis_type,
)
from bio_harness.workflows.fallback_catalog_core_templates import core_fallback_templates
from bio_harness.workflows.fallback_catalog_plan_builders import (
    DEFAULT_CACHE_ROOTS,
    DEFAULT_TEST_READS_PER_FASTQ,
    _build_fallback_template_plan,
)
from bio_harness.workflows.fallback_catalog_specialty_templates import specialty_fallback_templates
from bio_harness.workflows.fallback_catalog_utils import (
    _contract_signal_score as _contract_signal_score,
    _discover_fastq_pairs as _discover_fastq_pairs,
    _discover_long_read_fastqs as _discover_long_read_fastqs,
    _effective_required_exec_tools as _effective_required_exec_tools,
    _keyword_score as _keyword_score,
    _pick_first_pair as _pick_first_pair,
    _pick_two_group_pairs as _pick_two_group_pairs,
    _resolve_optional_existing_inputs as _resolve_optional_existing_inputs,
    _resolve_reference_file as _resolve_reference_file,
)


def _tool_available(tool_name: str, override: dict[str, bool] | None = None) -> bool:
    if isinstance(override, dict) and tool_name in override:
        return bool(override[tool_name])
    return requirement_available(tool_name)


def build_ranked_fallback_catalog() -> list[dict[str, Any]]:
    # Sources are primary/official docs and canonical publications.
    source_refs = {
        "star": "https://github.com/alexdobin/STAR",
        "hisat2": "https://daehwankimlab.github.io/hisat2/",
        "bwa": "https://github.com/lh3/bwa",
        "bowtie2": "https://bowtie-bio.sourceforge.net/bowtie2/manual.shtml",
        "minimap2": "https://github.com/lh3/minimap2",
        "gatk_hc": "https://gatk.broadinstitute.org/hc/en-us/articles/360037225632-HaplotypeCaller",
        "gatk_mutect2": "https://gatk.broadinstitute.org/hc/en-us/articles/360037593851-Mutect2",
        "bcftools": "https://samtools.github.io/bcftools/bcftools",
        "varscan": "https://pmc.ncbi.nlm.nih.gov/articles/PMC4278659/",
        "freebayes": "https://github.com/freebayes/freebayes",
        "deseq2": "https://bioconductor.org/packages/DESeq2/",
        "edger": "https://bioconductor.org/packages/edgeR/",
        "limma": "https://bioconductor.org/packages/limma/",
        "rmats": "https://rnaseq-mats.sourceforge.io/rmats4.0.2/user_guide.htm",
        "dexseq": "https://bioconductor.org/packages/DEXSeq/",
        "majiq_paper": "https://doi.org/10.1101/gr.209759.116",
        "blast": "https://www.ncbi.nlm.nih.gov/books/NBK569839/",
        "hmmer": "https://www.ebi.ac.uk/Tools/hmmer/",
        "prokka": "https://github.com/tseemann/prokka",
        "vep": "https://useast.ensembl.org/info/docs/tools/vep/index.html",
        "snpeff": "https://pcingola.github.io/SnpEff/",
        "bismark": "https://www.bioinformatics.babraham.ac.uk/projects/bismark/",
        "kraken2": "https://github.com/DerrickWood/kraken2/wiki/Manual",
        "bracken": "https://ccb.jhu.edu/software/bracken/",
        "star_fusion": "https://github.com/STAR-Fusion/STAR-Fusion/wiki",
        "cnvkit": "https://cnvkit.readthedocs.io/en/stable/",
        "mixcr": "https://mixcr.readthedocs.io/en/master/",
        "iqtree": "https://iqtree.github.io/doc/",
    }
    catalog = core_fallback_templates(source_refs)
    catalog.extend(specialty_fallback_templates(source_refs))
    return [dict(item) for item in sorted(catalog, key=lambda x: int(x.get("rank", 999)))]


def ranked_fallback_catalog_metadata() -> list[dict[str, Any]]:
    keep_keys = {
        "rank",
        "pipeline_id",
        "use_case",
        "assumptions",
        "required_inputs",
        "optional_inputs",
        "expected_outputs",
        "required_tools",
        "reference_requirements",
        "supports_short_read",
        "supports_long_read",
        "recovery_safety",
        "contract_capabilities",
        "contract_coverage_signals",
        "tool_wrappers",
        "skill_name",
    }
    out: list[dict[str, Any]] = []
    for item in build_ranked_fallback_catalog():
        row = {k: item.get(k) for k in keep_keys}
        out.append(row)
    return out


def _count_cache_hits(selected_dir: str, template: dict[str, Any]) -> int:
    root = Path(str(selected_dir or "")).expanduser()
    if not root.exists():
        return 0
    hits = 0
    for raw in template.get("cache_hints", []) if isinstance(template.get("cache_hints"), list) else []:
        pat = str(raw).strip()
        if not pat:
            continue
        try:
            if pat.startswith("outputs/"):
                matches = list(root.glob(pat))
            else:
                matches = list(Path(".").glob(pat))
        except Exception:
            matches = []
        for m in matches:
            try:
                if m.exists():
                    hits += 1
                    break
            except OSError:
                continue
    return hits


def select_ranked_fallback_plan(
    *,
    contract: dict[str, Any],
    prompt: str,
    data_root: str,
    selected_dir: str,
    reference_fasta: str = "",
    annotation_gtf: str = "",
    control_tag: str = "S1",
    treatment_tag: str = "S6",
    subset_mode: bool = True,
    test_reads_per_fastq: int = DEFAULT_TEST_READS_PER_FASTQ,
    cache_paths: dict[str, str] | None = None,
    tool_availability_override: dict[str, bool] | None = None,
    excluded_pipeline_ids: list[str] | None = None,
    graph_store: Any | None = None,
    preference_profile: dict[str, Any] | None = None,
    provenance_mode: str = "standard",
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    catalog = build_ranked_fallback_catalog()
    analysis_type = requested_analysis_type(contract, preference_profile)
    requested_caps = {
        str(x).strip()
        for x in (contract.get("must_include_capabilities", []) if isinstance(contract, dict) else [])
        if str(x).strip()
    }
    full_catalog_size = len(catalog)
    analysis_filtered_catalog = [
        template
        for template in catalog
        if template_matches_analysis_type(
            template,
            analysis_type=analysis_type,
            requested_capabilities=requested_caps,
        )
    ]
    if analysis_type and not analysis_filtered_catalog:
        return None, {
            "why": "no_same_class_fallback",
            "analysis_type": analysis_type,
            "catalog_size": len(catalog),
        }
    same_class_catalog_enforced = bool(analysis_filtered_catalog) and len(analysis_filtered_catalog) < full_catalog_size
    if analysis_filtered_catalog:
        catalog = analysis_filtered_catalog
    pair_map = _discover_fastq_pairs(data_root)
    long_fastqs = _discover_long_read_fastqs(data_root)
    existing_inputs = _resolve_optional_existing_inputs(data_root, selected_dir)
    cache_opts = dict(DEFAULT_CACHE_ROOTS)
    cache_opts.update(dict(cache_paths or {}))

    refs = {
        "reference_fasta": _resolve_reference_file("fasta", reference_fasta, data_root, selected_dir),
        "annotation_gtf": _resolve_reference_file("gtf", annotation_gtf, data_root, selected_dir),
    }

    preference_profile = dict(preference_profile or {})
    tool_blacklist = {
        str(x).strip().lower()
        for x in (
            preference_profile.get("tool_blacklist", [])
            if isinstance(preference_profile.get("tool_blacklist", []), list)
            else []
        )
        if str(x).strip()
    }
    preferred_tools = {
        str(x).strip().lower()
        for x in (
            preference_profile.get("preferred_tools", [])
            if isinstance(preference_profile.get("preferred_tools", []), list)
            else []
        )
        if str(x).strip()
    }
    discouraged_tools = {
        str(x).strip().lower()
        for x in (
            preference_profile.get("discouraged_tools", [])
            if isinstance(preference_profile.get("discouraged_tools", []), list)
            else []
        )
        if str(x).strip()
    }
    preferred_pipeline_ids = {
        str(x).strip()
        for x in (
            preference_profile.get("preferred_pipeline_ids", [])
            if isinstance(preference_profile.get("preferred_pipeline_ids", []), list)
            else []
        )
        if str(x).strip()
    }
    tool_hints = {
        str(x).strip().lower()
        for x in (contract.get("explicit_tool_hints", []) if isinstance(contract, dict) else [])
        if str(x).strip()
    }
    required_tool_hints = {
        str(x).strip().lower()
        for x in (contract.get("required_tool_hints", []) if isinstance(contract, dict) else [])
        if str(x).strip()
    }
    if tool_blacklist:
        tool_hints = {
            hint for hint in tool_hints if not any(blocked in hint or hint in blocked for blocked in tool_blacklist)
        }
        required_tool_hints = {
            hint
            for hint in required_tool_hints
            if not any(blocked in hint or hint in blocked for blocked in tool_blacklist)
        }
    prompt_l = (prompt or "").lower()
    wants_long = any(x in prompt_l for x in ("long read", "long-read", "ont", "nanopore", "pacbio"))
    wants_short = any(x in prompt_l for x in ("short read", "short-read", "illumina", "paired-end", "pair-end"))
    first_pair = _pick_first_pair(pair_map)
    grouped_pairs = _pick_two_group_pairs(pair_map, control_tag, treatment_tag)
    has_fastq_pair = bool(first_pair[0] and first_pair[1])
    has_two_group_fastq_pair = bool(grouped_pairs[0][0] and grouped_pairs[0][1] and grouped_pairs[1][0] and grouped_pairs[1][1])
    has_long_fastq = bool(long_fastqs)
    excluded = {str(x).strip() for x in (excluded_pipeline_ids or []) if str(x).strip()}

    candidates: list[dict[str, Any]] = []
    for template in catalog:
        pipeline_id = str(template.get("pipeline_id", "")).strip()
        if pipeline_id and pipeline_id in excluded:
            continue
        required_inputs = [str(x).strip() for x in template.get("required_inputs", []) if str(x).strip()]
        req_exec_tools = _effective_required_exec_tools(
            template,
            existing_inputs,
            has_fastq_pair=has_fastq_pair,
            has_two_group_fastq_pair=has_two_group_fastq_pair,
            has_long_fastq=has_long_fastq,
            provenance_mode=provenance_mode,
        )

        missing_inputs: list[str] = []
        for req in required_inputs:
            if req == "fastq_pairs" and not _pick_first_pair(pair_map)[0]:
                missing_inputs.append(req)
            elif req == "two_group_fastq_pairs":
                grp = _pick_two_group_pairs(pair_map, control_tag, treatment_tag)
                if not grp[0][0] or not grp[1][0]:
                    missing_inputs.append(req)
            elif req == "long_reads_fastq":
                has_cached_bam = bool(existing_inputs.get("bam"))
                if not long_fastqs and not has_cached_bam:
                    missing_inputs.append(req)
            elif req == "reference_fasta" and not refs.get("reference_fasta"):
                missing_inputs.append(req)
            elif req == "annotation_gtf" and not refs.get("annotation_gtf"):
                missing_inputs.append(req)
            elif req == "counts_matrix" and not existing_inputs.get("counts_matrix"):
                missing_inputs.append(req)
            elif req == "metadata_table" and not existing_inputs.get("metadata_table"):
                missing_inputs.append(req)
            elif req == "protein_fasta" and not existing_inputs.get("protein_fasta"):
                missing_inputs.append(req)
            elif req == "vcf" and not existing_inputs.get("vcf"):
                missing_inputs.append(req)
            elif req == "fastq_pairs_or_bam":
                has_pair = bool(_pick_first_pair(pair_map)[0])
                has_bam = bool(existing_inputs.get("bam"))
                if not (has_pair or has_bam):
                    missing_inputs.append(req)
            elif req == "two_group_fastq_pairs_or_bam":
                grp = _pick_two_group_pairs(pair_map, control_tag, treatment_tag)
                has_group_pair = bool(grp[0][0] and grp[1][0])
                has_two_bam = len(existing_inputs.get("bam", [])) >= 2
                if not (has_group_pair or has_two_bam):
                    missing_inputs.append(req)

        missing_tools = [t for t in req_exec_tools if not _tool_available(t, override=tool_availability_override)]
        covered_caps = requested_caps.intersection(set(template.get("contract_capabilities", [])))
        missing_caps = requested_caps.difference(set(template.get("contract_capabilities", [])))
        explicit_tool_hits = {hint for hint in tool_hints if any(hint in str(t).lower() for t in template.get("required_tools", []))}
        required_tool_hits = {
            hint
            for hint in required_tool_hints
            if any(hint in str(t).lower() for t in template.get("required_tools", []))
        }
        preferred_tool_hits = {
            hint
            for hint in preferred_tools
            if any(hint in str(t).lower() for t in template.get("required_tools", []))
        }
        discouraged_tool_hits = {
            hint
            for hint in discouraged_tools
            if any(hint in str(t).lower() for t in template.get("required_tools", []))
        }
        blacklisted_tool_hits = {
            hint
            for hint in tool_blacklist
            if any(hint in str(t).lower() for t in template.get("required_tools", []))
        }
        missing_required_tool_hints = sorted(required_tool_hints.difference(required_tool_hits))
        kw_hits = _keyword_score(prompt, [str(x) for x in template.get("keywords", [])])
        contract_signal_hits = _contract_signal_score(prompt, template, requested_caps)
        cache_hits = _count_cache_hits(selected_dir, template)

        score = 400 - int(template.get("rank", 999))
        score += 30 * len(covered_caps)
        score -= 18 * len(missing_caps)
        score -= 30 * len(missing_inputs)
        score -= 8 * len(missing_tools)
        score -= 40 * len(missing_required_tool_hints)
        score += 10 * len(explicit_tool_hits)
        score += 18 * len(required_tool_hits)
        score += 16 * len(preferred_tool_hits)
        score -= 14 * len(discouraged_tool_hits)
        score += 6 * kw_hits
        score += 4 * contract_signal_hits
        score += 5 * cache_hits
        if pipeline_id and pipeline_id in preferred_pipeline_ids:
            score += 24
        if wants_long and bool(template.get("supports_long_read", False)):
            score += 12
        if wants_short and bool(template.get("supports_short_read", False)):
            score += 10

        safety = str(template.get("recovery_safety", "medium")).lower()
        if safety == "high":
            score += 6
        elif safety == "medium":
            score += 2
        else:
            score -= 2

        has_all_inputs = len(missing_inputs) == 0
        has_all_tools = len(missing_tools) == 0
        feasibility_tier = 2
        if has_all_inputs and has_all_tools:
            feasibility_tier = 0
        elif has_all_inputs:
            feasibility_tier = 1
        candidates.append(
            {
                "pipeline_id": template.get("pipeline_id", ""),
                "rank": int(template.get("rank", 999)),
                "score": int(score),
                "required_tools_effective": sorted(req_exec_tools),
                "missing_inputs": sorted(missing_inputs),
                "missing_tools": sorted(missing_tools),
                "covered_caps": sorted(covered_caps),
                "missing_caps": sorted(missing_caps),
                "explicit_tool_hits": sorted(explicit_tool_hits),
                "required_tool_hits": sorted(required_tool_hits),
                "preferred_tool_hits": sorted(preferred_tool_hits),
                "discouraged_tool_hits": sorted(discouraged_tool_hits),
                "blacklisted_tool_hits": sorted(blacklisted_tool_hits),
                "missing_required_tool_hints": missing_required_tool_hints,
                "contract_signal_hits": int(contract_signal_hits),
                "cache_hits": int(cache_hits),
                "feasible": bool(has_all_inputs and has_all_tools),
                "has_all_inputs": bool(has_all_inputs),
                "has_all_tools": bool(has_all_tools),
                "feasibility_tier": int(feasibility_tier),
            }
        )

    candidates.sort(
        key=lambda x: (
            int(x.get("feasibility_tier", 9)),
            len(x.get("missing_caps", [])),
            len(x.get("missing_required_tool_hints", [])),
            len(x.get("missing_inputs", [])),
            len(x.get("missing_tools", [])),
            -int(x.get("score", -9999)),
            int(x.get("rank", 9999)),
            str(x.get("pipeline_id", "")),
        )
    )

    graph_rank_error = ""
    graph_reuse_candidates: list[dict[str, Any]] = []
    graph_signal_enabled = False
    if graph_store is not None and candidates:
        try:
            graph_reuse_candidates = (
                graph_store.get_candidate_paths_for_capabilities(
                    capabilities=sorted(requested_caps),
                    constraints={
                        "contract": contract,
                        "prompt": prompt,
                        "preference_profile": preference_profile or {},
                    },
                    top_k=min(20, len(candidates)),
                )
                if hasattr(graph_store, "get_candidate_paths_for_capabilities")
                else []
            )
            graph_constraints = {
                "contract": contract,
                "prompt": prompt,
                "preference_profile": preference_profile or {},
                "reuse_candidates": graph_reuse_candidates,
                "requested_caps": sorted(requested_caps),
            }
            ranked = (
                graph_store.rank_paths(
                    paths=candidates,
                    capabilities=sorted(requested_caps),
                    constraints=graph_constraints,
                    top_k=len(candidates),
                )
                if hasattr(graph_store, "rank_paths")
                else None
            )
            if isinstance(ranked, list) and ranked:
                candidates = [dict(row) for row in ranked]
            if hasattr(graph_store, "has_rank_signal"):
                ids = [
                    str(row.get("pipeline_id", "")).strip()
                    for row in candidates
                    if str(row.get("pipeline_id", "")).strip()
                ]
                graph_signal_enabled = bool(
                    graph_store.has_rank_signal(
                        path_ids=ids,
                        preference_profile=preference_profile or {},
                    )
                )
            else:
                graph_signal_enabled = bool(graph_reuse_candidates or (preference_profile or {}))
        except Exception as exc:
            graph_rank_error = str(exc)

    unblocked_candidates = [row for row in candidates if not row.get("blacklisted_tool_hits", [])]
    if unblocked_candidates:
        candidates = unblocked_candidates + [row for row in candidates if row.get("blacklisted_tool_hits", [])]

    cap_complete = [c for c in candidates if len(c.get("missing_caps", [])) == 0]
    if same_class_catalog_enforced and candidates and not cap_complete:
        return None, {
            "why": "no_capability_complete_fallback",
            "analysis_type": analysis_type,
            "requested_capabilities": sorted(requested_caps),
            "candidates": candidates[:10],
            "catalog_size": len(catalog),
        }
    best = cap_complete[0] if cap_complete else (candidates[0] if candidates else None)
    if best is None:
        return None, {"why": "fallback_catalog_empty", "candidates": []}

    selected = next((t for t in catalog if str(t.get("pipeline_id", "")) == str(best.get("pipeline_id", ""))), None)
    if not selected:
        return None, {"why": "selected_template_missing", "selection": best, "candidates": candidates[:10]}

    selection_reason = "fallback_best_partial"
    if bool(best.get("feasible", False)):
        selection_reason = "fallback_best_runnable"
    elif bool(best.get("has_all_inputs", False)):
        selection_reason = "fallback_best_with_inputs_missing_tools"

    ctx = {
        "contract": contract,
        "prompt": prompt,
        "data_root": data_root,
        "selected_dir": selected_dir,
        "control_tag": control_tag,
        "treatment_tag": treatment_tag,
        "subset_mode": bool(subset_mode),
        "test_reads_per_fastq": int(test_reads_per_fastq),
        "cache_paths": cache_opts,
        "references": refs,
        "pair_map": pair_map,
        "long_fastqs": long_fastqs,
        "existing_inputs": existing_inputs,
        "provenance_mode": str(provenance_mode or "standard"),
    }
    plan = _build_fallback_template_plan(selected, ctx)
    if not isinstance(plan, dict):
        return None, {
            "why": "fallback_template_builder_failed",
            "selection": best,
            "selected_template": selected,
            "candidates": candidates[:10],
        }
    return plan, {
        "why": "fallback_selected",
        "selection_reason": selection_reason,
        "selection_score": int(best.get("score", 0)),
        "selection_graph_score": float(best.get("graph_total_score", 0.0)),
        "selection": best,
        "selected_template": selected,
        "resolved_references": refs,
        "control_tag": control_tag,
        "treatment_tag": treatment_tag,
        "subset_mode": bool(subset_mode),
        "test_reads_per_fastq": int(test_reads_per_fastq),
        "cache_paths": cache_opts,
        "excluded_pipeline_ids": sorted(excluded),
        "analysis_type": analysis_type,
        "preference_profile": dict(preference_profile or {}),
        "provenance_mode": str(provenance_mode or "standard"),
        "graph_signal_enabled": bool(graph_signal_enabled),
        "graph_reuse_candidates": graph_reuse_candidates[:10],
        "graph_rank_error": graph_rank_error,
        "candidates": candidates[:10],
        "catalog_size": len(catalog),
        "catalog_summary": ranked_fallback_catalog_metadata(),
        "discovered_inputs": {
            "fastq_pair_count": len([k for k, v in pair_map.items() if v.get("1") and v.get("2")]),
            "long_fastq_count": len(long_fastqs),
            "bam_count": len(existing_inputs.get("bam", [])),
            "counts_matrix_count": len(existing_inputs.get("counts_matrix", [])),
            "metadata_table_count": len(existing_inputs.get("metadata_table", [])),
            "protein_fasta_count": len(existing_inputs.get("protein_fasta", [])),
            "vcf_count": len(existing_inputs.get("vcf", [])),
        },
    }


def dump_ranked_fallback_catalog_json() -> str:
    return json.dumps(ranked_fallback_catalog_metadata(), indent=2)
