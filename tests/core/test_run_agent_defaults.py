from __future__ import annotations

import sys

from bio_harness.core.benchmark_policy import SCIENTIFIC_HARNESS_POLICY
from scripts.run_agent_e2e import _parse_args


def test_non_strict_runs_default_to_self_healing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_agent_e2e.py",
            "--prompt",
            "test",
            "--selected-dir",
            str(tmp_path / "workspace"),
            "--data-root",
            str(tmp_path / "workspace" / "inputs"),
            "--benchmark-policy",
            SCIENTIFIC_HARNESS_POLICY,
        ],
    )

    cfg = _parse_args()

    assert cfg.auto_install_missing_tools is True
    assert cfg.auto_setup_isolated_tools is True


def test_strict_runs_keep_self_healing_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_agent_e2e.py",
            "--prompt",
            "test",
            "--selected-dir",
            str(tmp_path / "workspace"),
            "--data-root",
            str(tmp_path / "workspace" / "inputs"),
            "--benchmark-policy",
            "bioagentbench_planning_strict",
        ],
    )

    cfg = _parse_args()

    assert cfg.auto_install_missing_tools is False
    assert cfg.auto_setup_isolated_tools is False


def test_explicit_opt_out_overrides_non_strict_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_agent_e2e.py",
            "--prompt",
            "test",
            "--selected-dir",
            str(tmp_path / "workspace"),
            "--data-root",
            str(tmp_path / "workspace" / "inputs"),
            "--no-auto-install-missing-tools",
            "--no-auto-setup-isolated-tools",
        ],
    )

    cfg = _parse_args()

    assert cfg.auto_install_missing_tools is False
    assert cfg.auto_setup_isolated_tools is False


def test_parse_args_narrows_data_root_from_explicit_prompt_paths(monkeypatch, tmp_path):
    selected_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "datasets" / "airway"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    counts = dataset_dir / "gene_counts.txt"
    metadata = dataset_dir / "sample_metadata.tsv"
    counts.write_text("gene\ts1\nA\t10\n", encoding="utf-8")
    metadata.write_text("sample\tcondition\ns1\tcontrol\n", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_agent_e2e.py",
            "--prompt",
            f"Use deseq2_run on {counts} and {metadata}.",
            "--selected-dir",
            str(selected_dir),
            "--data-root",
            str(selected_dir),
        ],
    )

    cfg = _parse_args()

    assert cfg.data_root == dataset_dir.resolve()


def test_parse_args_ignores_deseq_formula_tilde_when_narrowing_data_root(monkeypatch, tmp_path):
    selected_dir = tmp_path / "workspace"
    dataset_dir = tmp_path / "datasets" / "airway"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    counts = dataset_dir / "airway_counts.tsv"
    metadata = dataset_dir / "airway_metadata.tsv"
    counts.write_text("gene\ts1\nA\t10\n", encoding="utf-8")
    metadata.write_text("sample\tdex\ns1\ttrt\n", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_agent_e2e.py",
            "--prompt",
            (
                f"Run deseq2_run on {counts} with sample metadata {metadata}. "
                "Use design formula ~ dex and contrast dex_trt_vs_untrt."
            ),
            "--selected-dir",
            str(selected_dir),
            "--data-root",
            str(tmp_path),
        ],
    )

    cfg = _parse_args()

    assert cfg.data_root == dataset_dir.resolve()
