from __future__ import annotations

from bio_harness.core.uncommon_skill_framework import (
    build_uncommon_wrapper_command,
    load_uncommon_skill_catalog,
    render_uncommon_wrapper_template,
    validate_uncommon_skill_catalog,
)


def test_uncommon_catalog_validates_cleanly():
    catalog = load_uncommon_skill_catalog()
    errors = validate_uncommon_skill_catalog(catalog)
    assert errors == []


def test_uncommon_wrapper_command_is_deterministic_for_same_inputs():
    kwargs = {
        "reads_1": "/data/S1_R1.fastq.gz",
        "reads_2": "/data/S1_R2.fastq.gz",
        "output_dir": "/tmp/uncommon/immune",
        "output_report": "/tmp/uncommon/immune/clones.tsv",
        "threads": 2,
    }
    cmd_a = build_uncommon_wrapper_command("immune_repertoire_mixcr_style", kwargs)
    cmd_b = build_uncommon_wrapper_command("immune_repertoire_mixcr_style", kwargs)
    assert cmd_a == cmd_b
    assert "set -euo pipefail" in cmd_a


def test_wrapper_template_renderer_emits_minimal_callable_module():
    src = render_uncommon_wrapper_template("methylation_bismark_style")
    assert "def methylation_bismark_style(**kwargs) -> str:" in src
    assert "build_uncommon_wrapper_command" in src
