# Configuration

`config/` contains local YAML configuration files. The Python package does not
ship a second YAML config copy.

## Resolution Order

1. Built-in field defaults
2. `config/default.yaml`
3. Optional overlay, usually `config/batch.yaml`
4. CLI overrides such as `--workspace`, `--variant`, and `--prompts_dir`

## `paths`

Defined in `config/default.yaml`.

| Field | Required | Default | Meaning |
|-------|----------|---------|---------|
| `workspace_dir` | Yes | `Lean` | Lean workspace root, relative to the checkout unless absolute |
| `prompts_variant` | No | `light` | Prompt variant; `light` is the no-plan paper mode, `plan` is optional and token-heavy |
| `prompts_dir` | No | bundled prompts | External prompt root override |
| `output_dir` | No | `output` | Workspace-relative output root |
| `dataset_dir` | No | `dataset` | Workspace-relative dataset root |
| `session_logs_dir` | No | `output/session_logs` | Workspace-relative agent session-log root |

The workspace must contain `lakefile.toml` or `lakefile.lean`, plus
`lean-toolchain`.

## `batch`

Defined in `config/batch.yaml`.

| Field | Default | Meaning |
|-------|---------|---------|
| `concurrency` | `1` | Number of files to process concurrently |
| `pattern` | `*.lean` | Default glob when an input points to a directory |
| `recursive` | `false` | Whether directory inputs recurse by default |
| `heartbeat_seconds` | `60` | Per-task batch heartbeat interval |
| `output_root` | `output/phase` | Workspace-relative batch output root |
| `metadata_path` | batch run directory | Optional workspace-relative batch metadata path |
| `max_consecutive_failures` | `3` | Stop scheduling after repeated auth/runtime failures |
| `inputs` | `[]` | Files or directories to discover |
| `tasks` | `[]` | Explicit source entries with per-task overrides |
| `defaults` | `{}` | Per-task runtime defaults |

`inputs[]` may be a string path:

```yaml
inputs:
  - dataset/repeat_test.lean
```

Use an object only when a directory input needs a local glob override:

```yaml
inputs:
  - path: dataset
    pattern: "*.lean"
    recursive: false
```

`tasks[]` may be a string path or an object:

```yaml
tasks:
  - path: dataset/repeat_test.lean
    max_rounds: 20
    prompts_variant: plan
```

## Batch Task Settings

The following fields can appear in `batch.defaults` or in an individual
`batch.tasks[]` object:

| Field | Default | Meaning |
|-------|---------|---------|
| `mode` | `phase` | Only `phase` is supported |
| `agent` | `codex` | Agent backend: `codex` or legacy `claude` |
| `max_rounds` | `20` | Agent continuation limit |
| `check_after_complete` | `true` | Run Lean verification after `COMPLETE` |
| `allow_sorry` | `false` | Allow `sorry` during final verification |
| `prompts_variant` | config value | Per-task prompt variant override |

`check_after_complete` is currently batch-only. The single-file `run` and
single-phase `phase` CLI commands always set it to `true`.
