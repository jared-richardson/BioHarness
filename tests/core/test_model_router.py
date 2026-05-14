"""Tests for bio_harness.core.model_router."""
from __future__ import annotations

import pytest

from bio_harness.core.model_router import (
    ModelInfo,
    _parse_parameter_size,
    assess_prompt_complexity,
    discover_models,
    discover_models_from_ollama_tags,
    select_default_models,
    select_models,
)


# ---------------------------------------------------------------------------
# ModelInfo
# ---------------------------------------------------------------------------

class TestModelInfo:
    def test_tier_heavy(self):
        m = ModelInfo(name="big", parameter_count_b=122.0)
        assert m.tier == "heavy"

    def test_tier_fast(self):
        m = ModelInfo(name="mid", parameter_count_b=14.0)
        assert m.tier == "fast"

    def test_tier_light(self):
        m = ModelInfo(name="tiny", parameter_count_b=7.0)
        assert m.tier == "light"

    def test_tier_boundary_30(self):
        assert ModelInfo(name="x", parameter_count_b=30.0).tier == "heavy"
        assert ModelInfo(name="x", parameter_count_b=29.9).tier == "fast"

    def test_tier_boundary_10(self):
        assert ModelInfo(name="x", parameter_count_b=10.0).tier == "fast"
        assert ModelInfo(name="x", parameter_count_b=9.9).tier == "light"

    def test_is_coder_by_family(self):
        m = ModelInfo(name="something", family="qwen3next")
        assert m.is_coder is True

    def test_is_coder_by_name(self):
        m = ModelInfo(name="deepseek-coder:latest", family="deepseek")
        assert m.is_coder is True

    def test_not_coder(self):
        m = ModelInfo(name="qwen3.5:122b-a10b", family="qwen35moe")
        assert m.is_coder is False


# ---------------------------------------------------------------------------
# _parse_parameter_size
# ---------------------------------------------------------------------------

class TestParseParameterSize:
    def test_billions(self):
        assert _parse_parameter_size("122.1B") == pytest.approx(122.1)

    def test_millions(self):
        assert _parse_parameter_size("7000M") == pytest.approx(7.0)

    def test_trillions(self):
        assert _parse_parameter_size("1.5T") == pytest.approx(1500.0)

    def test_kilos(self):
        assert _parse_parameter_size("500K") == pytest.approx(0.0005)

    def test_bare_number(self):
        assert _parse_parameter_size("14") == pytest.approx(14.0)

    def test_empty(self):
        assert _parse_parameter_size("") == 0.0

    def test_none(self):
        assert _parse_parameter_size(None) == 0.0

    def test_garbage(self):
        assert _parse_parameter_size("garbage") == 0.0

    def test_with_spaces(self):
        assert _parse_parameter_size("  122.1 B  ") == pytest.approx(122.1)


# ---------------------------------------------------------------------------
# discover_models_from_ollama_tags
# ---------------------------------------------------------------------------

class TestDiscoverModelsFromOllamaTags:
    def test_basic_tags(self):
        tags = {
            "models": [
                {
                    "model": "qwen3.5:122b-a10b",
                    "size": int(67e9),
                    "details": {
                        "parameter_size": "122.1B",
                        "family": "qwen35moe",
                    },
                },
                {
                    "model": "qwen3-coder-next:latest",
                    "size": int(5e9),
                    "details": {
                        "parameter_size": "14B",
                        "family": "qwen3next",
                    },
                },
            ]
        }
        models = discover_models_from_ollama_tags(tags)
        assert len(models) == 2
        assert models[0].name == "qwen3.5:122b-a10b"
        assert models[0].parameter_count_b == pytest.approx(122.1)
        assert models[0].family == "qwen35moe"
        assert models[1].name == "qwen3-coder-next:latest"
        assert models[1].parameter_count_b == pytest.approx(14.0)
        assert models[1].family == "qwen3next"

    def test_empty_tags(self):
        assert discover_models_from_ollama_tags({}) == []
        assert discover_models_from_ollama_tags({"models": []}) == []

    def test_missing_details(self):
        tags = {"models": [{"model": "basic:latest", "size": int(3e9)}]}
        models = discover_models_from_ollama_tags(tags)
        assert len(models) == 1
        assert models[0].name == "basic:latest"
        assert models[0].parameter_count_b == 0.0

    def test_skips_non_dict(self):
        tags = {"models": ["string-entry", None, 123]}
        assert discover_models_from_ollama_tags(tags) == []

    def test_skips_nameless(self):
        tags = {"models": [{"details": {"parameter_size": "7B"}}]}
        assert discover_models_from_ollama_tags(tags) == []


# ---------------------------------------------------------------------------
# discover_models
# ---------------------------------------------------------------------------

class TestDiscoverModels:
    def test_uses_metadata_method(self):
        class MockBackend:
            def list_models_with_metadata(self):
                return [
                    {"name": "heavy:latest", "parameter_count_b": 70.0, "family": "llama", "size_gb": 40.0},
                    {"name": "fast:latest", "parameter_count_b": 14.0, "family": "qwen3next", "size_gb": 8.0},
                ]
            def list_models(self):
                return ["heavy:latest", "fast:latest"]

        models = discover_models(MockBackend())
        assert len(models) == 2
        assert models[0].parameter_count_b == 70.0

    def test_fallback_to_list_models(self):
        class MockBackend:
            def list_models(self):
                return ["model-a", "model-b"]

        models = discover_models(MockBackend())
        assert len(models) == 2
        assert models[0].name == "model-a"
        assert models[0].parameter_count_b == 0.0

    def test_metadata_failure_falls_back(self):
        class MockBackend:
            def list_models_with_metadata(self):
                raise RuntimeError("Ollama not responding")
            def list_models(self):
                return ["fallback-model"]

        models = discover_models(MockBackend())
        assert len(models) == 1
        assert models[0].name == "fallback-model"

    def test_empty_metadata_falls_back(self):
        class MockBackend:
            def list_models_with_metadata(self):
                return []
            def list_models(self):
                return ["fallback-model"]

        models = discover_models(MockBackend())
        assert len(models) == 1
        assert models[0].name == "fallback-model"

    def test_both_fail_returns_empty(self):
        class MockBackend:
            def list_models_with_metadata(self):
                raise RuntimeError("fail")
            def list_models(self):
                raise RuntimeError("fail")

        assert discover_models(MockBackend()) == []


# ---------------------------------------------------------------------------
# assess_prompt_complexity
# ---------------------------------------------------------------------------

class TestAssessPromptComplexity:
    def test_unknown_type_is_high(self):
        assert assess_prompt_complexity(analysis_type=None) == "high"
        assert assess_prompt_complexity(analysis_type="") == "high"

    def test_high_complexity_types(self):
        assert assess_prompt_complexity(analysis_type="bacterial_evolution_variant_calling") == "high"
        assert assess_prompt_complexity(analysis_type="rna_seq_differential_expression") == "high"
        assert assess_prompt_complexity(analysis_type="multi_model_dge_pathway") == "high"

    def test_template_types_are_low(self):
        # These have template compilers so should be low complexity
        # (depends on what's in TEMPLATE_COMPILER_TYPES)
        result = assess_prompt_complexity(analysis_type="phylogenetics")
        # phylogenetics has a template compiler, should be "low"
        assert result == "low"

    def test_unrecognized_type_is_high(self):
        assert assess_prompt_complexity(analysis_type="completely_unknown_type_xyz") == "high"


# ---------------------------------------------------------------------------
# select_models
# ---------------------------------------------------------------------------

class TestSelectModels:
    HEAVY = ModelInfo(name="qwen3.5:122b-a10b", parameter_count_b=122.0, family="qwen35moe")
    FAST_CODER = ModelInfo(name="qwen3-coder-next:latest", parameter_count_b=14.0, family="qwen3next")
    CODELLAMA = ModelInfo(name="codellama:latest", parameter_count_b=34.0, family="codellama")

    def test_empty_models(self):
        assert select_models(available_models=[]) == ("", "")

    def test_single_model(self):
        result = select_models(available_models=[self.FAST_CODER])
        assert result == ("qwen3-coder-next:latest", "qwen3-coder-next:latest")

    def test_high_complexity_uses_heavy_planner(self):
        planner, executor = select_models(
            available_models=[self.HEAVY, self.FAST_CODER],
            prompt_complexity="high",
            has_template_compiler=False,
        )
        assert planner == "qwen3.5:122b-a10b"
        assert executor == "qwen3-coder-next:latest"

    def test_template_compiler_uses_fast(self):
        planner, executor = select_models(
            available_models=[self.HEAVY, self.FAST_CODER],
            has_template_compiler=True,
            prompt_complexity="low",
        )
        assert planner == "qwen3-coder-next:latest"
        assert executor == "qwen3-coder-next:latest"

    def test_template_compiler_high_complexity_still_uses_heavy(self):
        planner, executor = select_models(
            available_models=[self.HEAVY, self.FAST_CODER],
            has_template_compiler=True,
            prompt_complexity="high",
        )
        assert planner == "qwen3.5:122b-a10b"
        assert executor == "qwen3-coder-next:latest"

    def test_medium_complexity_uses_fast(self):
        planner, executor = select_models(
            available_models=[self.HEAVY, self.FAST_CODER],
            prompt_complexity="medium",
            has_template_compiler=False,
        )
        assert planner == "qwen3-coder-next:latest"
        assert executor == "qwen3-coder-next:latest"

    def test_three_models_picks_correct(self):
        planner, executor = select_models(
            available_models=[self.HEAVY, self.FAST_CODER, self.CODELLAMA],
            prompt_complexity="high",
            has_template_compiler=False,
        )
        assert planner == "qwen3.5:122b-a10b"  # heaviest
        # executor should be the best coder: among coders, codellama (34B) > qwen3-coder-next (14B)
        assert executor == "codellama:latest"

    def test_no_coder_selects_smallest_as_fast(self):
        models = [
            ModelInfo(name="big", parameter_count_b=70.0, family="llama"),
            ModelInfo(name="small", parameter_count_b=7.0, family="llama"),
        ]
        planner, executor = select_models(
            available_models=models,
            prompt_complexity="high",
            has_template_compiler=False,
        )
        assert planner == "big"
        assert executor == "small"

    def test_auto_complexity_from_analysis_type(self):
        # phylogenetics has a template compiler → should be low → both fast
        planner, executor = select_models(
            available_models=[self.HEAVY, self.FAST_CODER],
            analysis_type="phylogenetics",
        )
        assert planner == "qwen3-coder-next:latest"
        assert executor == "qwen3-coder-next:latest"

    def test_auto_complexity_unknown_type_uses_heavy(self):
        planner, executor = select_models(
            available_models=[self.HEAVY, self.FAST_CODER],
            analysis_type="unknown_brand_new_type",
        )
        assert planner == "qwen3.5:122b-a10b"
        assert executor == "qwen3-coder-next:latest"


# ---------------------------------------------------------------------------
# select_default_models
# ---------------------------------------------------------------------------

class TestSelectDefaultModels:
    def test_empty(self):
        assert select_default_models([]) == ("", "")

    def test_single_model(self):
        m = ModelInfo(name="only-one", parameter_count_b=14.0)
        assert select_default_models([m]) == ("only-one", "only-one")

    def test_two_models(self):
        heavy = ModelInfo(name="heavy", parameter_count_b=122.0, family="qwen35moe")
        fast = ModelInfo(name="fast", parameter_count_b=14.0, family="qwen3next")
        planner, executor = select_default_models([heavy, fast])
        assert planner == "fast"
        assert executor == "fast"

    def test_selects_coder_for_fast(self):
        big = ModelInfo(name="big", parameter_count_b=70.0, family="llama")
        coder = ModelInfo(name="deepseek-coder:latest", parameter_count_b=14.0, family="deepseek")
        small = ModelInfo(name="small", parameter_count_b=7.0, family="llama")
        planner, executor = select_default_models([big, coder, small])
        assert planner == "deepseek-coder:latest"
        assert executor == "deepseek-coder:latest"
