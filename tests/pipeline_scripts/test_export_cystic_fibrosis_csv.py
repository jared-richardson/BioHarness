from __future__ import annotations

import csv
from pathlib import Path

from bio_harness.pipeline_scripts.export_cystic_fibrosis_csv import export_cystic_fibrosis_csv
from scripts.validate_cystic_fibrosis import validate


def test_export_cystic_fibrosis_csv_isolates_recessive_cftr_variant(tmp_path: Path) -> None:
    vcf = tmp_path / "ex1.eff.vcf"
    family = tmp_path / "family_description.txt"
    output = tmp_path / "final" / "cf_variants.csv"
    vcf.write_text(
        "\n".join(
            [
                "##fileformat=VCFv4.2",
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA12877\tNA12878\tNA12879\tNA12880\tNA12885\tNA12886",
                "7\t117200000\t.\tA\tG\t.\tPASS\tANN=G|missense_variant|MODERATE|GENE1|ENSG1|transcript|ENST1|protein_coding|1/1|c.100A>G|p.Lys34Arg\tGT\t0/1\t0/1\t1/1\t0/1\t0/1\t1/1",
                "7\t117227832\t.\tG\tT\t.\tPASS\tANN=T|stop_gained|HIGH|CFTR|ENSG00000001626|transcript|ENST00000003084|protein_coding|12/27|c.1624G>T|p.Gly542*\tGT\t0/1\t0/1\t1/1\t0/1\t1/1\t1/1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    family.write_text(
        "\n".join(
            [
                "- Father: NA12877 (unaffected male)",
                "- Mother: NA12878 (unaffected female)",
                "1. NA12879 (affected female)",
                "2. NA12880 (unaffected female)",
                "7. NA12885 (affected female)",
                "8. NA12886 (affected male)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    export_cystic_fibrosis_csv(
        input_vcf=vcf,
        family_description=family,
        output_csv=output,
        gene_hint="CFTR",
        clinvar_vcf=None,
    )

    rows = list(csv.DictReader(output.open("r", encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["position"] == "117227832"
    assert rows[0]["gene_name"] == "CFTR"
    assert rows[0]["impact"] == "HIGH"
    assert rows[0]["clinical_significance"] == ""


def test_validate_cystic_fibrosis_allows_missing_optional_clinvar_fields(tmp_path: Path) -> None:
    truth = tmp_path / "truth.csv"
    output = tmp_path / "cf_variants.csv"
    header = (
        "chromosome,position,variant_id,reference,alternate,gene_name,gene_id,annotation,impact,"
        "transcript_id,hgvs_c,hgvs_p,clinical_significance,diseases,review_status,rs_id\n"
    )
    truth.write_text(
        header
        + "7,117227832,7115,G,T,CFTR,ENSG00000001626,stop_gained,HIGH,ENST00000003084,c.1624G>T,p.Gly542*,Pathogenic,Cystic_fibrosis,practice_guideline,113993959\n",
        encoding="utf-8",
    )
    output.write_text(
        header
        + "7,117227832,,G,T,CFTR,ENSG00000001626,stop_gained,HIGH,ENST00000003084,c.1624G>T,p.Gly542*,,,,,\n",
        encoding="utf-8",
    )

    assert validate(truth, output) is True
