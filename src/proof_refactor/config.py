"""Shared configuration for the packaged Proof-Refactor runtime."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import find_dotenv, load_dotenv

# Semantic overlay name -> config filename.
_OVERLAYS: dict[str, str] = {
    "batch": "batch.yaml",
}


def bundled_prompts_dir() -> Path:
    """Return the packaged prompt root."""
    return Path(__file__).resolve().parent / "prompts"


def load_project_dotenv() -> Path | None:
    """Load the nearest `.env` without overriding explicit environment variables."""
    raw_path = find_dotenv(usecwd=True)
    if not raw_path:
        return None

    dotenv_path = Path(raw_path)
    load_dotenv(dotenv_path, override=False)
    return dotenv_path


@dataclass(frozen=True)
class PathsConfig:
    workspace_dir: Path
    prompts_dir: Path | None
    prompts_variant: str
    output_dir: Path
    dataset_dir: Path
    session_logs_dir: Path


@dataclass
class BatchConfig:
    defaults: dict[str, Any]
    tasks: list[dict[str, Any]]
    inputs: list[Any]
    concurrency: int = 1
    pattern: str = "*.lean"
    recursive: bool = False
    heartbeat_seconds: int = 60
    output_root: Path | None = None
    metadata_path: Path | None = None
    max_consecutive_failures: int = 3


@dataclass
class AppConfig:
    config_root: Path
    paths: PathsConfig
    batch: BatchConfig | None = None         # populated when overlay="batch"


_BUILTIN_PATH_DEFAULTS: dict[str, Any] = {
    "workspace_dir": None,
    "prompts_variant": "plan",
    "output_dir": "output",
    "dataset_dir": "dataset",
    "session_logs_dir": "output/session_logs",
}


def _builtin_defaults() -> dict[str, Any]:
    return {"paths": dict(_BUILTIN_PATH_DEFAULTS)}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = base.copy()
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve_config_overlay(config_root: Path, overlay: str) -> Path:
    """Resolve config overlays from explicit paths or checkout-local config files."""
    is_named_overlay = overlay in _OVERLAYS
    filename = _OVERLAYS.get(overlay, overlay)
    raw = Path(filename)
    if raw.is_absolute():
        return raw

    checkout_config_dir = config_root / "config"
    if is_named_overlay:
        candidates = [checkout_config_dir / raw]
    elif raw.parts and raw.parts[0] == "config":
        candidates = [config_root / raw]
    else:
        candidates = [
            config_root / raw,
            checkout_config_dir / raw,
        ]

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _relative_path(base_dir: Path, value: Any) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(str(value))
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def _workspace_path(workspace_dir: Path, value: Any, default: str) -> Path:
    path = _relative_path(workspace_dir, value if value not in (None, "") else default)
    assert path is not None
    return path


def _validate_workspace(workspace_dir: Path) -> None:
    if not workspace_dir.exists():
        raise FileNotFoundError(
            f"Lean workspace does not exist: {workspace_dir}. "
            "Set `paths.workspace_dir` or pass `--workspace`."
        )
    if not workspace_dir.is_dir():
        raise NotADirectoryError(f"Lean workspace is not a directory: {workspace_dir}")

    lakefiles = (workspace_dir / "lakefile.toml", workspace_dir / "lakefile.lean")
    if not any(path.exists() for path in lakefiles):
        raise FileNotFoundError(
            f"Lean workspace is missing `lakefile.toml` or `lakefile.lean`: {workspace_dir}"
        )
    if not (workspace_dir / "lean-toolchain").exists():
        raise FileNotFoundError(f"Lean workspace is missing `lean-toolchain`: {workspace_dir}")


def _parse(raw: dict, config_root: Path) -> AppConfig:
    p = raw.get("paths", {})
    workspace_dir = _relative_path(config_root, p.get("workspace_dir"))
    if workspace_dir is None:
        raise ValueError("`paths.workspace_dir` must be set in config or via `--workspace`.")
    _validate_workspace(workspace_dir)

    paths = PathsConfig(
        workspace_dir=workspace_dir,
        prompts_dir=_relative_path(config_root, p.get("prompts_dir")),
        prompts_variant=p.get("prompts_variant", "plan"),
        output_dir=_workspace_path(workspace_dir, p.get("output_dir"), "output"),
        dataset_dir=_workspace_path(workspace_dir, p.get("dataset_dir"), "dataset"),
        session_logs_dir=_workspace_path(workspace_dir, p.get("session_logs_dir"), "output/session_logs"),
    )
    batch = None
    if "batch" in raw:
        b = raw["batch"]
        batch = BatchConfig(
            defaults=b.get("defaults", {}),
            tasks=b.get("tasks", []),
            inputs=b.get("inputs", []),
            concurrency=b.get("concurrency", 1),
            pattern=b.get("pattern", "*.lean"),
            recursive=b.get("recursive", False),
            heartbeat_seconds=b.get("heartbeat_seconds", 60),
            output_root=_workspace_path(workspace_dir, b.get("output_root"), "output/phase"),
            metadata_path=_relative_path(workspace_dir, b.get("metadata_path")),
            max_consecutive_failures=b.get("max_consecutive_failures", 3),
        )

    return AppConfig(
        config_root=config_root,
        paths=paths,
        batch=batch,
    )


def get_config(
    overlay: str | None = None,
    *,
    workspace_dir: str = "",
    prompts_dir: str = "",
) -> AppConfig:
    """Load built-in defaults, checkout config, an optional overlay, then CLI overrides."""
    config_root = Path.cwd().resolve()
    raw = _builtin_defaults()

    checkout_default = config_root / "config" / "default.yaml"
    if checkout_default.exists():
        raw = _deep_merge(raw, _load_yaml(checkout_default))
    if overlay:
        raw = _deep_merge(raw, _load_yaml(_resolve_config_overlay(config_root, overlay)))

    paths = raw.setdefault("paths", {})
    if workspace_dir:
        paths["workspace_dir"] = workspace_dir
    if prompts_dir:
        paths["prompts_dir"] = prompts_dir
    return _parse(raw, config_root)


def resolve_variant_dir(cfg: AppConfig, override: str = "") -> Path:
    """Return the prompt sub-directory for the active variant.

    `override` takes precedence over `cfg.paths.prompts_variant`. Packaged
    prompts are used unless config or CLI points at an external prompt root.
    """
    variant = (override or cfg.paths.prompts_variant or "plan").strip()
    prompt_root = cfg.paths.prompts_dir or bundled_prompts_dir()
    variant_dir = prompt_root / variant
    if not variant_dir.is_dir():
        raise FileNotFoundError(
            f"Prompt variant `{variant}` was not found under prompt root: {prompt_root}"
        )
    return variant_dir
