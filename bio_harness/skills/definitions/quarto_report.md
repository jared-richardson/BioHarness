---
name: quarto_report
description: Build a researcher-facing run report bundle and render the generated Quarto document when Quarto is available.
when_to_use: Use after a run completes to render a polished Quarto report from the generated run bundle
when_not_to_use: Do not use before the run is complete or as a substitute for the core analysis workflow
risk_level: low
tools_required:
- python3
capabilities:
- run_reporting
input_types:
- directory
- json
output_types:
- directory
- html
- pdf
- docx
analysis_categories:
- general
parameters:
  run_input:
    type: path
    description: Completed selected-dir path or result.json path.
    required: true
    file_role: input_file
  output_dir:
    type: path
    description: Optional output directory for the generated report bundle.
    required: false
    file_role: output_dir
system_requirements:
  min_ram_gb: 2
  min_cores: 1
---
# Usage Guide

- Case 1: Render a completed benchmark run into a Quarto HTML/PDF report for collaborators.
- Case 2: Convert a run bundle into a manuscript-ready narrative summary.

# Common Pitfalls

- Quarto rendering is optional and depends on the `quarto` executable being installed.
- This skill builds on the run-report bundle; it does not replace benchmark deliverables.
