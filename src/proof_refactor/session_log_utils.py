"""
Helpers for discovering per-round Claude session logs.
"""

from __future__ import annotations

from pathlib import Path


_PHASE_ORDER = {
    "extract": 0,
    "design": 1,
    "prove": 2,
    "repair": 3,
}


def discover_round_logs(session_log_dir: str | Path) -> list[dict]:
    """
    Discover round_N.txt logs under a session log root.

    Supports both layouts:
    - <session_logs_dir>/<task_id>/round_N.txt
    - <session_logs_dir>/<task_id>/<phase>/round_N.txt
    """
    root = Path(session_log_dir)
    if not root.exists():
        return []

    entries: list[dict] = []
    for path in root.rglob("round_*.txt"):
        stem_parts = path.stem.split("_", 1)
        if len(stem_parts) != 2 or stem_parts[0] != "round":
            continue
        try:
            round_num = int(stem_parts[1])
        except ValueError:
            continue

        rel = path.relative_to(root)
        phase = rel.parts[-2] if len(rel.parts) > 1 else None
        label = f"{phase}/round_{round_num}" if phase else f"round_{round_num}"
        entries.append(
            {
                "path": path,
                "round": round_num,
                "phase": phase,
                "label": label,
                "relative_path": rel.as_posix(),
            }
        )

    entries.sort(
        key=lambda entry: (
            -1 if entry["phase"] is None else _PHASE_ORDER.get(entry["phase"], 999),
            entry["round"],
            entry["relative_path"],
        )
    )
    return entries
