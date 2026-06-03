# Common Rules (light variant)

Read this file exactly once at the start of each phase. It contains shared hard rules only.

This is the **no-plan** variant: there is no `refactor_plan.md`. The work file is the only durable cross-phase state. Per-phase notes go in the agent log under `## Checkpoints`.

## Verification

- Use `lean_diagnostic_messages` for Lean verification.
- Use `lean_goal` or a successful `lean_multi_attempt` result to confirm that the active proof hole has no remaining goals; empty diagnostics alone do not prove completion.
- Declaration-scoped or severity-filtered diagnostics are allowed for local object work when the work file has unrelated pending `sorry`s or diagnostics.
- Whole-file diagnostics are reserved for final cleanup and phase completion gates; they are not required after local proof edits or scaffold deletion.
- If a diagnostic result is unclear or may hide an error introduced outside the active object, escalate to a direct whole-file check. Do not keep re-checking the same unchanged file state.
- Once a scaffold or declaration has been folded back or deleted in the current phase, do not run targeted diagnostics against that deleted name.
- Do NOT use Bash for Lean verification, scratch Lean experiments, or whole-file source dumping. `proof-refactor ask` commands listed in phase prompts are allowed.
- Do NOT use `lake build`, `lake env lean`, or other shell Lean verification.
- `sorry` warnings are acceptable during `design`, `prove`, and unfinished `repair`.
- Real Lean errors are not acceptable.
- If Lean MCP is unavailable, stop cleanly with `END_REASON:LIMIT`.

## Scaffolds, Helpers, and Annotations

The work file is the only durable cross-phase state. There is no plan file.

### Vocabulary

- **Owner declaration** — top-level `theorem`/`lemma` whose name is a bare identifier (e.g. `putnam_1963_a4`). The original problem statement.
- **Helper declaration** — top-level `theorem`/`lemma` whose name is a bare identifier (e.g. `prod_one_add_div_le`), inserted by design with body `by sorry`. Helpers may be shared across owners; the first owning encounter in source order is where the body is proved, later occurrences are references.
- **Scaffold declaration** — temporary top-level declaration extracted from one local proof site inside an owner. Naming is always `<owner_name>.<scaffold_name>` (e.g. `putnam_1963_a4.bound_step`), placed directly above the owner. In the owner proof, the original site appears as `extract "<scaffold_name>" {{ ... }}`. A scaffold records a proved local proof step or local goal transformation that originally lived inside the owner. Scaffolds are inserted by `lean_extract` during extract, proved at top level during prove, then **consumed** (rewritten back into the owner and deleted) during repair. Scaffolds are not retained final lemmas. **Scaffolds are deleted in repair; helpers survive — never drop a helper just because a scaffold has the same statement.**
- **Same-file `def` / `abbrev`** may appear in reads or proof bodies, but they are never scheduled declarations.

### Identity and ordering

- Declaration identity is the Lean declaration name.
- Helper names are globally unique across the work file.
- Within one owner `D`'s region (helpers above `D` introduced for `D`, scaffolds `<D>.<...>` directly above `D`, and `D`'s body), prove order is source order: helpers first, then scaffolds, then `D` body if it carries `sorry`.

### On-file dependency annotation (`-- uses = [...]`)

Because there is no plan file, each consumer (owner or scaffold) records the helpers it calls as a single Lean line comment directly above its `theorem` header. These are ordinary comments — Lean ignores them. Helpers themselves are not annotated; the set of owners that own/use a given helper is recovered by scanning these forward `uses = [...]` lines across the work file.

```text
-- uses = [prod_one_add_div_le, exists_harmonic_tail_gt]
theorem putnam_1963_a4 ... := by ...
```

```text
-- uses = [prod_one_add_div_le]
theorem putnam_1963_a4.bound_step ... := by ...
```

Phase responsibilities:

- **Design** writes `-- uses = [helper_a, ...]` above each owner the design assigned helpers to, and above each scaffold an authoritative helper covers. Helpers carry no annotation.
- **Prove** reads these `uses = [...]` lists and enforces the call contract on the consumer.
- **Repair** strips every `-- uses = [...]` line at the end and writes a `/-- mathematical-meaning -/` Lean doc-comment above each surviving helper. The mapping "helpers belonging to this owner" is computed by forward scan, not from a stored reverse annotation.

### Hard rules for `uses = [...]`

- `uses = [...]` may mention **helper names only** — never scaffold names (`<owner>.<scaffold>`), never owner names, never imported lemmas, never the declaration's own name, never local `def`/`abbrev` names. A scaffold is consumed by its owner via the `extract "<scaffold_name>" {{ ... }}` site, never by being listed as a dependency.
- `uses = [...]` on a declaration `D` may mention only helper names whose declaration appears **earlier in source order** than `D`'s declaration in the work file.
- `uses = [...]` on a scaffold follows the same restrictions as on owners (helper names only).
- A finished proof body MUST actually call every name listed in its `uses = [...]`. Earlier presence in scope does not suffice; prose does not suffice.
- When the helper list is empty, omit the `uses = [...]` line entirely.

### Scaffolds are never called by name

Scaffolds exist only to be proved at top level and consumed by repair. **Do not call a scaffold by name from any other declaration's body.** The owner satisfies its `extract "<scaffold_name>" {{ ... }}` site with the existing block content already inside the owner; repair replaces that site with the right closure later. Helper bodies and other scaffold bodies likewise never call scaffolds. This is the structural reason scaffold names are also forbidden inside any `uses = [...]` list.

## Context Discipline

1. **Source order first** — use `lean_file_outline` once per phase to learn declaration names and order. After that, navigate by declaration name and the `<owner>.<scaffold>` naming convention.
2. **Name-targeted reads** — read only the declaration or object regions you need, located by declaration name, helper/scaffold name, `extract "<scaffold_name>"` site, or direct dependency names from the active proof body.
3. **Never read entire files** — do NOT issue a whole-file read of the work file or the source file. Do NOT use shell commands to dump entire files.
4. **No subagent for plain reads** — never spawn `Agent` / `Explore` / `general-purpose` (or any subagent) just to read project files for you. Use `Read` directly on the path. Subagents are reserved for cases where you would not otherwise be the one consuming the result.

If a tool requires a prior read before editing, satisfy it with one targeted read of the edit region. A grep, search result, or outline does not satisfy this pre-edit read requirement.

## Work File Policy

- The work file is the durable structural proof plane for `design`, `prove`, and `repair`.
- `prove` works directly in the work file and reads only the current object, its earlier helper dependencies, and the matching scaffold call site when needed.
- A shared helper declaration is physically placed before any later declaration that references it; design must not insert a duplicate of an existing in-scope helper with the same signature.
- Do not create shadow imports, copied declaration overlays, or scratch renaming layers.
- `import Extraction` is introduced by `lean_extract` during extract and stays in the work file through design, prove, and repair. Phases must not delete it; the Python runner strips it after the post-repair check passes.

## Search Tools (Library Lemmas)

`common.md` owns search policy. `lean-lsp-tools-api.md` only describes tool mechanics.

- Search before hand-proving when a goal or helper candidate looks standard, symmetric, algebraic, order-theoretic, or otherwise Mathlib-ish.
- Skip search when required local helpers, concise closure tactics, or obvious stable declarations already close the object.
- Use this bounded order:
  1. `lean_leansearch` — default semantic search for ideas, mechanisms, and goal meaning.
  2. `lean_loogle` — only when semantic search was insufficient and the target type shape is clear.
  3. `lean_local_search` — only when a candidate name or namespace is already suspected.
  4. Stuck-only helpers: `lean_hammer_premise`, `lean_state_search`, and `lean_profile_proof`; these are not the normal search path.
- Stop as soon as a search result clearly settles the mechanism or gives a satisfactory lemma. Do not continue searching just to be thorough.
- Search tools are allowed in `design` and `prove`; `repair` should not use search to invent a new proof route.

## Background Waits

When waiting for a background command's output (e.g., `proof-refactor ask` writing its result to a temp file):

- Use `Monitor` with an `until`-loop, e.g. `until [ -s "$OUTPUT" ]; do sleep 2; done`. One Monitor call streams a single completion event back — no busy-wait.
- Or launch the command with `run_in_background: true` and wait for the completion notification.
- **Never** chain `sleep N && <check>` in a Bash call. The harness blocks this pattern; retrying with a shorter sleep does not work around it.

## Outputs

- Each phase session ends with exactly one final line:
  - `END_REASON:COMPLETE`
  - `END_REASON:LIMIT`
- Each phase also writes a short agent log at the phase-specific path listed in that phase's `## Paths` section, containing at minimum the sections:
  - `## Meta`
  - `## Checkpoints`
  - `## Summary`
- All cross-phase progress is recorded in the work file (helpers inserted, scaffolds present/consumed, `sorry`s replaced) and in the agent log. There is no plan file to edit.
