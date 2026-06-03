"""
Claude subprocess interface for `claude -p` and `claude -c` sessions.

This module owns only the low-level Claude process loop:
  - run_claude_once()    execute one Claude subprocess, capture output, detect END_REASON
  - run_claude_session() run the multi-round prompt/continue loop
  - get_line_counts()    lightweight file line-count utility
"""

import contextlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional, List

from .task import RoundResult

CompleteCallback = Callable[[], bool | str]
CLAUDE_PERMISSION_MODE = "bypassPermissions"
ROUND_SLEEP_SECONDS = 1.0


# Keep runner status messages visible promptly even when stdout is redirected.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

# Match END_REASON:{reason} leniently inside assistant/result text.
PAT_REASON = re.compile(r"(?i)END_REASON\s*:\s*(LIMIT|COMPLETE)\b")

# Detect terminal auth / quota failures that should stop the session immediately.
AUTH_ERROR_PATTERNS = [
    re.compile(r"failed to authenticate", re.I),
    re.compile(r"api token quota has been exhausted", re.I),
    re.compile(r"authentication_failed", re.I),
    re.compile(r"\b403\b.*(?:anthropic|api|quota|authenticate|authentication)", re.I),
]


@contextlib.contextmanager
def windows_keepawake():
    """
    Prevent Windows from sleeping for the duration of the block (WSL2-safe).

    Calls SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) via a
    background powershell.exe loop that refreshes every 30 s.  The loop process
    is killed on exit, which releases the inhibit automatically.

    No-ops silently if powershell.exe is not found (native Linux / CI).
    """
    ps_script = (
        "Add-Type -MemberDefinition '"
        "[DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint f);'"
        " -Namespace WinAPI -Name Power -PassThru | Out-Null;"
        "while ($true) { [WinAPI.Power]::SetThreadExecutionState(0x80000001); Start-Sleep 30 }"
    )
    proc = None
    try:
        proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-Command", ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("[info] Sleep inhibit active (powershell.exe keepawake started)")
    except FileNotFoundError:
        pass  # Not running under WSL2 / Windows
    try:
        yield
    finally:
        if proc is not None:
            proc.kill()
            print("[info] Sleep inhibit released")


def _extract_searchable_text(output: str) -> str:
    """
    Extract plain text from output for END_REASON detection.

    When output_format="stream-json", Claude writes JSON Lines where the agent's
    text is nested inside assistant/result objects. We extract only those plain
    text payloads instead of searching raw JSON lines.

    Assistant/result extraction avoids false negatives when END_REASON appears
    inside backticks or code fences in the final reply, and avoids searching the
    coordinator prompt text embedded elsewhere in the stream log.
    """
    parts: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
                if not isinstance(data, dict):
                    continue
                if data.get("type") == "assistant":
                    for block in data.get("message", {}).get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(block["text"])
                    continue
                if data.get("type") == "result":
                    result_text = data.get("result")
                    if isinstance(result_text, str):
                        parts.append(result_text)
                    continue
                continue
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        parts.append(stripped)
    return "\n".join(parts)


def _detect_auth_error(output: str) -> Optional[str]:
    """Detect terminal auth / quota failures in raw or extracted output."""
    searchable = _extract_searchable_text(output)
    haystacks = (output, searchable)
    for pattern in AUTH_ERROR_PATTERNS:
        if any(pattern.search(text) for text in haystacks):
            return "AUTH_ERROR"
    return None


def get_line_counts(files: List[Path]) -> dict:
    """Get line counts for files. Returns {filename: line_count}."""
    counts = {}
    for f in files:
        try:
            counts[f.name] = sum(1 for _ in open(f, encoding="utf-8"))
        except Exception:
            counts[f.name] = 0
    return counts


def run_claude_once(
    args: List[str],
    env: Optional[dict] = None,
    cwd: Optional[Path] = None,
    log_file: Optional[Path] = None,
) -> tuple[str, Optional[str], int]:
    """
    Execute a single claude command and capture output.

    Stdout is redirected to `log_file` or an anonymous temporary file so the
    subprocess does not block on an unread pipe.

    Args:
        args: Claude command arguments list
        env: Environment variables for the Claude subprocess
        cwd: Working directory
        log_file: Optional log file path.

    Returns:
        (stdout, end_reason, returncode)
        end_reason: "COMPLETE" | "LIMIT" | "AUTH_ERROR" | None
    """
    popen_kwargs: dict = {"stderr": subprocess.STDOUT}
    if env:
        popen_kwargs["env"] = env
    if cwd:
        popen_kwargs["cwd"] = str(cwd)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)

    if log_file:
        with open(log_file, "wb") as fh:
            proc = subprocess.Popen(args, stdout=fh, **popen_kwargs)
            proc.wait()
        out = log_file.read_text(encoding="utf-8", errors="replace")
    else:
        with tempfile.TemporaryFile(mode="wb") as fh:
            proc = subprocess.Popen(args, stdout=fh, **popen_kwargs)
            proc.wait()
            fh.seek(0)
            out = fh.read().decode("utf-8", errors="replace")

    searchable = _extract_searchable_text(out)
    m = PAT_REASON.search(searchable)
    reason = m.group(1).upper() if m else None
    if reason is None:
        reason = _detect_auth_error(out)
    return out, reason, proc.returncode


def run_claude_session(
    prompt: str,
    cwd: Optional[Path] = None,
    output_format: Optional[str] = None,
    max_rounds: int = 20,
    env: Optional[dict] = None,
    on_complete: Optional[CompleteCallback] = None,
    result_dir: Optional[Path] = None,
    task_id: Optional[str] = None,
    files_to_track: Optional[List[Path]] = None,
    session_log_dir: Optional[Path] = None,
) -> tuple[str, int, List[RoundResult]]:
    """
    Run a complete Claude session with automatic continue logic.

    Round 1: claude -p <prompt>  (new session)
    Round N: claude -c -p continue  (server-side context continuation)
    After 2 consecutive LIMITs: claude -p <prompt>  (reset to fresh session)

    Args:
        prompt: Initial prompt
        cwd: Working directory
        output_format: Claude output format (normally "stream-json" or None)
        max_rounds: Maximum rounds
        env: Environment variables
        on_complete: Callback when COMPLETE received. Return True to accept COMPLETE,
            False to resend the original prompt, or a string to send a specific
            follow-up prompt.
        result_dir: Directory to save compact per-round JSON summaries (None = skip)
        task_id: Task ID for organizing result files
        files_to_track: Files to track line counts for
        session_log_dir: Directory to save per-round conversation logs (round_N.txt)

    Returns:
        (end_reason, rounds_used, round_results)
    """
    print(f"[info] Using prompt:\n{prompt[:120]}{'...' if len(prompt) > 120 else ''}\n")

    # Build base command
    base = ["claude", "-p"]
    if output_format:
        base += ["--output-format", output_format]
        if output_format == "stream-json":
            base += ["--verbose"]
    base += ["--permission-mode", CLAUDE_PERMISSION_MODE]

    round_results: List[RoundResult] = []
    initial_line_counts = get_line_counts(files_to_track) if files_to_track else {}

    def record_round(round_num: int, stdout: str, reason: Optional[str], returncode: int, duration: float) -> RoundResult:
        """Record a round result."""
        line_counts = get_line_counts(files_to_track) if files_to_track else {}

        result = RoundResult(
            round_number=round_num,
            stdout=stdout,
            end_reason=reason,
            returncode=returncode,
            duration_seconds=duration,
            line_counts=line_counts,
        )
        round_results.append(result)

        # Print line count changes
        if line_counts and initial_line_counts:
            # vs initial
            init_changes = []
            for filename, current in line_counts.items():
                if filename in initial_line_counts:
                    diff = current - initial_line_counts[filename]
                    if diff != 0:
                        ratio = diff / initial_line_counts[filename] * 100 if initial_line_counts[filename] > 0 else 0
                        init_changes.append((filename, initial_line_counts[filename], current, diff, ratio))
            # vs previous round
            prev_changes = []
            if round_results:
                prev_counts = round_results[-1].line_counts
                for filename, current in line_counts.items():
                    if filename in prev_counts:
                        diff = current - prev_counts[filename]
                        if diff != 0:
                            ratio = diff / prev_counts[filename] * 100 if prev_counts[filename] > 0 else 0
                            prev_changes.append((filename, prev_counts[filename], current, diff, ratio))

            if init_changes:
                print("[info] Line changes (vs initial):")
                for filename, initial, final, diff, ratio in init_changes:
                    sign = "+" if diff > 0 else ""
                    print(f"  {filename}: {initial} -> {final} ({sign}{diff}, {ratio:+.1f}%)")
            if prev_changes:
                print("[info] Line changes (vs prev round):")
                for filename, prev, final, diff, ratio in prev_changes:
                    sign = "+" if diff > 0 else ""
                    print(f"  {filename}: {prev} -> {final} ({sign}{diff}, {ratio:+.1f}%)")

        # Save round result immediately if result_dir is specified
        if result_dir and task_id:
            result_path = result_dir / task_id
            result_path.mkdir(parents=True, exist_ok=True)
            round_file = result_path / f"round_{round_num}.json"
            with open(round_file, "w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
            print(f"[info] Round {round_num} completed in {result.duration_seconds:.1f}s, result saved to {round_file}")

        return result

    def _log_path(round_num: int) -> Optional[Path]:
        if session_log_dir:
            return session_log_dir / f"round_{round_num}.txt"
        return None

    # First call: new session
    round_start = time.time()
    stdout, reason, returncode = run_claude_once(base + [prompt], env=env, cwd=cwd, log_file=_log_path(1))
    round_duration = time.time() - round_start
    record_round(1, stdout, reason, returncode, round_duration)

    if reason == "AUTH_ERROR":
        print("[warn] Authentication / quota failure detected. Stopping session immediately.")
        return reason, 1, round_results

    rounds = 1
    consecutive_limits = 1 if reason == "LIMIT" else 0  # Track consecutive LIMIT count
    max_consecutive_limits = 2  # Reset session after 2 consecutive LIMITs
    consecutive_no_signal = 1 if reason is None else 0
    max_consecutive_no_signal = 5
    complete_verification_failed = False

    while reason == "LIMIT" or reason is None or reason == "COMPLETE":
        print("=" * 60)

        time.sleep(ROUND_SLEEP_SECONDS)

        # If COMPLETE, run verification callback
        if reason == "COMPLETE":
            print("\n[info] Received COMPLETE signal.")
            if on_complete:
                complete_result = on_complete()
                if complete_result is True:
                    # Verification passed
                    complete_verification_failed = False
                    break
                else:
                    # Verification failed, retry with either a targeted prompt or the original prompt.
                    complete_verification_failed = True
                    retry_prompt = complete_result if isinstance(complete_result, str) else prompt
                    print("[info] Verification failed, resending prompt...")
                    if rounds >= max_rounds:
                        print(
                            f"\n[warn] Reached maximum round count {max_rounds} after failed COMPLETE verification, stopping as LIMIT.",
                            file=sys.stderr,
                        )
                        reason = "LIMIT"
                        break
                    rounds += 1

                    round_start = time.time()
                    stdout, reason, returncode = run_claude_once(
                        base + [retry_prompt],
                        env=env,
                        cwd=cwd,
                        log_file=_log_path(rounds),
                    )
                    round_duration = time.time() - round_start
                    record_round(rounds, stdout, reason, returncode, round_duration)
                    continue
            else:
                # No verification callback, accept COMPLETE
                break

        if rounds >= max_rounds:
            print(
                f"\n[warn] Reached maximum round count {max_rounds}, stopping.",
                file=sys.stderr,
            )
            reason = "LIMIT"
            break

        rounds += 1

        # Check if we need to reset session due to consecutive LIMITs
        should_reset_session = (reason == "LIMIT" and consecutive_limits >= max_consecutive_limits)

        # Continue with prompt again if reason is None or need to reset session
        round_start = time.time()
        if reason is None:
            print("[info] No END_REASON detected, continuing with prompt...")
            stdout, reason, returncode = run_claude_once(base + [prompt], env=env, cwd=cwd, log_file=_log_path(rounds))
        elif should_reset_session:
            print(f"[info] Resetting session after {consecutive_limits} consecutive LIMITs...")
            stdout, reason, returncode = run_claude_once(base + [prompt], env=env, cwd=cwd, log_file=_log_path(rounds))
            consecutive_limits = 0  # Reset counter after starting new session
        else:
            # Continue the same session
            cmd = ["claude", "-c", "-p"]
            if output_format:
                cmd += ["--output-format", output_format]
                if output_format == "stream-json":
                    cmd += ["--verbose"]
            cmd += ["--permission-mode", CLAUDE_PERMISSION_MODE]
            stdout, reason, returncode = run_claude_once(cmd + ["continue"], env=env, cwd=cwd, log_file=_log_path(rounds))
        round_duration = time.time() - round_start

        record_round(rounds, stdout, reason, returncode, round_duration)
        if reason == "AUTH_ERROR":
            print("[warn] Authentication / quota failure detected. Stopping session immediately.")
            break

        # Update consecutive counters
        if reason == "LIMIT":
            consecutive_limits += 1
        else:
            consecutive_limits = 0

        if reason is None:
            consecutive_no_signal += 1
            if consecutive_no_signal >= max_consecutive_no_signal:
                print(
                    f"\n[warn] {consecutive_no_signal} consecutive rounds with no END_REASON. Stopping as LIMIT.",
                    file=sys.stderr,
                )
                reason = "LIMIT"
                break
        else:
            consecutive_no_signal = 0

    if complete_verification_failed and reason in (None, "COMPLETE"):
        print("[warn] COMPLETE was emitted but verification never passed. Coercing final end reason to LIMIT.")
        reason = "LIMIT"

    return reason, rounds, round_results
