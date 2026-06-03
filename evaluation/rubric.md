===== DETAILED EVALUATION RUBRIC =====

The proof files are assumed to have already passed Lean verification. Do not score correctness. Score only refactor quality: structure, helper quality, tactic transparency, reuse, and human readability.

All scores are from 1.0 to 5.0, where higher is better. Half-points such as 3.5 or 4.5 are allowed. Be consistent across methods. When comparing benchmark_refactor and proof_refactor_pipeline, calibrate their scores relative to each other.

Do not reward theorem count or lemma count by itself. Do not reward length reduction by itself. Penalize fake modularity: wrappers, dead helpers, huge helpers, or tactic-golf helpers. Prefer meaningful, named, locally checkable mathematical lemmas. A single-use helper is acceptable if it exposes real structure. Broad tactics are acceptable only when they close local routine goals, not when they hide the main proof idea.

## structure

Required metrics:

- main_theorem_slimness
- complexity_distribution
- dependency_clarity

Meaning:

- main_theorem_slimness: whether the main theorem has become a readable proof outline rather than a monolithic proof body.
- complexity_distribution: whether proof complexity is distributed into meaningful helper lemmas rather than hidden in one huge helper.
- dependency_clarity: whether the dependency relation among helpers and the main theorem is clear and shallow.

Scoring guide:

- 5: main proof is slim, helper structure is balanced, dependencies are clear.
- 4: good structure with minor imbalance.
- 3: some structural improvement, but still proof-script-heavy or uneven.
- 2: main proof is shorter mostly by hiding complexity.
- 1: structure is worse or obviously metric-gamed.

Inspection checklist:

- Inspect top-level helper lemmas.
- Inspect private helper lemmas.
- Inspect the main theorem body.
- Inspect local `have` blocks.
- Judge whether the main theorem reads like a proof outline.
- Judge whether helper lemmas carry meaningful proof structure.

## signature_quality

Required metrics:

- statement_naturalness
- binder_economy
- generality

Meaning:

- statement_naturalness: whether helper statements look like meaningful mathematical lemmas.
- binder_economy: whether helper signatures avoid excessive local hypotheses and unnecessary parameters.
- generality: whether helper lemmas are generalized beyond a local proof fragment when appropriate.

Note that: If two helpers' signatures have the same conclusion, but one has more general assumptions, the more general one is better and add extra assumptions should be penalized. 





Scoring guide:

- 5: helper statements are natural, concise, and reusable.
- 4: mostly natural, with some theorem-specific statements.
- 3: usable but local or proof-script-like.
- 2: many over-specialized helpers.
- 1: helpers are mostly meaningless wrappers or fragments.

Inspection checklist:

- Judge whether each helper name expresses mathematical content.
- Judge whether each helper statement is natural.
- Judge whether the statement is too local to the original proof.
- Judge whether it has too many binders or hypotheses.
- Judge whether it is more general than the immediate scaffold or merely copied from it.

## tactic_quality

Required metrics:

- tactic_transparency
- explicit_lemma_use
- broad_tactic_control

Meaning:

- tactic_transparency: whether proof bodies use understandable local reasoning rather than opaque closures.
- explicit_lemma_use: whether the refactored proof actually calls named helpers in meaningful places.
- broad_tactic_control: whether broad tactics such as `simp_all`, `aesop`, `grind`, `omega`, or large `nlinarith` calls are controlled and not hiding major structure.

Do not penalize all automation. Local uses of `ring`, `linarith`, `norm_num`, `field_simp`, `positivity`, or explicit `simp [lemmas]` may be good style. A typical good use case is 
to use this tactic to prove some straightforward algebraic manipulation or inequality
or logical tautology but tedious.


Benign or usually acceptable tactics:

```lean
ring
norm_num
field_simp
positivity
linarith [explicit facts]
simp [specific_lemma]
rw [specific_lemma]
```

Potentially broad or opaque tactics:

```lean
simp_all
aesop
grind
omega
large nlinarith without explicit structure
```

Scoring guide:

- 5: explicit lemma use dominates; broad tactics are rare and local.
- 4: some broad tactics, but they do not hide major proof structure.
- 3: moderate opaque closure.
- 2: many helpers are closed by broad tactics.
- 1: proof is mostly tactic golf.

Inspection checklist:

- Judge whether broad tactics are used only for local routine goals.
- Judge whether named helpers are actually called in meaningful places.
- Judge whether the proof hides the main idea behind automation.

## reuse

Required metrics:

- helper_usefulness
- reuse_potential
- no_dead_helpers

Meaning:

- helper_usefulness: whether helpers play real structural roles in the proof.
- reuse_potential: whether some helpers could plausibly be reused outside the exact local proof position.
- no_dead_helpers: whether there are no unused, orphan, or purely decorative helpers.

Scoring guide:

- 5: helpers are clearly useful; several have reuse potential; no dead helpers.
- 4: all helpers are justified; some reuse potential.
- 3: mostly single-use helpers, but structurally useful.
- 2: many single-use helpers with weak semantic value.
- 1: unused or fake helpers are common.

Inspection checklist:

- Check whether helpers are actually used.
- Check whether single-use helpers are structurally meaningful.
- Check whether helpers are reused by multiple parts of the proof.
- Check whether any helpers are unused or merely decorative.
- Check whether any helpers are wrappers around another lemma without real abstraction.

## human_review

Required metrics:

- proof_readability
- mathlib_style
- maintainability

Meaning:

- proof_readability: whether a human can read the proof as a clear mathematical argument.
- mathlib_style: whether names, locality, statement shape, binder order, and tactic usage are close to Mathlib style.
- maintainability: whether future changes would likely be localized and easy to repair.

Mathlib style is general as much as possible, if you generalize type or typeclass assumptions, e.g form `f : ℝ → ℝ` to `f : α → β` with some typeclass assumptions, it is better. And if you find the proof use more mathlib lemmas for those new proof, it is better. 

Scoring guide:

- 5: very readable, Mathlib-like, easy to maintain.
- 4: good proof-engineering style.
- 3: acceptable but still somewhat ad hoc.
- 2: hard to maintain.
- 1: barely readable despite compiling.

Inspection checklist:

- Judge whether a human reader can recover the mathematical proof outline.
- Judge whether names, binder order, locality, and statement shape are close to Mathlib style.
- Judge whether future proof repairs would likely be localized.

## overall

Compute overall.score as the simple average of all 15 non-overall metric scores for the method, rounded to 2 decimals.

## Output requirement

Return only the required JSON object. Each method must include a `reason` string. The `reason` should be concise but substantive, usually 3-6 sentences, explaining why the method received its scores. Do not put Markdown outside the JSON.
