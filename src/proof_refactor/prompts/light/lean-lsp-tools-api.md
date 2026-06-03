# Lean MCP Tools Reference

This is the Proof-Refactor phase reference for Lean MCP usage. It is intentionally
MCP-only and assumes the phase is running from the Lean workspace root with
the work file path shown in the phase prompt.

Use this file together with `common.md`: `common.md` owns verification/search
policy; this file only describes tool mechanics and common misuses.

Examples below show both invocation shape and typical return shape — use them to
parse tool output, not as policy.

## Core Rules

- Prefer live-file MCP tools over isolated experiments.
- Read only the source region needed for the current object or declaration.
- Use `lean_goal` and `lean_multi_attempt` only at real tactic positions inside a
  proof body, normally the line containing the current `sorry`.
- Use `lean_run_code` only for a small independent Lean fact that cannot be tested
  in the live work file. Do not use it as the default proof playground.

## Tool Guide

### `lean_goal`

Use before writing tactics for the active helper/scaffold, or after a material
local rewrite when the remaining goal is unclear.

- `file_path`: project-relative work file path.
- `line`: one-indexed line inside the current proof body.
- `column`: omit unless a precise term position is needed.

Expected use:

```text
lean_goal(file_path=work_file, line=sorry_line)
```

Typical result:

```json
{
  "goals_before": [],
  "goals_after": [
    {"goal": "n + m = m + n", "hypotheses": ["n : ℕ", "m : ℕ"]}
  ]
}
```

An empty `goals_after` array means the line closes all visible goals; completion
still follows the rules in `common.md`.

If the tool reports no useful goal, re-check that the line is inside the proof
body rather than on the declaration header.

### `lean_diagnostic_messages`

Use as directed by `common.md` and the active phase after accepted edit batches.

Expected use:

```text
lean_diagnostic_messages(file_path=work_file)
lean_diagnostic_messages(file_path=work_file, declaration_name=object_or_owner)
lean_diagnostic_messages(file_path=work_file, severity=1)
lean_diagnostic_messages(file_path=work_file, start_line=line, end_line=line)
```

Typical result:

```text
Clean:  []
Errors: ["l13c9-l13c17, severity: 1\nUnknown identifier `add_comm`",
         "l20c30-l20c49, severity: 1\nFunction expected at StrictMono"]
```

When imports fail, the structured payload carries a `failed_dependencies` list
(e.g. `"Unknown package 'Mathlib'"`); treat that as a build problem, not a proof
problem.

Diagnostics report Lean messages for the requested scope/filter. Completion
rules, `sorry` tolerance, and whole-file escalation are defined in `common.md`.

### `lean_code_actions`

Use when diagnostics contain a concrete "Try this" suggestion from `simp?`,
`exact?`, `apply?`, or a similar query tactic.

Expected use:

```text
lean_code_actions(file_path=work_file, line=suggestion_line)
```

The resolved result is a concrete edit (for example, replacing `simp?` with
`simp only [Nat.add_comm]`) ready to paste into the work file. After applying,
re-verify per `common.md`.

Apply only the resolved edit that belongs to the active object or declaration.

### `lean_multi_attempt`

Use to screen 2-3 short tactic candidates before editing.

Constraints:

- Target the line containing the current `sorry` or a real tactic line.
- Keep snippets single-line whenever possible.
- Include indentation exactly as it should appear in the proof body.
- Do not include comments in snippets.
- Do not use it to test a full multi-line proof. Edit the proof and verify
  instead.

Good candidate shape:

```text
lean_multi_attempt(file_path=work_file, line=sorry_line, snippets=[
  "  simpa using h",
  "  exact helper_name x hx",
  "  nlinarith [h1, h2]"
])
```

Bad candidate shape:

```text
lean_multi_attempt(file_path=work_file, line=decl_header_line, snippets=[
  "theorem new_name : P := by ..."
])
```

Typical result:

```text
[
  {"snippet": "  simpa using h",            "goals": []},
  {"snippet": "  exact Nat.lt_succ_self n", "goals": [...],
                                            "error": "Unknown identifier `n`"}
]
```

`goals: []` means that snippet closed all visible goals at the target line; the
work-file edit still has to pass `common.md` verification (empty goals alone do
not prove completion).

If all candidates fail with command-level parse errors, the line is probably not
a tactic position. Locate the active `sorry` or edit directly and verify.

### `lean_leansearch`

Semantic search for Mathlib-ish facts. Follow the search order in `common.md`.

Use concise natural-language or mixed Lean queries:

```text
lean_leansearch(query="division inequality positive denominator")
lean_leansearch(query="strict monotone on derivative positive")
```

Typical hit:

```json
{"name": "inner_mul_le_norm_mul_norm",
 "type": "⟪x, y⟫ ≤ ‖x‖ * ‖y‖",
 "module": "Analysis.InnerProductSpace.Basic",
 "docString": "Cauchy-Schwarz inequality"}
```

Stop after a satisfactory result. Do not search repeatedly just to be thorough.

### `lean_loogle`

Type-pattern search when the theorem shape is clear but the name is not.

Example query styles:

```text
"?a / ?c <= ?b"
"|- _ < _ -> _ * _ < _ * _"
```

Typical hit:

```json
{"name": "List.map",
 "type": "(α → β) → List α → List β",
 "module": "Init.Data.List.Basic"}
```

Keep queries close to the active goal. Test promising names in the live file.

### `lean_hammer_premise`

Use only when the normal search path did not give a direct route and you need
candidate premises for automation.

Expected use:

```text
lean_hammer_premise(file_path=work_file, line=sorry_line, column=2, num_results=16)
```

Typical result — a plain array of theorem-name strings:

```text
["Finset.sum_comm", "List.map_id", "MulOpposite.unop_injective", ...]
```

Feed a few returned names into `simp only [...]`, `aesop`, or `grind [...]`
candidates, then screen with `lean_multi_attempt` before editing. Do not treat
it as a replacement for understanding the active goal.

### `lean_state_search`

Use only for a stubborn, specific proof state after the normal search path was
insufficient.

Expected use:

```text
lean_state_search(file_path=work_file, line=sorry_line, column=2, num_results=5)
```

Typical hit:

```json
{"name": "lemma_name",
 "state": "similar goal shape",
 "nextTactic": "apply lemma_name",
 "relevance": 0.88}
```

`nextTactic` is the tactic mathlib actually used at the matched state — adapt
it and screen with `lean_multi_attempt` before editing.

This is a stuck-case helper, not the default search tool for every object.

### `lean_local_search`

Use for confirmation when a candidate name or namespace is already suspected.

Examples:

```text
lean_local_search(query="div_le_iff", limit=10)
lean_local_search(query="StrictMonoOn", limit=10)
```

Typical hit:

```json
{"name": "add_zero",
 "kind": "theorem",
 "file": "Init/Grind/Ring/Envelope.lean"}
```

This is not the broad discovery tool for a mathematical idea.

### `lean_file_outline`

Use only when name-targeted reads fail and you need declaration locations or a
compact file structure view. Do not use it as a substitute for reading the
specific declaration region before editing.

Typical result:

```json
{"imports": ["Mathlib.Data.Real.Basic"],
 "declarations": [
   {"name": "add_comm", "kind": "theorem", "line": 12,
    "type": "∀ a b : ℕ, a + b = b + a"}
 ]}
```

### `lean_hover_info`

Use sparingly to inspect a signature or implicit argument at a concrete
identifier position. It is useful for API-shape mistakes such as wrong namespace,
argument order, or coercion target.

`column` is required and must point at the first character of the identifier.

Expected use:

```text
lean_hover_info(file_path=work_file, line=ident_line, column=ident_start_col)
```

Typical result:

```json
{"range":       {"start": {"line": 20, "character": 30},
                 "end":   {"line": 20, "character": 45}},
 "contents":    "Type signature and doc",
 "diagnostics": ["errors at that location, if any"]}
```

### `lean_run_code`

Use only for a small independent Lean fact that cannot be tested in the live
work file; editing and verifying the work file is the normal path (per
`common.md` Work File Policy).

- `code`: the full Lean snippet to run in isolation.

Expected use:

```text
lean_run_code(code="#check @Nat.add_comm")
```

Typical result:

```text
l1c1-l1c18, severity: 3
Nat.add_comm : ∀ (n m : ℕ), n + m = m + n
```

Severity 1 is error, 2 is warning, 3 is info. Each call runs in isolation with
no persistent state, so imports and helpers must be restated in the snippet.

### `lean_profile_proof`

Use only when a verified or nearly verified proof is slow, times out, or causes
diagnostics to lag.

Expected use:

```text
lean_profile_proof(file_path=work_file, line=decl_start_line, top_n=5)
```

Typical result:

```json
{"total_time_ms": 2450,
 "lines": [
   {"line": 42, "tactic": "simp [complex_lemma]", "time_ms": 1200},
   {"line": 43, "tactic": "ring",                 "time_ms":  850}
 ]}
```

Focus on lines consuming more than roughly 20% of `total_time_ms`; those are
worth replacing with explicit rewrites.

Do not use profiling during normal sorry filling; it is a performance diagnostic
for slow proof bodies.
