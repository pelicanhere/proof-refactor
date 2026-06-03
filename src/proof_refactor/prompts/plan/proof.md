# Prove Phase

You are the **prove phase** for task `{theorem_name}`. This is one of four sequential phases driven by the Python runner.

Read `{prompt_dir}/common.md` and `{prompt_dir}/lean-lsp-tools-api.md` once, then prove objects locally in `{work_file_rel}`.

## Paths

- work: `{work_file_rel}`
- plan: `{plan_rel}`
- prove input template: `{phase_dir_rel}/prove_<decl>__<object>.input.md`
- prove output template: `{phase_dir_rel}/prove_<decl>__<object>.output.md`
- agent log: `{agent_logs_dir_rel}/prove_{run_stamp}.md`
- Lean workspace root: current working directory (use workspace-relative paths; do not prefix them with the workspace path)

## Mission

Walk every declaration with `status: designed` in source order. Within each declaration, process objects in this order:

1. helper entries in list order
2. scaffold entries in list order

For each object `O`:

1. read only the local context you need
2. prove `O` directly in place
3. if `uses=[...]` is non-empty, actually call every listed helper in the final proof body
4. use Lean MCP directly to understand, test, and verify the proof
5. keep the finished proof as short as reasonably possible

After one declaration `D` has all helper/scaffold objects done, update `D`'s plan block in one batch, then move to the next declaration.

Default behavior: **do not use `proof-refactor ask prove`**. Use it only as a last resort after local MCP-guided attempts and relevant search have failed.

Do **not** introduce any new helper/theorem/lemma declarations in this phase. If the current object appears to need a new lemma, stop with `END_REASON:LIMIT`; that is a design failure, not a prove-phase task.

Maintain a short agent log at `{agent_logs_dir_rel}/prove_{run_stamp}.md` throughout the phase. It must contain at least `## Meta`, `## Checkpoints`, `## Summary`.

## Preconditions

- `{work_file_rel}` and `{plan_rel}` both exist.
- If no declaration has `status: designed`, write `target_phase: repair` in `## Meta`, write the agent log, and emit `END_REASON:COMPLETE`.

## Hard Rules

### 1. `uses` must be called

For every non-shared-reference object:

- if `uses=[a, b, ...]`, each listed helper must be actually called in the finished proof body
- earlier helper declarations merely existing in the file do not satisfy this
- prose does not satisfy this

### 2. Prove one object at a time

- work declaration-by-declaration, object-by-object
- do not move to the next unresolved object until the current one is either `done` or marked blocked
- stop the phase at the first owning object that does not finish

### 3. Use local context only

- do not whole-file read the work file
- for the current declaration `D`, read only the material needed for `D`:
  - `D`'s current declaration body from `{work_file_rel}`
  - each top-level scaffold declaration `<D>.<scaffold>` named in `D`'s plan block, only when that scaffold or its local proof context matters for the active object
  - each helper declaration named in the active object's `uses=[...]`
- if a declaration body, scaffold declaration, or helper declaration has already been read and has not changed, do not read it again
- do not spawn subagents for proof search, diagnostics, or verification

### 4. No new lemmas

- do not add new top-level helper/scaffold/theorem/lemma declarations
- small local proof steps such as `have` are allowed only inside the active proof body

### 5. Batch plan updates per declaration

- do not update `D`'s plan block object-by-object while `D` is still in progress
- only after every helper and scaffold object in `D` is `done` may you rebuild `### D` in the plan
- if the phase stops on a blocked object before `D` is complete, leave `### D` unchanged and record the failure in the agent log

## Shared-helper ownership

Before attempting a helper `O` in declaration `D`:

- if no earlier declaration block contains the same helper name, `O` is the owning encounter and must be actually proved
- if an earlier occurrence exists and is already `done`, `O` is a shared reference:
  - do not edit the work file
  - mark the current helper entry `status=done`
  - preserve its existing `attempts`
  - append a `done (shared reference)` line to `## Session Log`
- if an earlier occurrence exists but is not `done`, stop with `END_REASON:LIMIT`

Only owning helper encounters and scaffolds use the workflow below.

## Workflow

Process declarations one at a time in plan block order.

For each declaration `D` with `status: designed`:

### 1. Read declaration-local context once

- read the current `### D` block from `{plan_rel}` once at the start of `D`
- read only the material needed for `D`:
  - `D`'s current declaration body from `{work_file_rel}` once at the start of `D`, then reuse it across `D`'s objects until you edit that region
  - each top-level scaffold declaration `<D>.<scaffold>` named in `D`'s plan block, only when that scaffold or its local proof context matters for the active object
  - each helper declaration named in the active object's `uses=[...]`
- if `O` is a scaffold, read the matching `extract "<O>"` call site only when the local proof context matters
- if any of the above regions has already been read and has not changed, reuse the earlier read instead of issuing another one

Use `Read` directly. Use `lean_file_outline` only if direct name-targeted reads fail.

### 2. Object loop

Process `D`'s objects in prove order:

1. helpers in list order
2. scaffolds in list order

Let `O` be the current object.

For each `O`:

- if `O` is already `done`, skip it
- if `O` is a shared helper reference, mark it done in your local declaration-state scratch, preserve `attempts`, and continue
- otherwise run the local prove loop below

### 3. Local prove loop for one object

For the active object `O`, follow the sorry-filling pattern:

1. understand context and goal
2. search/use local ingredients first
3. generate 2-3 short candidates
4. test before applying
5. ask only as a last resort
6. apply the working solution and verify

#### 3.1 Understand context and goal

1. locate the active `sorry` inside `O`'s proof body
2. run `lean_goal` at that tactic position first
3. use the current declaration body, relevant scaffold context, and `uses` helpers to understand the local proof state

Never target declaration headers or non-tactic lines with `lean_goal` / `lean_multi_attempt`.

#### 3.2 Search/use local ingredients first

Before broader search, try to solve the goal by explicitly calling the helpers listed in `O.uses`.

Preferred first forms:

- `exact helper_name ...`
- `apply helper_name`
- `refine helper_name ...`
- `simpa using helper_name ...`

If `uses` is non-empty, the final proof must still contain explicit calls to those helpers before you mark `O` done.

If the goal still looks standard or Mathlib-shaped, use the bounded Lean MCP search order from `common.md` before attempting longer manual proofs.

#### 3.3 Generate 2-3 short candidates

Generate at most 2-3 short candidate proofs for `O`.

Prefer candidates in this order:

1. direct calls to required helpers from `uses`
2. short automation tactics such as `simpa`, `exact`, `rfl`, `omega`, `linarith`, `nlinarith`, `ring`, `aesop`, `grind`, maybe need
to feed hypotheses with these tactics, e.g. `linarith [h1, h2]`,
`grind [h1, h2, helper1, helper2]`. 
3. one relevant library lemma found by Lean MCP search
4. one or two small manual proof steps

Keep candidates short and local. Shortest working proof wins.

#### 3.4 Test before applying

Use `lean_multi_attempt` to test the 2-3 short candidates before editing the file when possible.

- keep `lean_multi_attempt` snippets short and local
- if all current candidates fail, you may generate a small replacement batch, but stay within a small local search budget for this object
- if diagnostics or a query tactic offers a concrete `Try this`, use `lean_code_actions` and then re-check
- if a multiline proof is clearly needed, edit the object body directly and verify instead of forcing everything through one-line snippets

Do not stop after the first failed candidate if another short candidate remains untried.

#### 3.5 Ask only as a last resort

Default: skip ask.

Only consider `proof-refactor ask prove` after the same active object / proof hole has failed more than 10 local attempts.

Count as a local attempt:

- a failed direct helper-based candidate
- a failed `lean_multi_attempt` candidate
- a failed direct proof-body edit followed by verification

Use `proof-refactor ask prove` only if all of the following are true:

- the same active object / proof hole has already failed more than 10 local attempts
- required-helper attempts failed
- short local candidates failed
- relevant Lean MCP search did not settle the proof

If you skip ask, do not create `prove_*.input.md` / `prove_*.output.md`.

If you do use ask:

- write `{phase_dir_rel}/prove_<D>__<O>.input.md` with only the local context needed for `O`
- run `proof-refactor ask prove`
- treat the result as advisory only
- if ask exits non-zero, record it in the agent log and continue locally

Ask never replaces direct Lean MCP verification. If ask suggests a candidate, test it in the same local object loop before applying.

#### 3.6 Apply the working solution and verify

After you have a candidate proof that appears to work:

1. write the finished proof body into `O` in `{work_file_rel}`
2. confirm the active proof hole has no remaining goals with `lean_goal` or a successful `lean_multi_attempt` result
3. run declaration-scoped `lean_diagnostic_messages`
4. if verification fails, continue working on the same object with another short candidate or a small local repair, then re-run declaration-scoped diagnostics

Stay within a bounded local attempt budget for the object, consistent with sorry-filling:

- work in small 2-3 candidate batches
- you may iterate a few such local batches on the same object
- do not turn one object into an unbounded open-ended search session
- only after the same active object / proof hole has failed more than 10 local attempts may you use last-resort ask
- if local MCP-guided attempts remain inconclusive after that, mark the object blocked

Before marking `O` done, check that every helper in `O.uses` is actually called in the final proof body.

If `O` finishes, record its result in your local declaration-state scratch:

- owning helper/scaffold: `status=done`, `attempts=<new N>`
- shared helper reference: `status=done`, unchanged `attempts`

If `O` still does not finish:

- record `status=partial` if there was real progress, otherwise `status=hard`
- record `attempts=<new N>`
- update the agent log with the blocking object and reason
- stop with `END_REASON:LIMIT`

### 4. Update the plan for D

Only after every helper and scaffold object in `D` is `done` in the current declaration loop:

- rebuild `### D` in `{plan_rel}` in one batch
- update every helper/scaffold object line in `D` to its final `status` / `attempts`
- preserve `uses`, `action`, `annotation`, `extract_suggestion`, and declaration `status`

Then append one line to `## Session Log`:

```text
YYYY-MM-DD HH:MM · <D>:prove · all objects done
```

After that, continue to the next designed declaration.

## End

After every `status: designed` declaration in source order has all objects `done`:

- write `target_phase: repair` in `## Meta`
- update `{agent_logs_dir_rel}/prove_{run_stamp}.md`
- emit `END_REASON:COMPLETE`

Output exactly one final line:

- `END_REASON:COMPLETE`
- `END_REASON:LIMIT`

## Rules

- Attempt objects sequentially in the authoritative prove order: helpers first, then scaffolds, per declaration.
- If a scaffold is being proved, it may use helpers and stable imported declarations, but never another scaffold.
- Edit only the body of the owning object currently being attempted. Shared references never trigger a work-file edit.
- Do not add new declarations, new imports, scratch files, or shadow overlays.
- Do not write or rewrite any `prove_*.output.md` yourself.
- Do not edit extract-owned fields (`action`, `extract_suggestion`) or design-owned list structure (`helpers:` / `scaffolds:`), except for updating an object's `status` and `attempts`.
- Do not change the owning declaration's `status`; it remains `designed` until repair.
- On any non-`done` unresolved owning object, stop the phase immediately after updating the plan and session log.
