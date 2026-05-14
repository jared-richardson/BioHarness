---
name: tool_name_function
description: &gt;
  A concise, 1-sentence description of what this does (e.g., "Aligns RNA-seq reads to a reference genome using STAR").
risk_level: [low|medium|high] (High risk = deletes files or uses massive RAM)
tools_required: [tool_name]
system_requirements:
  min_ram_gb: 8
  min_cores: 4
parameters:
  input_file:
    type: path
    description: Path to the input FASTQ file.
    required: true
  output_dir:
    type: path
    description: Directory to save results.
    required: true
  threads:
    type: integer
    description: Number of threads (default to system max - 2).
    default: 4
---
# Usage Guide
(Provide 2-3 examples of how to use this tool for different biology use cases)
- Case 1: Standard Illumina processing...
- Case 2: Low-memory mode...

# Common Pitfalls
(List known errors the LLM should watch out for)
- Error: "Genome not found" -&gt; Check reference path.
