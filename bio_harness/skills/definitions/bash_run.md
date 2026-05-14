---
name: bash_run
description: 'Executes a user-approved shell command inside the workspace sandbox. Use this when a specialized skill is not
  yet available, but a safe command-line step is needed.

  '
when_to_use: Use as a last resort when no specialized skill exists for the required single operation
when_not_to_use: Do not use when a dedicated skill wrapper exists or when the workflow needs multiple operations
risk_level: high
tools_required: []
input_types: []
output_types: []
analysis_categories:
- general
parameters:
  command:
    type: string
    description: Full shell command to execute.
    required: true
  working_directory:
    type: string
    description: Optional execution directory for the shell command. Must stay inside the selected workspace root.
system_requirements:
  min_ram_gb: 2
  min_cores: 1
---
# Usage Guide
Use `bash_run` only for one direct command when no dedicated wrapper exists.

- Keep commands scoped to files under `workspace/`.
- Keep each command to one logical operation.
- Prefer dedicated wrappers such as `bcftools_filter_run`, `bcftools_norm_run`, `bcftools_isec_run`, `tabix_index_run`, `shared_variants_export_run`, and other typed skills whenever they exist.
- Do not use destructive commands.
- Prefer explicit paths and expected output files.

# Common Pitfalls
- Pipes, `&&`, `;`, `||`, loops, and side-effecting conditionals are rejected by semantic validation.
- If a command references a non-existent file, execution fails with non-zero exit.
- Workspace guard will block commands that attempt to write outside the allowed root.
