"""Per-run metadata and human-readable log index helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .task import TaskMetadata, TaskResult


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, data: dict) -> None:
    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_recorded_source_path(run_dir: Path) -> Path | None:
    """Return the newest original source path recorded for a run directory."""
    path = run_dir / "run_metadata.json"
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    runs = [entry for entry in payload.get("runs", []) if isinstance(entry, dict)]
    for record in reversed(runs):
        raw_path = record.get("source_path")
        if isinstance(raw_path, str) and raw_path.strip():
            return Path(raw_path).expanduser()
    return None


def _run_metadata_record(
    task: TaskMetadata,
    run_dir: Path,
    session_log_root: Path,
    result: Optional[TaskResult] = None,
) -> dict:
    result_json_path = (
        Path(task.result_dir) / task.task_id / "result.json"
        if task.result_dir
        else None
    )
    record = {
        "task_id": task.task_id,
        "theorem_name": task.theorem_name,
        "task_type": task.task_type,
        "source_path": str(task.source_path) if task.source_path else None,
        "target_path": str(task.target_path),
        "cwd": str(task.cwd) if task.cwd else None,
        "agent": task.agent,
        "created_at": task.created_at.isoformat(),
        "phased": bool(task.phase_prompts),
        "phases": list(task.phase_prompts) if task.phase_prompts else [],
        "phase_name": task.phase_name,
        "run_dir": str(run_dir),
        "session_log_dir": str(session_log_root),
        "ask_session_dir": str(session_log_root / "ask"),
        "session_stats_path": str(session_log_root / "stats.json"),
        "session_errors_path": str(session_log_root / "errors.json"),
        "result_dir": str(task.result_dir) if task.result_dir else None,
        "result_json_path": str(result_json_path) if result_json_path else None,
        "status": "running",
    }
    if result is not None:
        record.update(
            {
                "status": "success" if result.success else "failed",
                "success": result.success,
                "end_reason": result.end_reason,
                "rounds_used": result.rounds_used,
                "duration_seconds": result.duration_seconds,
                "error_message": result.error_message,
                "ended_at": result.end_time.isoformat(),
            }
        )
    return record


def _relative_link(label: str, raw_path: str | None, run_dir: Path) -> str:
    if not raw_path:
        return "-"
    try:
        target = os.path.relpath(Path(raw_path), run_dir)
    except ValueError:
        target = str(raw_path)
    target = target.replace("\\", "/").replace(" ", "%20")
    return f"[{label}]({target})"


def _run_log_row(record: dict, run_dir: Path) -> str:
    created_at = record.get("created_at") or "-"
    task_id = record.get("task_id") or "-"
    status = record.get("status") or "unknown"
    phases = _phase_summary(record)
    session = _relative_link("logs", record.get("session_log_dir"), run_dir)
    ask = _relative_link("ask", record.get("ask_session_dir"), run_dir)
    stats = _relative_link("stats", record.get("session_stats_path"), run_dir)
    result = _relative_link("result", record.get("result_json_path"), run_dir)
    return f"| `{created_at}` | `{task_id}` | `{status}` | `{phases}` | {session} | {ask} | {stats} | {result} |"


def _phase_summary(record: dict) -> str:
    phase_name = record.get("phase_name")
    if phase_name:
        return str(phase_name)
    phases = ", ".join(str(phase) for phase in record.get("phases", []))
    return phases or "single session"


def _render_run_log(payload: dict, run_dir: Path) -> str:
    runs = [entry for entry in payload.get("runs", []) if isinstance(entry, dict)]
    latest_task_id = payload.get("latest_task_id")
    latest = next(
        (entry for entry in reversed(runs) if entry.get("task_id") == latest_task_id),
        runs[-1] if runs else None,
    )

    lines = [
        "# Run Log",
        "",
        "Human-readable links for this theorem run. `run_metadata.json` is the machine-readable source/task/phase/session-state index.",
        "",
        "## Latest",
        "",
    ]
    if latest is None:
        lines.append("No task has been recorded yet.")
    else:
        phases = _phase_summary(latest)
        lines.extend(
            [
                f"- Task: `{latest.get('task_id') or '-'}`",
                f"- Status: `{latest.get('status') or 'unknown'}`",
                f"- Phases: `{phases}`",
                f"- Session logs: {_relative_link('directory', latest.get('session_log_dir'), run_dir)}",
                f"- Ask sessions: {_relative_link('directory', latest.get('ask_session_dir'), run_dir)}",
                f"- Stats: {_relative_link('stats.json', latest.get('session_stats_path'), run_dir)}",
                f"- Errors: {_relative_link('errors.json', latest.get('session_errors_path'), run_dir)}",
                f"- Result: {_relative_link('result.json', latest.get('result_json_path'), run_dir)}",
            ]
        )

    lines.extend(
        [
            "",
            "## History",
            "",
            "| Created | Task | Status | Phase | Session | Ask | Stats | Result |",
            "|---------|------|--------|-------|---------|-----|-------|--------|",
        ]
    )
    if runs:
        lines.extend(_run_log_row(record, run_dir) for record in reversed(runs))
    else:
        lines.append("| - | - | - | - | - | - | - | - |")
    lines.append("")
    return "\n".join(lines)


def write_run_metadata(
    task: TaskMetadata,
    run_dir: Path,
    session_log_root: Path,
    result: Optional[TaskResult] = None,
) -> Path:
    """Update machine and Markdown indexes for a theorem run directory."""
    path = run_dir / "run_metadata.json"
    payload: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            payload = loaded

    runs = [
        entry
        for entry in payload.get("runs", [])
        if isinstance(entry, dict) and entry.get("task_id") != task.task_id
    ]
    runs.append(_run_metadata_record(task, run_dir, session_log_root, result=result))

    payload = {
        "run_dir": str(run_dir),
        "latest_task_id": task.task_id,
        "runs": runs,
    }
    _atomic_write_json(path, payload)
    _atomic_write_text(run_dir / "run_log.md", _render_run_log(payload, run_dir))
    return path
