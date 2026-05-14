#!/usr/bin/env python3
"""Download a user-approved URL into the workspace with an audit-first policy."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.execution_policy import DEFAULT_EXECUTION_POLICY_MODE  # noqa: E402
from bio_harness.core.trusted_downloads import download_with_policy, write_download_receipt  # noqa: E402


DEFAULT_OUTPUT = PROJECT_ROOT / "workspace" / "downloads" / "downloaded_resource"
DEFAULT_RECEIPT = PROJECT_ROOT / "workspace" / "download_receipts" / "latest_trusted_download.json"


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for trusted downloads."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="URL to fetch.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Destination path under the workspace. Defaults to workspace/downloads/downloaded_resource.",
    )
    parser.add_argument(
        "--policy-mode",
        choices=["off", "audit", "trusted_only"],
        default=DEFAULT_EXECUTION_POLICY_MODE,
        help="Execution policy mode. Defaults to audit.",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="Additional trusted host to allow. May be repeated.",
    )
    parser.add_argument("--max-bytes", type=int, default=0, help="Optional byte cap. Zero disables the cap.")
    parser.add_argument("--sha256", default="", help="Optional SHA256 checksum to enforce.")
    parser.add_argument(
        "--receipt",
        default=str(DEFAULT_RECEIPT),
        help="JSON receipt path. Defaults to workspace/download_receipts/latest_trusted_download.json.",
    )
    return parser


def main() -> int:
    """Run the trusted-download CLI."""

    parser = build_parser()
    args = parser.parse_args()
    receipt = download_with_policy(
        args.url,
        destination=args.output,
        mode=args.policy_mode,
        allowed_hosts=args.allow_host,
        max_bytes=args.max_bytes or None,
        expected_sha256=args.sha256,
    )
    receipt_path = write_download_receipt(receipt, args.receipt)
    print(
        f"[trusted-download] status={receipt['status']} host={receipt['host']} "
        f"bytes={receipt['bytes_written']} receipt={receipt_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
