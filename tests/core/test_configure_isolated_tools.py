from __future__ import annotations

import json

from scripts import configure_isolated_tools


def test_parse_keyed_path_parses_tool_pairs():
    payload = configure_isolated_tools._parse_keyed_path(["prokka=/tmp/prokka", "STAR-Fusion=/tmp/starfusion"])

    assert payload == {
        "prokka": "/tmp/prokka",
        "STAR-Fusion": "/tmp/starfusion",
    }


def test_configure_isolated_tools_main_dry_run(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "tool_launchers.json"
    recipe_path = tmp_path / "recipes.json"
    recipe_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tools": {
                    "prokka": {
                        "mode": "external_binary",
                        "launcher_name": "prokka",
                    }
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        configure_isolated_tools,
        "_parse_args",
        lambda: type(
            "Args",
            (),
            {
                "config_path": config_path,
                "recipe_path": recipe_path,
                "env_root": tmp_path / ".tool-envs",
                "tools": ["prokka"],
                "binary_paths": ["prokka=/tmp/prokka"],
                "install": False,
                "dry_run": True,
                "cnvkit_venv": None,
                "install_cnvkit": False,
                "prokka_bin": None,
                "star_fusion_bin": None,
            },
        )(),
    )

    code = configure_isolated_tools.main()
    captured = json.loads(capsys.readouterr().out)

    assert code == 0
    assert captured["success"] is True
    assert captured["reports"][0]["config"]["tools"]["prokka"]["argv"] == ["/private/tmp/prokka"]
