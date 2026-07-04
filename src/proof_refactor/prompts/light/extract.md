# Extract Phase (light)

You are the **extract phase** for task `{theorem_name}`. This is the first of four phases driven by the Python runner. You are a standalone agent session; Read `{prompt_dir}/common.md` once, then run the workflow below.

This is the no-plan variant. There is no `refactor_plan.md`. The work file plus the run-dir extract output are the only artifacts.

## Paths

- source: `{source_rel}`
- work: `{work_file_rel}`
- extract output: `{phase_dir_rel}/extract_{theorem_name}.md`
- extraction jobs JSON: `{phase_dir_rel}/extract_{theorem_name}_jobs.json`
- agent log: `{agent_logs_dir_rel}/extract_{run_stamp}.md`
- Lean workspace root: current working directory (use workspace-relative paths; do not prefix them with the workspace path)

## Declaration Seed

Treat this list as the authoritative declaration pool; do not use `lean_file_outline` for identity.

{decl_seed_block}

## Mission

1. **Bootstrap** the work file.
2. **Run** `proof-refactor ask extract` to classify each declaration and propose extractions.
3. **Apply** the extractions in one `lean_extract` pass.
4. Emit `END_REASON:COMPLETE` once the work file is Lean-clean (no real errors; `sorry` warnings are fine).

## Workflow

### 1. Bootstrap

- If `{work_file_rel}` does not exist, create it by copying `{source_rel}` verbatim (filesystem copy, no edits).
- If `{work_file_rel}` already exists (resuming a run), keep it as-is.

### 2. Run `proof-refactor ask extract`

From the Lean workspace root, run:

```bash
proof-refactor ask extract "{work_file_rel}" --prompts_dir "{prompts_root}" > "{phase_dir_rel}/extract_{theorem_name}.md" 2>&1
```

The extract output path is the raw ask output slot. Only `proof-refactor ask` may write it. If the command exits non-zero, read the failure text from that file, append a checkpoint failure line to the agent log, and stop with `END_REASON:LIMIT`.

### 3. Read the emitted fragments

Read `{phase_dir_rel}/extract_{theorem_name}.md`. On success it contains one fragment per top-level theorem/lemma in this shape:

```markdown
### decl_name
- **action**: design
- **annotation**: `one-line proof summary`
- **extract_suggestion**:
  - `(none)`
  - block=have h_main : foo := by\n  exact bar | scaffold=main_ineq | mechanism=strictMonoOn_of_deriv_pos + HasDerivAt
```

`extract_suggestion` is either:
- exactly one bullet `- (none)`, or
- one or more bullets `block=... | scaffold=... | mechanism=...`

`block` is the local proof code that should go inside `extract "name" {{ ... }}`, serialized on one line using literal `\n`. Do not use line numbers. Start at the first real tactic; do not include outer `by`, the target `have ... := by` / `let ... := by` header, branch markers `·`, or `case ... =>`. Preserve real internal newlines. Prefer the smallest meaningful block. For mirrored sibling branches, emit one bullet per branch rather than wrapping the shared parent block.

### 4. Parse extraction jobs

From the Lean workspace root:

```bash
proof-refactor parse_extract_jobs "{phase_dir_rel}/extract_{theorem_name}.md" > "{phase_dir_rel}/extract_{theorem_name}_jobs.json"
```

Read the JSON. Shape:

```json
{{
  "jobs": [
    {{
      "owner_decl": "decl_name",
      "extractions": [
        {{"block": "intro h\\n  exact h", "name": "step1"}}
      ]
    }}
  ]
}}
```

### 5. Apply extraction

- If `jobs` is empty, skip.
- Otherwise call `lean_extract` exactly once on the work file with that `jobs` array.
- `lean_extract` owns `import Extraction` insertion, block matching, and wrapper insertion in one pass.
- On Lean errors the file is kept as-is and diagnostics are returned (`success=false`). Do **not** re-run `proof-refactor ask extract`. Instead, attempt up to **3 manual extraction edits**:
  1. Read the returned diagnostic to locate the failing scaffold and what the unifier or parser is complaining about.
  2. Edit the work file directly: insert (or repair) the scaffold declaration `<owner_decl>.<scaffold_name>` above the owner with the corrected signature, and wrap the corresponding owner-body block in `extract "<scaffold_name>" {{ ... }}` if `lean_extract` failed before placing it. Use the `block` text from the JSON `extractions` entry as the body content of the wrapper.
  3. Run `lean_diagnostic_messages` after each edit; if a single edit regressed the file, revert that edit and try a different one.
  - Allowed manual edits are limited to: scaffold-signature shape (binder implicitness, missing/extra explicit binders, casts/coercions, type annotations) and `extract "<scaffold_name>"` wrapper insertion. Do NOT alter the owner's mathematical content, do NOT broaden or redesign a scaffold target, do NOT touch proof bodies.
  - Append a checkpoint line per attempt to the agent log: `YYYY-MM-DD HH:MM · extract:manual · attempt=<N> outcome=<ok|reverted|fail>`.
  - After 3 attempts or persistent failure, append a checkpoint failure line and stop with `END_REASON:LIMIT`.

### 6. Verify and end

- Run `lean_diagnostic_messages`.
- If diagnostics point only to the generated scaffold declaration signatures/headers, you may make one small targeted repair to those scaffold signatures in `{work_file_rel}`.
- Allowed repairs are limited to syntactic/type-shape fixes that preserve the extracted proof obligation: adding missing explicit binders, fixing binder implicitness, correcting casts/coercions, adding required type annotations, or replacing an ill-formed generated type with the definally equivalent type shown by nearby context.
- Do not edit owner declarations, `extract` blocks, proof bodies, helper code, or extraction suggestions during this repair.
- Do not broaden, simplify, or redesign a scaffold statement here. If the fix requires changing the mathematical target rather than repairing its generated signature, stop with `END_REASON:LIMIT`.
- After one targeted signature repair, run `lean_diagnostic_messages` again. If diagnostics remain outside generated scaffold signatures/headers, or the same scaffold still fails, stop with `END_REASON:LIMIT`.
- Otherwise emit exactly one final line: `END_REASON:COMPLETE`.

Also write a short log to `{agent_logs_dir_rel}/extract_{run_stamp}.md` with sections `## Meta`, `## Checkpoints`, `## Summary`. Record per-decl extraction counts and any signature repairs in `## Checkpoints`.

## Naming Conventions

Keep these three distinct for every scaffold:

- conceptual scaffold name: `branch_fwd`
- work-file extract call: `extract "branch_fwd"`
- top-level scaffold declaration: `<owner_decl>.branch_fwd`

## Rules

- Do NOT edit the work file directly except for: (a) bounded manual extraction edits in §5 when `lean_extract` fails, and (b) the one small generated scaffold-signature repair in §6 after a successful extract. Use `lean_extract` first for all extraction edits.
- Do NOT re-run `proof-refactor ask extract` to retry extraction; the JSON `extractions` jobs list is the authoritative input - fall back to manual edits instead.
- Do NOT classify declarations yourself; the ask output does that.
- Do NOT write or rewrite the extract output file yourself.
- Do NOT read the full source file or work file; targeted reads only.

## End Signal

Output exactly one final line:
- `END_REASON:COMPLETE`
- `END_REASON:LIMIT`
