# Refactor Plan: THEOREM_NAME

## Meta
- **task_label**: `THEOREM_NAME`
- **source**: `SOURCE_FILE`
- **work**: `WORK_FILE_PATH`
- **target_phase**: extract
- **status**: in_progress

<!-- target_phase values: extract | design | prove | repair | complete -->
<!-- Each phase session writes target_phase to the next phase on successful completion. -->
<!-- status values: in_progress | complete -->


---

## Sections
<!-- Static file-level index only. -->

<!-- Template:
### Main
- **source_span**: `SOURCE_FILE:1-999`
- **anchor_before**: `first_decl_name`
-->

---

## Declarations
<!-- Flat declaration blocks. Identity is the ### heading name. -->
<!-- Each block is replaced atomically — never edit individual fields. -->
<!-- Cross-stage truth lives only in the fields below. -->

<!-- Template:
### decl_name
- **status**: todo | designed | done | skipped
- **action**: design
- **annotation**: ``
- **extract_suggestion**:
  - `(none)`
  - block=have h_main : foo := by\n  have h_aux : bar := by\n    exact baz\n  exact qux | scaffold=main_ineq | mechanism=strictMonoOn_of_deriv_pos + HasDerivAt
- **helpers**:
  - `(pending)`
  - `helper_name | uses=[a, b] | status=todo | attempts=0`
- **scaffolds**:
  - `(pending)`
  - `scaffold_name | uses=[a, b] | status=todo | attempts=0`
--><!-- Object status: todo | done | partial | hard -->

<!-- Within one declaration, the `helpers:` list order followed by the `scaffolds:` list
     order is the authoritative local prove order.
     Shared helper names are globally unique.
     If the same helper appears in multiple declarations, prove it only on first unmet
     encounter in source order. Later occurrences are references only.
     Object-level `uses` may mention only helper names that appear earlier in the same
     declaration's `helpers:` list or whose first declaration encounter is earlier in
     source order.
     After design, bare scaffold names are invalid: each scaffold line must use the same
     canonical object-entry form with explicit `uses=[...]`. -->

---

## Session Log
<!-- Append-only: YYYY-MM-DD HH:MM · decl:stage · description -->
