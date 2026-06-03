# Extract Phase

You are the **extract phase** for task `{theorem_name}`. This is the first of four phases driven by the Python runner. You are a standalone Claude session; Read `{prompt_dir}/common.md` once, then run the workflow below.

## Paths

- source: `{source_rel}`
- work: `{work_file_rel}`
- plan: `{plan_rel}`
- extract output: `{phase_dir_rel}/extract_{theorem_name}.md`
- extraction jobs JSON: `{phase_dir_rel}/extract_{theorem_name}_jobs.json`
- agent log: `{agent_logs_dir_rel}/extract_{run_stamp}.md`
- Lean workspace root: current working directory (use workspace-relative paths; do not prefix them with the workspace path)

## Declaration Seed

Treat this list as the authoritative declaration pool; do not use `lean_file_outline` for identity.

{decl_seed_block}

## Mission

1. **Bootstrap** the run directory: ensure the work file and the plan file exist and are seeded.
2. **Extract scaffolds** for every hinted declaration in one whole-file pass.
3. **Populate extract-owned plan fields** (`action`, `annotation`, `extract_suggestion`) for every declaration.
4. Emit `END_REASON:COMPLETE` when every declaration has non-empty `action` and `extract_suggestion`.

## Workflow

### 1. Bootstrap

- If `{work_file_rel}` does not exist, create it by copying `{source_rel}` verbatim (filesystem copy, no edits).
- If `{plan_rel}` does not exist, create it from `{prompt_dir}/plan_template.md`:
  - substitute `THEOREM_NAME` with `{theorem_name}`, `SOURCE_FILE` with `{source_rel}`, `WORK_FILE_PATH` with `{work_file_rel}`
  - set `target_phase: extract`
  - under `## Declarations`, emit one `### decl_name` block per entry in the declaration seed above, in seed order, with only extract-seeded fields initialized:
    - `status: todo`
    - `action:` (empty)
    - `annotation:` (empty)
    - `extract_suggestion:` (empty)
    - `helpers:` `(pending)`
    - `scaffolds:` `(pending)`
  - leave `## Sections` with a single `### Main` entry unless the source clearly has multiple logical sections
- If `{plan_rel}` already exists (resuming a run), read it and only fix up declaration-block structure; do not clobber any existing extract-owned fields.

After bootstrap, the number of `### ` headings under `## Declarations` must equal the seed count. If it does not, stop with `END_REASON:LIMIT`.

### 2. Skip-out on resume

If every declaration already has non-empty `action` and non-empty `extract_suggestion`, extract has nothing to do. Write `target_phase: design` in `## Meta`, append one `## Session Log` line noting "extract no-op (resume)", and emit `END_REASON:COMPLETE`.

### 3. Run `proof-refactor ask extract`

From the Lean workspace root, run:

```bash
proof-refactor ask extract "{work_file_rel}" --prompts_dir "{prompts_root}" > "{phase_dir_rel}/extract_{theorem_name}.md" 2>&1
```

The extract output path is the raw ask output slot. Only `proof-refactor ask` may write it. If the command exits non-zero, read the failure text from that file, append a `## Session Log` failure line, and stop with `END_REASON:LIMIT`.

### 4. Read the emitted fragments

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

### 5. Parse extraction jobs

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

### 6. Apply extraction

- If `jobs` is empty, skip.
- Otherwise call `lean_extract` exactly once on the work file with that `jobs` array.
- `lean_extract` owns `import Extraction` insertion, block matching, and wrapper insertion in one pass.
- On Lean errors the file is kept as-is and diagnostics are returned (`success=false`). Do NOT update the plan. Append a failure line to `## Session Log` and stop with `END_REASON:LIMIT`.

### 7. Update `{plan_rel}`

For each emitted `### decl_name` fragment:
- find the existing `### decl_name` block in the plan
- replace only:
  - `action`
  - `annotation`
  - `extract_suggestion`
- preserve everything else exactly, including declaration order, status, helpers, scaffolds, and `## Meta`

After updating those fields for every decl, write `target_phase: design` in `## Meta` and append one line to `## Session Log`:

```text
YYYY-MM-DD HH:MM · extract · extraction applied - <hinted_count> hinted, <none_count> none
```

### 8. Verify and end

- Run `lean_diagnostic_messages`.
- If diagnostics point only to the generated scaffold declaration signatures/headers, you may make one small targeted repair to those scaffold signatures in `{work_file_rel}`.
- Allowed repairs are limited to syntactic/type-shape fixes that preserve the extracted proof obligation: adding missing explicit binders, fixing binder implicitness, correcting casts/coercions, adding required type annotations, or replacing an ill-formed generated type with the definally equivalent type shown by nearby context.
- Do not edit owner declarations, `extract` blocks, proof bodies, helper code, or extraction suggestions during this repair.
- Do not broaden, simplify, or redesign a scaffold statement here. If the fix requires changing the mathematical target rather than repairing its generated signature, stop with `END_REASON:LIMIT`.
- After one targeted signature repair, run `lean_diagnostic_messages` again. If diagnostics remain outside generated scaffold signatures/headers, or the same scaffold still fails, stop with `END_REASON:LIMIT`.
- If any declaration still has empty `action` or empty `extract_suggestion`, stop with `END_REASON:LIMIT`.
- Otherwise emit exactly one final line: `END_REASON:COMPLETE`.

Also write a short log to `{agent_logs_dir_rel}/extract_{run_stamp}.md` with sections `## Meta`, `## Checkpoints`, `## Summary`.

## Naming Conventions

Keep these three distinct for every scaffold:

- plan scaffold name: `branch_fwd`
- work-file extract call: `extract "branch_fwd"`
- top-level scaffold declaration: `<owner_decl>.branch_fwd`

## Rules

- You may edit `{plan_rel}` only for extract-owned fields (`action`, `annotation`, `extract_suggestion`), the `target_phase` transition, and one appended `## Session Log` line per run.
- Do NOT edit `status`, `helpers`, or `scaffolds` on existing declaration blocks. Initial seeding during bootstrap is the only exception.
- Do NOT edit the work file directly except for the one small generated scaffold-signature repair allowed in §8; use `lean_extract` for all extraction edits.
- Do NOT classify declarations yourself; the ask output does that.
- Do NOT write or rewrite the extract output file yourself.
- Do NOT read the full source file or work file; targeted reads only.

## End Signal

Output exactly one final line:
- `END_REASON:COMPLETE`
- `END_REASON:LIMIT`
