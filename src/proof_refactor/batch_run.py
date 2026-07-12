#!/usr/bin/env python3
"""Phase-only batch runner.

The batch config is read from YAML, normally `config/batch.yaml`. Inputs can be
individual Lean files or folders. Each source file gets its own JSONL event log,
while the final metadata JSON records output paths, session log paths, and time
statistics for every file.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import get_config, load_project_dotenv
from .phase import build_phase_format_kwargs, build_phase_prompts
from .runner import find_unique_run_dir, run_task, set_thread_log, clear_thread_log
from .task import TaskMetadata, TaskResult

load_project_dotenv()

@dataclass
class BatchItem:
    index: int
    total: int
    source_path: Path
    theorem_name: str
    task: TaskMetadata
    output_dir: Path
    work_file_path: Path
    refactor_plan_path: Path
    agent_logs_dir: Path
    session_log_dir: Path
    ask_session_dir: Path
    result_json_path: Path
    file_log_path: Path
    runner_log_path: Path


class JsonlLogger:
    def __init__(self, global_log_path: Path):
        self.global_log_path = global_log_path
        self.global_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: str, file_log_path: Path | None = None, **fields: Any) -> None:
        row = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with self._lock:
            self.global_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.global_log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            if file_log_path is not None:
                file_log_path.parent.mkdir(parents=True, exist_ok=True)
                with file_log_path.open("a", encoding="utf-8") as fh:
                    fh.write(line)


def _now() -> datetime:
    return datetime.now()


def _safe_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    return safe or "item"


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _resolve_path(raw: str | Path, config_root: Path, workspace_dir: Path, dataset_dir: Path) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()

    candidates = [
        config_root / path,
    ]
    if path.parts and path.parts[0].lower() == "dataset":
        rest = Path(*path.parts[1:]) if len(path.parts) > 1 else Path()
        candidates.append(dataset_dir / rest)
    candidates.extend([
        workspace_dir / path,
        dataset_dir / path,
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return (workspace_dir / path).resolve()


def _discover_from_input(
    raw_input: Any,
    config_root: Path,
    workspace_dir: Path,
    dataset_dir: Path,
    default_pattern: str,
    default_recursive: bool,
) -> list[Path]:
    if isinstance(raw_input, str):
        input_path = _resolve_path(raw_input, config_root, workspace_dir, dataset_dir)
        pattern = default_pattern
        recursive = default_recursive
    elif isinstance(raw_input, dict):
        raw_path = raw_input.get("path") or raw_input.get("source_file") or raw_input.get("source")
        if not raw_path:
            raise ValueError(f"Batch input is missing `path`: {raw_input!r}")
        input_path = _resolve_path(raw_path, config_root, workspace_dir, dataset_dir)
        pattern = raw_input.get("pattern", default_pattern)
        recursive = raw_input.get("recursive", default_recursive)
    else:
        raise TypeError(f"Unsupported batch input: {raw_input!r}")

    if input_path.is_file():
        if input_path.suffix != ".lean":
            return []
        return [input_path]
    if input_path.is_dir():
        iterator = input_path.rglob(pattern) if recursive else input_path.glob(pattern)
        return sorted(path.resolve() for path in iterator if path.is_file() and path.suffix == ".lean")
    raise FileNotFoundError(input_path)


def _task_source(task_entry: Any) -> str:
    if isinstance(task_entry, str):
        return task_entry
    if isinstance(task_entry, dict):
        raw = task_entry.get("source_file") or task_entry.get("path") or task_entry.get("source")
        if raw:
            return str(raw)
    raise ValueError(f"Task is missing `source_file`/`path`: {task_entry!r}")


def _discover_sources(app, cli_inputs: list[str] | None = None) -> tuple[list[Path], list[dict[str, Any]]]:
    batch = app.batch
    assert batch is not None

    sources: list[Path] = []
    overrides_by_source: dict[Path, dict[str, Any]] = {}

    input_entries: list[Any] = cli_inputs if cli_inputs else list(batch.inputs)
    if not input_entries and not batch.tasks:
        input_entries = [str(app.paths.dataset_dir)]

    for raw_input in input_entries:
        for path in _discover_from_input(
            raw_input,
            app.config_root,
            app.paths.workspace_dir,
            app.paths.dataset_dir,
            batch.pattern,
            batch.recursive,
        ):
            sources.append(path)

    for task_entry in batch.tasks:
        source = _resolve_path(
            _task_source(task_entry),
            app.config_root,
            app.paths.workspace_dir,
            app.paths.dataset_dir,
        )
        sources.append(source)
        if isinstance(task_entry, dict):
            overrides_by_source[source] = dict(task_entry)

    unique: list[Path] = []
    seen: set[str] = set()
    task_overrides: list[dict[str, Any]] = []
    for source in sources:
        key = str(source.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(source)
        task_overrides.append(overrides_by_source.get(source, {}))

    return unique, task_overrides


def _phase_settings(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    settings = {**defaults, **overrides}
    mode = settings.pop("mode", "phase")
    if mode != "phase":
        raise ValueError(f"Only phase mode is supported; remove `mode` or set it to `phase` (got {mode!r}).")
    return settings


def _settings_variant(settings: dict[str, Any]) -> str:
    """Extract `prompts_variant` from per-task settings (empty string falls back to config)."""
    return str(settings.get("prompts_variant", "") or "")


def _prepare_items(
    app,
    sources: list[Path],
    overrides: list[dict[str, Any]],
    batch_dir: Path,
    output_root: Path,
    reserve_outputs: bool = True,
) -> list[BatchItem]:
    batch = app.batch
    assert batch is not None

    items: list[BatchItem] = []
    used_log_names: set[str] = set()
    result_dir = batch_dir / "results"
    logs_dir = batch_dir / "logs"

    for index, (source_path, task_overrides) in enumerate(zip(sources, overrides), 1):
        if not source_path.exists():
            raise FileNotFoundError(source_path)
        if source_path.suffix != ".lean":
            raise ValueError(f"Not a Lean file: {source_path}")

        theorem_name = source_path.stem
        output_dir = find_unique_run_dir(output_root, theorem_name)
        if reserve_outputs:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / ".batch_source.json").write_text(
                json.dumps({"source_file": str(source_path)}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        settings = _phase_settings(batch.defaults, task_overrides)
        variant = _settings_variant(settings)
        fmt_kwargs = build_phase_format_kwargs(
            app,
            output_dir,
            theorem_name,
            source_path,
            create_dirs=reserve_outputs,
            variant=variant,
        )
        max_rounds = int(settings.get("max_rounds", 20))
        check_after_complete = bool(settings.get("check_after_complete", True))
        allow_sorry = bool(settings.get("allow_sorry", False))
        agent = str(settings.get("agent", "codex") or "codex")

        task_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        task = TaskMetadata(
            task_type="file",
            target_path=fmt_kwargs["work_file_path"],
            theorem_name=theorem_name,
            source_path=source_path,
            phase_prompts=build_phase_prompts(fmt_kwargs["prompt_dir"], fmt_kwargs),
            cwd=app.paths.workspace_dir,
            agent=agent,
            session_logs_dir=app.paths.session_logs_dir,
            max_rounds=max_rounds,
            check_after_complete=check_after_complete,
            allow_sorry=allow_sorry,
            result_dir=result_dir,
            output_format="stream-json",
            task_id=f"refactor_{theorem_name}_{task_stamp}",
        )

        log_name = _safe_name(theorem_name)
        if log_name in used_log_names:
            log_name = f"{log_name}_{index}"
        used_log_names.add(log_name)

        session_log_dir = app.paths.session_logs_dir / task.task_id
        file_log_dir = logs_dir / log_name
        items.append(
            BatchItem(
                index=index,
                total=len(sources),
                source_path=source_path,
                theorem_name=theorem_name,
                task=task,
                output_dir=output_dir,
                work_file_path=fmt_kwargs["work_file_path"],
                refactor_plan_path=fmt_kwargs["refactor_plan_path"],
                agent_logs_dir=fmt_kwargs["agent_logs_dir"],
                session_log_dir=session_log_dir,
                ask_session_dir=session_log_dir / "ask",
                result_json_path=result_dir / task.task_id / "result.json",
                file_log_path=file_log_dir / "events.jsonl",
                runner_log_path=file_log_dir / "runner.log",
            )
        )

    return items


def _initial_entry(item: BatchItem, app) -> dict[str, Any]:
    return {
        "index": item.index,
        "source_file": str(item.source_path),
        "source_file_relative": _rel(item.source_path, app.paths.workspace_dir),
        "theorem_name": item.theorem_name,
        "task_id": item.task.task_id,
        "status": "queued",
        "success": None,
        "output_dir": str(item.output_dir),
        "work_file_path": str(item.work_file_path),
        "refactor_plan_path": str(item.refactor_plan_path),
        "agent_logs_dir": str(item.agent_logs_dir),
        "run_metadata_path": str(item.output_dir / "run_metadata.json"),
        "run_log_path": str(item.output_dir / "run_log.md"),
        "session_log_dir": str(item.session_log_dir),
        "ask_session_dir": str(item.ask_session_dir),
        "session_stats_path": str(item.session_log_dir / "stats.json"),
        "result_json_path": str(item.result_json_path),
        "batch_file_log_path": str(item.file_log_path),
        "runner_log_path": str(item.runner_log_path),
        "agent": item.task.agent,
        "max_rounds": item.task.max_rounds,
    }


def _metadata_template(
    app,
    config_file: str,
    batch_id: str,
    batch_dir: Path,
    metadata_path: Path,
    events_log_path: Path,
    items: list[BatchItem],
    concurrency: int,
) -> dict[str, Any]:
    started_at = _now()
    return {
        "batch_id": batch_id,
        "config": config_file,
        "started_at": started_at.isoformat(),
        "ended_at": None,
        "duration_seconds": None,
        "concurrency": concurrency,
        "paths": {
            "batch_dir": str(batch_dir),
            "metadata_path": str(metadata_path),
            "events_log_path": str(events_log_path),
            "logs_dir": str(batch_dir / "logs"),
            "results_dir": str(batch_dir / "results"),
        },
        "summary": {
            "total": len(items),
            "queued": len(items),
            "running": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "dry_run": 0,
        },
        "results": [_initial_entry(item, app) for item in items],
    }


def _summarize(meta: dict[str, Any]) -> None:
    counts = {"queued": 0, "running": 0, "success": 0, "failed": 0, "skipped": 0, "dry_run": 0}
    for result in meta["results"]:
        status = result.get("status")
        if status in counts:
            counts[status] += 1
    meta["summary"].update(counts)


def _finish_entry(item: BatchItem, app, result: TaskResult, started_at: datetime, ended_at: datetime) -> dict[str, Any]:
    entry = _initial_entry(item, app)
    entry.update(
        {
            "status": "success" if result.success else "failed",
            "success": result.success,
            "end_reason": result.end_reason,
            "rounds_used": result.rounds_used,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": (ended_at - started_at).total_seconds(),
            "error_message": result.error_message,
            "mcp_stats": result.mcp_stats,
            "round_results": [rr.to_dict() for rr in result.round_results],
        }
    )
    return entry


def _run_item(
    item: BatchItem,
    app,
    logger: JsonlLogger,
    heartbeat_seconds: int,
    update_metadata: Callable[[int, dict[str, Any]], None],
) -> dict[str, Any]:
    started_at = _now()
    logger.write(
        "start",
        item.file_log_path,
        index=item.index,
        total=item.total,
        source_file=str(item.source_path),
        theorem_name=item.theorem_name,
        task_id=item.task.task_id,
        output_dir=str(item.output_dir),
        session_log_dir=str(item.session_log_dir),
    )
    update_metadata(
        item.index - 1,
        {
            "status": "running",
            "started_at": started_at.isoformat(),
        },
    )

    stop_heartbeat = threading.Event()

    def heartbeat() -> None:
        while not stop_heartbeat.wait(max(1, heartbeat_seconds)):
            logger.write(
                "heartbeat",
                item.file_log_path,
                index=item.index,
                total=item.total,
                source_file=str(item.source_path),
                theorem_name=item.theorem_name,
                task_id=item.task.task_id,
                elapsed_seconds=round((_now() - started_at).total_seconds(), 1),
                output_dir=str(item.output_dir),
                session_log_dir=str(item.session_log_dir),
            )

    heartbeat_thread: threading.Thread | None = None
    if heartbeat_seconds > 0:
        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()

    item.runner_log_path.parent.mkdir(parents=True, exist_ok=True)
    runner_log_fh = open(item.runner_log_path, "w", encoding="utf-8")
    set_thread_log(runner_log_fh)
    try:
        result = run_task(item.task)
    finally:
        clear_thread_log()
        runner_log_fh.close()
        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2)

    ended_at = _now()
    entry = _finish_entry(item, app, result, started_at, ended_at)
    logger.write(
        "finish",
        item.file_log_path,
        index=item.index,
        total=item.total,
        source_file=str(item.source_path),
        theorem_name=item.theorem_name,
        task_id=item.task.task_id,
        status=entry["status"],
        success=result.success,
        end_reason=result.end_reason,
        rounds_used=result.rounds_used,
        duration_seconds=entry["duration_seconds"],
        output_dir=str(item.output_dir),
        session_log_dir=str(item.session_log_dir),
        result_json_path=str(item.result_json_path),
    )
    return entry


def run_from_config(
    config_file: str = "batch",
    dry_run: bool = False,
    concurrency: int | None = None,
    inputs: list[str] | None = None,
    workspace: str = "",
    prompts_dir: str = "",
    agent: str = "",
) -> int:
    try:
        app = get_config(config_file, workspace_dir=workspace, prompts_dir=prompts_dir)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    if app.batch is None:
        print("[error] No `batch` section in config.", file=sys.stderr)
        return 1

    batch = app.batch
    if agent:
        batch.defaults = {**batch.defaults, "agent": agent}
    sources, overrides = _discover_sources(app, cli_inputs=inputs)
    if not sources:
        print("[warn] No Lean source files found.")
        return 0

    actual_concurrency = max(1, int(concurrency or batch.concurrency or 1))
    output_root = batch.output_root or (app.paths.output_dir / "phase")
    output_root.mkdir(parents=True, exist_ok=True)

    batch_id = datetime.now().strftime("batch_%Y%m%d_%H%M%S_%f")
    batch_dir = output_root / "_batch_runs" / batch_id
    metadata_path = batch.metadata_path or (batch_dir / "metadata.json")
    events_log_path = batch_dir / "batch_events.jsonl"

    items = _prepare_items(app, sources, overrides, batch_dir, output_root, reserve_outputs=not dry_run)
    metadata = _metadata_template(
        app=app,
        config_file=config_file,
        batch_id=batch_id,
        batch_dir=batch_dir,
        metadata_path=metadata_path,
        events_log_path=events_log_path,
        items=items,
        concurrency=actual_concurrency,
    )

    metadata_lock = threading.Lock()

    def update_metadata(index: int, patch: dict[str, Any]) -> None:
        with metadata_lock:
            metadata["results"][index].update(patch)
            _summarize(metadata)
            _atomic_write_json(metadata_path, metadata)

    logger = JsonlLogger(events_log_path)
    _atomic_write_json(metadata_path, metadata)

    print(f"[batch] Loaded {len(items)} file(s)")
    print(f"[batch] Concurrency: {actual_concurrency}")
    print(f"[batch] Metadata: {metadata_path}")
    print(f"[batch] Events:   {events_log_path}")

    if dry_run:
        ended_at = _now()
        metadata["ended_at"] = ended_at.isoformat()
        metadata["duration_seconds"] = (ended_at - datetime.fromisoformat(metadata["started_at"])).total_seconds()
        for result in metadata["results"]:
            result["status"] = "dry_run"
        _summarize(metadata)
        _atomic_write_json(metadata_path, metadata)
        for item in items:
            print(f"  [dry-run] {item.index}/{item.total} {item.source_path} -> {item.output_dir}")
        logger.write("dry_run", total=len(items), metadata_path=str(metadata_path))
        return 0

    started_at = datetime.fromisoformat(metadata["started_at"])
    failed = 0
    consecutive_failures = 0
    max_consecutive = batch.max_consecutive_failures
    logger.write("batch_start", total=len(items), concurrency=actual_concurrency, metadata_path=str(metadata_path))

    with concurrent.futures.ThreadPoolExecutor(max_workers=actual_concurrency) as pool:
        future_to_item = {
            pool.submit(_run_item, item, app, logger, batch.heartbeat_seconds, update_metadata): item
            for item in items
        }
        cancelled = False
        for future in concurrent.futures.as_completed(future_to_item):
            item = future_to_item[future]
            try:
                entry = future.result()
            except Exception as exc:
                ended_at = _now()
                failed += 1
                consecutive_failures += 1
                entry = _initial_entry(item, app)
                entry.update(
                    {
                        "status": "failed",
                        "success": False,
                        "started_at": None,
                        "ended_at": ended_at.isoformat(),
                        "duration_seconds": None,
                        "end_reason": "ERROR",
                        "error_message": str(exc),
                    }
                )
                logger.write(
                    "error",
                    item.file_log_path,
                    index=item.index,
                    total=item.total,
                    source_file=str(item.source_path),
                    theorem_name=item.theorem_name,
                    task_id=item.task.task_id,
                    error=str(exc),
                )
            else:
                if entry.get("success"):
                    consecutive_failures = 0
                else:
                    failed += 1
                    end_reason = entry.get("end_reason", "")
                    if end_reason in ("AUTH_ERROR", "ERROR"):
                        consecutive_failures += 1
                    else:
                        consecutive_failures = 0

            with metadata_lock:
                metadata["results"][item.index - 1] = entry
                _summarize(metadata)
                _atomic_write_json(metadata_path, metadata)

            status = "SUCCESS" if entry.get("success") else "FAILED"
            print(
                f"[{status}] {item.index}/{item.total} {item.theorem_name} "
                f"({entry.get('duration_seconds') or 0:.1f}s, {entry.get('rounds_used', 0)} rounds)"
            )

            if consecutive_failures >= max_consecutive and not cancelled:
                cancelled = True
                print(f"[batch] {consecutive_failures} consecutive failures — cancelling remaining tasks")
                logger.write("early_stop", reason=f"{consecutive_failures} consecutive failures", failed=failed)
                for f_remaining in future_to_item:
                    f_remaining.cancel()

    ended_at = _now()
    with metadata_lock:
        metadata["ended_at"] = ended_at.isoformat()
        metadata["duration_seconds"] = (ended_at - started_at).total_seconds()
        _summarize(metadata)
        _atomic_write_json(metadata_path, metadata)

    logger.write(
        "batch_finish",
        total=len(items),
        failed=failed,
        duration_seconds=metadata["duration_seconds"],
        metadata_path=str(metadata_path),
    )
    print(f"[batch] Done: {metadata['summary']['success']} success, {failed} failed")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run phase-mode proof refactoring over YAML-configured files/folders.")
    parser.add_argument("config", nargs="?", default="batch", help="Config overlay/path. Default: batch")
    parser.add_argument("-j", "--concurrency", type=int, default=None, help="Override batch.concurrency")
    parser.add_argument("--input", action="append", default=None, help="Override YAML inputs with a file/folder path")
    parser.add_argument("--agent", default="", help="Agent backend: codex (default) or claude")
    parser.add_argument("--workspace", default="", help="Lean workspace root override")
    parser.add_argument("--prompts-dir", default="", help="External prompt root override")
    parser.add_argument("--dry-run", action="store_true", help="List resolved files and write initial metadata only")
    args = parser.parse_args(argv)
    return run_from_config(
        config_file=args.config,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
        inputs=args.input,
        agent=args.agent,
        workspace=args.workspace,
        prompts_dir=args.prompts_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
