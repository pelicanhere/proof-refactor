import Mathlib.Tactic
import Extraction

theorem Nat.Set.inter_comm (s t : Set Nat) : s ∩ t = t ∩ s := by
  ext x; constructor
  · intro h
    rw [Set.mem_inter_iff] at h ⊢
    exact h.symm
  · intro h
    rw [Set.mem_inter_iff] at h ⊢
    exact h.symm
