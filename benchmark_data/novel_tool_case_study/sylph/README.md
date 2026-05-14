# Sylph Cold-Onboarding Smoke Fixtures

This directory contains tiny local fixtures for the cold `sylph` onboarding
case study. The goal is not biological realism. The goal is to provide a
bounded, tracked smoke-test surface for:

- `sylph sketch`
- `sylph profile`

Tracked inputs:

- `reference_genome.fa`: tiny synthetic reference genome
- `sample_reads.fastq`: tiny synthetic read set derived from that reference

These files are intentionally small so the gated integration test can run
quickly when `sylph` is installed locally.
