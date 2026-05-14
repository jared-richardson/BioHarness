from __future__ import annotations

import pytest

from bio_harness.core.bash_single_operation_policy import check_single_operation
from bio_harness.skills.library.tabix_index_run import tabix_index_run


def test_tabix_index_run_renders_single_invocation_with_force() -> None:
    command = tabix_index_run(
        input_file="/tmp/input.vcf.gz",
        preset="vcf",
        force=True,
    )

    assert "tabix" in command
    assert " -f " in f" {command} "
    assert " -p vcf " in f" {command} "
    assert "/tmp/input.vcf.gz" in command
    check = check_single_operation(command)
    assert check.passed is True


def test_tabix_index_run_omits_force_when_disabled() -> None:
    command = tabix_index_run(
        input_file="/tmp/input.gff.gz",
        preset="gff",
        force=False,
    )

    assert " -f " not in f" {command} "
    assert " -p gff " in f" {command} "


def test_tabix_index_run_requires_input_file() -> None:
    with pytest.raises(ValueError, match="input_file"):
        tabix_index_run(preset="vcf")
