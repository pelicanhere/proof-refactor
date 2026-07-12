"""
Task metadata and result definitions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Literal, Optional, List
import os

from .config import load_project_dotenv

AgentName = Literal["codex", "claude"]


@dataclass
class TaskMetadata:
    """Task metadata for agent-driven Lean proving tasks."""

    # Required fields
    task_type: Literal["file", "folder"]  # Task type
    target_path: str | Path  # Target path (file or folder)

    # Optional fields - Prompt (one of these must be provided)
    prompt: Optional[str] = None  # Direct prompt content
    prompt_file: Optional[str | Path] = None  # Read prompt from file

    # Optional fields - Execution parameters
    cwd: Optional[str | Path] = None  # Agent working directory
    max_rounds: int = 20  # Maximum rounds (continue count limit)
    check_after_complete: bool = True  # Whether to check lean files after completion
    allow_sorry: bool = False  # Whether to allow sorry in lean files (default: False)

    # Optional fields - Result output
    result_dir: Optional[str | Path] = None  # Result output directory (JSON files)
    session_logs_dir: Optional[str | Path] = None  # Base directory for per-task session logs
    output_format: Optional[str] = None  # Agent output format (usually stream-json / None)

    # Phase prompts: if set, runner uses phased flow (extract/design/prove/repair)
    # Keys must be exactly: "extract", "design", "prove", "repair"
    phase_prompts: Optional[Dict[str, str]] = None

    # Task identification
    theorem_name: Optional[str] = None  # Theorem name for this task (used in logging)
    source_path: Optional[str | Path] = None  # Original source file, before work-file copying
    phase_name: Optional[str] = None  # Explicit phase name for single-phase reruns

    # Auto-generated fields
    created_at: datetime = field(default_factory=datetime.now)
    task_id: str = field(default="")  # Auto-generated unique ID
    # Kept after existing fields so older positional TaskMetadata calls remain compatible.
    agent: AgentName = "codex"  # "codex" (default) or legacy "claude"

    def __post_init__(self):
        self.agent = str(self.agent).strip().lower()  # type: ignore[assignment]
        if self.agent not in ("codex", "claude"):
            raise ValueError(f"Unsupported agent: {self.agent!r}. Expected 'codex' or 'claude'.")

        # Auto-generate task_id
        if not self.task_id:
            timestamp = self.created_at.strftime("%Y%m%d_%H%M%S")
            target_name = self.theorem_name or Path(self.target_path).stem
            self.task_id = f"refactor_{target_name}_{timestamp}"

        # Normalize paths
        self.target_path = Path(self.target_path).resolve()
        if self.cwd:
            self.cwd = Path(self.cwd).resolve()
        if self.prompt_file:
            self.prompt_file = Path(self.prompt_file).resolve()
        if self.source_path:
            self.source_path = Path(self.source_path).resolve()
        if self.result_dir:
            self.result_dir = Path(self.result_dir).resolve()
        if self.session_logs_dir:
            self.session_logs_dir = Path(self.session_logs_dir).resolve()

    def get_prompt(self) -> str:
        """Get prompt content from prompt or prompt_file."""
        if self.prompt:
            return self.prompt.strip()
        elif self.prompt_file and Path(self.prompt_file).exists():
            return Path(self.prompt_file).read_text(encoding="utf-8").strip()
        else:
            raise ValueError("Either prompt or prompt_file must be provided")

    def get_check_path(self) -> Path:
        """Get the path to check for lean files."""
        return Path(self.target_path)

    def build_env(self) -> dict:
        """Build environment variables (including project .env and MCP log settings)."""
        load_project_dotenv()
        env = os.environ.copy()
        # Remove CLAUDECODE so nested `claude` subprocesses are not blocked
        env.pop("CLAUDECODE", None)
        # Avoid leaking this Codex controller thread into nested `codex exec` runs.
        env.pop("CODEX_THREAD_ID", None)
        return env

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "target_path": str(self.target_path),
            "prompt": self.prompt,
            "prompt_file": str(self.prompt_file) if self.prompt_file else None,
            "cwd": str(self.cwd) if self.cwd else None,
            "agent": self.agent,
            "max_rounds": self.max_rounds,
            "check_after_complete": self.check_after_complete,
            "allow_sorry": self.allow_sorry,
            "result_dir": str(self.result_dir) if self.result_dir else None,
            "session_logs_dir": str(self.session_logs_dir) if self.session_logs_dir else None,
            "output_format": self.output_format,
            "theorem_name": self.theorem_name,
            "source_path": str(self.source_path) if self.source_path else None,
            "phase_name": self.phase_name,
            "created_at": self.created_at.isoformat(),
        }

@dataclass
class RoundResult:
    """Result of a single agent round."""

    round_number: int
    stdout: str
    end_reason: Optional[str]  # COMPLETE / LIMIT / AUTH_ERROR / None
    returncode: int
    duration_seconds: float = 0.0
    line_counts: dict = field(default_factory=dict)  # {filename: line_count}
    token_usage: dict = field(default_factory=dict)  # {input_tokens, output_tokens, cache_*}

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "round_number": self.round_number,
            "end_reason": self.end_reason,
            "returncode": self.returncode,
            "duration_seconds": self.duration_seconds,
            "line_counts": self.line_counts,
            "token_usage": self.token_usage,
        }


@dataclass
class TaskResult:
    """Task execution result."""

    task_id: str
    success: bool
    end_reason: Optional[str]  # COMPLETE / LIMIT / AUTH_ERROR / ERROR
    rounds_used: int
    start_time: datetime
    end_time: datetime
    error_message: Optional[str] = None
    mcp_stats: Optional[dict] = None  # MCP tool call statistics
    round_results: List[RoundResult] = field(default_factory=list)  # Per-round results

    @property
    def duration_seconds(self) -> float:
        """Get duration in seconds."""
        return (self.end_time - self.start_time).total_seconds()

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_id": self.task_id,
            "success": self.success,
            "end_reason": self.end_reason,
            "rounds_used": self.rounds_used,
            "duration_seconds": self.duration_seconds,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "error_message": self.error_message,
            "mcp_stats": self.mcp_stats,
            "round_results": [rr.to_dict() for rr in self.round_results],
        }
