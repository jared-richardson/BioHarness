from __future__ import annotations

from bio_harness.pipeline_scripts.summarize_viral_paf import main


def test_summarize_viral_paf_writes_report_and_detected_list(tmp_path):
    panel_fai = tmp_path / "viral_panel.fna.fai"
    panel_fai.write_text(
        "virus_a\t100\t0\t0\t0\n"
        "virus_b\t200\t0\t0\t0\n",
        encoding="utf-8",
    )
    paf_path = tmp_path / "reads_vs_panel.paf"
    paf_path.write_text(
        "\n".join(
            [
                "read1\t100\t0\t90\t+\tvirus_a\t100\t0\t90\t90\t90\t60",
                "read2\t100\t0\t80\t+\tvirus_a\t100\t10\t90\t80\t80\t60",
                "read3\t100\t0\t20\t+\tvirus_b\t200\t0\t20\t20\t20\t60",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "output" / "classification_report.tsv"
    detected_path = tmp_path / "output" / "detected_viruses.txt"

    exit_code = main(
        [
            "--paf",
            str(paf_path),
            "--panel-fai",
            str(panel_fai),
            "--output-report",
            str(report_path),
            "--output-detected",
            str(detected_path),
            "--coverage-threshold",
            "50",
        ]
    )

    assert exit_code == 0
    report_text = report_path.read_text(encoding="utf-8")
    assert "virus_name\tref_length\tmapped_reads\tcovered_bases\tcoverage_pct\trelative_abundance" in report_text
    assert "virus_a\t100\t2\t90\t90.00\t0.6667" in report_text
    assert detected_path.read_text(encoding="utf-8") == "virus_a\n"
