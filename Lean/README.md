# Lean Workspace

`Lean/` is the example Lean 4 workspace used by the default config. `paths.workspace_dir` points the runner at this Lake
workspace.

## Layout

```text
Lean/
├── .github/workflows/     # Lean CI workflow
├── .mcp.json              # Claude MCP config for the local lean-lsp-mcp submodule
├── lakefile.toml
├── lake-manifest.json     # pinned Lake dependency manifest
├── lean-toolchain
├── Extraction.lean        # extraction tactic module entry
├── Extraction/            # tactic implementation and guard-message tests
├── dataset/               # input files for proof-refactor
└── output/                # generated output, gitignored
```

`Lean/.mcp.json` starts the local `lean-lsp-mcp` submodule:

```json
"args": ["--from", "../lean-lsp-mcp", "lean-lsp-mcp"]
```

## Build

Run this once after checkout or after Lean dependency changes:

```bash
cd Lean
lake exe cache get
lake build
cd ..
```

## Run A file

Put input files under `Lean/dataset/`, then run from the repository root:

```bash
uv run proof-refactor run dataset/my_theorem.lean
```

With the default config, output is written to:

```text
Lean/output/phase/<theorem>[_n]/
Lean/output/session_logs/<task_id>/
```

The important run files are `<theorem>_work.lean`, `run_log.md`,
`run_metadata.json`, optional `refactor_plan.md`, and `agent_logs/`.
See [../USAGE.md](../USAGE.md) for full command and artifact details.

## Extraction Tactic

`Lean/Extraction/Extract.lean` defines the local `extract { ... }` tactic used
by the pipeline. `Lean/Extraction/ExtractTest.lean` contains guard-message
examples for that tactic.
