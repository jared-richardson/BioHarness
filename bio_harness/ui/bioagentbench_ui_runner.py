"""Run BioAgentBench task sweeps through the Streamlit UI.

This module uses the repo's Playwright CLI wrapper to drive the real browser
surface instead of bypassing the UI. It is intended for reliability sweeps
after UI or orchestration changes.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.request import urlopen

from bio_harness.core.bioagentbench_official import (
    build_validator_argv,
    resolve_manifest_entries,
)
from bio_harness.ui.bioagentbench_ui_support import (
    benchmark_manifest_default_policy,
    build_ui_benchmark_prompt,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "benchmark_data" / "bioagentbench_official_manifest.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "workspace" / "runs" / "_bioagentbench_ui_reliability"
PLAYWRIGHT_WRAPPER = Path.home() / ".codex" / "skills" / "playwright" / "scripts" / "playwright_cli.sh"
STREAMLIT_BIN = PROJECT_ROOT / ".venv" / "bin" / "streamlit"
PYTHON_BIN = PROJECT_ROOT / ".venv" / "bin" / "python"

from bio_harness.core.schemas import TERMINAL_RUN_STATUSES

# Use the canonical set from schemas.py so all components agree on
# what constitutes a finished run.
TERMINAL_STATUSES = TERMINAL_RUN_STATUSES


@dataclass
class UiBenchmarkAttemptResult:
    """One UI-driven benchmark attempt result."""

    task_id: str
    attempt_index: int
    prompt: str
    run_dir: str
    harness_status: str
    validator_exit_code: int | None
    validator_passed: bool | None
    validator_log: str
    screenshot_path: str
    console_errors_path: str
    console_warnings_path: str
    duration_seconds: float
    benchmark_policy: str
    streamlit_url: str
    error_message: str = ""


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run BioAgentBench task sweeps through the Streamlit UI.")
    parser.add_argument("--manifest", type=str, default=str(DEFAULT_MANIFEST))
    parser.add_argument("--task-id", action="append", default=[], help="Restrict the sweep to specific task IDs.")
    parser.add_argument("--attempts", type=int, default=3, help="Number of UI attempts per task.")
    parser.add_argument("--port", type=int, default=8540, help="Streamlit port to launch for the sweep.")
    parser.add_argument("--session-name", type=str, default="ui_bench")
    parser.add_argument("--benchmark-policy", type=str, default=benchmark_manifest_default_policy())
    parser.add_argument("--output-root", type=str, default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--launch-timeout-seconds", type=int, default=180)
    parser.add_argument("--completion-timeout-seconds", type=int, default=1800)
    parser.add_argument("--page-timeout-seconds", type=int, default=120)
    parser.add_argument("--ui-plan-timeout-seconds", type=int, default=300)
    parser.add_argument("--headed", action="store_true", help="Launch the browser visibly for the sweep.")
    parser.add_argument("--reuse-server", action="store_true", help="Reuse an already running Streamlit server on the target port.")
    parser.add_argument("--keep-server", action="store_true", help="Do not terminate the launched Streamlit server at the end.")
    return parser.parse_args(argv)


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def _wait_for_http_ready(url: str, timeout_seconds: int) -> None:
    deadline = time.time() + float(timeout_seconds)
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2.0) as response:
                if int(response.status) < 500:
                    return
        except Exception:
            time.sleep(1.0)
            continue
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for Streamlit UI at {url}.")


def _launch_streamlit(args: argparse.Namespace) -> subprocess.Popen[str] | None:
    if args.reuse_server and _port_open(args.port):
        return None
    env = dict(os.environ)
    env["BIO_HARNESS_BENCHMARK_POLICY"] = str(args.benchmark_policy).strip()
    env["BIO_HARNESS_UI_PLAN_TIMEOUT_SECONDS"] = str(int(args.ui_plan_timeout_seconds))
    if not STREAMLIT_BIN.exists():
        raise FileNotFoundError(f"Missing Streamlit binary: {STREAMLIT_BIN}")
    proc = subprocess.Popen(
        [str(STREAMLIT_BIN), "run", "app.py", "--server.headless", "true", "--server.port", str(args.port)],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    _wait_for_http_ready(f"http://127.0.0.1:{int(args.port)}", timeout_seconds=args.page_timeout_seconds)
    return proc


def _run_pwcli(
    *,
    session_name: str,
    cli_args: Sequence[str],
    timeout_seconds: int = 180,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    if not PLAYWRIGHT_WRAPPER.exists():
        raise FileNotFoundError(f"Missing Playwright wrapper: {PLAYWRIGHT_WRAPPER}")
    cmd = ["bash", str(PLAYWRIGHT_WRAPPER), "--session", session_name, *cli_args]
    try:
        return subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=check,
        )
    except subprocess.CalledProcessError as exc:
        details = ((exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")).strip()
        raise RuntimeError(f"Playwright command failed: {' '.join(cmd)}\n{details}") from exc


def _safe_session_name(raw: str) -> str:
    """Return one short Playwright session token safe for Unix socket paths.

    Args:
        raw: Requested session name.

    Returns:
        One sanitized short session token.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(raw or "").strip())
    cleaned = cleaned.strip("._-") or "ui_bench"
    return cleaned[:24]


def _run_browser_js(session_name: str, code: str, *, timeout_seconds: int = 180) -> None:
    _run_pwcli(session_name=session_name, cli_args=["run-code", code], timeout_seconds=timeout_seconds)


def _open_browser(url: str, *, session_name: str, headed: bool) -> None:
    cli_args = ["open", url]
    if headed:
        cli_args.append("--headed")
    _run_pwcli(session_name=session_name, cli_args=cli_args, timeout_seconds=180)


def _ensure_ui_ready(url: str, *, session_name: str, timeout_seconds: int) -> None:
    code = f"""
async (page) => {{
  await page.goto({json.dumps(url)}, {{ waitUntil: 'domcontentloaded' }});
  await page.getByRole('heading', {{ name: 'BioHarness' }}).waitFor({{ timeout: {int(timeout_seconds) * 1000} }});
  await page.getByRole('textbox', {{ name: 'Ask BioHarness to analyze, execute, explain, or prepare data.' }}).waitFor({{ timeout: {int(timeout_seconds) * 1000} }});
}}
"""
    _run_browser_js(session_name, code, timeout_seconds=max(timeout_seconds + 15, 60))


def _prepare_attempt_code() -> str:
    """Build the Playwright snippet that normalizes one attempt UI state."""
    return """
async (page) => {
  const newChat = page.getByRole('button', { name: 'New chat' });
  await newChat.waitFor({ timeout: 120000 });
  await newChat.click();
  const autoStartInput = page.locator('input[aria-label="Auto-start on proceed/run"]');
  if (!(await autoStartInput.evaluate((el) => el.checked))) {
    await page.getByText('Auto-start on proceed/run', { exact: true }).click();
  }
  const subsetInput = page.locator('input[aria-label="Use small-sample subset"]');
  if (await subsetInput.evaluate((el) => el.checked)) {
    await page.getByText('Use small-sample subset', { exact: true }).click();
  }
  await page.getByRole('textbox', { name: 'Ask BioHarness to analyze, execute, explain, or prepare data.' }).waitFor({ timeout: 120000 });
}
"""


def _prepare_attempt_ui(session_name: str) -> None:
    code = _prepare_attempt_code()
    _run_browser_js(session_name, code, timeout_seconds=180)


def _prompt_submission_code(prompt: str) -> str:
    """Build one resilient Playwright snippet for chat prompt submission.

    Args:
        prompt: Prompt text to inject into the UI chat box.

    Returns:
        JavaScript source for the Playwright `run-code` helper.
    """
    textbox_label = "Ask BioHarness to analyze, execute, explain, or prepare data."
    return f"""
async (page) => {{
  const promptText = {json.dumps(prompt)};
  const chat = page.getByRole('textbox', {{ name: {json.dumps(textbox_label)} }});
  await chat.waitFor({{ timeout: 120000 }});
  await chat.click();
  await page.keyboard.press('Meta+A').catch(() => null);
  await page.keyboard.press('Control+A').catch(() => null);
  await page.keyboard.press('Backspace').catch(() => null);
  await page.keyboard.type(promptText, {{ delay: 0 }});
  await page.waitForFunction(
    ([label, expected]) => {{
      const selector = `textarea[aria-label="${{label}}"], input[aria-label="${{label}}"]`;
      const field = document.querySelector(selector);
      return !!field && 'value' in field && field.value === expected;
    }},
    [{json.dumps(textbox_label)}, promptText],
    {{ timeout: 10000 }}
  );
  await page.waitForFunction(
    () => {{
      const buttons = Array.from(document.querySelectorAll('button'));
      return buttons.some((button) => {{
        const text = (button.innerText || '').trim();
        const label = (button.getAttribute('aria-label') || '').trim();
        return (text === 'Send message' || label === 'Send message') && !button.disabled;
      }});
    }},
    {{ timeout: 10000 }}
  );
  await page.getByRole('button', {{ name: 'Send message' }}).click();
}}
"""


def _submit_prompt(session_name: str, prompt: str) -> None:
    code = _prompt_submission_code(prompt)
    _run_browser_js(session_name, code, timeout_seconds=180)


def _capture_screenshot(session_name: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    code = f"async (page) => {{ await page.screenshot({{ path: {json.dumps(str(output_path))}, fullPage: true }}); }}"
    _run_browser_js(session_name, code, timeout_seconds=180)


def _capture_console(session_name: str, level: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    proc = _run_pwcli(
        session_name=session_name,
        cli_args=["console", level],
        timeout_seconds=60,
        check=False,
    )
    output_path.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")


def _known_run_dirs() -> set[Path]:
    return {path.resolve(strict=False) for path in (PROJECT_ROOT / "workspace" / "runs").glob("20*") if path.is_dir()}


def _wait_for_new_run_dir(before: set[Path], *, timeout_seconds: int) -> Path:
    deadline = time.time() + float(timeout_seconds)
    while time.time() < deadline:
        current = _known_run_dirs()
        new_dirs = sorted(current - before, key=lambda path: path.stat().st_mtime)
        if new_dirs:
            return new_dirs[-1]
        time.sleep(1.0)
    raise TimeoutError("Timed out waiting for a new UI run directory.")


def _read_exit_payload(run_dir: Path) -> dict[str, Any]:
    exit_path = run_dir / "exit.json"
    if not exit_path.exists():
        return {}
    try:
        payload = json.loads(exit_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _latest_run_activity_ts(run_dir: Path) -> float:
    """Return the latest observable activity timestamp for one UI run dir.

    Args:
        run_dir: Concrete run directory created by the Streamlit UI.

    Returns:
        Maximum mtime across the run artifacts that change while execution is
        active. Returns ``0.0`` when none of those artifacts exist yet.
    """
    latest_ts = 0.0
    for name in ("events.jsonl", "state.json", "exit.json"):
        path = run_dir / name
        try:
            latest_ts = max(latest_ts, float(path.stat().st_mtime))
        except OSError:
            continue
    return latest_ts


def _is_terminal_status(status: str) -> bool:
    return str(status).strip().lower() in TERMINAL_STATUSES


def _wait_for_terminal_status(run_dir: Path, *, timeout_seconds: int) -> str:
    inactivity_timeout = float(timeout_seconds)
    latest_activity = max(time.time(), _latest_run_activity_ts(run_dir))
    while True:
        payload = _read_exit_payload(run_dir)
        status = str(payload.get("status", "") or "").strip().lower()
        if _is_terminal_status(status):
            return status
        latest_activity = max(latest_activity, _latest_run_activity_ts(run_dir))
        if (time.time() - latest_activity) > inactivity_timeout:
            raise TimeoutError(
                f"Timed out waiting for terminal status in {run_dir} after {int(inactivity_timeout)}s without run activity."
            )
        time.sleep(2.0)


def _validate_attempt(entry: dict[str, Any], *, run_dir: Path) -> tuple[int | None, str]:
    argv = build_validator_argv(entry, selected_dir=run_dir, python_executable=str(PYTHON_BIN if PYTHON_BIN.exists() else sys.executable))
    if not argv:
        return None, ""
    proc = subprocess.run(
        argv,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return int(proc.returncode), output


def _render_summary_markdown(results: Sequence[UiBenchmarkAttemptResult]) -> str:
    lines = [
        "# UI BioAgentBench Reliability Summary",
        "",
        "| Task | Attempt | Harness | Validation | Duration (s) | Run Dir |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in results:
        validation = "n/a" if row.validator_passed is None else ("pass" if row.validator_passed else "fail")
        lines.append(
            f"| {row.task_id} | {row.attempt_index} | {row.harness_status} | {validation} | "
            f"{row.duration_seconds:.1f} | `{row.run_dir}` |"
        )
        if row.error_message:
            lines.append(f"|  |  | error | `{row.error_message}` |  |  |")
    return "\n".join(lines) + "\n"


def _build_error_result(
    *,
    task_id: str,
    attempt_index: int,
    prompt: str,
    benchmark_policy: str,
    streamlit_url: str,
    duration_seconds: float,
    error_message: str,
    screenshot_path: Path,
    console_errors_path: Path,
    console_warnings_path: Path,
) -> UiBenchmarkAttemptResult:
    """Build a structured attempt result for one UI-runner failure.

    Args:
        task_id: Benchmark task ID.
        attempt_index: 1-based attempt index.
        prompt: Prompt issued to the UI.
        benchmark_policy: Active benchmark policy.
        streamlit_url: Streamlit URL used for the attempt.
        duration_seconds: Attempt wall-clock duration.
        error_message: Failure detail.
        screenshot_path: Stored screenshot path.
        console_errors_path: Stored console error log path.
        console_warnings_path: Stored console warning log path.

    Returns:
        One structured failed-attempt result.
    """
    return UiBenchmarkAttemptResult(
        task_id=task_id,
        attempt_index=attempt_index,
        prompt=prompt,
        run_dir="",
        harness_status="runner_error",
        validator_exit_code=None,
        validator_passed=False,
        validator_log="",
        screenshot_path=str(screenshot_path),
        console_errors_path=str(console_errors_path),
        console_warnings_path=str(console_warnings_path),
        duration_seconds=duration_seconds,
        benchmark_policy=benchmark_policy,
        streamlit_url=streamlit_url,
        error_message=error_message,
    )


def _select_entries(manifest_path: Path, task_ids: Sequence[str]) -> list[dict[str, Any]]:
    entries = resolve_manifest_entries(manifest_path)
    wanted = {task_id.strip() for task_id in task_ids if task_id.strip()}
    if not wanted:
        return entries
    return [entry for entry in entries if str(entry.get("task_id", "") or "").strip() in wanted]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the UI reliability sweep and write one structured summary.

    Args:
        argv: Optional CLI argument sequence.

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve() / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root.mkdir(parents=True, exist_ok=True)
    entries = _select_entries(manifest_path, args.task_id)
    if not entries:
        raise SystemExit("No benchmark entries matched the requested task IDs.")

    server_proc = _launch_streamlit(args)
    streamlit_url = f"http://127.0.0.1:{int(args.port)}"
    session_name = _safe_session_name(str(args.session_name).strip() or "ui_bench")
    results: list[UiBenchmarkAttemptResult] = []

    try:
        _open_browser(streamlit_url, session_name=session_name, headed=bool(args.headed))
        for entry in entries:
            task_id = str(entry.get("task_id", "") or "").strip()
            for attempt_index in range(1, int(args.attempts) + 1):
                attempt_dir = output_root / task_id / f"attempt_{attempt_index}"
                attempt_dir.mkdir(parents=True, exist_ok=True)
                prompt = build_ui_benchmark_prompt(entry)
                (attempt_dir / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
                start = time.time()
                screenshot_path = attempt_dir / "final_ui.png"
                console_errors_path = attempt_dir / "console_errors.log"
                console_warnings_path = attempt_dir / "console_warnings.log"
                try:
                    before_runs = _known_run_dirs()
                    _ensure_ui_ready(streamlit_url, session_name=session_name, timeout_seconds=args.page_timeout_seconds)
                    _prepare_attempt_ui(session_name)
                    _submit_prompt(session_name, prompt)
                    run_dir = _wait_for_new_run_dir(before_runs, timeout_seconds=args.launch_timeout_seconds)
                    harness_status = _wait_for_terminal_status(run_dir, timeout_seconds=args.completion_timeout_seconds)
                    duration_seconds = time.time() - start
                    _capture_screenshot(session_name, screenshot_path)
                    _capture_console(session_name, "error", console_errors_path)
                    _capture_console(session_name, "warning", console_warnings_path)
                    validator_exit_code, validator_log = _validate_attempt(entry, run_dir=run_dir)
                    validator_log_path = attempt_dir / "validator.log"
                    validator_log_path.write_text(validator_log, encoding="utf-8")
                    result = UiBenchmarkAttemptResult(
                        task_id=task_id,
                        attempt_index=attempt_index,
                        prompt=prompt,
                        run_dir=str(run_dir),
                        harness_status=harness_status,
                        validator_exit_code=validator_exit_code,
                        validator_passed=(validator_exit_code == 0) if validator_exit_code is not None else None,
                        validator_log=str(validator_log_path),
                        screenshot_path=str(screenshot_path),
                        console_errors_path=str(console_errors_path),
                        console_warnings_path=str(console_warnings_path),
                        duration_seconds=duration_seconds,
                        benchmark_policy=str(args.benchmark_policy),
                        streamlit_url=streamlit_url,
                    )
                except Exception as exc:
                    duration_seconds = time.time() - start
                    try:
                        _capture_screenshot(session_name, screenshot_path)
                    except Exception:
                        pass
                    try:
                        _capture_console(session_name, "error", console_errors_path)
                        _capture_console(session_name, "warning", console_warnings_path)
                    except Exception:
                        pass
                    result = _build_error_result(
                        task_id=task_id,
                        attempt_index=attempt_index,
                        prompt=prompt,
                        benchmark_policy=str(args.benchmark_policy),
                        streamlit_url=streamlit_url,
                        duration_seconds=duration_seconds,
                        error_message=str(exc),
                        screenshot_path=screenshot_path,
                        console_errors_path=console_errors_path,
                        console_warnings_path=console_warnings_path,
                    )
                results.append(result)
                (attempt_dir / "attempt_result.json").write_text(
                    json.dumps(asdict(result), indent=2) + "\n",
                    encoding="utf-8",
                )
    finally:
        if server_proc is not None and not args.keep_server:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server_proc.kill()

    summary_json = output_root / "summary.json"
    summary_md = output_root / "summary.md"
    summary_json.write_text(json.dumps([asdict(row) for row in results], indent=2) + "\n", encoding="utf-8")
    summary_md.write_text(_render_summary_markdown(results), encoding="utf-8")
    print(summary_md)
    return 0 if all(row.validator_passed is not False and row.harness_status == "completed" for row in results) else 1
