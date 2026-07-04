"""Proof-Refactor command-line entrypoint."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import fire

from .config import get_config, load_project_dotenv
from .phase import PHASE_FILES, build_phase_format_kwargs, build_phase_prompts, format_phase_prompt
from .run_metadata import read_recorded_source_path
from .runner import find_unique_run_dir, run_task
from .task import TaskMetadata

VALID_MODES = ("phase",)


def _finish(code: int) -> None:
    """Expose failures as process exits without printing success return values."""
    if code:
        raise SystemExit(code)


def _load_config(workspace: str = "", prompts_dir: str = ""):
    try:
        return get_config(workspace_dir=workspace, prompts_dir=prompts_dir)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(2) from None


def _resolve_source_path(source_file: str, workspace_dir: Path) -> Path:
    """Resolve source paths relative to the configured Lean workspace."""
    raw = Path(source_file)
    if raw.is_absolute():
        return raw.resolve()
    return (workspace_dir / raw).resolve()


def _resolve_run_dir(raw: str, phase_root: Path) -> Path:
    """Resolve a run dir name under output/phase, or accept an explicit path."""
    candidate = Path(raw)
    if candidate.exists():
        return candidate.resolve()

    direct = (phase_root / raw).resolve()
    if direct.exists():
        return direct

    def normalize_name(name: str) -> str:
        return re.sub(r"[\s_\-]+", "_", name.strip().lower())

    wanted = normalize_name(raw)
    matches = [
        entry.resolve()
        for entry in phase_root.iterdir()
        if entry.is_dir() and normalize_name(entry.name) == wanted
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        raise RuntimeError(f"Ambiguous run dir name `{raw}`. Matches: {names}")

    suggestions = [
        entry.name
        for entry in phase_root.iterdir()
        if entry.is_dir() and wanted in normalize_name(entry.name)
    ]
    if suggestions:
        preview = ", ".join(sorted(suggestions)[:8])
        raise FileNotFoundError(f"No run dir `{raw}`. Did you mean: {preview}?")
    raise FileNotFoundError(f"No run dir `{raw}` under {phase_root}")


def _find_work_file(run_dir: Path) -> Path:
    matches = sorted(run_dir.glob("*_work.lean"))
    if not matches:
        raise FileNotFoundError(f"No *_work.lean found in {run_dir}")
    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        raise RuntimeError(f"Expected exactly one *_work.lean in {run_dir}, found: {names}")
    return matches[0]


def _theorem_name_from_work_file(work_file: Path) -> str:
    stem = work_file.stem
    return stem[:-5] if stem.endswith("_work") else stem


class ProofRunner:
    """Proof refactoring CLI."""

    def run(
        self,
        source_file: str,
        max_rounds: int = 5,
        mode: str = "phase",
        variant: str = "",
        workspace: str = "",
        prompts_dir: str = "",
        agent: str = "codex",
    ) -> None:
        """Refactor a single Lean proof in the configured workspace."""
        cfg = _load_config(workspace, prompts_dir)

        if mode not in VALID_MODES:
            print(f"[error] Unknown mode: {mode!r}. Valid: {VALID_MODES}", file=sys.stderr)
            _finish(1)

        source_path = _resolve_source_path(source_file, cfg.paths.workspace_dir)
        theorem_name = source_path.stem

        theorem_dir = find_unique_run_dir(cfg.paths.output_dir / mode, theorem_name)
        theorem_dir.mkdir(parents=True, exist_ok=True)

        fmt_kwargs = build_phase_format_kwargs(cfg, theorem_dir, theorem_name, source_path, variant=variant)
        task = TaskMetadata(
            task_type="file",
            target_path=fmt_kwargs["work_file_path"],
            theorem_name=theorem_name,
            source_path=source_path,
            phase_prompts=build_phase_prompts(fmt_kwargs["prompt_dir"], fmt_kwargs),
            cwd=cfg.paths.workspace_dir,
            agent=agent,
            session_logs_dir=cfg.paths.session_logs_dir,
            max_rounds=max_rounds,
            check_after_complete=True,
            allow_sorry=False,
            output_format="stream-json",
        )

        result = run_task(task)
        print(f"{'SUCCESS' if result.success else 'FAILED'} in {result.rounds_used} round(s)")
        _finish(0 if result.success else 1)

    def phase(
        self,
        phase_name: str,
        run_dir: str,
        max_rounds: int = 5,
        variant: str = "",
        workspace: str = "",
        prompts_dir: str = "",
        agent: str = "codex",
    ) -> None:
        """Re-run one phase against an existing run directory."""
        cfg = _load_config(workspace, prompts_dir)

        if phase_name not in PHASE_FILES:
            print(
                f"[error] Unknown phase: {phase_name!r}. Valid: {list(PHASE_FILES)}",
                file=sys.stderr,
            )
            _finish(1)

        phase_root = (cfg.paths.output_dir / "phase").resolve()
        try:
            theorem_dir = _resolve_run_dir(run_dir, phase_root)
            work_file = _find_work_file(theorem_dir)
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"[error] {exc}", file=sys.stderr)
            _finish(1)

        theorem_name = _theorem_name_from_work_file(work_file)

        recorded_source = read_recorded_source_path(theorem_dir)
        candidate_source = recorded_source or (cfg.paths.dataset_dir / f"{theorem_name}.lean").resolve()
        source_path = candidate_source.resolve() if candidate_source.exists() else None
        if phase_name == "extract" and source_path is None:
            origin = f"recorded source `{recorded_source}`" if recorded_source else "run metadata or dataset fallback"
            print(f"[error] Extract phase requires an existing source file from {origin}.", file=sys.stderr)
            _finish(1)

        fmt_kwargs = build_phase_format_kwargs(cfg, theorem_dir, theorem_name, source_path, variant=variant)
        prompt_path = fmt_kwargs["prompt_dir"] / PHASE_FILES[phase_name]
        prompt = format_phase_prompt(prompt_path.read_text(encoding="utf-8"), fmt_kwargs)

        task = TaskMetadata(
            task_type="file",
            target_path=work_file,
            theorem_name=theorem_name,
            source_path=source_path,
            phase_name=phase_name,
            prompt=prompt,
            cwd=cfg.paths.workspace_dir,
            agent=agent,
            session_logs_dir=cfg.paths.session_logs_dir,
            max_rounds=max_rounds,
            check_after_complete=True,
            allow_sorry=False,
            output_format="stream-json",
        )

        print(f"[phase] {phase_name} on {theorem_dir}")
        result = run_task(task)
        print(f"{'SUCCESS' if result.success else 'FAILED'} in {result.rounds_used} round(s)")
        _finish(0 if result.success else 1)

    def batch(
        self,
        config_file: str | None = None,
        dry_run: bool = False,
        concurrency: int | None = None,
        input: list[str] | None = None,
        workspace: str = "",
        prompts_dir: str = "",
        agent: str = "",
    ) -> None:
        """Run the phase-mode batch config. Defaults to the `batch` overlay."""
        from .batch_run import run_from_config

        inputs = [input] if isinstance(input, str) else input
        _finish(run_from_config(
            config_file or "batch",
            dry_run=dry_run,
            concurrency=concurrency,
            inputs=inputs,
            agent=agent,
            workspace=workspace,
            prompts_dir=prompts_dir,
        ))

    def ask(self, profile: str, input_file: str, prompts_dir: str = "") -> None:
        """Run one packaged external-ask profile over an input file."""
        from .ask import run_profile_cli

        _finish(run_profile_cli(profile, input_file, prompts_dir))

    def parse_extract_jobs(self, extract_output: str) -> None:
        """Parse extract Markdown into JSON jobs."""
        from .parse_extract_jobs import parse_extract_jobs_cli

        _finish(parse_extract_jobs_cli(extract_output))


def main() -> None:
    load_project_dotenv()
    fire.Fire(ProofRunner)
