"""
Error extraction from Claude stream-json or Codex JSONL session logs.

Auto-called by mcp_stats.analyze_session_logs() after every session.
Saves errors.json alongside stats.json in the session log directory.
"""

import json
from pathlib import Path

try:
    from .session_log_utils import discover_round_logs
except ImportError:
    from session_log_utils import discover_round_logs  # type: ignore


def extract_errors_from_log(log_path: str | Path) -> dict:
    """
    Parse a single stream-json round_N.txt log file for errors.

    Collects:
    - Non-JSON plain-text lines (stderr output, Python tracebacks, etc.)
    - Tool result errors (is_error: true)
    - System error events (type=system, subtype=error)

    Returns:
        {
          "plain_text_errors": [...],
          "tool_errors": [{"tool_name": ..., "error_text": ..., "tool_use_id": ...}],
          "system_errors": [{"subtype": ..., "message": ...}],
          "has_errors": bool,
        }
    """
    log_path = Path(log_path)
    plain_text_errors: list[str] = []
    tool_errors: list[dict] = []
    system_errors: list[dict] = []
    pending_tool: dict[str, str] = {}  # tool_use_id -> tool_name

    if not log_path.exists():
        return {
            "plain_text_errors": [],
            "tool_errors": [],
            "system_errors": [],
            "has_errors": False,
        }

    for raw in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue

        if not stripped.startswith("{"):
            # Plain-text line — likely stderr (Python errors, tracebacks, etc.)
            plain_text_errors.append(stripped)
            continue

        try:
            evt = json.loads(stripped)
        except json.JSONDecodeError:
            # Malformed JSON — treat as plain text
            plain_text_errors.append(stripped)
            continue

        t = evt.get("type", "")

        if t == "item.completed":
            item = evt.get("item", {})
            if isinstance(item, dict) and item.get("type") == "mcp_tool_call":
                if item.get("error") or item.get("status") == "failed":
                    tool_errors.append({
                        "tool_name": str(item.get("tool", "mcp_tool_call")),
                        "error_text": str(item.get("error", item.get("result", ""))),
                        "tool_use_id": item.get("id", ""),
                    })
            elif isinstance(item, dict) and item.get("type") == "command_execution":
                exit_code = item.get("exit_code")
                if exit_code not in (0, None) or item.get("status") == "failed":
                    tool_errors.append({
                        "tool_name": "command_execution",
                        "error_text": str(item.get("aggregated_output", "")),
                        "tool_use_id": item.get("id", ""),
                    })
            continue

        if t == "assistant":
            # Track tool_use blocks so we can resolve names for tool_result errors
            for block in evt.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tid = block.get("id", "")
                    name = block.get("name", "unknown")
                    if tid:
                        pending_tool[tid] = name

        elif t == "user":
            for block in evt.get("message", {}).get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result" and block.get("is_error"):
                    tid = block.get("tool_use_id", "")
                    tool_name = pending_tool.pop(tid, "unknown")
                    content = block.get("content", "")
                    if isinstance(content, list):
                        # content may be a list of blocks
                        text_parts = [
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        error_text = " ".join(text_parts)
                    else:
                        error_text = str(content)
                    tool_errors.append({
                        "tool_name": tool_name,
                        "error_text": error_text,
                        "tool_use_id": tid,
                    })

        elif t == "system":
            subtype = evt.get("subtype", "")
            if subtype == "error":
                system_errors.append({
                    "subtype": subtype,
                    "message": evt.get("error", evt.get("message", str(evt))),
                })

    has_errors = bool(plain_text_errors or tool_errors or system_errors)
    return {
        "plain_text_errors": plain_text_errors,
        "tool_errors": tool_errors,
        "system_errors": system_errors,
        "has_errors": has_errors,
    }


def collect_session_errors(session_log_dir: str | Path) -> dict:
    """
    Parse all round_N.txt files under a session log root, aggregate errors,
    print a warning summary if any found, and save errors.json.

    Args:
        session_log_dir: Directory containing round_N.txt files.

    Returns:
        Aggregate error dict.
    """
    session_log_dir = Path(session_log_dir)
    round_entries = discover_round_logs(session_log_dir)

    if not round_entries:
        return {
            "per_round": [],
            "total_tool_errors": 0,
            "total_plain_text_errors": 0,
            "total_system_errors": 0,
            "has_errors": False,
        }

    per_round = []
    total_tool = 0
    total_plain = 0
    total_system = 0

    for round_entry in round_entries:
        rnd_file = round_entry["path"]
        rnd_num = round_entry["round"]
        r = extract_errors_from_log(rnd_file)

        n_tool = len(r["tool_errors"])
        n_plain = len(r["plain_text_errors"])
        n_sys = len(r["system_errors"])

        total_tool += n_tool
        total_plain += n_plain
        total_system += n_sys

        per_round.append({
            "phase": round_entry["phase"],
            "round": rnd_num,
            "label": round_entry["label"],
            "relative_path": round_entry["relative_path"],
            "plain_text_errors": r["plain_text_errors"],
            "tool_errors": r["tool_errors"],
            "system_errors": r["system_errors"],
            "has_errors": r["has_errors"],
        })

        if r["has_errors"]:
            parts = []
            if n_tool:
                parts.append(f"{n_tool} tool error{'s' if n_tool != 1 else ''}")
            if n_plain:
                parts.append(f"{n_plain} plain-text error{'s' if n_plain != 1 else ''}")
            if n_sys:
                parts.append(f"{n_sys} system error{'s' if n_sys != 1 else ''}")
            print(f"[warn] {round_entry['label']}: {', '.join(parts)}")
            # Print plain-text errors inline for quick visibility
            for line in r["plain_text_errors"]:
                print(f"  plain: {line}")
            for te in r["tool_errors"]:
                print(f"  tool({te['tool_name']}): {te['error_text'][:120]}")

    has_errors = bool(total_tool or total_plain or total_system)
    summary = {
        "per_round": per_round,
        "total_tool_errors": total_tool,
        "total_plain_text_errors": total_plain,
        "total_system_errors": total_system,
        "has_errors": has_errors,
    }

    (session_log_dir / "errors.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return summary
