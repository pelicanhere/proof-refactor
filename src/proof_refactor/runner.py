"""
Task management layer for executing an agent on Lean tasks.

Subprocess / session logic lives in proof_refactor.run_claude.
"""

import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import IO, Callable, Optional, List

from .config import get_config
from .ask import ASK_SESSION_DIR_ENV
from .lean_checker import (
    find_lean_files,
    check_lake_build,
    check_lean_files_parallel,
    remove_extraction_import,
)
from .mcp_stats import analyze_session_logs
from .run_claude import run_claude_session, windows_keepawake
from .run_metadata import write_run_metadata
from .session_errors import collect_session_errors
from .task import TaskMetadata, TaskResult, RoundResult

_thread_local = threading.local()


def set_thread_log(fh: IO[str]) -> None:
    _thread_local.log_file = fh


def clear_thread_log() -> None:
    _thread_local.log_file = None


def _log(*args, **kwargs) -> None:
    fh = getattr(_thread_local, "log_file", None)
    if fh is not None:
        kwargs["file"] = fh
        print(*args, **kwargs)
        fh.flush()
    else:
        print(*args, **kwargs)

PHASES = ["extract", "design", "prove", "repair"]


def find_unique_run_dir(parent_dir: Path, name: str) -> Path:
    """Return parent_dir/name if empty/absent, else parent_dir/name_1, name_2, …"""
    def _occupied(d: Path) -> bool:
        return d.exists() and any(d.iterdir())

    candidate = parent_dir / name
    if not _occupied(candidate):
        return candidate
    n = 1
    while True:
        candidate = parent_dir / f"{name}_{n}"
        if not _occupied(candidate):
            return candidate
        n += 1


def _run_dir(task: TaskMetadata) -> Path:
    return task.target_path.parent if task.task_type == "file" else task.target_path


def run_phase(
    phase: str,
    prompt: str,
    task: TaskMetadata,
    env: dict,
    files_to_track: List[Path],
    session_log_dir: Path,
    on_complete: Optional[Callable[[], bool | str]] = None,
) -> tuple:
    """Run one phase as a fresh agent session. Returns (end_reason, rounds_used, round_results)."""
    _log(f"\n{'=' * 60}")
    _log(f"[phase] {phase.upper()}")
    _log(f"[agent] {task.agent}")
    _log("=" * 60)

    end_reason, rounds_used, round_results = run_claude_session(
        prompt=prompt,
        cwd=task.cwd,
        output_format=task.output_format,
        agent=task.agent,
        max_rounds=task.max_rounds,
        env=env,
        on_complete=on_complete,
        task_id=task.task_id,
        files_to_track=files_to_track,
        session_log_dir=session_log_dir,
    )
    _log(f"[phase] {phase.upper()} → {end_reason} ({rounds_used} round(s))")
    return end_reason, rounds_used, round_results


def _run_phased(
    task: TaskMetadata,
    env: dict,
    files_to_track: List[Path],
    session_log_root: Path,
) -> tuple:
    """Drive 4 sequential phase sessions. Returns (end_reason, total_rounds, all_round_results)."""
    all_round_results: List[RoundResult] = []
    total_rounds = 0

    def _verify_final(repair_prompt: str | None = None) -> bool | str:
        if not task.check_after_complete:
            return True
        check_path = task.get_check_path()
        lean_files = (
            [check_path] if task.task_type == "file" and check_path.suffix == ".lean"
            else find_lean_files(check_path)
        )
        if not lean_files:
            return True

        workspace_dir = Path(task.cwd).resolve() if task.cwd else check_path.parent.resolve()

        def _log_output(stdout: str, stderr: str) -> None:
            for label, text in (("stdout", stdout), ("stderr", stderr)):
                text = text.strip()
                if not text:
                    continue
                if len(text) > 4000:
                    text = f"... <truncated>\n{text[-4000:]}"
                _log(f"[{label}]\n{text}")

        def _verify_files(label: str) -> bool:
            _log(f"[info] {label}: checking {len(lean_files)} .lean file(s)...")
            results = check_lean_files_parallel(lean_files)
            if task.allow_sorry:
                errors = [f for f, e, _, _, _ in results if e]
            else:
                errors = [f for f, e, s, _, _ in results if e or s]
            if errors:
                for f in errors:
                    _log(f"  [error] {f}")
                return False
            _log(f"[info] {label}: all {len(lean_files)} file(s) passed.")
            return True

        def _verify_lake_build(label: str) -> bool:
            _log(f"[info] {label}: running `lake build` in {workspace_dir}")
            has_error, stdout, stderr = check_lake_build(workspace_dir)
            if has_error:
                _log(f"[error] {label} failed.")
                _log_output(stdout, stderr)
                return False
            _log(f"[info] {label}: `lake build` passed.")
            return True

        def _cleanup_retry_prompt() -> str:
            base_prompt = repair_prompt or task.get_prompt()
            return (
                f"{base_prompt.rstrip()}\n\n"
                "Additional repair instruction:\n"
                "Extraction cleanup is incomplete. Remove any remaining `extract` blocks "
                "or dependencies on `Extraction`, then make the final Lean file build "
                "without `import Extraction`. Finish with `END_REASON: COMPLETE`."
            )

        if not _verify_files("Final verify before cleanup"):
            return False
        if not _verify_lake_build("Lake build before cleanup"):
            return False

        removed_import = False
        for f in lean_files:
            if remove_extraction_import(f):
                removed_import = True
                _log(f"[info] Removed `import Extraction` from {f}")

        if removed_import and not _verify_files("Final verify after removing `import Extraction`"):
            return _cleanup_retry_prompt()
        if not _verify_lake_build("Lake build after removing `import Extraction`"):
            return _cleanup_retry_prompt() if removed_import else False

        return True

    with windows_keepawake():
        for phase in PHASES:
            prompt = task.phase_prompts[phase]
            is_final = phase == PHASES[-1]
            phase_log_dir = session_log_root / phase
            _log(f"[info] Session logs ({phase}) → {phase_log_dir}/round_N.txt")

            end_reason, rounds_used, round_results = run_phase(
                phase=phase,
                prompt=prompt,
                task=task,
                env=env,
                files_to_track=files_to_track,
                session_log_dir=phase_log_dir,
                on_complete=(
                    (lambda prompt=prompt: _verify_final(prompt))
                    if is_final and task.check_after_complete
                    else None
                ),
            )
            all_round_results.extend(round_results)
            total_rounds += rounds_used

            if end_reason != "COMPLETE":
                _log(f"[info] Phase {phase.upper()} stopped with {end_reason}; halting task.")
                return end_reason, total_rounds, all_round_results

    return "COMPLETE", total_rounds, all_round_results


def _run_single(
    task: TaskMetadata,
    env: dict,
    files_to_track: List[Path],
    session_log_root: Path,
) -> tuple:
    """Legacy single-prompt session. Returns (end_reason, rounds_used, round_results)."""
    prompt = task.get_prompt()

    def on_complete_callback() -> bool:
        if not task.check_after_complete:
            return True
        check_path = task.get_check_path()
        lean_files = (
            [check_path] if task.task_type == "file" and check_path.suffix == ".lean"
            else find_lean_files(check_path)
        )
        if not lean_files:
            return True
        _log(f"[info] Verifying {len(lean_files)} .lean files...")
        results = check_lean_files_parallel(lean_files)
        if task.allow_sorry:
            errors = [f for f, e, _, _, _ in results if e]
        else:
            errors = [f for f, e, s, _, _ in results if e or s]
        if errors:
            _log(f"\n[error] {len(errors)} files have errors:")
            for f in errors:
                _log(f"  - {f}")
            return False
        _log(f"[info] All {len(lean_files)} files verified successfully!")
        return True

    plan_path = None
    plan_mtime_before = 0
    if task.theorem_name:
        plan_path = task.target_path.parent / "refactor_plan.md"
        plan_mtime_before = plan_path.stat().st_mtime if plan_path.exists() else 0

    result_dir_path = Path(task.result_dir) if task.result_dir else None
    _log(f"[info] Session logs → {session_log_root}/round_N.txt")
    _log(f"[agent] {task.agent}")

    with windows_keepawake():
        end_reason, rounds_used, round_results = run_claude_session(
            prompt=prompt,
            cwd=task.cwd,
            output_format=task.output_format,
            agent=task.agent,
            max_rounds=task.max_rounds,
            env=env,
            on_complete=on_complete_callback if task.check_after_complete else None,
            result_dir=result_dir_path,
            task_id=task.task_id,
            files_to_track=files_to_track,
            session_log_dir=session_log_root,
        )

    if plan_path is not None and plan_mtime_before != 0:
        if plan_path.exists():
            if plan_path.stat().st_mtime == plan_mtime_before:
                    _log("[warn] refactor_plan.md was NOT modified during this session.")
        else:
            _log("[warn] refactor_plan.md does not exist after session.")

    if task.check_after_complete and end_reason != "COMPLETE":
        check_path = task.get_check_path()
        lean_files = (
            [check_path] if task.task_type == "file" and check_path.suffix == ".lean"
            else find_lean_files(check_path)
        )
        if lean_files:
            stop_label = end_reason or "non-COMPLETE stop"
            _log(f"\n[info] Reached {stop_label}, final verify on {len(lean_files)} .lean files...")
            results = check_lean_files_parallel(lean_files)
            if task.allow_sorry:
                errors = [f for f, e, _, _, _ in results if e]
            else:
                errors = [f for f, e, s, _, _ in results if e or s]
            if errors:
                _log(f"[error] {len(errors)} files have errors:")
                for f in errors:
                    _log(f"  - {f}")
            else:
                _log(f"[info] All {len(lean_files)} files verified successfully!")
                if not task.allow_sorry:
                    end_reason = "COMPLETE"

    return end_reason, rounds_used, round_results


def run_task(task: TaskMetadata) -> TaskResult:
    """
    Execute a single task.

    Args:
        task: Task metadata

    Returns:
        Task result
    """
    start_time = datetime.now()
    error_message = None
    mcp_stats = None
    round_results: List[RoundResult] = []
    run_dir = _run_dir(task)
    session_logs_dir = Path(task.session_logs_dir) if task.session_logs_dir else get_config().paths.session_logs_dir
    session_log_root = session_logs_dir / task.task_id

    try:
        env = task.build_env()
        env[ASK_SESSION_DIR_ENV] = str(session_log_root / "ask")

        if task.task_type == "file":
            files_to_track = [task.target_path]
        else:
            files_to_track = find_lean_files(task.target_path)

        metadata_path = write_run_metadata(task, run_dir, session_log_root)
        _log(f"[info] Run metadata → {metadata_path}")

        if task.phase_prompts:
            end_reason, rounds_used, round_results = _run_phased(
                task, env, files_to_track, session_log_root
            )
        else:
            end_reason, rounds_used, round_results = _run_single(
                task, env, files_to_track, session_log_root
            )

        # Auto-generate session stats from all phase logs under session_log_root.
        _log(f"[info] Generating session stats from {session_log_root}")
        round_durations = [rr.duration_seconds for rr in round_results]
        mcp_stats = analyze_session_logs(
            session_log_root,
            round_durations=round_durations,
        )

        success = end_reason == "COMPLETE"

    except Exception as e:
        end_reason = "ERROR"
        rounds_used = 0
        success = False
        error_message = str(e)
        print(f"[error] Task failed: {e}", file=sys.stderr)
    finally:
        if session_log_root.exists():
            try:
                collect_session_errors(session_log_root)
            except Exception:
                pass

    end_time = datetime.now()

    result = TaskResult(
        task_id=task.task_id,
        success=success,
        end_reason=end_reason,
        rounds_used=rounds_used,
        start_time=start_time,
        end_time=end_time,
        error_message=error_message,
        mcp_stats=mcp_stats,
        round_results=round_results,
    )

    _log(f"\n[info] Task {task.task_id} completed:")
    _log(f"  Success: {result.success}")
    _log(f"  End reason: {result.end_reason}")
    _log(f"  Rounds used: {result.rounds_used}")
    _log(f"  Duration: {result.duration_seconds:.1f}s")

    # Save final result to JSON if result_dir is specified
    if task.result_dir:
        result_path = Path(task.result_dir) / task.task_id
        result_path.mkdir(parents=True, exist_ok=True)

        result_file = result_path / "result.json"
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)

        _log(f"[info] Final result saved to {result_file}")

    try:
        write_run_metadata(task, run_dir, session_log_root, result=result)
    except Exception as exc:
        _log(f"[warn] Failed to update run metadata: {exc}")

    return result
