#!/usr/bin/env python3
"""Configure isolated tool recipes and launcher paths for Bio-Harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.isolated_tool_recipes import DEFAULT_RECIPE_PATH, setup_isolated_tool  # noqa: E402
from bio_harness.core.tool_launchers import DEFAULT_LAUNCHER_PATH  # noqa: E402


def _parse_keyed_path(values: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in values or []:
        text = str(raw).strip()
        if "=" not in text:
            raise ValueError(f"Expected TOOL=PATH, got: {text}")
        key, value = text.split("=", 1)
        tool_name = key.strip()
        path_text = value.strip()
        if not tool_name or not path_text:
            raise ValueError(f"Expected TOOL=PATH, got: {text}")
        out[tool_name] = path_text
    return out


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_LAUNCHER_PATH, help="Launcher config JSON path.")
    parser.add_argument("--recipe-path", type=Path, default=DEFAULT_RECIPE_PATH, help="Isolated tool recipe catalog JSON path.")
    parser.add_argument("--env-root", type=Path, default=PROJECT_ROOT / ".tool-envs", help="Root directory for isolated tool environments.")
    parser.add_argument("--tool", action="append", dest="tools", default=None, help="Tool name to set up via isolated recipe. Repeatable.")
    parser.add_argument(
        "--binary-path",
        action="append",
        dest="binary_paths",
        default=None,
        help="External binary registration in TOOL=PATH form. Repeatable.",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Execute supported install recipes such as dedicated Python virtual environments.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Render the setup report without writing launcher config or installing.")

    # Backward-compatible convenience flags.
    parser.add_argument("--cnvkit-venv", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--install-cnvkit", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prokka-bin", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--star-fusion-bin", type=Path, default=None, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    binary_map = _parse_keyed_path(args.binary_paths)

    requested_tools: list[str] = list(args.tools or [])
    requested_tools.extend(binary_map.keys())
    if args.cnvkit_venv is not None:
        requested_tools.append("cnvkit.py")
        args.env_root = args.cnvkit_venv.expanduser().resolve().parent
        if args.install_cnvkit:
            args.install = True
    if args.prokka_bin is not None:
        requested_tools.append("prokka")
        binary_map["prokka"] = str(args.prokka_bin)
    if args.star_fusion_bin is not None:
        requested_tools.append("STAR-Fusion")
        binary_map["STAR-Fusion"] = str(args.star_fusion_bin)

    deduped_tools: list[str] = []
    seen: set[str] = set()
    for tool_name in requested_tools:
        token = str(tool_name).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped_tools.append(token)

    reports: list[dict] = []
    unresolved: list[str] = []
    for tool_name in deduped_tools:
        explicit_binary = binary_map.get(tool_name)
        explicit_env_path = None
        if tool_name == "cnvkit.py" and args.cnvkit_venv is not None:
            explicit_env_path = args.cnvkit_venv.expanduser().resolve()
        row = setup_isolated_tool(
            tool_name,
            recipe_path=args.recipe_path.expanduser().resolve(),
            config_path=args.config_path.expanduser().resolve(),
            binary_path=explicit_binary,
            env_root=args.env_root.expanduser().resolve(),
            env_path=explicit_env_path,
            install=bool(args.install),
            dry_run=bool(args.dry_run),
        )
        reports.append(row)
        if not row.get("success", False):
            unresolved.append(str(tool_name))

    output = {
        "config_path": str(args.config_path.expanduser().resolve()),
        "recipe_path": str(args.recipe_path.expanduser().resolve()),
        "env_root": str(args.env_root.expanduser().resolve()),
        "reports": reports,
        "success": not unresolved,
        "unresolved_tools": unresolved,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if output["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
