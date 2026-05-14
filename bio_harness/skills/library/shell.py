from __future__ import annotations

import shlex


def bash_run(command: str, working_directory: str = "") -> str:
    """Return a shell command for execution by the harness runner.

    Args:
        command: Shell command to execute.
        working_directory: Optional execution directory for the command.

    Returns:
        A shell command string ready for runner execution.

    Raises:
        ValueError: If ``command`` is empty.

    The runner enforces workspace-bound cwd restrictions and blocks dangerous
    patterns. When ``working_directory`` is supplied, the rendered command also
    includes a defensive ``cd`` so exported plans preserve the same behavior as
    live execution.
    """
    if not command or not command.strip():
        raise ValueError("'command' is required for bash_run")
    rendered = command.strip()
    working_dir = str(working_directory or "").strip()
    if working_dir:
        quoted_dir = shlex.quote(working_dir)
        return f"cd {quoted_dir} && {rendered}"
    return rendered
