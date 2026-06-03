"""
Tool statistics derived only from Claude stream-json session logs.

Auto-called by runner.run_task() after every session.
Also runnable as a debug-only module entry:

    uv run python -m proof_refactor.mcp_stats stats <workspace>/output/session_logs/<task_id>/round_1.txt
    uv run python -m proof_refactor.mcp_stats scan <workspace>/output/session_logs/
"""

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

try:
    from .session_errors import collect_session_errors
    from .session_log_utils import discover_round_logs
except ImportError:
    # Running as __main__ — relative imports unavailable
    from session_errors import collect_session_errors  # type: ignore
    from session_log_utils import discover_round_logs  # type: ignore


# USD per million tokens
_COST_PER_MTok: dict[str, dict[str, float]] = {
    "claude-opus-4-6": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_creation": 18.75},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_creation": 3.75},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00, "cache_read": 0.08, "cache_creation": 1.00},
}
_DEFAULT_MODEL = "claude-sonnet-4-6"
_MCP_PREFIX = "mcp__lean-lsp__"
_SHORT_MCP_TOOLS = {
    "lean_build",
    "lean_code_actions",
    "lean_completions",
    "lean_declaration_file",
    "lean_diagnostic_messages",
    "lean_file_outline",
    "lean_get_widget_source",
    "lean_get_widgets",
    "lean_goal",
    "lean_hammer_premise",
    "lean_hover_info",
    "lean_leanfinder",
    "lean_leansearch",
    "lean_local_search",
    "lean_loogle",
    "lean_multi_attempt",
    "lean_profile_proof",
    "lean_references",
    "lean_run_code",
    "lean_state_search",
    "lean_term_goal",
    "lean_verify",
}
_BASH_VERIFY_PATTERNS = (
    re.compile(r"\blake\s+build\b", re.I),
    re.compile(r"\blake\s+env\s+lean\b", re.I),
    re.compile(r"\bbuild to check\b", re.I),
    re.compile(r"\bfinal compilation check\b", re.I),
    re.compile(r"\bcheck .*errors?\b", re.I),
    re.compile(r"\bcheck .*compil", re.I),
    re.compile(r"\bverify\b", re.I),
    re.compile(r"\bcheck for sorry/error/warning\b", re.I),
)


def compute_cost(token_usage: dict, model: str | None = None) -> float:
    """Estimate USD cost from token counts using per-model pricing."""
    rates = _COST_PER_MTok.get(model or _DEFAULT_MODEL, _COST_PER_MTok[_DEFAULT_MODEL])
    return (
        token_usage.get("input_tokens", 0) / 1_000_000 * rates["input"]
        + token_usage.get("output_tokens", 0) / 1_000_000 * rates["output"]
        + token_usage.get("cache_read_input_tokens", 0) / 1_000_000 * rates["cache_read"]
        + token_usage.get("cache_creation_input_tokens", 0) / 1_000_000 * rates["cache_creation"]
    )


def _normalize_model(model: Optional[str]) -> Optional[str]:
    if not model:
        return None
    return re.sub(r"\[[^\]]+\]$", "", model)


def _normalize_tool_name(name: Optional[str]) -> str:
    if not name:
        return "unknown"
    if name.startswith(_MCP_PREFIX):
        return name[len(_MCP_PREFIX):]
    return name


def _is_mcp_tool(name: Optional[str]) -> bool:
    if not name:
        return False
    return name.startswith(_MCP_PREFIX) or _normalize_tool_name(name) in _SHORT_MCP_TOOLS


def _new_counter() -> dict[str, int]:
    return {"total": 0, "ok": 0, "fail": 0}


def _bump(bucket: dict[str, dict[str, int]], tool_name: str, status: str = "ok"):
    stats = bucket.setdefault(tool_name, _new_counter())
    stats["total"] += 1
    if status in ("ok", "fail"):
        stats[status] += 1


def _is_bash_verification(command_or_description: str) -> bool:
    text = command_or_description or ""
    return any(p.search(text) for p in _BASH_VERIFY_PATTERNS)


def analyze_stream_json_log(log_path: str | Path) -> dict:
    """
    Parse a single stream-json round_N.txt log file.

    Counts:
    - top-level Claude tool calls from assistant tool_use blocks
    - worker tool calls from system task_progress events
    - dispatched subagent types from Agent/Task tool inputs
    - bash verification detours detected from commands/descriptions
    """
    log_path = Path(log_path)
    claude_tools: dict[str, dict[str, int]] = {}
    mcp_tools: dict[str, dict[str, int]] = {}
    subagent_types: dict[str, int] = defaultdict(int)
    token_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    pending_top_level: dict[str, tuple[str, str]] = {}
    model: str | None = None
    bash_verification_calls = 0

    if not log_path.exists():
        return {
            "by_tool": {},
            "claude_tools": {"by_tool": {}},
            "mcp_tools": {"by_tool": {}},
            "subagent_types": {},
            "bash_verification_calls": 0,
            "token_usage": token_usage,
            "model": None,
        }

    for raw in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw or not raw.startswith("{"):
            continue
        try:
            evt = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(evt, dict):
            continue

        evt_type = evt.get("type", "")
        parent_tool_use_id = evt.get("parent_tool_use_id")

        if evt_type == "system" and evt.get("subtype") == "init" and model is None:
            model = _normalize_model(evt.get("model"))
            continue

        if evt_type == "assistant":
            msg = evt.get("message", {})
            if model is None:
                model = _normalize_model(msg.get("model"))

            usage = msg.get("usage", {})
            for key in token_usage:
                token_usage[key] += usage.get(key, 0)

            # Worker-internal tool uses are accounted for by task_progress events.
            if parent_tool_use_id is not None:
                continue

            for block in msg.get("content", []):
                if block.get("type") != "tool_use":
                    continue
                tool_id = block.get("id", "")
                raw_name = block.get("name", "unknown")
                norm_name = _normalize_tool_name(raw_name)
                bucket = mcp_tools if _is_mcp_tool(raw_name) else claude_tools
                _bump(bucket, norm_name, status="ok")
                pending_top_level[tool_id] = (
                    "mcp_tools" if _is_mcp_tool(raw_name) else "claude_tools",
                    norm_name,
                )

                if raw_name in {"Agent", "Task"}:
                    subagent_type = block.get("input", {}).get("subagent_type")
                    if isinstance(subagent_type, str) and subagent_type:
                        subagent_types[subagent_type] += 1

                if raw_name == "Bash":
                    command = block.get("input", {}).get("command", "")
                    if _is_bash_verification(command):
                        bash_verification_calls += 1

            continue

        if evt_type == "user":
            for block in evt.get("message", {}).get("content", []):
                if block.get("type") != "tool_result":
                    continue
                tool_id = block.get("tool_use_id", "")
                bucket_name, tool_name = pending_top_level.pop(tool_id, (None, None))
                if bucket_name is None or tool_name is None:
                    continue
                bucket = mcp_tools if bucket_name == "mcp_tools" else claude_tools
                stats = bucket.setdefault(tool_name, _new_counter())
                # total already counted when tool_use was emitted
                if block.get("is_error"):
                    stats["ok"] = max(0, stats["ok"] - 1)
                    stats["fail"] += 1
            continue

        if evt_type == "system" and evt.get("subtype") == "task_progress":
            raw_name = evt.get("last_tool_name", "unknown")
            norm_name = _normalize_tool_name(raw_name)
            bucket = mcp_tools if _is_mcp_tool(raw_name) else claude_tools
            _bump(bucket, norm_name, status="ok")
            description = evt.get("description", "")
            if norm_name == "Bash" and _is_bash_verification(description):
                bash_verification_calls += 1
            continue

    unified: dict[str, dict[str, int]] = {}
    for source in (claude_tools, mcp_tools):
        for tool_name, stats in source.items():
            dst = unified.setdefault(tool_name, _new_counter())
            for key in ("total", "ok", "fail"):
                dst[key] += stats[key]

    return {
        "by_tool": unified,
        "claude_tools": {"by_tool": claude_tools},
        "mcp_tools": {"by_tool": mcp_tools},
        "subagent_types": dict(subagent_types),
        "bash_verification_calls": bash_verification_calls,
        "token_usage": token_usage,
        "model": model,
    }


def analyze_session_logs(
    session_log_dir: str | Path,
    round_durations: Optional[list[float]] = None,
) -> dict:
    """
    Parse all round_N.txt files under a session log root and save stats.json.
    """
    session_log_dir = Path(session_log_dir)
    round_entries = discover_round_logs(session_log_dir)

    if not round_entries:
        return {
            "by_tool": {},
            "claude_tools": {"by_tool": {}},
            "mcp_tools": {"by_tool": {}},
            "subagent_types": {},
            "bash_verification_calls": 0,
            "token_usage": {},
            "per_round": [],
        }

    total_claude_tools: dict[str, dict[str, int]] = {}
    total_mcp_tools: dict[str, dict[str, int]] = {}
    total_tokens = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    total_subagent_types: dict[str, int] = defaultdict(int)
    total_bash_verification_calls = 0
    per_round = []
    detected_model: str | None = None

    for index, round_entry in enumerate(round_entries):
        round_file = round_entry["path"]
        round_num = round_entry["round"]
        round_stats = analyze_stream_json_log(round_file)
        if detected_model is None:
            detected_model = round_stats.get("model")

        duration = None
        if round_durations and index < len(round_durations):
            duration = round_durations[index]

        for tool_name, stats in round_stats["claude_tools"]["by_tool"].items():
            dst = total_claude_tools.setdefault(tool_name, _new_counter())
            for key in ("total", "ok", "fail"):
                dst[key] += stats[key]

        for tool_name, stats in round_stats["mcp_tools"]["by_tool"].items():
            dst = total_mcp_tools.setdefault(tool_name, _new_counter())
            for key in ("total", "ok", "fail"):
                dst[key] += stats[key]

        for key, value in round_stats["token_usage"].items():
            total_tokens[key] += value

        for subagent_type, count in round_stats["subagent_types"].items():
            total_subagent_types[subagent_type] += count

        total_bash_verification_calls += round_stats["bash_verification_calls"]

        per_round.append(
            {
                "phase": round_entry["phase"],
                "round": round_num,
                "label": round_entry["label"],
                "relative_path": round_entry["relative_path"],
                "claude_tools": round_stats["claude_tools"],
                "mcp_tools": round_stats["mcp_tools"],
                "subagent_types": round_stats["subagent_types"],
                "bash_verification_calls": round_stats["bash_verification_calls"],
                "token_usage": round_stats["token_usage"],
                "cost_usd": compute_cost(round_stats["token_usage"], model=detected_model),
                "duration_seconds": duration,
            }
        )

    unified: dict[str, dict[str, int]] = {}
    for source in (total_claude_tools, total_mcp_tools):
        for tool_name, stats in source.items():
            dst = unified.setdefault(tool_name, _new_counter())
            for key in ("total", "ok", "fail"):
                dst[key] += stats[key]

    total_duration = sum(
        round_info["duration_seconds"]
        for round_info in per_round
        if round_info["duration_seconds"] is not None
    ) or None

    summary = {
        "by_tool": unified,
        "claude_tools": {"by_tool": total_claude_tools},
        "mcp_tools": {"by_tool": total_mcp_tools},
        "subagent_types": dict(total_subagent_types),
        "bash_verification_calls": total_bash_verification_calls,
        "token_usage": total_tokens,
        "model": detected_model,
        "cost_usd": compute_cost(total_tokens, model=detected_model),
        "per_round": per_round,
        "total_duration_seconds": total_duration,
    }

    (session_log_dir / "stats.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    _print_stats(summary)
    collect_session_errors(session_log_dir)
    return summary


def _print_tool_table(by_tool: dict, header: str):
    if not by_tool:
        return
    width = 52
    print(f"\n  {header}")
    print(f"  {'Tool':<30} {'total':>6} {'ok':>5} {'fail':>5}")
    print("  " + "─" * width)
    total_calls = total_ok = total_fail = 0
    for tool_name in sorted(by_tool):
        stats = by_tool[tool_name]
        print(f"  {tool_name:<30} {stats['total']:>6} {stats['ok']:>5} {stats['fail']:>5}")
        total_calls += stats["total"]
        total_ok += stats["ok"]
        total_fail += stats["fail"]
    print("  " + "─" * width)
    print(f"  {'TOTAL':<30} {total_calls:>6} {total_ok:>5} {total_fail:>5}")


def _print_stats(summary: dict, label: str = ""):
    tokens = summary.get("token_usage", {})
    per_round = summary.get("per_round", [])
    total_duration = summary.get("total_duration_seconds")
    model = summary.get("model")
    cost_usd = summary.get("cost_usd")
    claude_tools = summary.get("claude_tools", {}).get("by_tool", {})
    mcp_tools = summary.get("mcp_tools", {}).get("by_tool", {})
    subagent_types = summary.get("subagent_types", {})
    bash_verification_calls = summary.get("bash_verification_calls", 0)

    if label:
        print(f"\n{label}")

    if not claude_tools and not mcp_tools and not tokens:
        print("[info] No stats available (no stream-json events found).")
        return

    print()
    _print_tool_table(claude_tools, "Tool (Claude)")
    _print_tool_table(mcp_tools, "Tool (Lean MCP)")

    if subagent_types:
        joined = ", ".join(f"{name}: {count}" for name, count in sorted(subagent_types.items()))
        print(f"\n  Subagents  {joined}")
    print(f"  Bash Lean verification calls  {bash_verification_calls}")

    if tokens:
        inp = tokens.get("input_tokens", 0)
        out = tokens.get("output_tokens", 0)
        cache_read = tokens.get("cache_read_input_tokens", 0)
        cache_creation = tokens.get("cache_creation_input_tokens", 0)
        cost_str = f"  |  cost=${cost_usd:.4f}" if cost_usd is not None else ""
        print(
            f"\n  Tokens  input={inp:,}  output={out:,}  "
            f"cache_read={cache_read:,}  cache_creation={cache_creation:,}{cost_str}"
        )
        if model and model != _DEFAULT_MODEL:
            print(f"  Model   {model}")

    if per_round:
        parts = [
            f"{(round_info.get('label') or ('round_' + str(round_info['round'])))}: {round_info['duration_seconds']:.1f}s"
            for round_info in per_round
            if round_info.get("duration_seconds") is not None
        ]
        if parts:
            total_str = f"  |  total: {total_duration:.1f}s" if total_duration else ""
            print(f"  Time    {',  '.join(parts)}{total_str}")
    print()


class McpStats:
    """CLI for inspecting tool-call and token stats from stream-json session logs."""

    def stats(self, log_file: str):
        """Show stats for a single stream-json round log file (round_N.txt)."""
        result = analyze_stream_json_log(Path(log_file))
        _print_stats(result, label=log_file)

    def scan(self, directory: str = "session_logs"):
        """Aggregate stats across all task sessions in a directory."""
        root = Path(directory)
        if not root.exists():
            print(f"[error] Directory not found: {root}")
            return
        task_dirs = sorted(d for d in root.iterdir() if d.is_dir())
        if not task_dirs:
            print(f"[warn] No session directories found in {root}")
            return
        for task_dir in task_dirs:
            if not discover_round_logs(task_dir):
                continue
            print(f"\n{'─' * 60}")
            print(f"  {task_dir.name}")
            print(f"{'─' * 60}")
            analyze_session_logs(task_dir)

    def errors(self, directory: str):
        """Show collected errors for a session log directory (reads errors.json or re-parses)."""
        path = Path(directory)
        if not path.exists():
            print(f"[error] Directory not found: {path}")
            return
        if discover_round_logs(path):
            collect_session_errors(path)
            return
        for task_dir in sorted(d for d in path.iterdir() if d.is_dir()):
            if discover_round_logs(task_dir):
                print(f"\n{'─' * 60}")
                print(f"  {task_dir.name}")
                print(f"{'─' * 60}")
                collect_session_errors(task_dir)


if __name__ == "__main__":
    import fire

    fire.Fire(McpStats)
