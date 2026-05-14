from __future__ import annotations

import json

from bio_harness.core import tool_launchers
from bio_harness.skills.library.cnv_cnvkit_style import cnv_cnvkit_style
from bio_harness.skills.library.prokka_annotate import prokka_annotate


def teardown_function() -> None:
    tool_launchers.refresh_tool_launchers()


def test_tool_launcher_command_and_guard_from_config(monkeypatch, tmp_path):
    config_path = tmp_path / "tool_launchers.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tools": {
                    "cnvkit.py": {
                        "argv": [str(tmp_path / "cnvkit-env" / "bin" / "cnvkit.py")],
                    }
                },
                "meta": {},
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BIO_HARNESS_TOOL_LAUNCHERS_PATH", str(config_path))
    tool_launchers.refresh_tool_launchers()

    expected = str(tmp_path / "cnvkit-env" / "bin" / "cnvkit.py")
    assert tool_launchers.tool_launcher_command("cnvkit.py") == expected
    assert tool_launchers.tool_launcher_guard_expr("cnvkit.py") == f"[ -x {expected} ]"


def test_uncommon_wrapper_uses_launcher_command(monkeypatch, tmp_path):
    config_path = tmp_path / "tool_launchers.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tools": {
                    "cnvkit.py": {"argv": [str(tmp_path / "cnvkit-env" / "bin" / "cnvkit.py")]},
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BIO_HARNESS_TOOL_LAUNCHERS_PATH", str(config_path))
    tool_launchers.refresh_tool_launchers()

    cmd = cnv_cnvkit_style(
        input_bam="/tmp/sample.bam",
        reference_fasta="/tmp/ref.fa",
        output_dir="/tmp/out",
        output_report="/tmp/out/cnv.tsv",
    )

    assert str(tmp_path / "cnvkit-env" / "bin" / "cnvkit.py") in cmd
    assert "command -v cnvkit.py" not in cmd
    assert "--segment-method none" in cmd
    assert "cnvkit_summary.py" in cmd


def test_prokka_wrapper_uses_launcher_command(monkeypatch, tmp_path):
    config_path = tmp_path / "tool_launchers.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tools": {
                    "prokka": {"argv": [str(tmp_path / "prokka-env" / "bin" / "prokka")]},
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BIO_HARNESS_TOOL_LAUNCHERS_PATH", str(config_path))
    tool_launchers.refresh_tool_launchers()

    cmd = prokka_annotate(
        output_dir="/tmp/prokka",
        sample_prefix="sample1",
        input_fasta="/tmp/genome.fa",
    )

    assert cmd.startswith(f"{tmp_path / 'prokka-env' / 'bin' / 'prokka'} --outdir /tmp/prokka")


def test_tool_launcher_uses_container_detects_docker_shim(monkeypatch, tmp_path) -> None:
    launcher_path = tmp_path / "prokka-env" / "bin" / "prokka"
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text(
        "#!/usr/bin/env bash\nexec docker run example/prokka \"$@\"\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "tool_launchers.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tools": {
                    "prokka": {"argv": [str(launcher_path)]},
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BIO_HARNESS_TOOL_LAUNCHERS_PATH", str(config_path))
    tool_launchers.refresh_tool_launchers()

    assert tool_launchers.tool_launcher_uses_container("prokka") is True


def test_tool_launcher_command_prefers_native_pixi_binary_over_container_shim(monkeypatch, tmp_path) -> None:
    launcher_path = tmp_path / "flye-env" / "bin" / "flye"
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text(
        "#!/usr/bin/env bash\nexec docker run example/flye \"$@\"\n",
        encoding="utf-8",
    )
    native_flye = tmp_path / "pixi-default" / "bin" / "flye"
    native_flye.parent.mkdir(parents=True, exist_ok=True)
    native_flye.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    native_flye.chmod(0o755)
    config_path = tmp_path / "tool_launchers.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tools": {
                    "flye": {"argv": [str(launcher_path)]},
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BIO_HARNESS_TOOL_LAUNCHERS_PATH", str(config_path))
    monkeypatch.setattr(
        "bio_harness.core.tool_launchers.which_with_pixi",
        lambda name: str(native_flye) if name == "flye" else None,
    )
    tool_launchers.refresh_tool_launchers()

    assert tool_launchers.tool_launcher_command("flye") == str(native_flye)
    assert tool_launchers.tool_launcher_guard_expr("flye") == f"[ -x {native_flye} ]"
