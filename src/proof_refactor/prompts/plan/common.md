# Common Rules

Read this file exactly once at the start of each phase. It contains shared hard rules only.

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

## Model

- The plan file (`refactor_plan.md`) is the durable truth for scheduling and declaration state.
- Declaration identity is by declaration name. Scaffold identity is by scaffold name within the owning declaration.
- Helper names are globally unique across the run. If the same helper name appears in multiple declaration blocks, those entries refer to the same shared helper declaration and later occurrences are references only.
- Object-level `uses` is local helper dependency metadata only.
- A scaffold entry may and often should depend on earlier local helpers or earlier globally shared helpers via `uses=[...]`; this is how design forces prove to call those helpers.
- **Scaffolds are deleted in repair; helpers survive.** Never drop a helper just because a scaffold has the same statement — keep the helper and put it in that scaffold's `uses=[...]`.
- Within one declaration, the `helpers:` list order followed by the `scaffolds:` list order is the authoritative local prove order.
- Declaration source order decides shared-helper ownership: the first unmet declaration encounter for a helper name is the only occurrence that is actually proved.
- Object-level `uses` may mention helper names only. Never put a scaffold, a stable same-file declaration, or an external imported theorem in any object-level `uses`.
- Object-level `uses` may mention only helper names that appear earlier in the same declaration's `helpers:` list or whose first declaration encounter is earlier in source order.
- Same-file `def` / `abbrev` may appear in reads or proof bodies, but they are never scheduled declarations.

## Phase Ownership of Plan Fields

Each phase session owns a specific slice of the plan and must not write outside its slice.

| Field | Phase that writes it |
|---|---|
| `status` (scheduling: `todo` → `designed` → `done`, or `skipped`) | design (→ designed/skipped), repair (→ done) |
| `action` | extract |
| `annotation` | extract, design, repair |
| `extract_suggestion` | extract |
| `helpers` (list and entries) | design; prove updates only each entry's `status`/`attempts` |
| `scaffolds` (list and entries) | design (seeds list); prove updates each entry's `status`/`attempts`; repair sets the whole list to `(none)` |
| `## Meta: target_phase` | each phase sets it to the next phase on successful completion |
| `## Session Log` | every phase appends one or more lines |

All phases may read any plan field.

## Context Discipline

1. **Plan truth first** — use the current declaration block and object `uses` as the control surface.
2. **Name-targeted reads** — read only the declaration or object regions you need, located by declaration name, helper/scaffold name, `extract "<scaffold_name>"`, or direct dependency names from `uses`.
3. **Never read entire files** — do NOT issue a whole-file read of the work file or the source file. Do NOT use shell commands to dump entire files.
4. **No subagent for plain reads** — never spawn `Agent` / `Explore` / `general-purpose` (or any subagent) just to read project files for you. Use `Read` directly on the path. Subagents are reserved for cases where you would not otherwise be the one consuming the result.

If a tool requires a prior read before editing, satisfy it with one targeted read of the edit region. A grep, search result, or outline does not satisfy this pre-edit read requirement.

## Work File Policy

- The work file is the durable structural proof plane for `design`, `prove`, and `repair`.
- `prove` works directly in the work file and reads only the current object, its earlier helper dependencies from `uses`, and the matching scaffold call site when needed.
- A shared helper declaration may be physically placed before multiple later declarations that reference it; later reference entries do not imply duplicate helper declarations in the work file.
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
- Plan edits happen inline during the phase; there is no separate STAGE_RESULT receipt to hand back.
