from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomllib

from scripts import stage_public_release_tree as stage_module
from scripts.stage_public_release_tree import stage_public_release_tree


def test_pyproject_dependencies_match_core_requirements() -> None:
    """Keep wheel metadata aligned with the source-checkout requirements."""

    repo_root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text())
    package_dependencies = set(pyproject["project"].get("dependencies", []))
    core_requirements = {
        line.strip()
        for line in (repo_root / "requirements" / "venv-core.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert package_dependencies == core_requirements


def test_pyproject_includes_runtime_package_data() -> None:
    """Keep wheel installs from dropping runtime catalogs and guidance files."""

    repo_root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text())
    package_data = set(pyproject["tool"]["setuptools"]["package-data"]["bio_harness"])

    assert {
        "capabilities/*.json",
        "harness/*.json",
        "pipeline_scripts/*.md",
        "skills/definitions/*.json",
        "skills/definitions/*.md",
        "skills/uncommon/*.json",
    } <= package_data


def test_stage_public_release_tree_copies_allowlist_and_excludes_generated(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    _write(repo_root / "pyproject.toml", "[project]\nname = 'demo'\n")
    _write(repo_root / "README.md", "# Demo\n")
    _write(repo_root / "bio_harness" / "__init__.py", "")
    _write(
        repo_root / "bio_harness" / "core" / "private_path_fixture.py",
        f'ROOT = "{repo_root.as_posix()}/workspace/private"\n',
    )
    _write(repo_root / "bio_harness" / "__pycache__" / "bad.pyc", "compiled")
    (repo_root / "bio_harness" / "binary.bin").write_bytes(b"\x00\x01\x02")
    _write(repo_root / "scripts" / "bootstrap_bioharness.py", "def main():\n    return 0\n")
    _write(
        repo_root / "scripts" / "prepare_fast_signal_mini_benchmarks.py",
        "def main():\n    return 0\n",
    )
    _write(repo_root / "scripts" / "run_qwen_skill_smoke_matrix.py", "def main():\n    return 0\n")
    _write(repo_root / "scripts" / "scan_public_release_tree.py", "def main():\n    return 0\n")
    _write(repo_root / "scripts" / "revise_manuscript_phase3.py", "internal = True\n")
    _write(repo_root / "scripts" / "__pycache__" / "bad.pyc", "compiled")
    _write(repo_root / "ui_v2_api.py", "def main():\n    return 0\n")
    _write(repo_root / "benchmark_data" / "result_review_decision" / "manifest.json", "{}\n")
    _write(repo_root / "benchmark_data" / "raw.fastq", "@r\nA\n+\n!\n")
    _write(repo_root / "workspace" / "benchmark_data" / "ablation_manifest_24.json", "{}\n")
    _write(
        repo_root / "workspace" / "benchmark_data" / "fast_signal_mini" / "manifest.json",
        '{"data_root": "/example/local/path"}\n',
    )
    _write(repo_root / "workspace" / "ignored.txt", "local")
    _write(
        repo_root / "ui_v2" / "src" / "App.tsx",
        "export default function App() { return null }\n",
    )
    _write(repo_root / "ui_v2" / "README.md", "# Vite template\n")
    _write(repo_root / "ui_v2" / "node_modules" / "pkg" / "index.js", "generated")

    output_dir = repo_root / "release" / "public" / "bio-harness"
    result = stage_public_release_tree(
        repo_root=repo_root,
        output_dir=output_dir,
        clean=True,
    )

    assert "README.md" in result.copied_files
    assert ".gitignore" in result.copied_files
    assert "bio_harness/__init__.py" in result.copied_files
    assert "scripts/bootstrap_bioharness.py" in result.copied_files
    assert "scripts/prepare_fast_signal_mini_benchmarks.py" in result.copied_files
    assert "scripts/run_qwen_skill_smoke_matrix.py" in result.copied_files
    assert "scripts/scan_public_release_tree.py" in result.copied_files
    assert "scripts/revise_manuscript_phase3.py" not in result.copied_files
    assert ".github/workflows/ci.yml" in result.copied_files
    assert ".github/workflows/package-smoke.yml" in result.copied_files
    assert "ui_v2_api.py" in result.copied_files
    assert "benchmark_data/result_review_decision/manifest.json" in result.copied_files
    assert "benchmark_data/manifests/ablation_manifest_24.json" in result.copied_files
    assert "benchmark_data/fast_signal_mini/manifest.json" not in result.copied_files
    assert "benchmark_data/fast_signal_mini/README.md" in result.copied_files
    assert "apps/web/src/App.tsx" in result.copied_files
    assert "apps/web/README.md" in result.copied_files
    assert result.blocked_files == []

    assert (output_dir / "README.md").is_file()
    staged_readme = (output_dir / "README.md").read_text()
    assert "apps/streamlit/app.py" in staged_readme
    assert "streamlit run app.py" not in staged_readme
    assert "prepare_fast_signal_mini_benchmarks.py" in staged_readme
    assert "recommended public Qwen path" in staged_readme
    assert "qwen3-coder-next:latest" in staged_readme
    assert "example/local/path" not in staged_readme
    staged_gitignore = (output_dir / ".gitignore").read_text()
    assert "benchmark_data/*" not in staged_gitignore
    assert "workspace/" in staged_gitignore
    assert (output_dir / "bio_harness" / "__init__.py").is_file()
    assert (output_dir / "bio_harness" / "binary.bin").read_bytes() == b"\x00\x01\x02"
    sanitized_fixture = output_dir / "bio_harness" / "core" / "private_path_fixture.py"
    assert repo_root.as_posix() not in sanitized_fixture.read_text()
    assert "<BIO_HARNESS_ROOT>/workspace/private" in sanitized_fixture.read_text()
    assert (output_dir / "scripts" / "README.md").is_file()
    ci_workflow = (output_dir / ".github" / "workflows" / "ci.yml").read_text()
    assert "scan_public_release_tree.py --root ." in ci_workflow
    assert "test_scan_public_release_tree.py" in ci_workflow
    assert "working-directory: apps/web" in ci_workflow
    assert "npm audit --audit-level=moderate" in ci_workflow
    package_smoke = (
        output_dir / ".github" / "workflows" / "package-smoke.yml"
    ).read_text()
    assert "bio-harness-run" in package_smoke
    assert "replay_fast_signal_fixtures.py tests/fixtures/fast_signal" in package_smoke
    assert (output_dir / "ui_v2_api.py").is_file()
    assert not (output_dir / "scripts" / "revise_manuscript_phase3.py").exists()
    assert not (output_dir / "bio_harness" / "__pycache__").exists()
    assert not (output_dir / "ui_v2").exists()
    assert not (output_dir / "apps" / "web" / "node_modules").exists()
    assert (output_dir / "apps" / "web" / "README.md").read_text().startswith(
        "# Bio-Harness Web UI"
    )
    web_readme = (output_dir / "apps" / "web" / "README.md").read_text()
    assert "VITE_API_BASE" in web_readme
    assert "BIO_HARNESS_UI_HOST=0.0.0.0" in web_readme
    ui_doc = (output_dir / "docs" / "ui.md").read_text()
    assert "React/Vite" in ui_doc
    assert "VITE_API_BASE" in ui_doc
    assert not (output_dir / "workspace").exists()
    assert not (output_dir / "benchmark_data" / "raw.fastq").exists()
    assert not (output_dir / "benchmark_data" / "fast_signal_mini" / "manifest.json").exists()
    assert (output_dir / "benchmark_data" / "README.md").is_file()
    benchmark_doc = (output_dir / "docs" / "benchmark_evidence.md").read_text()
    assert "qwen_coder_single_model_full_20260501_r1" in benchmark_doc
    assert "168/168" in benchmark_doc
    assert "not single-model `qwen_true_no_templates`" in benchmark_doc

    manifest = json.loads((output_dir / "release_manifest.json").read_text())
    assert manifest["blocked_files"] == []
    assert manifest["copied_count"] == len(result.copied_files)
    assert not Path(manifest["output_dir"]).is_absolute()


def test_stage_public_release_tree_dry_run_does_not_write(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _write(repo_root / "pyproject.toml", "[project]\nname = 'demo'\n")
    _write(repo_root / "bio_harness" / "__init__.py", "")

    output_dir = repo_root / "release" / "public" / "bio-harness"
    result = stage_public_release_tree(
        repo_root=repo_root,
        output_dir=output_dir,
        dry_run=True,
    )

    assert result.dry_run is True
    assert "bio_harness/__init__.py" in result.copied_files
    assert not output_dir.exists()


def test_stage_public_release_tree_clean_refuses_unsafe_output(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _write(repo_root / "pyproject.toml", "[project]\nname = 'demo'\n")
    _write(repo_root / "bio_harness" / "__init__.py", "")

    with pytest.raises(ValueError, match="Refusing to clean output outside"):
        stage_public_release_tree(
            repo_root=repo_root,
            output_dir=tmp_path / "outside",
            clean=True,
        )


def test_stage_public_release_tree_validation_and_clean_guards(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    release_root = repo_root / "release" / "public"
    output_dir = release_root / "bio-harness"
    output_dir.mkdir(parents=True)
    _write(output_dir / "old.txt", "old\n")

    with pytest.raises(ValueError, match="bio_harness"):
        stage_module._validate_repo_root(repo_root)

    (repo_root / "bio_harness").mkdir(parents=True)
    with pytest.raises(ValueError, match=r"pyproject\.toml"):
        stage_module._validate_repo_root(repo_root)

    _write(repo_root / "pyproject.toml", "[project]\nname = 'demo'\n")
    stage_module._validate_repo_root(repo_root)

    with pytest.raises(ValueError, match="release root directly"):
        stage_module._clean_output_dir(repo_root, release_root)

    stage_module._clean_output_dir(repo_root, output_dir)
    assert not output_dir.exists()


def test_stage_public_release_tree_helpers_handle_binary_and_blocked_files(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "binary.dat"
    binary.write_bytes(b"\xff\xfe")
    text = tmp_path / "text.md"
    _write(text, f"{tmp_path.as_posix()}/workspace\n")

    assert stage_module._read_public_text(binary, tmp_path) is None
    assert "<BIO_HARNESS_ROOT>/workspace" in stage_module._read_public_text(text, tmp_path)
    assert stage_module._find_blocked_files(tmp_path / "missing") == []

    blocked_root = tmp_path / "blocked"
    _write(blocked_root / "__pycache__" / "bad.pyc", "compiled")

    assert stage_module._find_blocked_files(blocked_root) == ["__pycache__/bad.pyc"]


def test_stage_public_release_tree_main_returns_blocked_status(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    _write(repo_root / "pyproject.toml", "[project]\nname = 'demo'\n")
    _write(repo_root / "bio_harness" / "__init__.py", "")
    output_dir = repo_root / "release" / "public" / "bio-harness"

    monkeypatch.setattr(
        "sys.argv",
        [
            "stage_public_release_tree.py",
            "--repo-root",
            str(repo_root),
            "--output-dir",
            str(output_dir),
            "--dry-run",
        ],
    )

    assert stage_module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
