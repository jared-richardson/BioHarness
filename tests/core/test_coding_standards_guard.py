from __future__ import annotations

from pathlib import Path

from bio_harness.core.coding_standards_guard import (
    line_count_violation,
    line_is_in_ranges,
    parse_added_line_ranges,
    public_docstring_violations,
)


def test_parse_added_line_ranges_reads_unified_zero_hunks() -> None:
    diff_text = (
        "@@ -0,0 +1,4 @@\n"
        "+line1\n"
        "+line2\n"
        "@@ -10 +20,2 @@\n"
        "+line3\n"
        "+line4\n"
    )

    assert parse_added_line_ranges(diff_text) == [(1, 4), (20, 21)]


def test_parse_added_line_ranges_ignores_zero_count_hunks() -> None:
    diff_text = "@@ -3,0 +8,0 @@\n"

    assert parse_added_line_ranges(diff_text) == []


def test_line_is_in_ranges_detects_inclusive_membership() -> None:
    ranges = [(3, 5), (10, 10)]

    assert line_is_in_ranges(3, ranges) is True
    assert line_is_in_ranges(5, ranges) is True
    assert line_is_in_ranges(6, ranges) is False
    assert line_is_in_ranges(10, ranges) is True


def test_public_docstring_violations_require_module_docstring_for_new_file() -> None:
    source = "def public_api():\n    pass\n"

    violations = public_docstring_violations(
        Path("scripts/example.py"),
        source,
        added_ranges=[(1, 2)],
        is_new_file=True,
    )

    messages = [violation.message for violation in violations]
    assert any("module docstring" in message for message in messages)
    assert any("public function `public_api`" in message for message in messages)


def test_public_docstring_violations_ignore_syntax_errors() -> None:
    violations = public_docstring_violations(
        Path("bio_harness/core/bad.py"),
        "def broken(:\n",
        added_ranges=[(1, 1)],
        is_new_file=True,
    )

    assert violations == []


def test_public_docstring_violations_require_added_public_classes() -> None:
    source = '"""module docstring"""\n\nclass PublicClass:\n    pass\n'

    violations = public_docstring_violations(
        Path("bio_harness/core/example.py"),
        source,
        added_ranges=[(3, 4)],
        is_new_file=False,
    )

    assert violations[0].line == 3
    assert "public class `PublicClass`" in violations[0].message


def test_public_docstring_violations_ignore_private_or_unchanged_defs() -> None:
    source = (
        '"""module docstring"""\n\n'
        "def public_api():\n"
        '    """ok"""\n'
        "    pass\n\n"
        "def _private_helper():\n"
        "    pass\n"
    )

    violations = public_docstring_violations(
        Path("bio_harness/core/example.py"),
        source,
        added_ranges=[(6, 7)],
        is_new_file=False,
    )

    assert violations == []


def test_line_count_violation_rejects_large_new_module() -> None:
    violation = line_count_violation(
        Path("bio_harness/core/new_module.py"),
        current_line_count=351,
        previous_line_count=None,
    )

    assert violation is not None
    assert "350 lines" in violation.message


def test_line_count_violation_allows_small_new_file() -> None:
    violation = line_count_violation(
        Path("bio_harness/core/new_module.py"),
        current_line_count=10,
        previous_line_count=None,
    )

    assert violation is None


def test_line_count_violation_blocks_growth_of_large_script() -> None:
    violation = line_count_violation(
        Path("scripts/big_script.py"),
        current_line_count=520,
        previous_line_count=510,
    )

    assert violation is not None
    assert "500-line guard" in violation.message


def test_line_count_violation_allows_shrinking_large_file() -> None:
    violation = line_count_violation(
        Path("scripts/big_script.py"),
        current_line_count=490,
        previous_line_count=520,
    )

    assert violation is None
