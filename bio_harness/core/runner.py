import subprocess
import queue
import threading
import os
import re
import shlex
import shutil
import signal
from pathlib import Path
import selectors
import time

from bio_harness.agents.orchestrator_shell_validation import find_inline_interpreter_commands
from bio_harness.core.execution_policy import inspect_execution_command
from bio_harness.core.shell_parse import split_shell_segments
from bio_harness.core.tool_env import build_pixi_execution_env

class CommandRunner:
    """
    Executes shell commands in a separate process and streams their output
    line-by-line into a queue for real-time processing.
    """

    BLOCKED_PATTERNS = (
        " rm -rf /",
        "sudo ",
        "shutdown",
        "reboot",
        "mkfs",
        "dd if=",
        ">:",
    )
    DISALLOWED_EXEC_PATTERN = re.compile(r"(^|[;&|()\s'\"`])git(\s|$)", flags=re.IGNORECASE)

    WRITE_COMMANDS = {
        "cp",
        "mv",
        "rsync",
        "mkdir",
        "touch",
        "truncate",
        "chmod",
        "chown",
        "ln",
        "rm",
    }

    def _build_launch_argv(self, command: str) -> tuple[list[str] | str, bool]:
        """Build the subprocess invocation for a shell command.

        On POSIX systems we execute through ``bash`` with ``pipefail`` enabled so
        left-side pipeline failures are preserved instead of being masked by the
        final pipeline segment. Windows retains the simpler ``shell=True`` path.

        Args:
            command: Shell command string to execute.

        Returns:
            A tuple of ``(argv, use_shell)`` suitable for ``subprocess.Popen``.

        Raises:
            FileNotFoundError: If ``bash`` is required but not available.
        """
        if os.name == "nt":
            return command, True
        bash_path = shutil.which("bash")
        if not bash_path:
            raise FileNotFoundError("bash")
        return [bash_path, "-o", "pipefail", "-c", command], False

    def _resolve_token_path(self, token: str, cwd: Path) -> Path | None:
        if not token or token.startswith("-"):
            return None
        if any(ch in token for ch in ("*", "?", "[", "]")):
            return None
        p = Path(token).expanduser()
        if not p.is_absolute():
            p = (cwd / p)
        return p.resolve(strict=False)

    def _path_in_root(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _validate_write_targets(self, command: str, cwd: Path, root: Path) -> None:
        readonly_root = (root / "inputs_readonly").resolve(strict=False)

        def _assert_not_readonly(path: Path) -> None:
            try:
                path.relative_to(readonly_root)
                raise PermissionError(
                    f"Denied: writes under read-only root '{readonly_root}' are forbidden."
                )
            except ValueError:
                return

        # Split command via shell-aware parser to avoid false positives from quoted operators.
        segments = [s.strip() for s in split_shell_segments(command) if s.strip()]
        for seg in segments:
            try:
                tokens = shlex.split(seg)
            except Exception:
                continue
            if not tokens:
                continue

            cmd = tokens[0]
            if cmd not in self.WRITE_COMMANDS:
                # Also block redirection writes outside root (best-effort).
                for match in re.finditer(r"(?:^|[^>])>>?\s*([^\s]+)", seg):
                    redir_target = match.group(1).strip().strip("'\"")
                    if redir_target in {"/dev/null", "NUL"}:
                        continue
                    target_path = self._resolve_token_path(redir_target, cwd)
                    if target_path:
                        _assert_not_readonly(target_path)
                    if target_path and not self._path_in_root(target_path, root):
                        raise PermissionError(
                            f"Denied: write target '{target_path}' is outside allowed root '{root}'."
                        )
                continue

            # For cp/mv/rsync/ln, only destination must be in root.
            if cmd in {"cp", "mv", "rsync", "ln"}:
                candidates = [t for t in tokens[1:] if not t.startswith("-")]
                if len(candidates) < 2:
                    continue
                dest = candidates[-1]
                dest_path = self._resolve_token_path(dest, cwd)
                if dest_path:
                    _assert_not_readonly(dest_path)
                if dest_path and not self._path_in_root(dest_path, root):
                    raise PermissionError(
                        f"Denied: destination '{dest_path}' is outside allowed root '{root}'."
                    )
                continue

            # For rm/chmod/chown/mkdir/touch/truncate, all path-like operands must remain in root.
            for token in tokens[1:]:
                path_candidate = self._resolve_token_path(token, cwd)
                if path_candidate is None:
                    continue
                _assert_not_readonly(path_candidate)
                if not self._path_in_root(path_candidate, root):
                    raise PermissionError(
                        f"Denied: write operation target '{path_candidate}' is outside allowed root '{root}'."
                    )

    def _validate_command(self, command: str) -> list[str]:
        lowered = f" {command.lower()}"
        for pattern in self.BLOCKED_PATTERNS:
            if pattern in lowered:
                raise PermissionError(f"Denied command: blocked pattern '{pattern.strip()}'")
        if self.DISALLOWED_EXEC_PATTERN.search(command):
            raise PermissionError("Denied command: runtime git commands are not allowed in execution plans.")
        inline = find_inline_interpreter_commands(command)
        if inline:
            joined = ", ".join(inline)
            raise PermissionError(
                "Denied command: inline interpreter forms are not allowed in raw shell execution "
                f"({joined}). Use a deterministic skill or checked-in helper script instead."
            )
        policy = inspect_execution_command(command)
        if policy["blocking"]:
            raise PermissionError("; ".join(policy["blocking"]))
        return list(policy["audits"])

    def _terminate_process(self, process: subprocess.Popen[bytes], *, grace_seconds: float = 5.0) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            process.terminate()
            try:
                process.wait(timeout=max(0.5, grace_seconds))
            except Exception:
                process.kill()
                process.wait(timeout=max(0.5, grace_seconds))
            return

        try:
            pgid = os.getpgid(process.pid)
        except Exception:
            pgid = None
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except Exception:
                pass
        else:
            try:
                process.terminate()
            except Exception:
                pass

        deadline = time.time() + max(0.5, grace_seconds)
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.05)

        if process.poll() is None:
            if pgid is not None:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except Exception:
                    pass
            else:
                try:
                    process.kill()
                except Exception:
                    pass
            try:
                process.wait(timeout=2.0)
            except Exception:
                pass

    def run_command(
        self,
        command: str,
        log_queue: queue.Queue,
        cwd: str | None = None,
        allowed_root: str | None = None,
        cancel_event: threading.Event | None = None,
        expected_outputs: list[str] | None = None,
    ) -> None:
        """
        Runs a shell command and streams its stdout/stderr to a queue.

        Args:
            command: The shell command string to execute.
            log_queue: A queue.Queue instance to put output lines into.
            cwd: The current working directory for the subprocess. If None, uses the current
                 working directory of the parent process.
        """
        process = None
        try:
            audit_notes = self._validate_command(command)
            if cwd is not None and allowed_root is not None:
                cwd_path = Path(cwd).resolve()
                root_path = Path(allowed_root).resolve()
                try:
                    cwd_path.relative_to(root_path)
                except ValueError:
                    raise PermissionError(
                        f"Denied: cwd '{cwd_path}' is outside allowed root '{root_path}'."
                    )
                self._validate_write_targets(command, cwd_path, root_path)

            start_ts = time.time()
            log_queue.put(f"[status] starting command in cwd={cwd or os.getcwd()}\n")
            log_queue.put(f"[command] {command}\n")
            for audit in audit_notes:
                log_queue.put(f"[stderr] __POLICY_AUDIT__:{audit}\n")

            launch_env = build_pixi_execution_env()
            launch_argv, use_shell = self._build_launch_argv(command)
            process = subprocess.Popen(
                launch_argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                bufsize=0,
                shell=use_shell,
                cwd=cwd,
                env=launch_env,
                start_new_session=(os.name != "nt"),
            )
            log_queue.put(f"[status] spawned pid={process.pid}\n")
            assert process.stdout is not None
            assert process.stderr is not None

            selector = selectors.DefaultSelector()
            os.set_blocking(process.stdout.fileno(), False)
            os.set_blocking(process.stderr.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")

            buffers: dict[str, bytes] = {"stdout": b"", "stderr": b""}
            heartbeat_stop = threading.Event()

            def _emit_heartbeat() -> None:
                while not heartbeat_stop.wait(5.0):
                    if process is None or process.poll() is not None:
                        return
                    elapsed = int(time.time() - start_ts)
                    log_queue.put(f"[status] running pid={process.pid} elapsed={elapsed}s\n")

            heartbeat_thread = threading.Thread(target=_emit_heartbeat, daemon=True)
            heartbeat_thread.start()

            while selector.get_map():
                if cancel_event is not None and cancel_event.is_set():
                    log_queue.put("[stderr] __COMMAND_CANCELLED__:external_stop\n")
                    self._terminate_process(process, grace_seconds=3.0)
                    break
                events = selector.select(timeout=0.5)

                for key, _ in events:
                    stream_name: str = key.data
                    try:
                        chunk = os.read(key.fileobj.fileno(), 4096)
                    except BlockingIOError:
                        continue

                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue

                    buffers[stream_name] += chunk.replace(b"\r", b"\n")
                    while b"\n" in buffers[stream_name]:
                        line_bytes, rest = buffers[stream_name].split(b"\n", 1)
                        buffers[stream_name] = rest
                        line = line_bytes.decode("utf-8", errors="replace")
                        log_queue.put(f"[{stream_name}] {line}\n")

            for stream_name, pending in buffers.items():
                if pending:
                    tail = pending.decode("utf-8", errors="replace")
                    log_queue.put(f"[{stream_name}] {tail}\n")

            return_code = process.wait() # Wait for the process to terminate
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1.0)
            log_queue.put(f"[exit_code={return_code}]\n")

            # Post-command output verification
            if expected_outputs:
                missing_outputs = []
                for expected in expected_outputs:
                    ep = Path(expected).expanduser()
                    if not ep.exists() or (ep.is_file() and ep.stat().st_size == 0):
                        missing_outputs.append(str(ep))
                if missing_outputs and return_code == 0:
                    log_queue.put(f"[stderr] __COMPLETED_NO_OUTPUT__:exit_0_but_missing={','.join(missing_outputs)}\n")
                elif not missing_outputs and return_code != 0:
                    log_queue.put(f"[stdout] __NONZERO_WITH_OUTPUT__:exit_{return_code}_but_outputs_exist\n")

            elapsed = int(time.time() - start_ts)
            log_queue.put(f"[status] finished elapsed={elapsed}s\n")

        except FileNotFoundError:
            tool = command.split()[0] if command.strip() else "unknown"
            log_queue.put(f"[stderr] __COMMAND_NOT_FOUND__:{tool}\n")
            log_queue.put(f"[stderr] Error: Command not found. Ensure '{tool}' is in your PATH.\n")
            log_queue.put("[exit_code=127]\n")
            log_queue.put("[status] finished elapsed=0s\n")
        except PermissionError as e:
            msg = str(e).strip()
            log_queue.put(f"[stderr] __POLICY_BLOCK__:{msg}\n")
            log_queue.put(f"[stderr] {msg}\n")
            log_queue.put("[exit_code=126]\n")
            log_queue.put("[status] finished elapsed=0s\n")
        except Exception as e:
            msg = str(e).strip()
            log_queue.put(f"[stderr] __COMMAND_RUNNER_ERROR__:{msg}\n")
            log_queue.put(f"[stderr] An unexpected error occurred: {msg}\n")
            log_queue.put("[exit_code=1]\n")
            log_queue.put("[status] finished elapsed=0s\n")
        finally:
            try:
                if 'heartbeat_stop' in locals():
                    heartbeat_stop.set()
                if 'heartbeat_thread' in locals():
                    heartbeat_thread.join(timeout=1.0)
            except Exception:
                pass
            if process and process.poll() is None: # If process is still running
                self._terminate_process(process, grace_seconds=3.0)
            log_queue.put(None)  # Sentinel value to signal end of stream
