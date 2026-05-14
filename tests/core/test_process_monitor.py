from __future__ import annotations

import os
import time
from pathlib import Path

from bio_harness.core.process_monitor import collect_process_snapshot, collect_recent_outputs, infer_process_label


def test_infer_process_label_from_java_jar():
    label = infer_process_label(
        command_hint="java -jar /opt/FastQC/fastqc.jar --threads 4 reads.fastq",
        runtime_cmdlines=["java -jar /opt/FastQC/fastqc.jar --threads 4 reads.fastq"],
    )
    assert label == "fastqc"


def test_infer_process_label_from_bash_wrapped_star():
    label = infer_process_label(
        command_hint="bash -lc '/usr/local/bin/STAR --runThreadN 8 --genomeDir /ref'",
        runtime_cmdlines=["/usr/local/bin/STAR --runThreadN 8 --genomeDir /ref"],
    )
    assert label == "star"


def test_infer_process_label_from_python_script():
    label = infer_process_label(
        command_hint="python /tools/rmats.py --b1 control.txt --b2 treatment.txt",
        runtime_cmdlines=["python /tools/rmats.py --b1 control.txt --b2 treatment.txt"],
    )
    assert label == "rmats"


def test_collect_process_snapshot_invalid_pid_is_not_alive():
    snap = collect_process_snapshot(-1, command_hint="")
    assert snap["alive"] is False
    assert snap["live_process_count"] == 0
    assert snap["inferred_tool"] == "unknown"


def test_collect_process_snapshot_current_pid_has_expected_fields():
    snap = collect_process_snapshot(os.getpid(), command_hint="bash -lc 'echo ok'")
    assert isinstance(snap["alive"], bool)
    assert isinstance(snap["live_process_count"], int)
    assert "inferred_tool" in snap
    assert "top_processes" in snap


def test_collect_recent_outputs_filters_by_since_ts(tmp_path: Path):
    old_file = tmp_path / "old.txt"
    old_file.write_text("old", encoding="utf-8")
    now = time.time()
    os.utime(old_file, (now - 200, now - 200))

    new_file = tmp_path / "new.txt"
    new_file.write_text("new", encoding="utf-8")
    os.utime(new_file, (now, now))

    snap = collect_recent_outputs(tmp_path, since_ts=now - 10, max_files=5, max_scan=1000)
    paths = {str(item["path"]) for item in snap["recent_files"]}
    assert "new.txt" in paths
    assert "old.txt" not in paths
    assert snap["latest_mtime"] > 0
