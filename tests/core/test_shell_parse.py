import re

import pytest

from bio_harness.core.shell_parse import (
    extract_tools,
    split_shell_chain_segments,
    split_shell_pipeline_segments,
    split_shell_segments,
    strip_shell_comments,
)


def _find_redirect_input(seg: str):
    """Reproduce the redirect extraction logic from Orchestrator._extract_step_requirements."""
    # Strip heredoc content — everything after << DELIM is heredoc body
    _seg = seg
    _heredoc_start = re.search(r"<<-?\s*['\"]?\w+['\"]?", _seg)
    if _heredoc_start:
        _seg = _seg[:_heredoc_start.start()]
    # Strip quoted strings
    _seg = re.sub(r"'[^']*'", " ", _seg)
    _seg = re.sub(r'"[^"]*"', " ", _seg)
    m = re.search(r"<\s*([^\s]+)", _seg)
    if not m:
        return None
    raw = m.group(1).strip("'\"")
    if raw in ("-",) or raw.startswith("-"):
        return None
    return raw


def test_extract_tools_ignores_shell_control_and_builtins():
    command = (
        "if command -v bash >/dev/null; then "
        "x=1; for i in 1 2; do continue; done; "
        "case foo in foo) : ;; esac; exit 0; "
        "fi"
    )
    assert extract_tools(command) == []


def test_extract_tools_extracts_real_command_heads():
    assert extract_tools("export X=1; env FOO=bar bash -lc 'echo hi'") == ["bash"]


def test_extract_tools_skips_command_v_probe():
    assert extract_tools("command -v fastqc >/dev/null; echo ready") == []


def test_split_shell_segments_respects_quoted_operators():
    command = "echo 'a|b;c&&d' | tr '|' ':' ; head -n1 sample.txt"
    assert split_shell_segments(command) == ["echo 'a|b;c&&d'", "tr '|' ':'", "head -n1 sample.txt"]
    assert split_shell_chain_segments(command) == ["echo 'a|b;c&&d' | tr '|' ':'", "head -n1 sample.txt"]
    assert split_shell_pipeline_segments("printf 'a|b' | head -1") == ["printf 'a|b'", "head -1"]


def test_extract_tools_does_not_parse_quoted_awk_program_tokens_as_commands():
    command = "bam=\"$(awk 'NF {sub(/\\r$/, \"\", $0); print; exit}' \"$bam_list\")\"; printf '%s\\n' \"$bam\""
    assert "print" not in extract_tools(command)


def test_split_segments_preserves_quoted_less_than_operators():
    """Quoted comparison operators like 'QUAL<20' must stay within one segment."""
    command = "bcftools filter -e 'QUAL<20 || DP<10' -O v -o out.vcf in.vcf"
    segments = split_shell_segments(command)
    # The entire command should be a single segment — || inside quotes is not a chain split.
    assert len(segments) == 1
    assert "QUAL<20" in segments[0]


def test_split_shell_segments_drops_line_continuation_prefixes() -> None:
    command = (
        "cd selected && \\\n"
        "bcftools view -i 'QUAL>=30 && FORMAT/DP>=5 && FORMAT/DP<=100' "
        "-v snps,indels -Oz -o filtered.vcf.gz calls/raw.vcf && \\\n"
        "bcftools index -t filtered.vcf.gz"
    )

    segments = split_shell_segments(command)

    assert segments == [
        "cd selected",
        "bcftools view -i 'QUAL>=30 && FORMAT/DP>=5 && FORMAT/DP<=100' -v snps,indels -Oz -o filtered.vcf.gz calls/raw.vcf",
        "bcftools index -t filtered.vcf.gz",
    ]


def test_strip_shell_comments_preserves_quoted_programs() -> None:
    command = (
        "cd selected && \\\n"
        "# filter high/moderate variants\n"
        "SnpSift filter \"(ANN[*].IMPACT = 'HIGH') || (ANN[*].IMPACT = 'MODERATE')\" "
        "evol1.vcf > shared/evol1_high_mod.vcf && \\\n"
        "awk 'BEGIN{print \"#not-a-comment\"}' shared/evol1_high_mod.vcf"
    )

    cleaned = strip_shell_comments(command)

    assert "high/moderate" not in cleaned
    assert "#not-a-comment" in cleaned


# ── Redirect extraction — parametrized ────────────────────────────────────


@pytest.mark.parametrize(
    "segment",
    [
        pytest.param(
            "bcftools filter -e 'QUAL<20 || DP<10' -O v -o out.vcf in.vcf",
            id="single_quoted_bcftools_filter",
        ),
        pytest.param(
            'awk -F"\\t" "$4<30 {print}" input.bed > filtered.bed',
            id="double_quoted_expression",
        ),
        pytest.param("Rscript -e 'x <- 42'", id="r_assignment_operator"),
        pytest.param("cat < -", id="stdin_dash"),
        pytest.param(
            "python3 << 'PYEOF'\nimport csv\nprint('hello')\nPYEOF",
            id="heredoc_operator",
        ),
        pytest.param("cat <<< 'hello world'", id="herestring_operator"),
        pytest.param(
            "python3 << 'PYEOF'\nimport csv\nfor i in range(n):\n    if i < j:\n        pass\nPYEOF",
            id="heredoc_python_comparison",
        ),
    ],
)
def test_redirect_not_detected_in_safe_patterns(segment: str):
    """Redirect extraction must NOT produce a match for these patterns."""
    assert _find_redirect_input(segment) is None


@pytest.mark.parametrize(
    "segment, expected",
    [
        pytest.param(
            "sort < /data/input.txt > /data/output.txt",
            "/data/input.txt",
            id="outside_quotes",
        ),
        pytest.param(
            "sort < /data/input.txt",
            "/data/input.txt",
            id="before_heredoc",
        ),
    ],
)
def test_redirect_detected_for_real_redirects(segment: str, expected: str):
    """Real shell redirects must still be detected correctly."""
    assert _find_redirect_input(segment) == expected
