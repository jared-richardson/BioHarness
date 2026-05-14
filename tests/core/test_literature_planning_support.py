"""Tests for planner-time literature assistance support."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from bio_harness.core.literature_planning_support import (
    generate_literature_planning_support,
)
from scripts.run_agent_e2e_plan_context import AgentE2EPlanContextMixin


class _FakeLibrarian:
    def pubmed_search(self, query: str, max_results: int = 10):  # noqa: ARG002
        return [
            {
                "pmid": "1",
                "title": "Recommended minimap2 presets for direct RNA sequencing",
                "abstract": "Published methods recommend splice-aware presets for direct RNA workflows.",
                "year": "2024",
            }
        ]

    def citation_search(self, query: str, max_results: int = 10):  # noqa: ARG002
        return []

    def web_search(self, query: str, max_results: int = 10, allowed_domains: list[str] | None = None):  # noqa: ARG002
        return [
            {
                "title": "minimap2 documentation",
                "body": "Recommended workflow and preset guidance.",
                "href": "https://academic.oup.com/bioinformatics/article/34/18/3094/4994778",
            }
        ]


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.biollm = SimpleNamespace()

    def configure_planner_trace(self, *_args, **_kwargs) -> None:
        return

    def build_analysis_spec(self, *_args, **_kwargs) -> dict[str, object]:
        return {
            "analysis_type": "long_read_rna",
            "chosen_method": "minimap2",
            "preferred_tools": ["minimap2"],
        }

    def _get_librarian(self) -> _FakeLibrarian:
        return _FakeLibrarian()


class _DummyPlanContextHarness(AgentE2EPlanContextMixin):
    def __init__(self, tmp_path: Path, prompt: str) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.cfg = SimpleNamespace(
            prompt=prompt,
            selected_dir=tmp_path / "selected",
            data_root=tmp_path / "data",
        )
        self.cfg.selected_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.data_root.mkdir(parents=True, exist_ok=True)
        self.run = {
            "run_uid": "run123",
            "run_files": {
                "run_dir": str(run_dir),
                "planner": str(run_dir / "planner"),
                "literature_planning_support_json": str(run_dir / "literature_planning_support.json"),
                "literature_planning_support_md": str(run_dir / "literature_planning_support.md"),
            },
        }
        self.orchestrator = _FakeOrchestrator()
        self._events: list[dict[str, object]] = []
        self.path_graph = SimpleNamespace()

    def _benchmark_policy(self) -> str:
        return "scientific_harness"

    def _append_event(self, *, step_id, agent, event_type, severity, payload) -> None:  # noqa: ANN001
        self._events.append(
            {
                "step_id": step_id,
                "agent": agent,
                "event_type": event_type,
                "severity": severity,
                "payload": payload,
            }
        )


def test_generate_literature_planning_support_writes_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    payload = generate_literature_planning_support(
        user_query="What minimap2 preset is recommended in published methods?",
        analysis_spec={"analysis_type": "long_read_rna", "preferred_tools": ["minimap2"]},
        benchmark_policy="scientific_harness",
        run_dir=run_dir,
        librarian=_FakeLibrarian(),
    )

    assert payload["status"] == "applied"
    assert payload["visible_to_planner"] is True
    assert payload["query_class"] == "parameter_recommendation"
    assert Path(payload["json_path"]).exists()
    assert Path(payload["markdown_path"]).exists()


def test_prepare_analysis_spec_attaches_literature_support(tmp_path: Path) -> None:
    harness = _DummyPlanContextHarness(
        tmp_path,
        "What minimap2 preset is recommended in published methods?",
    )

    harness._prepare_analysis_spec(contract={})

    support = harness.run["analysis_spec"]["literature_planning_support"]
    assert support["status"] == "applied"
    assert support["visible_to_planner"] is True
    assert Path(support["json_path"]).exists()
    assert any(item["event_type"] == "LITERATURE_PLANNING_SUPPORT_EVALUATED" for item in harness._events)

    json_payload = json.loads(Path(support["json_path"]).read_text(encoding="utf-8"))
    assert json_payload["summary"]["query_class"] == "parameter_recommendation"
