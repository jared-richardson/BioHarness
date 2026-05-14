from __future__ import annotations

import json

from bio_harness.core import isolated_tool_recipes, tool_launchers


def teardown_function() -> None:
    isolated_tool_recipes.refresh_isolated_tool_recipes()
    tool_launchers.refresh_tool_launchers()


def test_isolated_tool_recipe_resolves_alias() -> None:
    recipe = isolated_tool_recipes.isolated_tool_recipe("star-fusion")

    assert recipe is not None
    assert recipe["launcher_name"] == "STAR-Fusion"
    assert recipe["mode"] == "docker_wrapper"


def test_setup_isolated_tool_docker_wrapper_writes_launcher_config(monkeypatch, tmp_path):
    config_path = tmp_path / "tool_launchers.json"
    pulls: list[list[str]] = []
    monkeypatch.setattr(
        isolated_tool_recipes,
        "_run_commands",
        lambda commands, *, dry_run=False: pulls.extend(commands) or [{"argv": commands[0], "returncode": 0}],
    )

    report = isolated_tool_recipes.setup_isolated_tool(
        "prokka",
        config_path=config_path,
        env_root=tmp_path / ".tool-envs",
        install=True,
        dry_run=False,
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert report["success"] is True
    assert pulls == [["docker", "pull", "quay.io/biocontainers/prokka:1.14.6--pl5321hdfd78af_4"]]
    assert payload["tools"]["prokka"]["argv"] == [str(tmp_path / ".tool-envs" / "prokka" / "bin" / "prokka")]


def test_setup_isolated_tool_docker_build_wrapper_writes_launcher_config(monkeypatch, tmp_path):
    config_path = tmp_path / "tool_launchers.json"
    builds: list[list[str]] = []
    monkeypatch.setattr(
        isolated_tool_recipes,
        "_run_commands",
        lambda commands, *, dry_run=False: builds.extend(commands) or [{"argv": commands[0], "returncode": 0}],
    )

    report = isolated_tool_recipes.setup_isolated_tool(
        "flye",
        config_path=config_path,
        env_root=tmp_path / ".tool-envs",
        install=True,
        dry_run=False,
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert report["success"] is True
    assert builds == [[
        "docker",
        "build",
        "-f",
        str((isolated_tool_recipes.PROJECT_ROOT / "docker" / "isolated-tools" / "flye.Dockerfile").resolve()),
        "-t",
        "bioharness/flye-ubuntu-amd64:2.9.6",
        "--platform",
        "linux/amd64",
        str(isolated_tool_recipes.PROJECT_ROOT.resolve()),
    ]]
    assert payload["tools"]["flye"]["argv"] == [str(tmp_path / ".tool-envs" / "flye" / "bin" / "flye")]


def test_setup_isolated_tool_docker_wrapper_without_platform_uses_safe_empty_array(monkeypatch, tmp_path):
    config_path = tmp_path / "tool_launchers.json"
    monkeypatch.setattr(
        isolated_tool_recipes,
        "_run_commands",
        lambda commands, *, dry_run=False: [{"argv": commands[0], "returncode": 0}],
    )

    report = isolated_tool_recipes.setup_isolated_tool(
        "vep",
        config_path=config_path,
        env_root=tmp_path / ".tool-envs",
        install=True,
        dry_run=False,
    )

    wrapper_path = tmp_path / ".tool-envs" / "vep" / "bin" / "vep"
    script_text = wrapper_path.read_text(encoding="utf-8")
    assert report["success"] is True
    assert 'declare -a PLATFORM_FLAG=()' in script_text
    assert 'docker_args=(run --rm -u "$(id -u):$(id -g)" -w "$cwd")' in script_text
    assert "docker_supports_platform_flag()" in script_text
    assert "if docker_supports_platform_flag; then" in script_text
    assert "os.path.realpath" in script_text
    assert 'if [ -n "$resolved" ] && [ "$resolved" != "$raw" ]; then' in script_text


def test_setup_isolated_tool_docker_build_wrapper_gates_platform_flag_on_daemon_support(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "tool_launchers.json"
    monkeypatch.setattr(
        isolated_tool_recipes,
        "_run_commands",
        lambda commands, *, dry_run=False: [{"argv": commands[0], "returncode": 0}],
    )

    report = isolated_tool_recipes.setup_isolated_tool(
        "flye",
        config_path=config_path,
        env_root=tmp_path / ".tool-envs",
        install=False,
        dry_run=False,
    )

    wrapper_path = tmp_path / ".tool-envs" / "flye" / "bin" / "flye"
    script_text = wrapper_path.read_text(encoding="utf-8")
    assert report["success"] is True
    assert "docker_supports_platform_flag()" in script_text
    assert "docker version --format '{{.Server.APIVersion}}'" in script_text
    assert "if docker_supports_platform_flag; then" in script_text


def test_setup_isolated_tool_binary_override_beats_default_recipe(tmp_path):
    config_path = tmp_path / "tool_launchers.json"

    report = isolated_tool_recipes.setup_isolated_tool(
        "prokka",
        config_path=config_path,
        binary_path="/tmp/prokka",
        dry_run=True,
    )

    assert report["success"] is True
    assert report["config"]["tools"]["prokka"]["argv"] == ["/private/tmp/prokka"]


def test_setup_isolated_tool_pip_venv_dry_run_uses_exact_env_path(tmp_path):
    config_path = tmp_path / "tool_launchers.json"
    env_path = tmp_path / ".tool-envs" / "cnvkit-special"

    report = isolated_tool_recipes.setup_isolated_tool(
        "cnvkit.py",
        config_path=config_path,
        env_path=env_path,
        install=True,
        dry_run=True,
    )

    assert report["success"] is True
    assert report["commands"][0]["argv"][-1] == str(env_path)
    assert report["config"]["tools"]["cnvkit.py"]["argv"] == [str(env_path / "bin" / "cnvkit.py")]


def test_setup_isolated_tools_for_missing_reports_unresolved_without_binary_for_external_tool(tmp_path):
    report = isolated_tool_recipes.setup_isolated_tools_for_missing(
        ["made-up-tool", "cnvkit.py"],
        config_path=tmp_path / "tool_launchers.json",
        env_root=tmp_path / ".tool-envs",
        install=False,
        dry_run=True,
    )

    assert report["success"] is False
    assert report["resolved_tools"] == ["cnvkit.py"]
    assert report["unresolved_tools"] == ["made-up-tool"]
