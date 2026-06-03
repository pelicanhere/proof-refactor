# Prove Phase (light)

You are the **prove phase** for task `{theorem_name}`. This is one of four sequential phases driven by the Python runner.

Read `{prompt_dir}/common.md` and `{prompt_dir}/lean-lsp-tools-api.md` once, then prove objects locally in `{work_file_rel}`.

This is the no-plan variant. There is no `refactor_plan.md`. The work file is the only durable state. Prove order is the source order of `theorem`/`lemma` declarations whose body is currently `by sorry`.

## Paths

- work: `{work_file_rel}`
- prove input template: `{phase_dir_rel}/prove_<decl>__<object>.input.md`
- prove output template: `{phase_dir_rel}/prove_<decl>__<object>.output.md`
- agent log: `{agent_logs_dir_rel}/prove_{run_stamp}.md`
- Lean workspace root: current working directory (use workspace-relative paths; do not prefix them with the workspace path)

## Mission

Walk every top-level `theorem`/`lemma` whose body is `by sorry` in source order and prove it directly in place.

`{prompt_dir}/common.md` defines the structural vocabulary (owner / helper / scaffold) and the on-file dependency annotation (`-- uses = [...]`). Read it once before starting.

Source order is the prove order: a helper or scaffold inserted above an owner is visited before that owner. Helpers may be shared across multiple owners; the first occurrence (lowest in source order) is where the body is proved. A helper that is no longer `sorry` upstream is treated as already-proved and just called.

For each `sorry` object `O`:

1. ensure the local context for `O`'s owner region has been read (one-shot, see Workflow §1)
2. prove `O` directly in place
3. obey the `uses = [...]` contract on `O`: every name listed must be actually called in the finished proof body
4. use Lean MCP directly to understand, test, and verify the proof
5. keep the finished proof as short as reasonably possible

Default behavior: **do not use `proof-refactor ask prove`**. Use it only as a last resort after local MCP-guided attempts and relevant search have failed.

Do **not** introduce any new helper/theorem/lemma declarations in this phase. If the current object appears to need a new lemma, stop with `END_REASON:LIMIT`; that is a design failure, not a prove-phase task.

Maintain a short agent log at `{agent_logs_dir_rel}/prove_{run_stamp}.md` throughout the phase. It must contain at least `## Meta`, `## Checkpoints`, `## Summary`. Append a checkpoint line per finished object.

## Preconditions

- `{work_file_rel}` exists.
- If no top-level declaration has body `by sorry`, write the agent log and emit `END_REASON:COMPLETE`.

## Hard Rules

### 1. `uses = [...]` enforcement

- Every name in `-- uses = [...]` on a declaration MUST be actually called in that declaration's finished proof body. Earlier presence in scope does not suffice; prose does not suffice. (Mirrors the plan-variant proof prompt.)
- Allowed call forms: `exact helper_name ...`, `apply helper_name`, `refine helper_name ...`, `simpa using helper_name ...`, or any other tactic that names the helper as part of its arguments (e.g. `linarith [helper_name h1 h2]`, `grind [helper_name]`).
- When proving the owner: do not introduce a local `have` whose statement is the same as a top-level helper above the owner. **Call the helper instead.** A local `have` whose name shadows a global helper is always a bug.
- When proving a helper, that helper's body may call earlier-in-source-order helpers; it is independent otherwise.

### 2. Scaffolds are never called by name

- Scaffolds are NEVER called by name from any other declaration's body. The owner satisfies its `extract "<scaffold_name>" {{ ... }}` site with the existing block content already inside the owner; repair will consume scaffolds later. Do **not** write `exact <owner>.<scaffold_name> ...`, `apply <owner>.<scaffold_name>`, or any other reference to a scaffold name in the owner body or in any helper body.
- Scaffolds are proved at top level by filling their own `<owner>.<scaffold_name> := by sorry` body. They remain as top-level declarations until repair runs.

### 3. Prove one object at a time

- work in source order, object-by-object
- do not move to the next unresolved object until the current one is either `done` (no `sorry` and verified) or marked blocked
- stop the phase at the first object that does not finish

### 4. Local context, read once per owner region

- do not whole-file read the work file
- the one-shot read pattern is described in Workflow §1 below
- if a region has already been read and has not changed, do not read it again
- do not spawn subagents for proof search, diagnostics, or verification

### 5. No new lemmas

- do not add new top-level helper/scaffold/theorem/lemma declarations
- small local proof steps such as `have` are allowed only inside the active proof body — but never with the same statement as an existing top-level helper (see §1)

### 6. Append per-object checkpoint

- after each object finishes (or is marked blocked), append one line to `## Checkpoints` in the agent log:
  - `YYYY-MM-DD HH:MM · <decl>:<status> · attempts=<N>`
- if the phase stops on a blocked object, record the failure reason in `## Summary` and stop

## Shared-helper handling

A helper declaration only needs to be proved once (at its source-order location). If a later owner's `-- uses = [...]` lists a helper that already exists above it without `sorry`, the call is just an `exact helper_name ...` or similar in the owner's body — no work-file edit at the helper site is needed.

## Workflow

Use `lean_file_outline` once at the start of the phase to learn the declaration order. Then walk `sorry` declarations in source order — fill them in place, declaration by declaration.

### 1. Read all local context up front, then just fill sorries

Each `sorry` belongs to some owner declaration `D`'s region: helpers inserted above `D` for `D`, scaffolds `<D>.<...>` directly above `D`, and `D`'s own body if it carries internal `sorry`. The first time you encounter a `sorry` that lies in `D`'s region, do the following one-shot read and reuse it for every subsequent `sorry` in `D`'s region (until you edit a region, in which case re-read only that region):

- read `D`'s full declaration body from `{work_file_rel}`
- read every top-level scaffold declaration `<D>.<scaffold>` immediately above `D`
- read every helper declaration named in `D`'s `-- uses = [...]` annotation
- parse `D`'s `-- uses = [...]` annotation — those names must be actually called in `D`'s finished body
- for each scaffold above `D` that carries `-- uses = [...]`, parse its `uses = [...]` list — those names must be actually called in the finished scaffold body
- read the matching `extract "<scaffold_name>"` call site inside `D`'s body **only when** filling the corresponding scaffold's body and the local proof context at that site actually matters (most scaffolds can be proved from their signature alone)

Do not re-read a region that has not changed. Do not whole-file read the work file. Use `Read` directly. Use `lean_file_outline` only if direct name-targeted reads fail.

After this one-shot context read, just fill `sorry`s in source order — helpers first (in their own source order, which matches `uses = [...]` dependency order), then scaffolds (`<D>.<...>`, in source order), then `D`'s body if it carries any internal `sorry`.

### 2. Local prove loop for one object

For the active object `O`, follow the sorry-filling pattern:

1. understand context and goal
2. search/use local ingredients first
3. generate 2-3 short candidates
4. test before applying
5. ask only as a last resort
6. apply the working solution and verify

#### 2.1 Understand context and goal

1. locate the active `sorry` inside `O`'s proof body
2. run `lean_goal` at that tactic position first
3. use the current declaration body, relevant scaffold/helper context, and visible upstream declarations to understand the local proof state

Never target declaration headers or non-tactic lines with `lean_goal` / `lean_multi_attempt`.

#### 2.2 Search/use local ingredients first

Before broader search, try to solve the goal by explicitly calling helpers from `O`'s `-- uses = [...]` list (they are already proved earlier in source order, or you are about to prove them next). Never call a scaffold by name.

Preferred first forms:

- `exact helper_name ...`
- `apply helper_name`
- `refine helper_name ...`
- `simpa using helper_name ...`

If `O` is the owner declaration, the existing `extract "<scaffold_name>" {{ ... }}` block bodies are the owner's local proof for those sites — do not pull scaffolds in by name. Owner-side `sorry`s that are NOT inside an `extract` block are filled with `uses = [...]` helper calls, library lemmas, or short manual steps as usual.

If the goal still looks standard or Mathlib-shaped, use the bounded Lean MCP search order from `common.md` before attempting longer manual proofs.

#### 2.3 Generate 2-3 short candidates

Generate at most 2-3 short candidate proofs for `O`.

Prefer candidates in this order:

1. Calls to helpers listed in `O`'s `-- uses = [...]` annotation (already proved earlier in source order) 
2. short automation tactics such as `simpa`, `exact`, `rfl`, `omega`, `linarith`, `nlinarith`, `ring`, `field_simp`, `aesop`, `grind`, and small combinations like `field_simp [h1, h2]; grind` or `grind`, possibly fed with lemmas(e.g. `linarith [helper1]`, `grind [helper1, helper2]`).
3. one relevant library lemma found by Lean MCP search
4. one or two small manual proof steps

Keep candidates short and local. Shortest working proof wins.

#### 2.4 Test before applying

Use `lean_multi_attempt` to test the 2-3 short candidates before editing the file when possible.

- keep `lean_multi_attempt` snippets short and local
- if all current candidates fail, you may generate a small replacement batch, but stay within a small local search budget for this object
- if diagnostics or a query tactic offers a concrete `Try this`, use `lean_code_actions` and then re-check
- if a multiline proof is clearly needed, edit the object body directly and verify instead of forcing everything through one-line snippets

Do not stop after the first failed candidate if another short candidate remains untried.

#### 2.5 Ask only as a last resort

Default: skip ask.

Only consider `proof-refactor ask prove` after the same active object / proof hole has failed more than 10 local attempts.

Count as a local attempt:

- a failed direct helper-based candidate
- a failed `lean_multi_attempt` candidate
- a failed direct proof-body edit followed by verification

Use `proof-refactor ask prove` only if all of the following are true:

- the same active object / proof hole has already failed more than 10 local attempts
- required-upstream-call attempts failed
- short local candidates failed
- relevant Lean MCP search did not settle the proof

If you skip ask, do not create `prove_*.input.md` / `prove_*.output.md`.

If you do use ask:

- write `{phase_dir_rel}/prove_<O>.input.md` with only the local context needed for `O`
- run `proof-refactor ask prove`
- treat the result as advisory only
- if ask exits non-zero, record it in the agent log and continue locally

Ask never replaces direct Lean MCP verification. If ask suggests a candidate, test it in the same local object loop before applying.

#### 2.6 Apply the working solution and verify

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

Before marking `O` done, check that every helper listed in `O`'s `-- uses = [...]` annotation is actually called in the final proof body. A helper named in `uses = [...]` but never called means the proof is incomplete; either rewrite the body to use the helper, or fix the design failure (stop with `END_REASON:LIMIT`).

If `O` finishes, append a checkpoint line:

```text
YYYY-MM-DD HH:MM · <O>:done · attempts=<N>
```

If `O` still does not finish:

- append a checkpoint line with `partial` (real progress) or `hard` (no progress)
- update the agent log `## Summary` with the blocking object and reason
- stop with `END_REASON:LIMIT`

After `O` finishes, continue to the next `sorry` declaration in source order.

## End

After every top-level declaration is `sorry`-free in source order:

- update `{agent_logs_dir_rel}/prove_{run_stamp}.md`
- emit `END_REASON:COMPLETE`

Output exactly one final line:

- `END_REASON:COMPLETE`
- `END_REASON:LIMIT`

## Rules

- Attempt objects sequentially in source order. The first `sorry` declaration encountered is the active object until it is `done` or blocked.
- A scaffold proof may use helpers and stable imported declarations, but never another scaffold.
- Edit only the body of the owning object currently being attempted. A helper that is already `sorry`-free upstream is just called, not re-edited.
- Do not add new declarations, new imports, scratch files, or shadow overlays.
- Do not write or rewrite any `prove_*.output.md` yourself.
- On any non-`done` unresolved object, stop the phase immediately after appending the checkpoint line.
