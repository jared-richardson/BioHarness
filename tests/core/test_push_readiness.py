from __future__ import annotations

from pathlib import Path

from scripts.check_push_readiness import (
    PUSH_TEST_TARGETS,
    build_pytest_command,
    should_skip_pre_push,
)


def test_should_skip_pre_push_accepts_explicit_truthy_values() -> None:
    assert should_skip_pre_push({"BIO_HARNESS_SKIP_PRE_PUSH": "1"}) is True
    assert should_skip_pre_push({"BIO_HARNESS_SKIP_PRE_PUSH": "true"}) is True
    assert should_skip_pre_push({"BIO_HARNESS_SKIP_PRE_PUSH": "yes"}) is True


def test_should_skip_pre_push_defaults_to_false() -> None:
    assert should_skip_pre_push({}) is False
    assert should_skip_pre_push({"BIO_HARNESS_SKIP_PRE_PUSH": "0"}) is False


def test_build_pytest_command_contains_curated_targets() -> None:
    command = build_pytest_command()

    assert command[0] == "pytest"
    assert command[-1] == "-q"
    assert tuple(command[1:-1]) == PUSH_TEST_TARGETS


def test_push_test_targets_exist_in_repo() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    missing = [target for target in PUSH_TEST_TARGETS if not (repo_root / target).exists()]

    assert missing == []
