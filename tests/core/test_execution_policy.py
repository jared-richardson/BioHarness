from __future__ import annotations

from bio_harness.core.execution_policy import inspect_execution_command


def test_execution_policy_audits_install_and_download_activity():
    report = inspect_execution_command(
        "curl -L https://github.com/example/repo && pip install package",
        mode="audit",
    )

    assert report["blocking"] == []
    assert "execution_policy_audit:runtime_download:github.com" in report["audits"]
    assert "execution_policy_audit:runtime_install:pip_install" in report["audits"]


def test_execution_policy_blocks_untrusted_download_in_trusted_only_mode():
    report = inspect_execution_command(
        "wget https://example.com/file.txt",
        mode="trusted_only",
    )

    assert report["blocking"] == ["execution_policy_block:runtime_download_untrusted_host:example.com"]


def test_execution_policy_allows_trusted_download_in_trusted_only_mode():
    report = inspect_execution_command(
        "curl -L https://github.com/example/repo/releases/download/v1/tool.tar.gz",
        mode="trusted_only",
    )

    assert report["blocking"] == []
