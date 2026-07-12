# Codex Runner Compatibility Plan

## Investigation Summary

- The current runner is centered in `src/proof_refactor/run_claude.py`. Higher-level code calls `run_claude_session()` from `runner.py`, so we can keep that Python interface and add an `agent` selector instead of replacing all call sites with a new API.
- Claude currently uses `claude -p <prompt>` for new sessions and `claude -c -p continue` for continuation. Logs are saved as `round_N.txt`, and `mcp_stats.py` parses Claude `stream-json` logs for tool and token statistics.
- Local Codex CLI is available as `codex-cli 0.129.0`. Its non-interactive entrypoint is `codex exec`, not `codex -p`; `-p` means profile in Codex.
- Codex continuation should use `codex exec resume <thread_id> -` with the prompt on stdin, not `--last`, because batch mode can run tasks concurrently and `--last` can resume the wrong session.
- Codex JSONL includes `thread.started.thread_id`, `item.completed.item.text`, and `turn.completed.usage`. This is sufficient for `END_REASON` detection, per-round logs, and token aggregation.
- Passing a prompt as a Codex positional argument while stdin is not a TTY can emit `Reading additional input from stdin...`. To keep logs clean and avoid command-line length issues, the runner should pass Codex prompts via stdin with the `-` prompt marker.
- Codex MCP configuration uses `mcp_servers` in Codex config. The existing Lean workspace provides Claude-style `Lean/.mcp.json`, so the runner should translate that file into Codex `-c mcp_servers...` overrides for each Codex subprocess. This avoids depending on user-global Codex MCP state and fixes broken/global `lean-lsp` entries.

## Compatibility Goals

- Default agent should be `codex`, matching the migration request.
- Legacy Claude behavior remains available with an `agent` flag or task field set to `claude`.
- Keep existing `run_claude_session()` and `run_claude_once()` interfaces compatible for internal callers, adding optional parameters only.
- Preserve phase flow, verification callbacks, session logs, run metadata, batch metadata, and result JSON shape.
- Preserve existing Claude token/tool statistics and extend token parsing for Codex JSONL.

## Implementation Plan

1. Add `agent: Literal["codex", "claude"] = "codex"` to `TaskMetadata`, serialize it in task/result metadata, and validate it during task normalization.
2. Add CLI flags:
   - `proof-refactor run ... --agent codex|claude`
   - `proof-refactor phase ... --agent codex|claude`
   - `proof-refactor batch ... --agent codex|claude`
3. Add batch config support for `agent` in `batch.defaults` and per-task overrides, defaulting to `codex`.
4. Update `run_claude_session()` to build commands by agent:
   - Claude: keep the existing command behavior.
   - Codex new session: `codex exec [--json] --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check [-c mcp_servers...] [-C cwd] -`
   - Codex continuation: `codex exec resume [--json] --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check [-c mcp_servers...] <thread_id> -`
5. Capture Codex `thread_id` from the first round and reuse it only for same-session continuation. Reset it when the existing LIMIT reset logic starts a fresh session.
6. Extend searchable text extraction to recognize Codex `item.completed` agent messages.
7. Extend `mcp_stats.py` to aggregate Codex `turn.completed.usage` into existing token fields while leaving Claude parsing intact.
8. Update docs only where they describe agent selection and setup checks.

## Test Plan

1. Static/smoke checks:
   - `python -m compileall src`
   - Short Codex session through `run_claude_session(agent="codex", output_format="stream-json")`
   - Short Claude-compatible construction path if `claude` is installed or via direct command-building/parser tests if not.
2. Stats checks:
   - Parse a saved Codex JSONL round log and verify nonzero token totals.
   - Verify Codex `mcp_tool_call` events are counted under `mcp_tools`.
   - Confirm existing Claude-style parser behavior remains covered by code path and does not regress structurally.
3. Real task:
   - Run `uv run proof-refactor run dataset/repeat_test.lean --agent codex` against `Lean/dataset/repeat_test.lean`.
   - Verify final result, round logs, `stats.json`, `errors.json`, and Lean verification outcome.
4. Review:
   - Inspect `git diff`.
   - Check that unrelated user changes, especially `Lean/Extraction/ExtractTest.lean`, were not touched.
