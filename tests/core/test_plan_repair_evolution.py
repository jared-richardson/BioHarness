from __future__ import annotations

from pathlib import Path

from bio_harness.harness.plan_repair_evolution import (
    _canonical_evolution_bam_path,
    _repair_evolution_alignment_path_bindings,
    _repair_evolution_spades_reference_usage,
)


def test_canonical_evolution_bam_path_normalizes_evolved_labels(tmp_path: Path) -> None:
    # Fix #25: canonical BAM naming MUST match the strict binder's
    # {slug}_aligned.bam convention (see
    # bio_harness/core/strict_artifact_binding_variant_binders.py). Previously
    # this helper returned {slug}.bam which silently corrupted strict-bound
    # evolved-sample paths during plan normalization — see the Fix #25
    # docstring in plan_repair_evolution.py for the exp41 evidence.
    selected_dir = tmp_path / "workspace"

    bam_path = _canonical_evolution_bam_path(selected_dir, "Evolved Line 2")

    assert bam_path == str((selected_dir / "alignments" / "evol2_aligned.bam").resolve(strict=False))


def test_repair_evolution_alignment_path_bindings_rewrites_bwa_and_freebayes_paths(tmp_path: Path) -> None:
    selected_dir = tmp_path / "workspace"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": "/tmp/evol1_R1.fastq.gz",
                    "reads_2": "/tmp/evol1_R2.fastq.gz",
                    "reference_fasta": "/tmp/ancestor.fa",
                    "output_bam": "/tmp/misaligned/evol1_sorted.bam",
                },
            },
                {
                    "step_id": 2,
                    "tool_name": "freebayes_call",
                    "arguments": {
                    "input_bam": "/tmp/EVOL1.bam",
                        "reference_fasta": "",
                    "output_vcf": "/tmp/EVOL1.vcf",
                    },
                },
            ]
        }

    repaired, meta = _repair_evolution_alignment_path_bindings(
        plan,
        request_text="Run experimental evolution variant calling for evolved isolates vs ancestor.",
        selected_dir=selected_dir,
    )

    # Fix #25: canonical path uses {slug}_aligned.bam to match the strict
    # binder's scaffold. Before Fix #25 this repair emitted evol1.bam,
    # which conflicted with the strict binder's evol1_aligned.bam — the
    # bwa_mem_align wrapper wrote evol1.bam on disk, then the next step's
    # strict binder requested evol1_aligned.bam (missing), and the Fix #22b
    # disk-existence pre-check rejected the candidate (exp41 failure).
    expected_bam = str((selected_dir / "alignments" / "evol1_aligned.bam").resolve(strict=False))
    assert meta["changed"] is True
    assert repaired["plan"][0]["arguments"]["output_bam"] == expected_bam
    assert repaired["plan"][1]["arguments"]["input_bam"] == expected_bam


def test_repair_evolution_spades_reference_usage_preserves_consumed_ancestor_steps(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "workspace"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "/inputs/anc_R1.fastq.gz",
                    "reads_2": "/inputs/anc_R2.fastq.gz",
                    "output_dir": str(selected_dir / "anc_assembly"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": str(selected_dir / "anc_assembly" / "scaffolds.fasta"),
                    "output_gff": str(selected_dir / "anc_assembly" / "genes.gff"),
                    "output_faa": str(selected_dir / "anc_assembly" / "genes.faa"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": "/inputs/anc_R1.fastq.gz",
                    "reads_2": "/inputs/anc_R2.fastq.gz",
                    "reference_fasta": str(selected_dir / "anc_assembly" / "scaffolds.fasta"),
                    "output_bam": str(selected_dir / "ancestor_align" / "anc.sorted.bam"),
                },
            },
            {
                "step_id": 4,
                "tool_name": "freebayes_call",
                "arguments": {
                    "input_bam": str(selected_dir / "ancestor_align" / "anc.sorted.bam"),
                    "reference_fasta": str(selected_dir / "anc_assembly" / "scaffolds.fasta"),
                    "output_vcf": "ancestor_raw.vcf",
                },
            },
            {
                "step_id": 5,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": "/inputs/evol1_R1.fastq.gz",
                    "reads_2": "/inputs/evol1_R2.fastq.gz",
                    "reference_fasta": str(selected_dir / "anc_assembly" / "scaffolds.fasta"),
                    "output_bam": str(selected_dir / "alignments" / "evol1.bam"),
                },
            },
            {
                "step_id": 6,
                "tool_name": "freebayes_call",
                "arguments": {
                    "input_bam": str(selected_dir / "alignments" / "evol1.bam"),
                    "reference_fasta": str(selected_dir / "anc_assembly" / "scaffolds.fasta"),
                    "output_vcf": "evol1_raw.vcf",
                },
            },
            {
                "step_id": 7,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o filtered_anc.vcf.gz ancestor_raw.vcf && "
                        "bcftools view -Oz -o filtered_evol1.vcf.gz evol1_raw.vcf"
                    )
                },
            },
        ]
    }

    repaired, meta = _repair_evolution_spades_reference_usage(
        plan,
        "experimental evolution variant calling relative to ancestor",
        selected_dir=selected_dir,
    )

    tool_names = [step["tool_name"] for step in repaired["plan"]]
    assert tool_names.count("bwa_mem_align") == 2
    assert tool_names.count("freebayes_call") == 2
    assert all(
        replacement.get("argument") != "remove_unused_steps"
        for replacement in meta.get("replacements", [])
    )


def test_repair_evolution_spades_reference_usage_supports_safe_reference_only_mode(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "workspace"
    ancestor_scaffolds = str((selected_dir / "anc_assembly" / "scaffolds.fasta").resolve(strict=False))
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "/inputs/anc_R1.fastq.gz",
                    "reads_2": "/inputs/anc_R2.fastq.gz",
                    "output_dir": str(selected_dir / "anc_assembly"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": "spades_assemble.fasta",
                    "output_gff": "prodigal_ancestor.gff",
                    "output_faa": "prodigal_ancestor.faa",
                },
            },
            {
                "step_id": 3,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": "/inputs/evol1_R1.fastq.gz",
                    "reads_2": "/inputs/evol1_R2.fastq.gz",
                    "reference_fasta": "spades_assemble.fasta",
                    "output_bam": "evol1_aligned.bam",
                },
            },
            {
                "step_id": 4,
                "tool_name": "freebayes_call",
                "arguments": {
                    "input_bam": "evol1_aligned.bam",
                    "reference_fasta": "spades_assemble.fasta",
                    "output_vcf": "evol1_freebayes.vcf",
                },
            },
        ]
    }

    repaired, meta = _repair_evolution_spades_reference_usage(
        plan,
        "Identify and annotate genome variants shared by two evolved lines relative to an ancestor.",
        selected_dir=selected_dir,
        allow_destructive_mutations=False,
    )

    assert [step["tool_name"] for step in repaired["plan"]] == [
        "spades_assemble",
        "prodigal_annotate",
        "bwa_mem_align",
        "freebayes_call",
    ]
    assert repaired["plan"][1]["arguments"]["input_fasta"] == ancestor_scaffolds
    assert repaired["plan"][2]["arguments"]["reference_fasta"] == ancestor_scaffolds
    assert repaired["plan"][3]["arguments"]["reference_fasta"] == ancestor_scaffolds
    assert meta["changed"] is True
    assert all(
        replacement.get("argument") != "remove_unused_steps"
        for replacement in meta.get("replacements", [])
    )


def test_canonical_evolution_bam_path_matches_strict_binder_convention_fix_25(tmp_path: Path) -> None:
    """Fix #25: guard against the two canonical BAM-naming schemes diverging.

    Root cause addressed: ``_repair_evolution_alignment_path_bindings`` runs
    in the scientific-harness plan normalizer AFTER
    ``rebind_direct_plan_for_strict_mode`` has already bound evolved-sample
    output_bam paths onto the strict binder's ``{slug}_aligned.bam`` scaffold.
    Before Fix #25 the repair emitted the bare ``{slug}.bam`` form, silently
    overwriting ``evol1_aligned.bam`` with ``evol1.bam``. The bwa_mem_align
    wrapper then wrote the final BAM as ``evol1.bam`` on disk, but the next
    step's strict binder still expected ``evol1_aligned.bam``. The Fix #22b
    disk-existence pre-check rejected the candidate with "Candidate step
    references inputs that are not available yet" and stalled the run (exp41
    failed at turn 7 for exactly this reason). This regression test asserts
    the repair helper emits filenames that match the strict binder's
    scaffold exactly, for each evolved-slug family the helper recognizes.
    """

    from bio_harness.core.strict_artifact_binding_variant_binders import (
        _bind_bacterial_evolution_variant_calling,
    )
    from bio_harness.core.strict_artifact_binding import (
        make_strict_artifact_binding_context,
    )

    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)

    for sample_tag, expected_filename in (
        ("Evolved Line 1", "evol1_aligned.bam"),
        ("evol2", "evol2_aligned.bam"),
        ("evolved-3", "evol3_aligned.bam"),
    ):
        repair_path = _canonical_evolution_bam_path(selected_dir, sample_tag)
        assert repair_path == str(
            (selected_dir / "alignments" / expected_filename).resolve(strict=False)
        ), f"_canonical_evolution_bam_path({sample_tag!r}) -> {repair_path}"

    # Cross-check against the strict binder directly by invoking the binder
    # path that assigns the canonical output_bam for an evol1 bwa_mem_align
    # step. Both sides must land on the same filename or the Fix #22b
    # disk-existence pre-check will reject downstream steps.
    step_spec = {
        "step_id": 5,
        "tool_name": "bwa_mem_align",
        "arguments": {
            "reference_fasta": str(selected_dir / "assembly" / "scaffolds.fasta"),
            "reads_1": "/tmp/evol1_R1.fastq.gz",
            "reads_2": "/tmp/evol1_R2.fastq.gz",
            "output_bam": str(selected_dir / "alignments" / "evol1_aligned.bam"),
            "threads": 8,
            "postprocess_mode": "fixmate_markdup_q20",
        },
    }
    workflow_step = {"branch_id": "evol1", "objective": ""}
    ctx = make_strict_artifact_binding_context(
        step_spec=step_spec,
        workflow_step=workflow_step,
        analysis_spec={
            "analysis_type": "bacterial_evolution_variant_calling",
            "selected_dir": str(selected_dir),
        },
    )
    bound = _bind_bacterial_evolution_variant_calling(step_spec, ctx)
    strict_path = bound["arguments"]["output_bam"]
    repair_path_evol1 = _canonical_evolution_bam_path(selected_dir, "evol1")
    assert strict_path == repair_path_evol1, (
        f"strict binder emitted {strict_path!r} but repair emitted "
        f"{repair_path_evol1!r} — these must match or the bwa wrapper will "
        f"write a BAM the next step's strict binder cannot find"
    )
