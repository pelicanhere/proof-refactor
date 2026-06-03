# Bundled Prompts

Prompt files for the four-phase runtime. These files are package data and are
used when `paths.prompts_dir` is not set.

## Variants

| Directory | Purpose |
|-----------|---------|
| `light/` | No-plan variant used in the paper; durable state lives in the work file |
| `plan/` | Optional plan variant; may help on longer Lean files but uses substantially more tokens |
| `informal/` | External ask profiles used by `proof-refactor ask` |

`light/` and `plan/` are parallel variants. Each runtime variant should provide
the same phase files:

```text
extract.md
design.md
proof.md
repair.md
common.md
lean-lsp-tools-api.md
```

`plan/` additionally contains `plan_template.md`.

## Phase Artifacts

Each run writes artifacts under its run directory, normally
`<workspace>/output/phase/<run>/`.

| Artifact | Purpose |
|----------|---------|
| `<theorem>_work.lean` | Main work file edited by all phases |
| `agent_logs/<phase>_<run_stamp>.md` | Short phase-local agent log with meta, checkpoints, and summary |
| `extract_<theorem>.md` | Raw extract phase output for parsed extraction jobs |
| `extract_<theorem>_jobs.json` | Parsed `lean_extract_batch` job list |
| `design_batch_*.input.md` / `design_batch_*.output.md` | External design ask input/output |
| `prove_*.input.md` / `prove_*.output.md` | External prove ask input/output |
| `repair_*.input.md` / `repair_*.output.md` | External repair ask input/output |
| `refactor_plan.md` | Plan-variant durable state; absent in `light` |

`proof-refactor ask` is the unified external ask entrypoint for `extract`,
`design`, `prove`, and `repair`. `*.output.md` files are raw ask output slots;
phases should not rewrite them manually.

Use `paths.prompts_variant` or CLI `--variant` to choose a variant. Use
`paths.prompts_dir` or CLI `--prompts_dir` to replace this prompt root
externally.
