import Aesop.BuiltinRules.Intros
import LeanSearchClient.Syntax
import Mathlib.Algebra.Group.Nat.Defs
import Mathlib.Lean.Meta.RefinedDiscrTree.Encode
import Mathlib.Tactic.ExtractGoal
import Mathlib.Tactic.SwapVar

namespace Mathlib.Tactic.Extract

open Lean Elab Tactic Meta

/-! ## Core utilities -/

/-- Collect all FVarIds used in the proof term after running tactics. -/
def collectProofUsedFVars (goalsBefore goalsAfter : List MVarId) : MetaM (Array FVarId) :=
  withoutModifyingMCtx do
    for goal in goalsAfter do goal.assign default
    let mut fvarsState : CollectFVars.State := {}
    for goal in goalsBefore do
      fvarsState := collectFVars fvarsState (← instantiateMVars (.mvar goal))
    pure fvarsState.fvarIds

open PrettyPrinter Delaborator in
def PrettyPrinter.ppSignatureNoUniverses (c : Name) : MetaM FormatWithInfos := do
  let decl ← getConstInfo c
  let e := Expr.const c (decl.levelParams.map mkLevelParam)
  if pp.raw.get (← getOptions) then
    return s!"{c} : {decl.type}"
  else
    let (stx, infos) ←
      delabCore e (delab := delabConstWithSignature (universes := false))
    return ⟨← ppTerm ⟨stx⟩, infos⟩

def MessageData.signatureNoUniverses (c : Name) : MessageData :=
  .lazy fun ctx => do
    match (← ctx.runMetaM (PrettyPrinter.ppSignatureNoUniverses c) |>.toBaseIO) with
    | .ok fmt => return .ofFormatWithInfos fmt
    | .error ex => return m!"[Error pretty printing signature: {ex}]{Format.line}{c}"

/-! ## Main tactic -/

/--
`extract { tacs }` runs `tacs` and outputs `new_goal → old_goal` or just `goal` as a theorem signature.
`extract * { tacs }` keeps all variables without cleanup.
-/
syntax (name := extractTactic)
  "extract" ("*")? (ppSpace str)? "{" tacticSeq "}" : tactic

elab_rules : tactic
  | `(tactic| extract $[*%$star]? $[$name?:str]? { $seq:tacticSeq }) => do
    let name ← if let some name := name?
                then pure name.getString.toName
                else mkAuxDeclName `extracted
    let keepAll := star.isSome
    let goal ← getMainGoal

    -- Record external fvars and original context (before running tactics, no cleanup)
    let (tyBefore, lctxBefore, instsBefore, externalFVarSet) ← goal.withContext do
      let fvarIds := (← getLCtx).getFVarIds
      let fvarSet := fvarIds.foldl (init := (∅ : Std.HashSet FVarId)) (·.insert ·)
      pure (← instantiateMVars (← goal.getType), ← getLCtx, ← getLocalInstances, fvarSet)
    evalTacticSeq seq
    let goalsAfter ← getUnsolvedGoals
    let proofUsedFVars ← collectProofUsedFVars [goal] goalsAfter

    let msg ← withoutModifyingEnv <| withoutModifyingState do
      let (innerTy, forceKeepAll) ← match goalsAfter with
        | [] =>
          pure (tyBefore, false)
        | [g] =>
          let isFalse := (← instantiateMVars (← g.getType)).consumeMData.isConstOf ``False
          let (g, _) ← g.renameInaccessibleFVars
          g.withContext do
            -- Cleanup: preserve all external fvars, only clean unused internal ones
            let externalFVars : Array FVarId := (← getLCtx).getFVarIds.filter ((externalFVarSet : Std.HashSet FVarId).contains ·)
            let g ← if keepAll || isFalse then pure g
                     else g.cleanup (toPreserve := externalFVars) (indirectProps := False)
            let internalFVars := (← g.getDecl).lctx.getFVarIds.filter (!externalFVarSet.contains ·)
            let (_, g) ← g.revert (clearAuxDeclsInsteadOfRevert := true) internalFVars

            let tyAfter ← instantiateMVars (← g.getType)
            if (← isDefEq tyAfter tyBefore) || isFalse then
              pure (tyBefore, isFalse)
            else
              pure (← mkArrow tyAfter tyBefore, false)
        | _ => throwError "Tactic `extract` failed: multiple goals not supported"

      withLCtx lctxBefore instsBefore do
        let g := (← mkFreshExprMVar innerTy).mvarId!
        let g ← if keepAll || forceKeepAll then pure g
          else g.cleanup (toPreserve := proofUsedFVars.filter (externalFVarSet.contains ·)) (indirectProps := False)
        let (_, g) ← g.revert (clearAuxDeclsInsteadOfRevert := true) (← g.getDecl).lctx.getFVarIds
        let ty ← instantiateMVars (← g.getType)
        if ty.hasExprMVar then
          throwError "Extracted type has metavariables: {ty}"
        let ty ← Term.levelMVarToParam ty
        let seenLevels := collectLevelParams {} ty
        let levels := (← Term.getLevelNames).filter
                        fun u => seenLevels.visitedLevel.contains (.param u)
        addAndCompile <| Declaration.axiomDecl
          { name := name, levelParams := levels, isUnsafe := false, type := ty }
        withOptions
          (·.setBool `pp.funBinderTypes true
          |>.setBool `pp.proofs true
          |>.setBool `pp.coercions true
          |>.setBool `pp.numericTypes true
          |>.setBool `pp.letVarTypes true) do
          let sig ← addMessageContext <| MessageData.signatureNoUniverses name
          let cmd := if ← Meta.isProp ty then "theorem" else "def"
          pure m!"{cmd} {sig} := sorry"
    logInfo msg

end Mathlib.Tactic.Extract
