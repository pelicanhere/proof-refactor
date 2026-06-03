import Extraction.Extract


/--
info: theorem extract_test_one (n : ℕ) : ∃ (f : ℕ → ℕ), f (n + (1 : ℕ) ^ (2 : ℕ)) + (1 : ℕ) = (1 : ℕ) + n := sorry
---
warning: declaration uses 'sorry'
-/
#guard_msgs in
example (hn : 1 = 1) :
    ∀ n, ∃ f : Nat → Nat, f (n + 1 ^ 2) + 1 = 1 + n := by
  intro n
  extract "extract_test_one" {
    let f : Nat → Nat := fun x => n + x
    refine ⟨f, ?_⟩
    sorry
  }

/--
info: theorem extract_test_nested (n : ℕ) :
  let f : ℕ → ℕ := fun (x : ℕ) ↦ x + n;
  (f = fun (x : ℕ) ↦ x + n) → ∃ (f : ℕ → ℕ), f (n + (1 : ℕ) ^ (2 : ℕ)) = n + (1 : ℕ) := sorry
---
warning: declaration uses 'sorry'
-/
#guard_msgs in
example : ∀ n : Nat, ∃ f : Nat → Nat, f (n + 1 ^ 2) = n + 1 := by
  intro n
  let g := fun (x : Nat) => x + id x
  let f : Nat → Nat := fun x => x + n
  have hf : f = fun x => x + n := by rfl
  extract "extract_test_nested" {
    refine ⟨f, ?_⟩
    rw [hf]
    sorry
  }

/-- info: theorem extract_test_cleanup : True := sorry -/
#guard_msgs (info) in
example : True := by
  have h : 1 = 1 := rfl
  extract "extract_test_cleanup" {
    trivial
  }

-- External fvars should appear as parameters only when used.
/--
info: theorem extract_test_external_fvars (x : ℕ) (h1 : x = (2 : ℕ)) (h2 : (2 : ℕ) = (3 : ℕ)) :
  (∀ (n : ℕ), n > (3 : ℕ) → n > (0 : ℕ)) → ∀ (n : ℕ), n > x → n > (0 : ℕ) := sorry
-/
#guard_msgs (info) in
example (x : Nat) (h : x > 0) (h1 : x = 2) (h2 : 2 = 3) :
    ∀ n, n > x → n > 0 := by
  extract "extract_test_external_fvars" {
    rw [h1, h2]
  }
  intro n hn
  exact Nat.lt_trans (by decide : 0 < 3) hn
