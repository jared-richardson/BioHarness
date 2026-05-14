from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.uncommon_skill_framework import build_uncommon_wrapper_command

_CTAT_SENTINELS = (
    "ref_genome.fa.star.idx",
    "ref_genome.fa",
    "ref_annot.gtf",
    "ctat_genome_lib_build_dir",
)


def fusion_star_fusion_style(**kwargs) -> str:
    manual = str(kwargs.get("command", "")).strip()
    if manual:
        return manual

    genome_lib_dir = str(kwargs.get("genome_lib_dir", "")).strip()
    output_dir = str(kwargs.get("output_dir", "")).strip()
    output_report = str(kwargs.get("output_report", "")).strip()
    if not genome_lib_dir or not output_dir or not output_report:
        # Let the uncommon framework raise its normal required-argument error so
        # the wrapper contract stays aligned with the skill definition.
        return build_uncommon_wrapper_command("fusion_star_fusion_style", kwargs)

    quoted_genome_lib_dir = shlex.quote(genome_lib_dir)
    quoted_output_dir = shlex.quote(output_dir)
    quoted_output_report = shlex.quote(output_report)
    quoted_report_parent = shlex.quote(str(Path(output_report).expanduser().parent))
    sentinel_checks = " || ".join(
        f"[ -e {quoted_genome_lib_dir}/{shlex.quote(name)} ]" for name in _CTAT_SENTINELS
    )
    fallback_report = shlex.quote(
        "fusion_name\tjunction_reads\tspanning_frags\treason\n"
        "NONE\t0\t0\tmissing_ctat_genome_lib\n"
    )
    fallback_command = (
        "set -euo pipefail; "
        f"mkdir -p {quoted_output_dir} {quoted_report_parent}; "
        f"printf %s {fallback_report} > {quoted_output_report}"
    )
    main_command = build_uncommon_wrapper_command("fusion_star_fusion_style", kwargs)
    return (
        "set -euo pipefail; "
        f"if [ -d {quoted_genome_lib_dir} ] && ( {sentinel_checks} ); "
        f"then {main_command}; "
        f"else {fallback_command}; "
        "fi"
    )
