from __future__ import annotations

from pathlib import Path

import pytest

from bio_harness.agents.orchestrator import Orchestrator


class _RecordingBioLLM:
    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self.model_name = "dummy"
        self.host = None

    def configure_planner_trace(self, planner_trace_dir=None, planner_trace_context=None) -> None:
        return None

    def generate_text(self, system_prompt: str, user_prompt: str, num_ctx: int = 8192) -> str:
        self.calls.append((system_prompt, user_prompt, num_ctx))
        return "1) What I understood\n2) Autonomous next actions I will take now\n3) Assumptions/defaults used\n4) "

    def summarize_text(self, text: str, max_words: int = 120) -> str:
        return text[: max_words * 8]


@pytest.fixture
def orchestrator_for_user_help(monkeypatch, tmp_path: Path) -> Orchestrator:
    skills_dir = tmp_path / "skills" / "definitions"
    skill_library_dir = tmp_path / "skills" / "library"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_library_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "star_align.md").write_text(
        "---\n"
        "name: star_align\n"
        "description: STAR alignment wrapper.\n"
        "risk_level: low\n"
        "tools_required:\n"
        "  - star\n"
        "parameters:\n"
        "  input_fastq:\n"
        "    type: path\n"
        "    description: Input FASTQ.\n"
        "    required: true\n"
        "analysis_categories:\n"
        "  - alignment\n"
        "capabilities:\n"
        "  - alignment\n"
        "---\n"
        "Use STAR for RNA-seq alignment.\n",
        encoding="utf-8",
    )
    (skills_dir / "salmon_quant.md").write_text(
        "---\n"
        "name: salmon_quant\n"
        "description: Salmon quantification wrapper.\n"
        "risk_level: low\n"
        "tools_required:\n"
        "  - salmon\n"
        "parameters:\n"
        "  reads_1:\n"
        "    type: path\n"
        "    description: Input FASTQ.\n"
        "    required: true\n"
        "analysis_categories:\n"
        "  - quantification\n"
        "capabilities:\n"
        "  - quantification\n"
        "---\n"
        "Use Salmon for transcript quantification.\n",
        encoding="utf-8",
    )
    (skill_library_dir / "star_align.py").write_text(
        "def star_align(**kwargs):\n"
        "    return 'star'\n",
        encoding="utf-8",
    )
    (skill_library_dir / "salmon_quant.py").write_text(
        "def salmon_quant(**kwargs):\n"
        "    return 'salmon'\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("bio_harness.agents.orchestrator.BioLLM", _RecordingBioLLM)
    orchestrator = Orchestrator(skills_dir=skills_dir, skill_library_dir=skill_library_dir)
    monkeypatch.setattr(orchestrator, "_subagent_dataset_scout", lambda *args, **kwargs: {})
    monkeypatch.setattr(orchestrator, "_subagent_requirements", lambda *args, **kwargs: [])
    monkeypatch.setattr(orchestrator, "_infer_autonomy_mode", lambda *args, **kwargs: False)
    monkeypatch.setattr(orchestrator, "_detect_context_completeness", lambda *args, **kwargs: {"complete": False})
    return orchestrator


def test_interactive_turn_includes_harness_help_context_for_help_queries(
    orchestrator_for_user_help: Orchestrator,
) -> None:
    orchestrator_for_user_help.interactive_turn("session-1", "What capabilities does Bio-Harness have by category?")

    system_prompt, user_prompt, _ = orchestrator_for_user_help.biollm.calls[-1]
    assert "question about the local Bio-Harness repository" in system_prompt
    assert "Do not substitute information from any unrelated project" in system_prompt
    assert "Harness help context:" in user_prompt
    assert "## Bio-Harness User Help" in user_prompt
    assert "python3 scripts/stage_inputs.py --help" in user_prompt
    assert "python3 scripts/setup_llm_backend.py --help" in user_prompt
    assert "1) Direct answer" in user_prompt


def test_interactive_turn_includes_model_setup_help_for_runtime_questions(
    orchestrator_for_user_help: Orchestrator,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "bio_harness.agents.orchestrator.build_llm_setup_report",
        lambda **_kwargs: {"ready": False},
    )
    monkeypatch.setattr(
        "bio_harness.agents.orchestrator.render_llm_setup_text",
        lambda _report: "## LLM Backend Setup\n- `python3 scripts/setup_llm_backend.py --help`\n",
    )

    turn = orchestrator_for_user_help.interactive_turn("session-setup", "How do I set up Ollama and pull the right model?")

    assert turn["assistant_message"].startswith("## LLM Backend Setup")
    assert "python3 scripts/setup_llm_backend.py --help" in turn["assistant_message"]
    assert orchestrator_for_user_help.biollm.calls == []


def test_interactive_turn_skips_harness_help_context_for_normal_science_queries(
    orchestrator_for_user_help: Orchestrator,
) -> None:
    orchestrator_for_user_help.interactive_turn("session-2", "Run STAR alignment on these FASTQ files.")

    system_prompt, user_prompt, _ = orchestrator_for_user_help.biollm.calls[-1]
    assert "answer from the deterministic harness help context" not in system_prompt
    assert "Harness help context:" not in user_prompt


def test_interactive_turn_focuses_harness_help_context_for_specific_skill_queries(
    orchestrator_for_user_help: Orchestrator,
) -> None:
    orchestrator_for_user_help.interactive_turn(
        "session-3",
        "Which Bio-Harness skill should I use for STAR alignment?",
    )

    _, user_prompt, _ = orchestrator_for_user_help.biollm.calls[-1]
    assert "Harness help context:" in user_prompt
    assert "Focused help slice for this question: star_align" in user_prompt
    assert "salmon_quant" not in user_prompt
