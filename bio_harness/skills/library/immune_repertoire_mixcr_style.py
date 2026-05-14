from __future__ import annotations

from bio_harness.core.uncommon_skill_framework import build_uncommon_wrapper_command


def immune_repertoire_mixcr_style(**kwargs) -> str:
    return build_uncommon_wrapper_command("immune_repertoire_mixcr_style", kwargs)
