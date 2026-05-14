from scripts.run_agent_e2e_state import _generic_template_fallback_used


def test_generic_template_fallback_used_false_for_diagnostic_only_selection() -> None:
    selection = {
        "selected_pipeline_id": "",
        "why": "no_same_class_fallback",
        "selection": {},
    }

    assert _generic_template_fallback_used(selection) is False


def test_generic_template_fallback_used_true_for_selected_pipeline() -> None:
    selection = {
        "selected_pipeline_id": "rna_seq_bulk_deseq2",
        "why": "ranked_fallback_template_selected",
    }

    assert _generic_template_fallback_used(selection) is True


def test_generic_template_fallback_used_true_for_stub_create_action() -> None:
    selection = {
        "selected_pipeline_id": "",
        "why": "created_stub_plan",
        "selection": {"action": "create"},
    }

    assert _generic_template_fallback_used(selection) is True


def test_generic_template_fallback_used_true_for_composed_template_segments() -> None:
    selection = {
        "selected_pipeline_id": "",
        "why": "ranked_fallback_template_selected",
        "composition": {
            "applied": True,
            "selected_pipeline_ids": ["segment_a", "segment_b"],
        },
    }

    assert _generic_template_fallback_used(selection) is True
