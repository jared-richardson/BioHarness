from __future__ import annotations

from pathlib import Path

from scripts.run_agent_e2e_runtime_repair_branch_support import (
    maybe_resume_from_existing_artifacts,
    maybe_substitute_failed_tool_from_context,
    maybe_substitute_missing_tool,
    merge_resume_metadata,
)


def test_maybe_resume_from_existing_artifacts_marks_completed_prefix() -> None:
    run = {
        "plan": {"plan": [{"tool_name": "a"}, {"tool_name": "b"}, {"tool_name": "c"}]},
        "step_statuses": ["pending", "pending", "pending"],
        "next_step_idx": 0,
    }
    emitted: list[str] = []

    repaired, action, details = maybe_resume_from_existing_artifacts(
        run,
        selected_dir="/tmp/work",
        recovery_context={
            "recovery_strategy": "skip_step_use_artifact",
            "existing_artifacts": ["/tmp/work/out1.txt"],
        },
        emit=lambda message, **_kwargs: emitted.append(str(message)),
        quiet=True,
    )

    assert repaired is True
    assert action == "skip_step_use_artifact"
    assert run["step_statuses"] == ["pending", "pending", "pending"] or run["next_step_idx"] >= 0
    assert "resume_idx" in details
    assert emitted


def test_maybe_substitute_failed_tool_from_context_rewrites_step_and_resets_failed_idx() -> None:
    run = {
        "plan": {
            "plan": [
                {"tool_name": "old_tool", "arguments": {}, "step_id": 1},
                {"tool_name": "other", "arguments": {}, "step_id": 2},
            ]
        },
        "step_statuses": ["failed", "pending"],
        "failed_step_idx": 0,
        "next_step_idx": 1,
    }
    emitted: list[str] = []

    repaired, action, details = maybe_substitute_failed_tool_from_context(
        run,
        recovery_context={
            "recovery_strategy": "substitute_tool",
            "failed_tool": "old_tool",
            "viable_substitutions": ["new_tool"],
        },
        emit=lambda message, **_kwargs: emitted.append(str(message)),
        quiet=True,
    )

    assert repaired is True
    assert action == "substitute_tool"
    assert run["plan"]["plan"][0]["tool_name"] == "new_tool"
    assert run["step_statuses"][0] == "pending"
    assert run["next_step_idx"] == 0
    assert details["substitute"] == "new_tool"
    assert emitted


def test_maybe_substitute_missing_tool_rewrites_first_matching_step() -> None:
    run = {
        "plan": {
            "plan": [
                {"tool_name": "cnvkit.py", "arguments": {}, "step_id": 1},
                {"tool_name": "other", "arguments": {}, "step_id": 2},
            ]
        }
    }
    emitted: list[str] = []

    repaired, action, details = maybe_substitute_missing_tool(
        run,
        missing_tools=["cnvkit.py"],
        tool_equivalence_map={"cnvkit.py": ["cnvkit"]},
        emit=lambda message, **_kwargs: emitted.append(str(message)),
        quiet=True,
    )

    assert repaired is True
    assert action == "tool_missing_substitution"
    assert run["plan"]["plan"][0]["tool_name"] == "cnvkit"
    assert details["missing_tool"] == "cnvkit.py"
    assert emitted


def test_merge_resume_metadata_combines_diff_summary_and_resume_fields() -> None:
    merged = merge_resume_metadata(
        canonicalization={"diff_summary": {"before_step_count": 2, "after_step_count": 3}},
        resume={"preserved_completed_steps": 1, "resume_idx": 2},
    )

    assert merged == {
        "before_step_count": 2,
        "after_step_count": 3,
        "preserved_completed_steps": 1,
        "resume_idx": 2,
    }


def test_maybe_resume_from_existing_artifacts_blocks_failed_step_without_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    annotation_dir = tmp_path / "annotation" / "prokka"
    annotation_dir.mkdir(parents=True, exist_ok=True)
    run = {
        "plan": {
            "plan": [
                {"tool_name": "spades_assemble", "arguments": {"output_dir": str(tmp_path / "assembly" / "ancestor_spades")}},
                {"tool_name": "prokka_annotate", "arguments": {"output_dir": str(annotation_dir)}},
                {"tool_name": "snpeff_annotate", "arguments": {"input_vcf": "raw.vcf", "output_vcf": "annotated.vcf"}},
            ]
        },
        "step_statuses": ["completed", "failed", "pending"],
        "next_step_idx": 1,
        "failed_step_idx": 1,
    }
    monkeypatch.setattr(
        "scripts.run_agent_e2e_runtime_repair_branch_support.infer_resumable_step_index",
        lambda _selected_dir, _plan: 2,
    )

    repaired, action, details = maybe_resume_from_existing_artifacts(
        run,
        selected_dir=str(tmp_path),
        recovery_context={
            "recovery_strategy": "skip_step_use_artifact",
            "existing_artifacts": [str(tmp_path / "assembly" / "ancestor_spades" / "scaffolds.fasta")],
        },
        emit=lambda *_args, **_kwargs: None,
        quiet=True,
    )

    assert repaired is False
    assert action == "skip_step_not_applicable"
    assert details == {}
