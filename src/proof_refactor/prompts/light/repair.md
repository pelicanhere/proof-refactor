# Repair Phase (light)

You are the **repair phase** for task `{theorem_name}`. This is the final phase driven by the Python runner.

Read `{prompt_dir}/common.md` and `{prompt_dir}/lean-lsp-tools-api.md` once, then repair declarations locally.

This is the no-plan variant. There is no `refactor_plan.md`. The work file is the only durable state.

## Paths

- phase dir: `{phase_dir_rel}`
- work: `{work_file_rel}`
- agent log: `{agent_logs_dir_rel}/repair_{run_stamp}.md`
- Lean workspace root: current working directory (use workspace-relative paths; do not prefix them with the workspace path)

## Mission

For each owner declaration `D` that:

- contains at least one `extract "<scaffold_name>"` site, AND
- has the matching `<D>.<scaffold_name>` declaration above it sorry-free (i.e. proved),

repair it in source order.

**Repair means: scaffold-guided rewrite.** Reorganize `D` by following the proof structure already certified in its scaffolds.

Repair does **not** prove new helpers, create new top-level declarations, or keep scaffolds (every consumed scaffold is deleted).

## What scaffolds and helpers are

See `{prompt_dir}/common.md` § Scaffolds, Helpers, and Annotations for the canonical vocabulary, the `-- uses = [...]` annotation rule, and the rule that scaffolds are never called by name.

Repair-time relevant facts: helpers may survive this phase as final lemmas; scaffolds never do — every scaffold is consumed and deleted in §3 below. Design-time annotations are stripped in §5.

## Preconditions

- `{work_file_rel}` exists.
- Every owner declaration to be repaired has all the scaffolds it references already proved (`<D>.<scaffold>` declarations above are sorry-free).
- If an owner still has a referenced scaffold that is `by sorry`, stop with `END_REASON:LIMIT`.

## Workflow

Use `lean_file_outline` once at the start to learn the declaration order. For each owner declaration `D` in source order that has at least one `extract` site whose scaffold above it is sorry-free:

### 1. Read local context once

Read only the material needed for `D`:

- `D`'s current declaration body from `{work_file_rel}`
- each top-level scaffold declaration `<D>.<scaffold>` above `D` referenced by an `extract` site in `D`
- each helper declaration named in `D`'s body or in the matching scaffold proofs

Reuse helper reads from earlier in this phase. Do not whole-file read the work file. Use `lean_file_outline` only if direct targeted reads fail.

### 2. Look first

Read `D`'s body, the scaffolds, and the helpers together. Two questions before editing:

- Which owner-local `have`s, `let`s, or intermediate lines were the source material for those scaffolds/helpers and therefore should disappear after rewrite?
- After those lines disappear, which remaining uses must be rewired to helper-backed proof steps?

### 3. Rewrite the owner and consume the scaffold

One continuous edit pass with per-edit verification. Apply substeps in order. After every edit run `lean_diagnostic_messages(file_path=work_file, declaration_name=D)`; on regression, revert that single edit and continue.

Caps per `D`: repair pass (§3-§4) <= 8 hunks; <= 60 lines per hunk; saturate at 3 consecutive failures. The stuck-spot escape below applies during any substep where regressions cluster at one spot.

MCP tools used in this step:

- `lean_multi_attempt(file_path=work_file, line=L, snippets=[...])` — screen 2-3 one-line candidates before editing; `L` is the line you're about to replace.

For each scaffold, rewrite the matching owner site to the smallest owner-side proof justified by the scaffold's proved structure. If the scaffold proof, the scaffold's `-- uses = [...]` line, or the owner's `-- uses = [...]` line names a helper, the rewritten owner site MUST call that helper by name in the final owner body. The call may be direct (`exact helper_name ...`, `simpa using helper_name ...`) or inside a short local wrapper proof, but it may not be replaced by an unrelated manual proof fragment. In the same edit, delete any owner-local `have`, `let`, or trivial binder that the rewrite has made obsolete, rewrite downstream uses to the new form, and then delete the consumed top-level scaffold declaration. Do **not** mechanically paste scaffold text just because it compiles. After this step, no scaffold name may remain in the final output; helper declarations stay unchanged.

If a required helper call does not typecheck because of local `let` opacity, instance mismatch, coercion mismatch, or another adapter problem, repair the owner-side adapter locally and retry. Do not fall back to proving the same fact manually without the helper. If the helper still cannot be called within the edit budget, stop with `END_REASON:LIMIT` and record the helper-consumption failure.

#### What scaffold-guided rewrite means here

Example 1: helper extracted from an owner-local `have`
Then you can delete the original `have`

Before repair: the owner still contains the original extracted proof block.
```lean
-- This helper comes from `main.bound_step`
theorem le_chain_helper {{a b c : α}} [Preorder α]
    (hab : a <= b) (hbc : b <= c) : a <= c := by
  exact le_trans hab hbc

theorem main.bound_step (hx : x <= z) (hy : z <= y) : x <= y := by
  exact le_chain_helper hx hy

theorem main (hx : x <= z) (hy : z <= y) (hz : R) : Goal := by
  have h_bound : x <= y := by
    extract "bound_step" {{
      have hxz : x <= z := hx
      have hzy : z <= y := hy
      exact le_trans hxz hzy
    }}
  exact finish h_bound hz
```

After repair: delete the original `have` and call the abstract helper directly.
```lean
theorem main (hx : x <= z) (hy : z <= y) (hz : R) : Goal := by
  exact finish (le_chain_helper hx hy) hz
```

Example 2: multiple extracted scaffolds share the same helper and just
directly call it in the scaffold body, so the owner can call it directly too.
just delete the original `have`s and call the helper directly.

Before repair: the owner still contains the original extracted `have`s.
```lean
theorem swap_mem_helper {{α : Type*}} {{a b : Set α}} {{x : α}}
    (hx : x ∈ a ∩ b) : x ∈ b ∩ a := by
  exact ⟨hx.2, hx.1⟩

theorem main.subset_swap_left (hx : x ∈ s ∩ t) : x ∈ t ∩ s := by
  exact swap_mem_helper hx

theorem main.subset_swap_right (hy : y ∈ u ∩ v) : y ∈ v ∩ u := by
  exact swap_mem_helper hy

theorem main
    (hx : x ∈ s ∩ t) (hy : y ∈ u ∩ v)
    (finish : x ∈ t ∩ s → y ∈ v ∩ u → Goal) : Goal := by
  have hswap₁ : x ∈ t ∩ s := by
    extract "subset_swap_left" {{
      rw [Set.mem_inter_iff] at hx
      rw [Set.mem_inter_iff]
      exact ⟨hx.2, hx.1⟩
    }}
  have hswap₂ : y ∈ v ∩ u := by
    extract "subset_swap_right" {{
      rw [Set.mem_inter_iff] at hy
      rw [Set.mem_inter_iff]
      exact ⟨hy.2, hy.1⟩
    }}
  exact finish hswap₁ hswap₂
```

After repair: delete both original `have`s and call the shared helper directly.
```lean
theorem main
    (hx : x ∈ s ∩ t) (hy : y ∈ u ∩ v)
    (finish : x ∈ t ∩ s → y ∈ v ∩ u → Goal) : Goal := by
  exact finish (swap_mem_helper hx) (swap_mem_helper hy)
```

Example 3: keep the owner-local `have`, but replace its extracted body
by a call to the proved helper.

The scaffold is a temporary proof obligation produced by extraction.
During repair, the scaffold is deleted, but the local `have` is kept
because the positivity fact is a meaningful intermediate fact used later.

Before repair: the owner still contains the original extracted proof block.
```lean
theorem positive_of_pos_le {{α : Type*}} [Preorder α] {{a b : α}}
    (ha : 0 < a) (hab : a ≤ b) : 0 < b := by
  exact lt_of_lt_of_le ha hab

-- Temporary scaffold above the owner, proved using the helper.
theorem main.denom_pos (hn : 0 < n) (hnm : n ≤ m) : 0 < m := by
  exact positive_of_pos_le hn hnm

theorem main
    (hn : 0 < n) (hnm : n ≤ m)
    (finish : 0 < m → m ≠ 0 → Goal) : Goal := by
  have hm_pos : 0 < m := by
    extract "denom_pos" {{
      exact lt_of_lt_of_le hn hnm
    }}
  have hm_ne_zero : m ≠ 0 := by
    exact Nat.ne_of_gt hm_pos
  exact finish hm_pos hm_ne_zero

After repair: keep the local `have`, but replace the extracted block
by a call to the helper. The temporary scaffold `main.denom_pos` is deleted.
```lean
theorem main
    (hn : 0 < n) (hnm : n ≤ m)
    (finish : 0 < m → m ≠ 0 → Goal) : Goal := by
  have hm_pos : 0 < m := by
    exact positive_of_pos_le hn hnm
  have hm_ne_zero : m ≠ 0 := by
    exact Nat.ne_of_gt hm_pos
  exact finish hm_pos hm_ne_zero
```

Before repair: the extract block is a middle proof fragment.
It changes the current goal from `g (f x) <= g (f y)` to `x <= y`.
```lean
theorem monotone_comp_apply_helper
    {{α β γ : Type*}} [Preorder α] [Preorder β] [Preorder γ]
    {{f : α → β}} {{g : β → γ}} {{x y : α}}
    (hf : Monotone f) (hg : Monotone g) (hxy : x <= y) :
    g (f x) <= g (f y) := by
  exact hg (hf hxy)

-- Temporary scaffold above the owner.
-- It represents the extracted goal-transforming fragment:
-- after this fragment, the remaining goal is `x <= y`.
theorem main.apply_mono_chain
    (hf : Monotone f) (hg : Monotone g) :
    x <= y → g (f x) <= g (f y) := by
  intro hxy
  exact monotone_comp_apply_helper hf hg hxy

theorem main
    (hf : Monotone f) (hg : Monotone g)
    (hxz : x <= z) (hzy : z <= y) :
    g (f x) <= g (f y) := by
  extract "apply_mono_chain" {{
    apply hg
    apply hf
  }}
  exact le_trans hxz hzy
```

After repair: replace the extracted goal-transforming fragment
by the helper-based proof. The temporary scaffold is deleted.

```lean
theorem monotone_comp_apply_helper
    {{α β γ : Type*}} [Preorder α] [Preorder β] [Preorder γ]
    {{f : α → β}} {{g : β → γ}} {{x y : α}}
    (hf : Monotone f) (hg : Monotone g) (hxy : x <= y) :
    g (f x) <= g (f y) := by
  exact hg (hf hxy)

theorem main
    (hf : Monotone f) (hg : Monotone g)
    (hxz : x <= z) (hzy : z <= y) :
    g (f x) <= g (f y) := by
  exact monotone_comp_apply_helper hf hg (le_trans hxz hzy)
```

### 4. Apply offered code actions

Run after §3, before §5.

```text
lean_diagnostic_messages(file_path=work_file, declaration_name=D)
```

This pass is specifically for warnings and linter diagnostics inside `D`; do not add a severity filter here. For each warning whose payload offers a code action — `Try this:`, linter auto-fixes (`linter.unusedVariables`, `linter.unusedSimpArgs`, `simpNF`, ...), or similar — apply via `lean_code_actions(file_path=work_file, line=<warning_line>)`. Verify after; revert any action that broke `D`. If a warning has no offered action but the fix is a small obvious local edit inside `D`, make that manual fix once and verify again. In particular, remove unnecessary semicolon broadcasts in single-goal code such as `rw [...]` followed by `<;> ring` / `<;> simp` / `<;> linarith`; rewrite them to the plain tactic (`ring`, `simp`, `linarith`, etc.) when only one goal remains. Also fix similarly obvious trivial unused binders or redundant `simp` arguments. Skip only warnings with no action and no clear small local fix. Run once; do not loop. If an action lands slightly off, fix the small local fallout manually and verify again.

### 5. Strip design annotations and add mathematical doc-comments

After the scaffold-guided rewrite (§3) and the code-actions pass (§4) verify cleanly, perform a final cleanup pass on `D` and on every helper named in `D`'s `-- uses = [...]` line or in any of `D`'s consumed scaffolds' `-- uses = [...]` lines. Helpers do not carry their own annotation; the "helpers belonging to `D`" set is computed by this forward scan.

Before stripping annotations, enforce helper consumption:

- every helper named in `D`'s `-- uses = [...]` line, or in any of `D`'s consumed scaffolds' `-- uses = [...]` lines, must be called by name in the final body of `D`
- calls that existed only in deleted scaffold declarations do not count
- if any such helper is not called in `D`, do not finalize its cleanup (no doc-comment, no annotation strip) and do not leave it as a final lemma; repair the owner to call it, or stop with `END_REASON:LIMIT`

For `D`:

- delete the `-- uses = [...]` line directly above `D`'s `theorem` header, if present
- consumed scaffolds and their `-- uses = [...]` lines were already deleted alongside the scaffold declaration in §3 — confirm none remain

For each helper `H` named in `D`'s (or `D`'s consumed scaffolds') `-- uses = [...]` lines:

- if `H` was folded back into `D` (or any other owner) during §3 and no longer exists at top level, no cleanup is needed
- otherwise `H` survives at top level. If a doc-comment was already written above `H` by an earlier owner's §5, leave it. Otherwise write a Lean `/-- ... -/` doc-comment in one or two sentences describing `H`'s mathematical content. The doc-comment states the lemma's meaning — no proof references, no implementation hints, no mention of "helper", "scaffold", or "putnam"
- if Lean/linter diagnostics show that `H`'s statement has unused parameters, unused variables, or similarly trivial unused assumptions, you may make the smallest local statement edit that removes only that unused material, then update `H`'s proof and the already-required calls from `D` accordingly. Do not otherwise strengthen, generalize, rename, reorder, or restate `H`
- **Multi-owner deferral, derived by scan**: before writing `H`'s doc-comment, scan all surviving `-- uses = [...]` lines in the work file (excluding `D`'s own line, which is about to be deleted). If any other owner `D'` later in source order still names `H` in its (or its scaffolds') `-- uses = [...]` and has not yet been repaired in this phase, defer `H`'s doc-comment to that later owner's §5; leave `H` untouched for now
- if `H` has no remaining consumer in any `-- uses = [...]` line (e.g. its only consumers were dropped), the cleanup is best-effort: emit a generic doc-comment derived from `H`'s signature

Example before:
```lean
-- uses = [prod_one_add_div_le]
theorem putnam_1963_a4 ... := by ...

theorem prod_one_add_div_le (N M : ℕ) (hN : 0 < N) (hNM : N ≤ M) (r : ℝ)
    (hr0 : 0 ≤ r) (hr1 : r ≤ 1) :
    ∏ j ∈ Finset.Icc N M, (1 + r / (j : ℝ)) ≤ ((M : ℝ) + 1) / (N : ℝ) := by ...
```

After:
```lean
/-- Telescoping bound: for `0 ≤ r ≤ 1` and `0 < N ≤ M`, the product
`∏ j ∈ [N, M], (1 + r / j)` is bounded above by `(M + 1) / N`. -/
theorem prod_one_add_div_le ... := by ...
```

Run `lean_diagnostic_messages` after annotation cleanup. Doc-comments attach to the immediately following declaration; misplacement shifts them onto the wrong target. If diagnostics regress, revert the cleanup edit on the offending declaration and try again.

### 6. Log

After successful verification of `D`, append one line to the agent log under `## Checkpoints`:

```text
YYYY-MM-DD HH:MM · <D>:repair · scaffolds consumed; pivots:{{N}}; spots:{{M}}; outcome:ok
```

Repeat for the next repairable owner until all are processed.

### 7. End

After all repairable declarations have been processed:

- emit `END_REASON:COMPLETE`

Also write a short log to `{agent_logs_dir_rel}/repair_{run_stamp}.md` with sections `## Meta`, `## Checkpoints`, `## Summary`.

Output exactly one final line:

- `END_REASON:COMPLETE`
- `END_REASON:LIMIT`
