"""Tests for safe shell binding resolution."""

from __future__ import annotations

from bio_harness.core.shell_bindings import (
    analyze_shell_segments,
    default_shell_path_bindings,
    resolve_shell_text,
)


def test_resolve_shell_text_supports_simple_and_chained_variables() -> None:
    bindings = {
        "OUTPUT_DIR": "/tmp/selected",
        "ISec_DIR": "/tmp/selected/isec_step11",
    }

    result = resolve_shell_text("${ISec_DIR}/0000.vcf", bindings=bindings)

    assert result.unsupported is False
    assert result.unresolved_names == ()
    assert result.resolved_text == "/tmp/selected/isec_step11/0000.vcf"


def test_resolve_shell_text_rejects_unsupported_parameter_expansion() -> None:
    result = resolve_shell_text("${OUTPUT_DIR:-/tmp}/file.vcf", bindings={})

    assert result.unsupported is True
    assert result.unresolved_names == ()


def test_analyze_shell_segments_tracks_assignments_in_order() -> None:
    analyses = analyze_shell_segments(
        'OUTPUT_DIR="/tmp/selected" && ISec_DIR=$OUTPUT_DIR/isec_step11 && RESULT_VCF=$ISec_DIR/0000.vcf',
        bindings=default_shell_path_bindings("/tmp/selected"),
    )

    assert analyses[-1].bindings_after["OUTPUT_DIR"] == "/tmp/selected"
    assert analyses[-1].bindings_after["ISec_DIR"] == "/tmp/selected/isec_step11"
    assert analyses[-1].bindings_after["RESULT_VCF"] == "/tmp/selected/isec_step11/0000.vcf"


def test_analyze_shell_segments_tracks_assignments_across_newlines_and_for_loops() -> None:
    analyses = analyze_shell_segments(
        (
            'set -euo pipefail\n'
            'OUTPUT_DIR="/tmp/selected"\n'
            'ANC_VCF="${OUTPUT_DIR}/anc.filtered.vcf.gz"\n'
            'for vcf in "${ANC_VCF}" "${OUTPUT_DIR}/evol1.filtered.vcf.gz"; do\n'
            '  echo "${vcf}"\n'
            'done\n'
        ),
        bindings=default_shell_path_bindings("/tmp/selected"),
    )

    assert analyses[1].bindings_after["OUTPUT_DIR"] == "/tmp/selected"
    assert analyses[2].bindings_after["ANC_VCF"] == "/tmp/selected/anc.filtered.vcf.gz"
    assert analyses[3].bindings_after["vcf"] == "/tmp/selected/anc.filtered.vcf.gz"
    assert analyses[4].unresolved_names == ()
