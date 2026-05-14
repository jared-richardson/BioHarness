from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from scripts.run_agent_e2e_research_support import (
    handle_explicit_research_prompt,
    is_explicit_research_prompt,
)


class _FakeLLM:
    model_name = "fake-model"

    def summarize_text(self, text: str, instruction: str) -> str:  # noqa: ARG002
        return "Use DESeq2.\n- deseq2 alpha = 0.05"


class _FakeLibrarian:
    def pubmed_search(self, query: str, max_results: int = 10):  # noqa: ARG002
        return [
            {
                "pmid": "1",
                "title": "DESeq2 best practices",
                "abstract": "Differential expression guidance.",
                "year": "2024",
            }
        ]

    def citation_search(self, query: str, max_results: int = 10):  # noqa: ARG002
        return []

    def web_search(self, query: str, max_results: int = 10, allowed_domains: list[str] | None = None):  # noqa: ARG002
        return []


class _DummyHarness:
    def __init__(self, tmp_path: Path, prompt: str) -> None:
        self.cfg = SimpleNamespace(
            prompt=prompt,
            selected_dir=tmp_path / "selected",
            data_root=tmp_path / "data",
            path_graph_user_key="default",
            path_graph_scope="global",
        )
        self.cfg.selected_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.data_root.mkdir(parents=True, exist_ok=True)
        self.path_graph = SimpleNamespace(db_path=tmp_path / "path_graph.sqlite")
        self.orchestrator = SimpleNamespace(
            _get_librarian=lambda: _FakeLibrarian(),
            biollm=_FakeLLM(),
        )
        self.run: dict[str, object] = {}
        self._events: list[dict[str, object]] = []

    def _init_run(self) -> None:
        run_dir = self.cfg.selected_dir / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.run = {
            "run_uid": "research-run",
            "status": "initialized",
            "error": "",
            "user_request": self.cfg.prompt,
            "run_files": {
                "run_dir": str(run_dir),
                "state": str(run_dir / "state.json"),
                "events": str(run_dir / "events.jsonl"),
                "stdout": str(run_dir / "stdout.log"),
                "stderr": str(run_dir / "stderr.log"),
                "exec": str(run_dir / "execution.log"),
                "exit": str(run_dir / "exit.json"),
                "assistance_manifest": str(run_dir / "assistance_manifest.json"),
                "summary": str(run_dir / "summary.json"),
            },
        }

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

    def _persist_state(self) -> None:
        Path(self.run["run_files"]["state"]).write_text(json.dumps(self.run, indent=2), encoding="utf-8")

    def _write_exit(self) -> None:
        Path(self.run["run_files"]["exit"]).write_text(json.dumps({"status": self.run["status"]}), encoding="utf-8")

    def _assistance_manifest_payload(self) -> dict[str, object]:
        return {"generic_template_fallback_used": False}


def test_is_explicit_research_prompt_detects_prefix() -> None:
    assert is_explicit_research_prompt("Research: what is DESeq2?")
    assert not is_explicit_research_prompt("Plan an RNA-seq workflow")


def test_handle_explicit_research_prompt_writes_report_artifacts(tmp_path: Path) -> None:
    harness = _DummyHarness(tmp_path, "Research: What is best practice for DESeq2 in RNA-seq?")

    payload = handle_explicit_research_prompt(harness, benchmark_policy="scientific_harness")

    assert payload["status"] == "completed"
    assert payload["research_report"]["question"].startswith("What is best practice")
    assert payload["research_report"]["evidence_sufficiency"] == "sufficient"
    assert payload["research_report"]["primary_literature_count"] >= 1
    assert payload["research_report"]["backend_health_summary"][0]["backend"] == "pubmed"
    assert payload["research_report"]["backend_statuses"][0]["backend"] == "pubmed"
    assert (harness.cfg.selected_dir / "final" / "research_report.json").exists()
    assert (harness.cfg.selected_dir / "final" / "research_report.md").exists()
    assert harness._events[0]["event_type"] == "RESEARCH_STARTED"
    assert harness._events[1]["event_type"] == "RESEARCH_COMPLETED"
