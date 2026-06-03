# Proof-Refactor Usage

This file is the detailed operator guide. Run commands from the repository root
unless noted otherwise.

## Setup Checklist

Required tools:

| Tool | Check |
|---|---|
| Git | `git --version` |
| Lean/elan | `lean --version` |
| uv | `uv --version` |
| Claude Code | `claude -p "reply OK"` |

Initialize the customized MCP submodule:

```bash
git submodule update --init --recursive
```

Install Python dependencies:

```bash
uv sync
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

Write local config and create `.env`:

```bash
bash configure.sh --workspace Lean --variant light
```

Build the example Lean workspace:

```bash
cd Lean
lake exe cache get
lake build
cd ..
```

## Environment

Claude Code authentication is handled by the `claude` CLI. The `.env` file is
for `proof-refactor ask`, which calls an OpenAI-compatible helper endpoint.

Manual `.env` setup:

```bash
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
```

Minimal contents:

```text
GEMINI_API_KEY=your_key
BASE_URL=https://your-openai-compatible-endpoint/v1
MODEL=gemini-3.1-pro-preview
LEAN_REPL=true
```

| Field | Required | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | yes | API key for the helper endpoint used by `proof-refactor ask` |
| `BASE_URL` | yes | Base URL for that endpoint |
| `MODEL` | no | helper model name |
| `LEAN_REPL` | no | enables the MCP Lean REPL fast path when supported |

`AUTH_ERROR` from `proof-refactor run` usually means Claude Code authentication
or proxy configuration is broken, not `.env`.

## MCP

`Lean/.mcp.json` starts the local submodule:

```json
"args": [
  "--from",
  "../lean-lsp-mcp",
  "lean-lsp-mcp"
]
```

Use `--with-editable` only while actively developing `lean-lsp-mcp`. The
default non-editable local install is better for normal runs.

## Commands

Single file:

```bash
uv run proof-refactor run dataset/repeat_test.lean
uv run proof-refactor run dataset/repeat_test.lean --max_rounds 40
uv run proof-refactor run dataset/repeat_test.lean --variant plan
```

Re-run one phase:

```bash
uv run proof-refactor phase extract repeat_test
uv run proof-refactor phase design repeat_test
uv run proof-refactor phase prove repeat_test
uv run proof-refactor phase repair repeat_test
```

Batch:

```bash
uv run proof-refactor batch
uv run proof-refactor batch config/batch.yaml
uv run proof-refactor batch config/batch.yaml --dry_run
uv run proof-refactor batch config/batch.yaml --concurrency 2
```

Utility commands:

```bash
uv run proof-refactor ask extract Lean/output/phase/repeat_test/extract_repeat_test.md
uv run proof-refactor parse_extract_jobs Lean/output/phase/repeat_test/extract_repeat_test.md
```

## Paths

The default workspace is configured in `config/default.yaml`:

```yaml
paths:
  workspace_dir: Lean
  prompts_variant: light
  output_dir: output
  dataset_dir: dataset
  session_logs_dir: output/session_logs
```

Path rules:

| Path | Resolved Against |
|---|---|
| `dataset/repeat_test.lean` | `paths.workspace_dir` |
| `output/phase` | `paths.workspace_dir` |
| `output/session_logs` | `paths.workspace_dir` |
| `paths.prompts_dir` | repository root unless absolute |

Use `--workspace /path/to/lean-repo` to process another Lake workspace. The
workspace must contain `lean-toolchain` and either `lakefile.toml` or
`lakefile.lean`.

## Config Files

Load order:

1. Built-in defaults
2. `config/default.yaml`
3. Optional overlay such as `config/batch.yaml`
4. CLI overrides

Important batch settings:

| Field | Meaning |
|---|---|
| `batch.concurrency` | number of files to process concurrently |
| `batch.inputs` | files or directories to discover |
| `batch.tasks` | explicit task list with per-task overrides |
| `batch.defaults.max_rounds` | default Claude round limit |
| `batch.defaults.check_after_complete` | final Lean verification for batch tasks |

`check_after_complete` is currently batch-only. Single-file `run` and
single-phase `phase` always keep final checking enabled.

See [config/README.md](config/README.md) for the full field list.

## Prompts

Bundled prompts live under:

```text
src/proof_refactor/prompts/
```

Variants:

| Variant | Role |
|---|---|
| `light` | No-plan mode; durable state lives in the work file. This is the mode used in the paper. |
| `plan` | Optional mode with an extra `refactor_plan.md`; it may help on longer Lean files but uses substantially more tokens. |
| `informal` | helper profiles for `proof-refactor ask` |

Override the prompt root with `paths.prompts_dir` or `--prompts_dir`.

## Output

Run output:

```text
Lean/output/phase/<theorem>[_n]/
```

Common files:

| File | Meaning |
|---|---|
| `<theorem>_work.lean` | main work file |
| `refactor_plan.md` | plan-mode durable state |
| `run_log.md` | human-readable task/session links |
| `run_metadata.json` | machine-readable run metadata |
| `extract_<theorem>.md` | raw extraction ask output |
| `extract_<theorem>_jobs.json` | parsed extraction jobs |
| `agent_logs/` | phase-local logs |

Session logs:

```text
Lean/output/session_logs/<task_id>/<phase>/round_N.txt
Lean/output/session_logs/<task_id>/ask/<profile>/<session>.json
```

Batch metadata stores each item's `session_log_dir`, `ask_session_dir`, result
path, and output path.

## Evaluation

Score one comparison:

```bash
uv run python evaluation/score.py \
  --original <original.lean> \
  --baseline <baseline.lean> \
  --proof-refactor <refactor.lean> \
  --rubric-file evaluation/rubric.md \
  --output-json <output-score.json>
```

Score matching files in bulk:

```bash
uv run python evaluation/batch_score.py \
  --original-dir <original-dir> \
  --baseline-dir <baseline-dir> \
  --proof-refactor-dir <refactor-dir> \
  --rubric-file evaluation/rubric.md \
  --output-json <scores.json> \
  --skip-existing \
  --workers 4
```

## Troubleshooting

`AUTH_ERROR` before any Lean/MCP work:

- Test `claude -p "reply OK"`.
- Check Claude login/API key.
- Disable or fix proxy settings if Claude returns authentication failures.

`lean_extract` is missing:

- Run `git submodule update --init --recursive`.
- Check `Lean/.mcp.json` uses `--from ../lean-lsp-mcp`.
- Restart Claude Code so it reloads MCP config.

Lean build failures:

- Run `cd Lean && lake exe cache get && lake build`.
- Confirm the source file is inside a Lake workspace.
