# Design Phase (light)

You are the **design phase** for task `{theorem_name}`. This is one of four sequential phases driven by the Python runner. Read `{prompt_dir}/common.md` and `{prompt_dir}/lean-lsp-tools-api.md` once, then follow the workflow below.

This is the no-plan variant. There is no `refactor_plan.md`. Helper signatures are inserted as real `theorem helper_name <sig> := by sorry` declarations directly into the work file. Per-decl notes go in the agent log under `## Checkpoints`.

## Paths

- work: `{work_file_rel}`
- design input template: `{phase_dir_rel}/design_batch_<label>.input.md`
- design output template: `{phase_dir_rel}/design_batch_<label>.output.md`
- design reask input template: `{phase_dir_rel}/design_batch_<label>.reask.input.md`
- design reask output template: `{phase_dir_rel}/design_batch_<label>.reask.output.md`
- agent log: `{agent_logs_dir_rel}/design_{run_stamp}.md`
- Lean workspace root: current working directory (use workspace-relative paths; do not prefix them with the workspace path)

## Mission

Handle every top-level theorem/lemma in the work file in source order:
1. learn the declaration list once via `lean_file_outline`
2. read each owner declaration's region plus the contiguous owner-local scaffold cluster directly above it
3. group **compatible** owner declarations loosely from that current context for ask efficiency
4. pass current member context **plus earlier designed helpers** into `proof-refactor ask design`
5. optionally run one wide-trigger `design.reask` pass to catch omissions or correct weak first-pass helper design
6. materialize the final authoritative helper set for the group as real `theorem helper := by sorry` declarations in the work file
7. record per-decl checkpoint lines in the agent log

There is no durable batch state. Grouping is design-time only.

## Preconditions

- `{work_file_rel}` exists.
- Extract has already extracted scaffolds (top-level `<owner>.<scaffold>` declarations exist for each owner that needed extraction).

If any precondition fails, append a checkpoint failure line to the agent log and stop with `END_REASON:LIMIT`.

## Workflow

### 1. Scan declarations

Call `lean_file_outline` once on `{work_file_rel}` to learn the top-level declaration list and source order. Distinguish:

- **owner declarations**: top-level `theorem`/`lemma` whose name does not contain a `.` introduced by extraction (i.e. not of the form `<owner>.<scaffold>`).
- **scaffold declarations**: top-level `theorem`/`lemma` of the form `<owner>.<scaffold_name>` placed directly above their owner.

Walk owner declarations in source order. For each owner `D`:

- read `D`'s declaration region from `{work_file_rel}`
- if scaffolds for `D` exist directly above it (i.e. `<D>.<name>` cluster), read that contiguous owner-local scaffold cluster
- if `D` has no `extract "..."` sites and no scaffold cluster, treat its design as trivial: nothing to design, skip to the next owner

Boundary rules for these targeted reads:

- `D`'s declaration region means the complete top-level Lean declaration for `D`, from `D`'s declaration header through the end of its proof/body. It does **not** include any top-level scaffold or helper declarations above `D`.
- the contiguous owner-local scaffold cluster directly above `D` means the maximal consecutive block of top-level declarations immediately preceding `D` whose names exactly match `<D>.<scaffold_name>`. This cluster includes each scaffold declaration plus any attached attributes or doc comments. Stop at the first non-matching top-level declaration; do not absorb earlier helpers or unrelated declarations.

Use `Read` directly on the path for every read above. Do not delegate any of these reads to a subagent (`Agent`, `Explore`, `general-purpose`). Each declaration region and each contiguous scaffold cluster should be read at most once in this phase — when several owners sit immediately above the same cluster, reuse the prior read. Only re-read if the work file has been edited since the previous read of the same region.

### 2. Form design groups

Partition the non-trivial owner declarations into transient design groups in source order using the owner/scaffold reads from §1.

Grouping policy:

- prefer merging when one design discussion can reasonably cover all members, even if some members need light signature variants
- differences like `dist` vs `edist`, `Metric` vs `EMetric`, or nearby wrapper/notation changes do **not** force a split by themselves
- split only when one ask would become misleading or noisy:
  - genuinely different proof ideas
  - genuinely different helper families
  - incompatible insertion scope
  - incompatible local context that would make one shared design discussion confusing
- if one shared family works but some members need local variants, keep one group and ask for the shared family plus the variants
- When in doubt, prefer merging to splitting; ask is more efficient with larger groups, and a shared helper family can often be designed with minor signature variants or `uses` differences

Every non-trivial owner must belong to exactly one transient group. Choose any short `<label>` for filenames only.

### 3. First ask per group

For each group in group order:

- reuse the declaration-region and scaffold-cluster reads from §1; only re-read if the file has been edited since
- collect **earlier designed helpers** that are already in the work file above this group and relevant to the same mechanism
- use search as needed; stop once the helper family is clear

All reads use `Read` directly. Do not spawn a subagent to read project files.

Write `{phase_dir_rel}/design_batch_<label>.input.md` as a compact local Markdown context with:

- `members`
- `member_sources` — Lean source code of each member's owner declaration **plus the scaffold cluster directly above it**. This is the authoritative source for ask. Do not duplicate scaffold signatures or extract bodies elsewhere in the input and do not omit extract sites and proof bodies from this source; ask needs the full local context to design helpers.
- `relevant_definitions` (optional) — signatures or short bodies of `variable` blocks and local `def`/`abbrev` declarations from the work file whose names appear in the member declaration or scaffold signatures. Read these by name using targeted `Read` calls; do not read the whole file. Omit this section if all types in the signatures are standard Mathlib or self-evident from the scaffold text.
- `search_evidence` — keep this section short and factual. Send complete formal signatures (no proof), not just names.
You must send the lemma you searched before using a search tool like `lean_leansearch`.
Use this section only for search queries, returned signatures/results, and relevant earlier designed helpers already in scope.
Do **not** include local recommendations, utility tests, or conclusions about whether helpers are needed. `search_evidence` supplies evidence; ask decides the helper design.

Then run:

```bash
proof-refactor ask design "{phase_dir_rel}/design_batch_<label>.input.md" --prompts_dir "{prompts_root}" > "{phase_dir_rel}/design_batch_<label>.output.md" 2>&1
```

The output path is the raw ask output slot; only `proof-refactor ask` may write it.

If ask exits non-zero, read the failure text, record it in the agent log, then either salvage locally or stop with `END_REASON:LIMIT`.

### 4. Optional re-ask

After the first ask, decide whether one re-ask is warranted. **Re-ask exists to clean helper signatures, generalize them, and add omissive helpers.** It does not exist to drop helpers because scaffolds happen to share a statement — see the hard rule below. Trigger re-ask broadly when the first answer appears incomplete, too sparse, overfit, or mis-scoped.

Typical signals:

- helper count is suspiciously low for the amount of scaffolded structure
- only tiny local helpers were proposed
- a large outer scaffolded fact still has no helper-shaped abstraction
- repeated scaffolds in this group with no helpers to unify them
- helper signatures carry too many local aliases, witness names, or extra premises
- the first ask appears to have captured the wrong boundary of the mechanism
- omissions caused by extraction : `extract` did not cover a useful proof fragment (e.g some long proofs within have (>30 line)). 
  If so, you need use `lean-goal` for that place and add them into your input to enrich your ask context (Only when extract omission).

**Hard rule — scaffold/helper statement collisions.** Do NOT drop a helper because a scaffold has the same statement. Scaffolds are deleted in repair; helpers survive. If a helper's signature matches a scaffold's, keep the helper and add the helper name to that scaffold's `uses = [...]` list.

Run at most one re-ask per group.

If re-ask is triggered, write `{phase_dir_rel}/design_batch_<label>.reask.input.md` containing:

- the original group context
- the first ask output
- optional short revision notes if you have concrete suspected omissions, overfit signatures, missed generalizations, or search-covered trivial helpers
- an explicit instruction that this re-ask may revise, replace, rename, drop, generalize, or add helpers, and that its returned helper set should be treated as the corrected final answer for the group

`proof-refactor ask design` also carries prior same-batch conversation by default, including from `design_batch_<label>.input.md` to `design_batch_<label>.reask.input.md`. Still include the first ask output in the re-ask input so the revision target is explicit and auditable; a separate `gap_check` section is not required.

Then run:

```bash
proof-refactor ask design "{phase_dir_rel}/design_batch_<label>.reask.input.md" --prompts_dir "{prompts_root}" > "{phase_dir_rel}/design_batch_<label>.reask.output.md" 2>&1
```

If re-ask exits non-zero, read the failure text, record it in the agent log, then either salvage locally or stop with `END_REASON:LIMIT`.

Re-ask is authoritative when it runs. Treat `{phase_dir_rel}/design_batch_<label>.reask.output.md` as the final helper design for the group. Helpers that appeared only in the first ask output and not in the re-ask output are dropped.

### 5. Materialize helpers in the work file

For the active group:

- choose the authoritative ask output:
  - first ask output if no re-ask ran
  - re-ask output if re-ask ran
- extract helper names and Lean formal signatures from the authoritative ask output
- treat the authoritative ask output as authoritative for the active group
- keep every helper from the authoritative ask output, and no first-ask-only helpers that the re-ask dropped
- insert each authoritative helper as a real top-level Lean declaration of the form
  ```lean
  theorem helper_name <sig> := by sorry
  ```
  exactly once, at the least common visible scope, anchored before the first member declaration in the group (and above any matching scaffold cluster)
- preserve the `private` keyword when the authoritative ask output declares a helper as `private lemma` (narrow local-computation helper); insert it verbatim as `private lemma helper_name <sig> := by sorry`. Both `lemma` and `private lemma` helpers survive repair; `private` is namespace-local
- if a helper name is reused across declarations, it must refer to the same helper declaration — do not insert a duplicate
- if an authoritative helper has the same name and same formal signature as an earlier helper already in scope (already declared above this group in the work file), reuse that existing declaration rather than inserting a duplicate
- if the authoritative ask output is internally malformed or contradictory, stop with `END_REASON:LIMIT`
- a member may legitimately end with zero new helpers when the authoritative ask output actually says that no additional helper is needed

#### Annotations

After inserting helpers and reusing earlier-in-scope ones, write the on-file dependency contract as `-- uses = [...]` line comments above the **consumers** (owners and scaffolds). Helpers themselves are not annotated. The annotation rules and legal targets are defined in `{prompt_dir}/common.md` (§ Scaffolds, Helpers, and Annotations); apply them here.

For each member owner declaration in this group that the design assigns one or more helpers to, write a line directly above its existing `theorem` header listing every helper that owner must call:

```lean
-- uses = [helper_a, helper_b]
theorem putnam_xxxx ... := by ...
```

If the design assigns one or more helpers to a scaffold (`<owner>.<scaffold_name>` declaration above the owner), write a similar `-- uses = [...]` line above that scaffold declaration.

Rules:

- Skip the `-- uses = [...]` line entirely on owners or scaffolds whose authoritative helper list is empty.
- Do **not** write any `-- uses = [...]` (or any other dependency-annotation) line above a helper declaration. Helpers are pure producers; their consumer set is derived by scanning forward `uses = [...]` lines.
- When a helper is reused from an earlier group (already declared above), update the new owner's or scaffold's `uses = [...]` list only; the helper declaration itself is untouched.
- Names inside the brackets are helper names only — no scaffold names (`<owner>.<scaffold>`), no owner names, no imported lemmas, no self-name, no local `def`/`abbrev` names.

### 6. Verify and log

- run `lean_diagnostic_messages`
- if a helper signature or name needs local repair, repair it before continuing
- if verification still fails (errors outside acceptable `sorry` warnings), stop with `END_REASON:LIMIT`
- after annotation lines are written, run `lean_diagnostic_messages` once more — annotations are comments and should not break elaboration, but this catches accidental insertion above the wrong declaration or a mangled `theorem` header

For each group, append one line to the agent log under `## Checkpoints`:

```text
YYYY-MM-DD HH:MM · <first_member_decl>:design · <N> helpers, <M> members
```

If re-ask ran, also append:

```text
YYYY-MM-DD HH:MM · <first_member_decl>:design.reask · revised=<N> added=<M> dropped=<K>
```

### 7. End

After all owner declarations have been processed:

- if every non-trivial owner has its helper declarations inserted (and `lean_diagnostic_messages` shows no real errors), emit `END_REASON:COMPLETE`
- otherwise emit `END_REASON:LIMIT`

Output exactly one final line:

- `END_REASON:COMPLETE`
- `END_REASON:LIMIT`

## Rules

- Do not prove helpers or scaffolds in this phase. Helpers are inserted with body `by sorry`.
- Do not insert, remove, or rewrite `extract` wrappers in this phase.
- Shared helper declarations may be inserted once and then referenced from multiple owner declarations; insertion order is the source-order rule above.
- Do not invent new helper declarations in local routing that were not proposed by the authoritative ask output.
- If re-ask runs, the re-ask output is the authoritative final helper design for that group.
- Do not keep first-ask-only helpers that the re-ask dropped.
- Do not write or rewrite any `design_batch_*.output.md` yourself.
- Do not whole-file `Read` the work file. Use targeted region reads only, and do not repeat a region read unless the file has changed in this phase.
- Do not spawn `Agent` / `Explore` / `general-purpose` to read project files; call `Read` directly.
- Do not use `lean_run_code` in design. Helper-signature truth comes from inserting the helper into `{work_file_rel}` and running `lean_diagnostic_messages`; library lookup uses `lean_leansearch`, `lean_loogle`, `lean_local_search`. Design produces signatures and structure, not proof bodies.
