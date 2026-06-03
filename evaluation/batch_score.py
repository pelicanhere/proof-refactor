#!/usr/bin/env python3
"""Batch-score Lean proof refactor comparisons."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

if __package__:
    from .score import (
        METHOD_BASELINE,
        METHOD_PIPELINE,
        METRIC_GROUPS,
        ProblemFiles,
        ScoreOptions,
        add_score_arguments,
        build_tool_config,
        read_text,
        score_problem,
    )
else:
    from score import (
        METHOD_BASELINE,
        METHOD_PIPELINE,
        METRIC_GROUPS,
        ProblemFiles,
        ScoreOptions,
        add_score_arguments,
        build_tool_config,
        read_text,
        score_problem,
    )


def list_problem_files(directory: Path, extensions: tuple[str, ...]) -> dict[str, Path]:
    if not directory.exists() or not directory.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    result: dict[str, Path] = {}
    for path in directory.iterdir():
        if path.is_file() and (not extensions or path.suffix in extensions):
            result[path.stem] = path
    return result


def find_common_problems(
    original_dir: Path,
    baseline_dir: Path,
    proof_refactor_dir: Path,
    extensions: tuple[str, ...],
) -> list[ProblemFiles]:
    originals = list_problem_files(original_dir, extensions)
    baselines = list_problem_files(baseline_dir, extensions)
    pipelines = list_problem_files(proof_refactor_dir, extensions)

    return [
        ProblemFiles(
            question_name=name,
            original_path=originals[name],
            baseline_path=baselines[name],
            proof_refactor_path=pipelines[name],
        )
        for name in sorted(set(originals) & set(baselines) & set(pipelines))
    ]


def load_existing_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Output JSON must be a top-level object: {path}")
    return data


def completed_questions(data: dict[str, Any]) -> set[str]:
    """Return questions that already have complete scores for both methods."""
    done: set[str] = set()
    for question_name, question_obj in data.items():
        if not isinstance(question_obj, dict):
            continue

        for method in (METHOD_BASELINE, METHOD_PIPELINE):
            method_obj = question_obj.get(method)
            overall = method_obj.get("overall") if isinstance(method_obj, dict) else None
            if not isinstance(overall, dict) or not isinstance(overall.get("score"), (int, float)):
                break
        else:
            done.add(question_name)
    return done


def merge_score(existing: dict[str, Any], new_score: dict[str, Any]) -> dict[str, Any]:
    for question_name, methods in new_score.items():
        if not isinstance(methods, dict):
            raise ValueError(f"Question entry must be object: {question_name}")
        if not isinstance(existing.setdefault(question_name, {}), dict):
            existing[question_name] = {}
        existing[question_name].update(methods)
    return existing


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_markdown_log(question_name: str, question_score: dict[str, Any]) -> str:
    lines = [
        f"# {question_name} Refactor Scoring Rationale",
        "",
        "## Scores",
        "",
        "| Method | Overall |",
        "|---|---:|",
    ]
    for method in (METHOD_BASELINE, METHOD_PIPELINE):
        score = question_score[method]["overall"]["score"]
        lines.append(f"| `{method}` | {score:.2f} / 5 |")
    lines.append("")

    for method in (METHOD_BASELINE, METHOD_PIPELINE):
        obj = question_score[method]
        lines.extend(
            [
                f"## {method}",
                "",
                obj.get("reason", ""),
                "",
                "| Direction | Metric | Score |",
                "|---|---|---:|",
            ]
        )
        for group, metrics in METRIC_GROUPS.items():
            for metric in metrics:
                lines.append(f"| `{group}` | `{metric}` | {obj[group][metric]:.1f} |")
        lines.extend([f"| `overall` | `score` | {obj['overall']['score']:.2f} |", ""])

    baseline_score = question_score[METHOD_BASELINE]["overall"]["score"]
    pipeline_score = question_score[METHOD_PIPELINE]["overall"]["score"]
    if pipeline_score > baseline_score:
        final = f"The proof_refactor_pipeline result is judged better by {pipeline_score - baseline_score:.2f} points overall."
    elif baseline_score > pipeline_score:
        final = f"The benchmark_refactor result is judged better by {baseline_score - pipeline_score:.2f} points overall."
    else:
        final = "The two refactor methods receive the same overall score."
    lines.extend(["## Final Judgment", "", final, ""])
    return "\n".join(lines)


def write_mark_log(output_json: Path, question_name: str, question_score: dict[str, Any]) -> Path:
    log_dir = output_json.parent / "mark_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{question_name}.md"
    log_path.write_text(make_markdown_log(question_name, question_score), encoding="utf-8")
    return log_path


def iter_selected(problems: list[ProblemFiles], only: set[str] | None, skip_done: set[str]) -> Iterable[ProblemFiles]:
    for problem in problems:
        if only is not None and problem.question_name not in only:
            continue
        if problem.question_name in skip_done:
            continue
        yield problem


def write_error_log(raw_response_dir: Path, question_name: str, exc: BaseException) -> Path:
    raw_response_dir.mkdir(parents=True, exist_ok=True)
    path = raw_response_dir / f"{question_name}.error.txt"
    path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch-score Lean proof refactors via an OpenAI-compatible API.")
    parser.add_argument("--original-dir", required=True, type=Path)
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--proof-refactor-dir", required=True, type=Path)
    parser.add_argument("--rubric-file", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--extensions", nargs="+", default=[".lean"], help="File suffixes to score, default: .lean")
    parser.add_argument("--only", nargs="*", default=None, help="Optional list of question names to score")
    parser.add_argument("--skip-existing", action="store_true", help="Skip questions already present in output JSON")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of common problems to score")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds between sequential score submissions")
    parser.add_argument("--workers", type=int, default=1, help="Number of problems to score in parallel")
    parser.add_argument("--fail-fast", action="store_true", help="Stop the batch after one scoring failure")
    parser.add_argument("--dry-run", action="store_true", help="List common problems without API calls")
    add_score_arguments(parser)
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    options = ScoreOptions.from_args(args)
    if not options.api_key and not args.dry_run:
        print("Missing API key. Set OPENAI_API_KEY or pass --api-key.", file=sys.stderr)
        return 2
    if not args.rubric_file.exists() or not args.rubric_file.is_file():
        print(f"Rubric file not found: {args.rubric_file}", file=sys.stderr)
        return 2

    rubric_text = read_text(args.rubric_file, args.max_chars_rubric)
    if not rubric_text.strip():
        print(f"Rubric file is empty: {args.rubric_file}", file=sys.stderr)
        return 2

    problems = find_common_problems(
        args.original_dir,
        args.baseline_dir,
        args.proof_refactor_dir,
        tuple(args.extensions),
    )
    if args.limit is not None:
        problems = problems[: args.limit]

    existing = load_existing_json(args.output_json)
    skip_done = completed_questions(existing) if args.skip_existing else set()
    only = set(args.only) if args.only else None
    selected = list(iter_selected(problems, only, skip_done))
    print(f"Found {len(problems)} common problem(s). Selected {len(selected)} for scoring.")
    print(f"Using rubric: {args.rubric_file}")
    if options.enable_lean_search:
        print(f"LeanSearch tool enabled: {args.lean_search_url}")
    if args.dry_run:
        for problem in selected:
            print(problem.question_name)
        return 0
    if args.workers < 1:
        raise ValueError("--workers must be >= 1")

    raw_response_dir = args.raw_response_dir or (args.output_json.parent / "raw_responses")
    tool_config = build_tool_config(args)

    def commit_score(problem: ProblemFiles, score: dict[str, Any]) -> None:
        existing_local = merge_score(load_existing_json(args.output_json), score)
        write_json(args.output_json, existing_local)
        log_path = write_mark_log(args.output_json, problem.question_name, existing_local[problem.question_name])
        baseline_score = existing_local[problem.question_name][METHOD_BASELINE]["overall"]["score"]
        pipeline_score = existing_local[problem.question_name][METHOD_PIPELINE]["overall"]["score"]
        print(
            f"  wrote {args.output_json} and {log_path} | "
            f"{METHOD_BASELINE}={baseline_score:.2f}, {METHOD_PIPELINE}={pipeline_score:.2f}"
        )

    if args.workers == 1:
        for index, problem in enumerate(selected, start=1):
            print(f"[{index}/{len(selected)}] Scoring {problem.question_name} ...")
            try:
                commit_score(problem, score_problem(problem, options, rubric_text, raw_response_dir, tool_config))
            except Exception as exc:
                error_path = write_error_log(raw_response_dir, problem.question_name, exc)
                print(
                    f"  ERROR scoring {problem.question_name}: {type(exc).__name__}: {exc}\n"
                    f"  wrote error log: {error_path}",
                    file=sys.stderr,
                )
                if args.fail_fast:
                    raise
            if args.sleep > 0 and index < len(selected):
                time.sleep(args.sleep)
    else:
        print(f"Parallel scoring enabled: workers={args.workers}")
        if args.sleep > 0:
            print("Note: --sleep only applies to sequential mode; ignored in parallel mode.")
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_problem = {
                executor.submit(score_problem, problem, options, rubric_text, raw_response_dir, tool_config): problem
                for problem in selected
            }
            for completed, future in enumerate(as_completed(future_to_problem), start=1):
                problem = future_to_problem[future]
                try:
                    commit_score(problem, future.result())
                    print(f"[{completed}/{len(selected)}] Completed {problem.question_name}")
                except Exception as exc:
                    error_path = write_error_log(raw_response_dir, problem.question_name, exc)
                    print(
                        f"[{completed}/{len(selected)}] ERROR scoring {problem.question_name}: "
                        f"{type(exc).__name__}: {exc}\n"
                        f"  wrote error log: {error_path}",
                        file=sys.stderr,
                    )
                    if args.fail_fast:
                        for pending in future_to_problem:
                            pending.cancel()
                        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
