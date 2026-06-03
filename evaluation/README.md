# Evaluation

`evaluation/` contains post-run scoring and simple Lean file analysis scripts.
These scripts are intentionally outside the installable `proof_refactor`
package.

Use your own original, baseline, and Proof-Refactor output paths.

## Score One File

```bash
uv run python evaluation/score.py \
  --original <original.lean> \
  --baseline <baseline.lean> \
  --proof-refactor <refactor.lean> \
  --rubric-file evaluation/rubric.md \
  --output-json <output-score.json>
```

## Score A Directory

The batch scorer matches files by name across the three directories.

```bash
uv run python evaluation/batch_score.py \
  --original-dir <original-dir> \
  --baseline-dir <baseline-dir> \
  --proof-refactor-dir <refactor-dir> \
  --rubric-file evaluation/rubric.md \
  --output-json <scores.json> \
  --skip-existing \
  --workers 4
```

## Count Lean Words

```bash
uv run python evaluation/word_counter.py <lean-file-or-dir> <output.json>
```

Write generated scores under `output/` or another ignored directory, not under
tracked benchmark folders.
