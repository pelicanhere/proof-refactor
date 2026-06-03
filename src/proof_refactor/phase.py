"""Shared prompt formatting for phase-mode runs."""

from datetime import datetime
from pathlib import Path
from typing import Any

from .config import AppConfig, resolve_variant_dir
from .lean_code_parser import extract_top_level_theorem_lemma_index

PHASE_FILES = {
    "extract": "extract.md",
    "design": "design.md",
    "prove": "proof.md",
    "repair": "repair.md",
}


def _workspace_rel(path: Path, workspace_dir: Path) -> str:
    try:
        return path.resolve().relative_to(workspace_dir.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _format_decl_seed(source_path: Path) -> str:
    code = source_path.read_text(encoding="utf-8")
    decls = extract_top_level_theorem_lemma_index(code)
    if not decls:
        return "- (no top-level theorem/lemma declarations found)"
    return "\n".join(
        f"- {decl['name']} | {decl['kind']} | {decl['start_line']}-{decl['end_line']} | section={decl['section']}"
        for decl in decls
    )


def build_phase_format_kwargs(
    cfg: AppConfig,
    run_dir: Path,
    theorem_name: str,
    source_path: Path | None,
    *,
    variant: str = "",
    create_dirs: bool = True,
) -> dict[str, Any]:
    """Build the placeholder values shared by all phase prompt variants."""
    work_file_path = run_dir / f"{theorem_name}_work.lean"
    refactor_plan_path = run_dir / "refactor_plan.md"
    agent_logs_dir = run_dir / "agent_logs"
    prompt_dir = resolve_variant_dir(cfg, variant)
    if create_dirs:
        agent_logs_dir.mkdir(parents=True, exist_ok=True)

    if source_path is not None and source_path.exists():
        source_rel = _workspace_rel(source_path, cfg.paths.workspace_dir)
        decl_seed_block = _format_decl_seed(source_path)
    else:
        source_rel = ""
        decl_seed_block = ""

    work_file_rel = _workspace_rel(work_file_path, cfg.paths.workspace_dir)
    plan_rel = _workspace_rel(refactor_plan_path, cfg.paths.workspace_dir)
    agent_logs_dir_rel = _workspace_rel(agent_logs_dir, cfg.paths.workspace_dir)
    phase_dir_rel = _workspace_rel(run_dir, cfg.paths.workspace_dir)
    work_module = (
        work_file_rel[:-5].replace("/", ".")
        if work_file_rel.endswith(".lean")
        else work_file_rel.replace("/", ".")
    )

    return {
        "source_file": source_path,
        "theorem_name": theorem_name,
        "work_file_path": work_file_path,
        "refactor_plan_path": refactor_plan_path,
        "agent_logs_dir": agent_logs_dir.resolve(),
        "prompt_dir": prompt_dir.resolve(),
        "prompts_root": prompt_dir.parent.resolve(),
        "project_root": cfg.config_root,
        "source_rel": source_rel,
        "work_file_rel": work_file_rel,
        "work_module": work_module,
        "plan_rel": plan_rel,
        "agent_logs_dir_rel": agent_logs_dir_rel,
        "phase_dir_rel": phase_dir_rel,
        "run_dir": run_dir,
        "run_stamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "decl_seed_block": decl_seed_block,
    }


def build_phase_prompts(prompt_dir: Path, fmt_kwargs: dict[str, Any]) -> dict[str, str]:
    """Read and format every phase prompt in order."""
    return {
        phase: format_phase_prompt((prompt_dir / filename).read_text(encoding="utf-8"), fmt_kwargs)
        for phase, filename in PHASE_FILES.items()
    }


def format_phase_prompt(template: str, fmt_kwargs: dict[str, Any]) -> str:
    """Render prompt paths with shell-safe slash separators on every platform."""
    values = {
        key: value.as_posix() if isinstance(value, Path) else value
        for key, value in fmt_kwargs.items()
    }
    return template.format(**values)
