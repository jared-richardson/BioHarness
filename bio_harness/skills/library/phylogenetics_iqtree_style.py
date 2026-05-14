from __future__ import annotations

from bio_harness.core.uncommon_skill_framework import build_uncommon_wrapper_command


def phylogenetics_iqtree_style(**kwargs) -> str:
    return build_uncommon_wrapper_command("phylogenetics_iqtree_style", kwargs)
