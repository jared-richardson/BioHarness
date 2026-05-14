"""Dynamic Skill Builder — generate skill wrappers from CLI help text.

Discovers tool CLI help, uses the fast LLM to extract structured parameters,
and generates a complete skill definition (.md + .py wrapper) that can be
installed via ``tool_onboarding.install_tool_onboarding_draft()``.
"""
from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bio_harness.core.tool_probe import build_probe_env, discover_cli_metadata

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CLI discovery
# ---------------------------------------------------------------------------

def _discover_env(tool_name: str) -> Dict[str, str]:
    """Build an env dict that includes conda/pixi bin directories.

    Many bioinformatics tools live in ``.pixi/envs/default/bin/`` and are not
    on the default system PATH.  We detect the project root (by walking up from
    this file) and prepend the pixi bin dir so that ``subprocess.run`` can find
    them.
    """
    return build_probe_env(tool_name)


def discover_cli(tool_name: str, *, timeout: int = 15) -> Dict[str, Any]:
    """Run ``tool --help``, ``tool -h``, etc. and capture output.

    Returns::

        {
            "tool_name": str,
            "executable": str | None,  # resolved path
            "help_text": str,
            "version": str,
            "subcommands": list[str],
        }
    """
    return discover_cli_metadata(tool_name, timeout=timeout)


# ---------------------------------------------------------------------------
# Help text parsing via LLM
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
You are a bioinformatics tool analyzer. Given the CLI help output below, extract structured information.

Tool name: {tool_name}
Help text:
```
{help_text}
```

Extract the following as valid JSON (no additional text):
{{
  "description": "one-line description of what the tool does",
  "when_to_use": "when to use this tool in a bioinformatics pipeline",
  "when_not_to_use": "when NOT to use this tool",
  "input_types": ["list of input file types: fastq, bam, fasta, vcf, gff, gtf, etc."],
  "output_types": ["list of output file types"],
  "analysis_categories": ["list of analysis categories: variant_calling, alignment, etc."],
  "parameters": [
    {{
      "name": "parameter_name",
      "flag": "--flag or -f",
      "type": "path|string|integer|boolean|float",
      "description": "what this parameter does",
      "required": true/false,
      "default": "default value or null",
      "file_role": "reference_genome|input_fastq_r1|input_fastq_r2|input_bam|input_vcf|annotation_gff|annotation_gtf|output_dir|null"
    }}
  ],
  "command_template": "template command with {{param_name}} placeholders"
}}

Rules:
- For parameters, use snake_case names (not the flag names)
- Identify which parameters are file inputs/outputs and set file_role accordingly
- The command_template should show a typical usage with the most important parameters
- Keep descriptions concise (under 80 chars each)
- Only include parameters that are commonly used (skip obscure/debugging flags)
"""


def parse_help_text(
    help_text: str,
    tool_name: str,
    *,
    llm: Any = None,
) -> Dict[str, Any]:
    """Use the fast LLM to extract structured parameter info from help text.

    Args:
        help_text: Raw CLI help output text.
        tool_name: Name of the tool.
        llm: Optional BioLLM instance. If None, uses regex-based extraction.

    Returns dict with: description, parameters, input_types, output_types,
    command_template, when_to_use, when_not_to_use.
    """
    if llm is not None:
        try:
            prompt = _EXTRACTION_PROMPT.format(
                tool_name=tool_name,
                help_text=help_text[:6000],
            )
            # Use fast model for extraction
            response = llm.complete(prompt, max_tokens=2000)
            parsed = _extract_json_from_response(str(response))
            if parsed and "parameters" in parsed:
                return parsed
        except Exception as e:
            logger.warning("LLM extraction failed for %s: %s", tool_name, e)

    # Fallback: regex-based extraction
    return _regex_parse_help(help_text, tool_name)


def _extract_json_from_response(text: str) -> Optional[Dict[str, Any]]:
    """Extract JSON from an LLM response that may contain markdown."""
    # Try to find JSON in code blocks first
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    # Try direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find JSON object in text
    brace_start = text.find("{")
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _regex_parse_help(help_text: str, tool_name: str) -> Dict[str, Any]:
    """Fallback regex-based parsing when LLM is unavailable."""
    params: List[Dict[str, Any]] = []

    # Match patterns like:  --flag VALUE   description
    # or:                   -f, --flag     description
    # or:                   -t INT         description
    # or:                   --output=FILE  description
    # or:                   -a:  description  (prodigal style)
    flag_pattern = re.compile(
        r"^\s+"  # leading whitespace
        r"(-\w(?:,\s*--[\w-]+)?|--[\w-]+)"  # flags: -f, --flag or --flag or -f
        r"(?:=[\[<]?\w+[\]>]?)?"  # optional =VALUE placeholder (--flag=FILE)
        r"(?:\s+[\[<]?\w+[\]>]?)?"  # optional space-separated value placeholder (--flag FILE)
        r":?\s{2,}(.+)$",  # optional colon + description (after 2+ spaces)
        re.MULTILINE,
    )
    for match in flag_pattern.finditer(help_text):
        flag_str = match.group(1).strip()
        desc = match.group(2).strip()
        # Extract primary flag
        flags = [f.strip() for f in flag_str.split(",")]
        primary_flag = flags[-1] if flags else flag_str
        # Convert to param name
        name = primary_flag.lstrip("-").replace("-", "_").lower()
        if not name:
            continue
        # Guess type
        ptype = "string"
        if any(w in desc.lower() for w in ("file", "path", "dir", "directory", "fasta", "fastq", "bam", "vcf")):
            ptype = "path"
        elif any(w in desc.lower() for w in ("number", "count", "threads", "int")):
            ptype = "integer"
        elif any(w in desc.lower() for w in ("true", "false", "enable", "disable", "flag")):
            ptype = "boolean"

        params.append({
            "name": name,
            "flag": primary_flag,
            "type": ptype,
            "description": desc[:80],
            "required": False,
            "default": None,
            "file_role": None,
        })

    # Extract first line as description
    first_line = ""
    for line in help_text.splitlines():
        line = line.strip()
        if line and not line.startswith("-") and len(line) > 10:
            first_line = line[:200]
            break

    return {
        "description": first_line or f"Run {tool_name} bioinformatics tool",
        "when_to_use": f"Use for {tool_name} analysis",
        "when_not_to_use": "",
        "input_types": [],
        "output_types": [],
        "analysis_categories": [],
        "parameters": params[:15],  # cap at 15 params
        "command_template": f"{tool_name}",
    }


# ---------------------------------------------------------------------------
# Skill draft generation
# ---------------------------------------------------------------------------

def generate_skill_draft(
    parsed: Dict[str, Any],
    tool_name: str,
) -> Dict[str, Any]:
    """Build a complete skill draft dict compatible with
    ``install_tool_onboarding_draft()``.

    Returns a dict with keys: name, description, risk_level, tools_required,
    capabilities, parameters, system_requirements, command_template,
    when_to_use, when_not_to_use, input_types, output_types,
    analysis_categories, wrapper_code.
    """
    skill_name = _sanitize_skill_name(tool_name)
    params = parsed.get("parameters", [])
    description = str(parsed.get("description", f"Run {tool_name}")).strip()

    # Build parameter dict for frontmatter
    param_dict: Dict[str, Dict[str, Any]] = {}
    required_params: List[str] = []
    for p in params:
        pname = str(p.get("name", "")).strip()
        if not pname:
            continue
        entry: Dict[str, Any] = {
            "type": p.get("type", "string"),
            "description": str(p.get("description", "")).strip()[:80],
            "required": bool(p.get("required", False)),
        }
        if p.get("default") is not None:
            entry["default"] = p["default"]
        if p.get("file_role"):
            entry["file_role"] = p["file_role"]
        param_dict[pname] = entry
        if entry["required"]:
            required_params.append(pname)

    # Build command template
    cmd_template = str(parsed.get("command_template", tool_name)).strip()
    if cmd_template == tool_name and params:
        # Build a simple template from params
        flag_parts = []
        for p in params:
            if p.get("required"):
                flag = p.get("flag", "")
                name = p.get("name", "")
                if flag:
                    flag_parts.append(f"{flag} {{{name}}}")
                else:
                    flag_parts.append(f"{{{name}}}")
        if flag_parts:
            cmd_template = f"{tool_name} " + " ".join(flag_parts)

    # Generate wrapper code
    wrapper_code = _generate_wrapper_code(skill_name, tool_name, params, cmd_template)

    return {
        "skill_name": skill_name,
        "name": skill_name,  # alias for validate_skill()
        "description": description,
        "risk_level": "medium",
        "tools_required": [tool_name],
        "capabilities": [],
        "parameters": param_dict,
        "system_requirements": {"min_ram_gb": 4, "min_cores": 2},
        "command_template": cmd_template,
        "when_to_use": str(parsed.get("when_to_use", "")).strip(),
        "when_not_to_use": str(parsed.get("when_not_to_use", "")).strip(),
        "input_types": parsed.get("input_types", []),
        "output_types": parsed.get("output_types", []),
        "analysis_categories": parsed.get("analysis_categories", []),
        "wrapper_code": wrapper_code,
    }


def _sanitize_skill_name(tool_name: str) -> str:
    """Convert a tool name to a valid skill name."""
    name = re.sub(r"[^a-zA-Z0-9_]", "_", tool_name.lower()).strip("_")
    return name or "custom_tool"


def _safe_python_identifier(name: str) -> str:
    """Convert a parameter name into a valid Python identifier.

    Handles: Python keywords (``continue``), names starting with digits
    (``1st_allele``), bare numeric names (``5``), and other edge cases.
    """
    import keyword

    # Prefix numeric starts
    if name and name[0].isdigit():
        name = f"p_{name}"
    # Replace any remaining non-identifier chars
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_")
    if not name:
        return ""
    # Avoid Python keywords
    if keyword.iskeyword(name):
        name = f"p_{name}"
    return name


def _generate_wrapper_code(
    skill_name: str,
    tool_name: str,
    params: List[Dict[str, Any]],
    cmd_template: str,
) -> str:
    """Generate a Python wrapper function for the skill."""
    lines = [
        "from __future__ import annotations",
        "",
        "import shlex",
        "import shutil",
        "",
        "",
        f"def {skill_name}(**kwargs) -> str:",
        f'    """Run {tool_name} with the given parameters."""',
        '    if "command" in kwargs and str(kwargs.get("command", "")).strip():',
        '        return str(kwargs["command"]).strip()',
        "",
    ]

    # Build command parts
    lines.append(f'    tool = shutil.which("{tool_name}") or "{tool_name}"')
    lines.append("    parts = [tool]")
    lines.append("")

    for p in params:
        raw_name = p.get("name", "")
        pname = _safe_python_identifier(raw_name)
        flag = p.get("flag", "")
        ptype = p.get("type", "string")
        required = p.get("required", False)
        default = p.get("default")

        if not pname:
            continue

        # Use original name as the kwarg key but sanitized name as the variable
        kwarg_key = raw_name
        if required:
            if default is not None:
                lines.append(f'    {pname} = str(kwargs.get("{kwarg_key}", {repr(default)})).strip()')
            else:
                lines.append(f'    {pname} = str(kwargs.get("{kwarg_key}", "")).strip()')
        else:
            lines.append(f'    {pname} = str(kwargs.get("{kwarg_key}", "")).strip()')

        if ptype == "boolean":
            lines.append(f"    if {pname} and {pname}.lower() not in ('false', '0', 'no'):")
            if flag:
                lines.append(f'        parts.append("{flag}")')
            else:
                lines.append(f'        parts.append("--{pname.replace("_", "-")}")')
        elif ptype == "path":
            lines.append(f"    if {pname}:")
            if flag:
                lines.append(f'        parts.extend(["{flag}", shlex.quote({pname})])')
            else:
                lines.append(f"        parts.append(shlex.quote({pname}))")
        else:
            lines.append(f"    if {pname}:")
            if flag:
                lines.append(f'        parts.extend(["{flag}", shlex.quote({pname})])')
            else:
                lines.append(f'        parts.extend(["--{pname.replace("_", "-")}", shlex.quote({pname})])')
        lines.append("")

    lines.append('    return " ".join(parts)')
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_skill(
    draft: Dict[str, Any],
    *,
    test_command: Optional[str] = None,
) -> Tuple[bool, str]:
    """Validate the generated skill draft.

    Checks:
    - Wrapper code compiles (syntax check)
    - Required fields present
    - Optionally dry-run the wrapper with test arguments

    Returns (is_valid, error_message_or_empty).
    """
    errors: List[str] = []

    # Check required fields
    for field in ("name", "description"):
        if not draft.get(field):
            errors.append(f"Missing required field: {field}")
    # Parameters must be present but may be empty (some tools have no flags)
    if "parameters" not in draft:
        errors.append("Missing required field: parameters")

    # Syntax check the wrapper code
    wrapper_code = draft.get("wrapper_code", "")
    if wrapper_code:
        try:
            ast.parse(wrapper_code)
        except SyntaxError as e:
            errors.append(f"Wrapper code syntax error: {e}")

    # Check parameter structure
    params = draft.get("parameters", {})
    if isinstance(params, dict):
        for pname, pdetails in params.items():
            if not isinstance(pdetails, dict):
                errors.append(f"Parameter {pname} must be a dict")
            elif "type" not in pdetails:
                errors.append(f"Parameter {pname} missing 'type'")

    if errors:
        return False, "; ".join(errors)

    return True, ""


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def build_skill_from_cli(
    tool_name: str,
    *,
    llm: Any = None,
    skills_defs_dir: Optional[Path] = None,
    skills_lib_dir: Optional[Path] = None,
    catalog_path: Optional[Path] = None,
    install: bool = True,
) -> Tuple[bool, str]:
    """Full pipeline: discover → parse → generate → validate → install.

    Uses existing ``install_tool_onboarding_draft()`` from ``tool_onboarding.py``
    for the install step.

    Args:
        tool_name: CLI tool name (must be on PATH).
        llm: Optional BioLLM instance for LLM-assisted parsing.
        skills_defs_dir: Path to skill definitions directory.
        skills_lib_dir: Path to skill library directory.
        catalog_path: Path to capability catalog.
        install: Whether to actually install the skill files.

    Returns:
        (success, message)
    """
    # Step 1: Discover
    logger.info("Discovering CLI help for: %s", tool_name)
    cli_info = discover_cli(tool_name)
    if not cli_info.get("help_text"):
        return False, f"Could not get help text for {tool_name}"

    # Step 2: Parse
    logger.info("Parsing help text for: %s", tool_name)
    parsed = parse_help_text(cli_info["help_text"], tool_name, llm=llm)
    if not parsed:
        return False, f"Failed to parse help text for {tool_name}"

    # Step 3: Generate
    logger.info("Generating skill draft for: %s", tool_name)
    draft = generate_skill_draft(parsed, tool_name)

    # Step 4: Validate
    valid, error = validate_skill(draft)
    if not valid:
        return False, f"Validation failed for {tool_name}: {error}"

    if not install:
        return True, f"Skill draft generated for {tool_name} (not installed)"

    # Step 5: Install
    if skills_defs_dir and skills_lib_dir and catalog_path:
        try:
            from bio_harness.core.tool_onboarding import install_tool_onboarding_draft
            source_meta = {
                "source": f"cli_help:{tool_name}",
                "source_mode": "cli_discovery",
            }
            success, msg = install_tool_onboarding_draft(
                draft,
                source_meta,
                skills_definitions_dir=skills_defs_dir,
                skills_library_dir=skills_lib_dir,
                capability_catalog_path=catalog_path,
                install_workflow="dynamic_skill_builder",
            )
            if success:
                # Also write the wrapper code
                wrapper_path = skills_lib_dir / f"{draft['name']}.py"
                wrapper_code = draft.get("wrapper_code", "")
                if wrapper_code and not wrapper_path.exists():
                    wrapper_path.write_text(wrapper_code, encoding="utf-8")
                    logger.info("Wrote wrapper to %s", wrapper_path)
                return True, f"Skill {draft['name']} installed successfully"
            return False, f"Installation failed: {msg}"
        except Exception as e:
            return False, f"Installation error: {e}"

    return True, f"Skill draft for {tool_name} generated but paths not provided for install"
