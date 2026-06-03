#!/usr/bin/env python3
"""Score one Lean proof refactor comparison."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from proof_refactor.config import load_project_dotenv

load_project_dotenv()

DEFAULT_BASE_URL = "https://www.packyapi.com/v1"
DEFAULT_MODEL = "gpt-5.4-high"
DEFAULT_LEAN_SEARCH_URL = "https://leansearch.net/search"

METHOD_BASELINE = "benchmark_refactor"
METHOD_PIPELINE = "proof_refactor_pipeline"


@dataclass(frozen=True)
class ProblemFiles:
    question_name: str
    original_path: Path
    baseline_path: Path
    proof_refactor_path: Path


@dataclass(frozen=True)
class ScoreOptions:
    api_key: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    api_retries: int
    api_retry_sleep: float
    max_chars_per_file: int
    no_json_repair: bool
    no_schema_repair: bool
    enable_lean_search: bool
    max_tool_rounds: int
    max_tool_calls: int

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "ScoreOptions":
        return cls(
            api_key=args.api_key or "",
            base_url=args.base_url,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            api_retries=args.api_retries,
            api_retry_sleep=args.api_retry_sleep,
            max_chars_per_file=args.max_chars_per_file,
            no_json_repair=args.no_json_repair,
            no_schema_repair=args.no_schema_repair,
            enable_lean_search=args.enable_lean_search,
            max_tool_rounds=args.max_tool_rounds,
            max_tool_calls=args.max_tool_calls,
        )


def add_score_arguments(parser: argparse.ArgumentParser) -> None:
    """Add options shared by single-problem and batch scoring CLIs."""
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY"))
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=3000)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--api-retries",
        type=int,
        default=2,
        help="Retries for scoring/repair API requests after timeout or transient HTTP errors",
    )
    parser.add_argument("--api-retry-sleep", type=float, default=15.0, help="Base seconds to sleep between API retries")
    parser.add_argument("--max-chars-per-file", type=int, default=120_000)
    parser.add_argument("--max-chars-rubric", type=int, default=80_000)
    parser.add_argument("--no-json-repair", action="store_true", help="Disable malformed JSON repair")
    parser.add_argument("--no-schema-repair", action="store_true", help="Disable missing schema field repair")
    parser.add_argument("--raw-response-dir", type=Path, default=None)
    parser.add_argument("--enable-lean-search", action="store_true", help="Enable restricted lean_search tool calls")
    parser.add_argument("--lean-search-url", default=os.getenv("LEAN_SEARCH_URL", DEFAULT_LEAN_SEARCH_URL))
    parser.add_argument("--lean-search-timeout", type=int, default=30)
    parser.add_argument("--lean-search-retries", type=int, default=2)
    parser.add_argument("--lean-search-retry-sleep", type=float, default=1.5)
    parser.add_argument("--lean-search-max-results", type=int, default=5)
    parser.add_argument("--lean-search-max-results-cap", type=int, default=10)
    parser.add_argument("--lean-search-max-field-chars", type=int, default=1200)
    parser.add_argument("--max-tool-rounds", type=int, default=4)
    parser.add_argument("--max-tool-calls", type=int, default=8)


def build_tool_config(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "url": args.lean_search_url,
        "timeout": args.lean_search_timeout,
        "retries": args.lean_search_retries,
        "retry_sleep": args.lean_search_retry_sleep,
        "default_max_results": args.lean_search_max_results,
        "max_results_cap": args.lean_search_max_results_cap,
        "max_field_chars": args.lean_search_max_field_chars,
    }


SYSTEM_PROMPT = r"""
You are a strict evaluator for Lean proof refactoring quality.

The proof files are assumed to have already passed Lean verification. Do not score correctness.

You compare two refactored Lean proofs against the original proof:

1. benchmark_refactor
2. proof_refactor_pipeline

Return only valid JSON. Do not include Markdown, comments, or prose outside JSON.

The JSON must have this exact top-level shape for the given question:

{
  "<question_name>": {
    "benchmark_refactor": {
      "structure": {
        "main_theorem_slimness": number,
        "complexity_distribution": number,
        "dependency_clarity": number
      },
      "signature_quality": {
        "statement_naturalness": number,
        "binder_economy": number,
        "generality": number
      },
      "tactic_quality": {
        "tactic_transparency": number,
        "explicit_lemma_use": number,
        "broad_tactic_control": number
      },
      "reuse": {
        "helper_usefulness": number,
        "reuse_potential": number,
        "no_dead_helpers": number
      },
      "human_review": {
        "proof_readability": number,
        "mathlib_style": number,
        "maintainability": number
      },
      "overall": {
        "score": number
      },
      "reason": string
    },
    "proof_refactor_pipeline": {
      "structure": {
        "main_theorem_slimness": number,
        "complexity_distribution": number,
        "dependency_clarity": number
      },
      "signature_quality": {
        "statement_naturalness": number,
        "binder_economy": number,
        "generality": number
      },
      "tactic_quality": {
        "tactic_transparency": number,
        "explicit_lemma_use": number,
        "broad_tactic_control": number
      },
      "reuse": {
        "helper_usefulness": number,
        "reuse_potential": number,
        "no_dead_helpers": number
      },
      "human_review": {
        "proof_readability": number,
        "mathlib_style": number,
        "maintainability": number
      },
      "overall": {
        "score": number
      },
      "reason": string
    }
  }
}

All metric scores must be numbers from 1.0 to 5.0. Half-points are allowed.
The overall score must be the simple average of the 15 non-overall metrics for that method, rounded to 2 decimals.
The reason string must be concise but substantive: 3-6 sentences explaining the score.

Use the detailed evaluation rubric placed after the three embedded Lean code blocks in the user message.

If a LeanSearch tool is available, use it sparingly. It is only for judging Mathlib-style naturalness,
reuse potential, or whether a proposed helper is close to an existing library lemma. Do not use tools to
re-check correctness. Prefer direct inspection when the code is clear.
""".strip()


JSON_REPAIR_SYSTEM_PROMPT = r"""
You repair malformed JSON into strict valid JSON.

Rules:
- Return only valid JSON.
- Preserve the data and numeric scores as much as possible.
- Use double quotes for all object keys and strings.
- Remove comments, Markdown, trailing commas, and any prose outside the JSON.
- Do not add new metrics unless required by the existing structure.
""".strip()


SCHEMA_REPAIR_SYSTEM_PROMPT = r"""
You repair a parsed scoring JSON object so that it exactly matches the required schema.

Rules:
- Return only valid JSON.
- Preserve existing scores and reasons as much as possible.
- Add any missing required metric keys with reasonable numeric scores from 1.0 to 5.0, inferred from nearby metrics and reasons.
- If a required group is missing, add it with reasonable numeric scores from 1.0 to 5.0.
- Ensure both methods exist: benchmark_refactor and proof_refactor_pipeline.
- Ensure every method contains the five metric groups, an overall object, and a nonempty reason string.
- Do not add Markdown or prose outside JSON.
""".strip()


LEAN_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "lean_search",
        "description": (
            "Search Mathlib/Lean using LeanSearch natural-language search. "
            "Use this sparingly when it helps judge whether a helper lemma is natural, "
            "Mathlib-like, reusable, or already close to an existing theorem. "
            "Do not use it to re-check proof correctness."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language, Lean term, or mixed query for LeanSearch.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of LeanSearch results to return.",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


def build_user_prompt(
    question_name: str,
    original_code: str,
    baseline_code: str,
    pipeline_code: str,
    rubric_text: str,
) -> str:
    return f"""
Question name: {question_name}

Compare the two refactor methods against the original Lean solution.

Remember:
- correctness has already been checked;
- score only refactor quality;
- return only the required JSON object;
- include a `reason` field under each method.

===== ORIGINAL LEAN SOLUTION =====
```lean
{original_code}
```

===== benchmark_refactor =====
```lean
{baseline_code}
```

===== proof_refactor_pipeline =====
```lean
{pipeline_code}
```

===== EXTERNAL EVALUATION RUBRIC =====
{rubric_text}
""".strip()


def read_text(path: Path, max_chars: int | None = None) -> str:
    text = path.read_text(encoding="utf-8")
    if max_chars is not None and len(text) > max_chars:
        raise ValueError(
            f"File is too large for configured max chars ({len(text)} > {max_chars}): {path}"
        )
    return text


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
        stripped = stripped.strip()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))

    if not isinstance(data, dict):
        raise ValueError("API response JSON must be an object")
    return data


def normalize_leansearch_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize one LeanSearch result into a compact, model-friendly shape."""
    item = raw.get("result", raw) if isinstance(raw, dict) else {}

    name = item.get("name")
    if isinstance(name, list):
        name = ".".join(str(part) for part in name)

    # Different LeanSearch deployments may expose slightly different fields.
    normalized = {
        "name": name,
        "kind": item.get("kind"),
        "type": item.get("type") or item.get("signature"),
        "module_name": item.get("module_name") or item.get("module"),
        "docstring": item.get("docstring"),
        "informal_name": item.get("informal_name"),
        "informal_description": item.get("informal_description"),
        "doc_url": item.get("doc_url"),
    }
    return {k: v for k, v in normalized.items() if v not in (None, "")}


def lean_search(
    query: str,
    *,
    num_results: int = 5,
    lean_search_url: str = DEFAULT_LEAN_SEARCH_URL,
    timeout: int = 30,
    retries: int = 2,
    retry_sleep: float = 1.5,
) -> list[dict[str, Any]]:
    """Call https://leansearch.net/search and return normalized first-batch results."""
    num_results = max(1, min(int(num_results), 10))
    payload = {"query": [query], "num_results": num_results}
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "proof-refactor-scorer/0.1",
    }

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                lean_search_url,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list) or not data:
                return []
            first_batch = data[0]
            if not isinstance(first_batch, list):
                return []
            return [normalize_leansearch_result(x) for x in first_batch]
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(retry_sleep * (attempt + 1))
                continue
            break

    assert last_error is not None
    raise last_error


def safe_lean_search_tool(args: dict[str, Any], tool_config: dict[str, Any]) -> dict[str, Any]:
    """Tool wrapper: never raises to the model; returns ok/error/results."""
    query = str(args.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "missing query", "results": []}

    max_results = int(args.get("max_results", tool_config["default_max_results"]))
    max_results = max(1, min(max_results, tool_config["max_results_cap"]))

    try:
        results = lean_search(
            query,
            num_results=max_results,
            lean_search_url=tool_config["url"],
            timeout=tool_config["timeout"],
            retries=tool_config["retries"],
            retry_sleep=tool_config["retry_sleep"],
        )
        # Trim result strings to avoid flooding the scoring context.
        trimmed_results: list[dict[str, Any]] = []
        for result in results:
            trimmed: dict[str, Any] = {}
            for key, value in result.items():
                if isinstance(value, str):
                    trimmed[key] = value[: tool_config["max_field_chars"]]
                else:
                    trimmed[key] = value
            trimmed_results.append(trimmed)
        return {"ok": True, "query": query, "results": trimmed_results}
    except Exception as exc:
        return {"ok": False, "query": query, "error": str(exc), "results": []}


def dispatch_tool(name: str, args: dict[str, Any], tool_config: dict[str, Any]) -> dict[str, Any]:
    if name == "lean_search":
        return safe_lean_search_tool(args, tool_config)
    return {"ok": False, "error": f"unknown tool: {name}"}


def message_to_dict(message: dict[str, Any]) -> dict[str, Any]:
    """Keep only Chat Completions message fields that are safe to send back."""
    out: dict[str, Any] = {"role": message.get("role", "assistant")}
    if "content" in message:
        out["content"] = message.get("content")
    else:
        out["content"] = None
    if "tool_calls" in message:
        out["tool_calls"] = message["tool_calls"]
    return out



def post_json_with_retry(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
    retries: int,
    retry_sleep: float,
    label: str,
) -> requests.Response:
    """POST JSON with retry for transient API/network failures."""
    last_error: BaseException | None = None
    retryable_status = {408, 409, 425, 429, 500, 502, 503, 504}

    for attempt in range(retries + 1):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code in retryable_status and attempt < retries:
                wait = retry_sleep * (attempt + 1)
                print(
                    f"{label}: HTTP {response.status_code}; retrying in {wait:.1f}s "
                    f"({attempt + 1}/{retries})...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            return response
        except (
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:
            last_error = exc
            if attempt < retries:
                wait = retry_sleep * (attempt + 1)
                print(
                    f"{label}: {type(exc).__name__}; retrying in {wait:.1f}s "
                    f"({attempt + 1}/{retries})...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            raise

    assert last_error is not None
    raise last_error


def write_raw_response(raw_response_dir: Path | None, question_name: str | None, suffix: str, content: str) -> Path | None:
    if raw_response_dir is None or question_name is None:
        return None
    raw_response_dir.mkdir(parents=True, exist_ok=True)
    path = raw_response_dir / f"{question_name}.{suffix}.txt"
    path.write_text(content, encoding="utf-8")
    return path


def repair_json_with_model(
    *,
    base_url: str,
    api_key: str,
    model: str,
    malformed_json_text: str,
    timeout: int,
    max_tokens: int,
    api_retries: int,
    api_retry_sleep: float,
) -> dict[str, Any]:
    """Ask the model to convert malformed JSON-like text into strict JSON."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": JSON_REPAIR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Repair this malformed JSON-like response into strict JSON only:\n\n" + malformed_json_text,
            },
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    response = post_json_with_retry(
        url,
        headers=headers,
        payload=payload,
        timeout=timeout,
        retries=api_retries,
        retry_sleep=api_retry_sleep,
        label="JSON repair API",
    )
    if response.status_code >= 400:
        raise RuntimeError(f"JSON repair API error {response.status_code}: {response.text[:2000]}")
    raw = response.json()
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected JSON repair API response shape: {raw}") from exc
    return extract_json_object(content)




def repair_schema_with_model(
    *,
    base_url: str,
    api_key: str,
    model: str,
    question_name: str,
    invalid_score_obj: dict[str, Any],
    validation_error: str,
    timeout: int,
    max_tokens: int,
    api_retries: int,
    api_retry_sleep: float,
) -> dict[str, Any]:
    """Ask the model to fill missing required schema fields in an already parsed JSON object."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    expected_schema = {
        question_name: {
            METHOD_BASELINE: {
                group: {metric: "number 1.0-5.0" for metric in metrics}
                for group, metrics in METRIC_GROUPS.items()
            }
            | {"overall": {"score": "number"}, "reason": "string"},
            METHOD_PIPELINE: {
                group: {metric: "number 1.0-5.0" for metric in metrics}
                for group, metrics in METRIC_GROUPS.items()
            }
            | {"overall": {"score": "number"}, "reason": "string"},
        }
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SCHEMA_REPAIR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "The following scoring JSON parsed successfully but failed schema validation.\n"
                    f"Question name: {question_name}\n"
                    f"Validation error: {validation_error}\n\n"
                    "Expected schema:\n"
                    + json.dumps(expected_schema, ensure_ascii=False, indent=2)
                    + "\n\nInvalid scoring JSON:\n"
                    + json.dumps(invalid_score_obj, ensure_ascii=False, indent=2)
                    + "\n\nReturn the repaired full JSON object only."
                ),
            },
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    response = post_json_with_retry(
        url,
        headers=headers,
        payload=payload,
        timeout=timeout,
        retries=api_retries,
        retry_sleep=api_retry_sleep,
        label="Schema repair API",
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Schema repair API error {response.status_code}: {response.text[:2000]}")
    raw = response.json()
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected schema repair API response shape: {raw}") from exc
    return extract_json_object(content)


def chat_completion_json(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    timeout: int,
    max_tokens: int,
    enable_lean_search: bool,
    max_tool_rounds: int,
    max_tool_calls: int,
    tool_config: dict[str, Any],
    question_name: str | None = None,
    raw_response_dir: Path | None = None,
    repair_json: bool = True,
    api_retries: int = 2,
    api_retry_sleep: float = 15.0,
) -> dict[str, Any]:
    """Call Chat Completions. If enabled, handle lean_search tool calls."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    tools = [LEAN_SEARCH_TOOL] if enable_lean_search else None
    tool_calls_used = 0

    for round_index in range(max_tool_rounds + 1):
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        response = post_json_with_retry(
            url,
            headers=headers,
            payload=payload,
            timeout=timeout,
            retries=api_retries,
            retry_sleep=api_retry_sleep,
            label="Scoring API",
        )
        if response.status_code >= 400:
            raise RuntimeError(f"API error {response.status_code}: {response.text[:2000]}")

        raw = response.json()
        try:
            message = raw["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected API response shape: {raw}") from exc

        tool_calls = message.get("tool_calls") or []
        if tools and tool_calls:
            if round_index >= max_tool_rounds:
                raise RuntimeError("Model requested tool calls after max_tool_rounds was reached")

            messages.append(message_to_dict(message))
            for call in tool_calls:
                if tool_calls_used >= max_tool_calls:
                    result = {
                        "ok": False,
                        "error": f"tool call budget exceeded ({max_tool_calls})",
                        "results": [],
                    }
                else:
                    try:
                        function = call.get("function", {})
                        name = function.get("name", "")
                        args_text = function.get("arguments", "{}")
                        args = json.loads(args_text) if isinstance(args_text, str) else dict(args_text)
                    except Exception as exc:
                        name = "<parse_error>"
                        args = {}
                        result = {"ok": False, "error": f"failed to parse tool call: {exc}", "results": []}
                    else:
                        result = dispatch_tool(name, args, tool_config)
                    tool_calls_used += 1

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

            continue

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"Model returned no JSON content: {raw}")

        write_raw_response(raw_response_dir, question_name, "raw", content)
        try:
            parsed = extract_json_object(content)
            return parsed
        except json.JSONDecodeError as exc:
            raw_path = write_raw_response(raw_response_dir, question_name, "malformed", content)
            if not repair_json:
                path_hint = f" Raw response saved to: {raw_path}" if raw_path else ""
                raise RuntimeError(f"Model returned malformed JSON: {exc}.{path_hint}") from exc

            repaired = repair_json_with_model(
                base_url=base_url,
                api_key=api_key,
                model=model,
                malformed_json_text=content,
                timeout=timeout,
                max_tokens=max_tokens,
                api_retries=api_retries,
                api_retry_sleep=api_retry_sleep,
            )
            write_raw_response(
                raw_response_dir,
                question_name,
                "repaired",
                json.dumps(repaired, ensure_ascii=False, indent=2),
            )
            return repaired

    raise RuntimeError("Exited tool loop without a final JSON response")


METRIC_GROUPS: dict[str, list[str]] = {
    "structure": [
        "main_theorem_slimness",
        "complexity_distribution",
        "dependency_clarity",
    ],
    "signature_quality": [
        "statement_naturalness",
        "binder_economy",
        "generality",
    ],
    "tactic_quality": [
        "tactic_transparency",
        "explicit_lemma_use",
        "broad_tactic_control",
    ],
    "reuse": [
        "helper_usefulness",
        "reuse_potential",
        "no_dead_helpers",
    ],
    "human_review": [
        "proof_readability",
        "mathlib_style",
        "maintainability",
    ],
}


def validate_score_obj(question_name: str, score_data: dict[str, Any]) -> dict[str, Any]:
    if question_name not in score_data:
        raise ValueError(f"Missing question key in API JSON: {question_name}")

    question_obj = score_data[question_name]
    if not isinstance(question_obj, dict):
        raise ValueError(f"Question entry must be an object: {question_name}")

    for method in (METHOD_BASELINE, METHOD_PIPELINE):
        if method not in question_obj:
            raise ValueError(f"Missing method key: {method}")
        method_obj = question_obj[method]
        if not isinstance(method_obj, dict):
            raise ValueError(f"Method entry must be an object: {method}")

        metric_values: list[float] = []
        for group, metrics in METRIC_GROUPS.items():
            if group not in method_obj or not isinstance(method_obj[group], dict):
                raise ValueError(f"Missing or invalid group `{group}` for method `{method}`")
            for metric in metrics:
                value = method_obj[group].get(metric)
                if not isinstance(value, (int, float)):
                    raise ValueError(f"Missing numeric metric `{group}.{metric}` for method `{method}`")
                value_float = float(value)
                if not (1.0 <= value_float <= 5.0):
                    raise ValueError(
                        f"Metric out of range `{group}.{metric}`={value_float} for method `{method}`"
                    )
                method_obj[group][metric] = value_float
                metric_values.append(value_float)

        overall = round(sum(metric_values) / len(metric_values), 2)
        method_obj.setdefault("overall", {})
        if not isinstance(method_obj["overall"], dict):
            method_obj["overall"] = {}
        method_obj["overall"]["score"] = overall

        reason = method_obj.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"Missing nonempty reason for method `{method}`")
        method_obj["reason"] = reason.strip()

    return score_data


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score one Lean proof refactor comparison.")
    parser.add_argument("--question-name", default=None)
    parser.add_argument("--original", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--proof-refactor", required=True, type=Path)
    parser.add_argument("--rubric-file", required=True, type=Path, help="External Markdown/TXT file containing the evaluation rubric")
    parser.add_argument("--output-json", type=Path, default=None)
    add_score_arguments(parser)
    return parser.parse_args(argv)


def score_problem(
    problem: ProblemFiles,
    options: ScoreOptions,
    rubric_text: str,
    raw_response_dir: Path | None,
    tool_config: dict[str, Any],
) -> dict[str, Any]:
    """Score one problem. This function is safe to run in worker threads.

    It does not write the shared output JSON. It may write per-question raw response
    files under raw_response_dir, which are independent across questions.
    """
    original_code = read_text(problem.original_path, options.max_chars_per_file)
    baseline_code = read_text(problem.baseline_path, options.max_chars_per_file)
    pipeline_code = read_text(problem.proof_refactor_path, options.max_chars_per_file)

    user_prompt = build_user_prompt(
        problem.question_name,
        original_code,
        baseline_code,
        pipeline_code,
        rubric_text,
    )

    raw_score = chat_completion_json(
        base_url=options.base_url,
        api_key=options.api_key,
        model=options.model,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=options.temperature,
        timeout=options.timeout,
        max_tokens=options.max_tokens,
        enable_lean_search=options.enable_lean_search,
        max_tool_rounds=options.max_tool_rounds,
        max_tool_calls=options.max_tool_calls,
        tool_config=tool_config,
        question_name=problem.question_name,
        raw_response_dir=raw_response_dir,
        repair_json=not options.no_json_repair,
        api_retries=options.api_retries,
        api_retry_sleep=options.api_retry_sleep,
    )
    try:
        return validate_score_obj(problem.question_name, raw_score)
    except ValueError as exc:
        write_raw_response(
            raw_response_dir,
            problem.question_name,
            "schema_invalid",
            json.dumps(raw_score, ensure_ascii=False, indent=2),
        )
        if options.no_schema_repair:
            raise
        repaired_score = repair_schema_with_model(
            base_url=options.base_url,
            api_key=options.api_key,
            model=options.model,
            question_name=problem.question_name,
            invalid_score_obj=raw_score,
            validation_error=str(exc),
            timeout=options.timeout,
            max_tokens=options.max_tokens,
            api_retries=options.api_retries,
            api_retry_sleep=options.api_retry_sleep,
        )
        write_raw_response(
            raw_response_dir,
            problem.question_name,
            "schema_repaired",
            json.dumps(repaired_score, ensure_ascii=False, indent=2),
        )
        return validate_score_obj(problem.question_name, repaired_score)


def main() -> int:
    args = parse_args()
    options = ScoreOptions.from_args(args)

    if not options.api_key:
        print("Missing API key. Set OPENAI_API_KEY or pass --api-key.", file=sys.stderr)
        return 2

    if not args.rubric_file.exists() or not args.rubric_file.is_file():
        print(f"Rubric file not found: {args.rubric_file}", file=sys.stderr)
        return 2

    rubric_text = read_text(args.rubric_file, args.max_chars_rubric)
    if not rubric_text.strip():
        print(f"Rubric file is empty: {args.rubric_file}", file=sys.stderr)
        return 2

    problem = ProblemFiles(
        question_name=args.question_name or args.original.stem,
        original_path=args.original,
        baseline_path=args.baseline,
        proof_refactor_path=args.proof_refactor,
    )
    if options.enable_lean_search:
        print(f"LeanSearch tool enabled: {args.lean_search_url}")
    raw_response_dir = args.raw_response_dir or (args.output_json.parent / "raw_responses" if args.output_json else None)
    score = score_problem(problem, options, rubric_text, raw_response_dir, build_tool_config(args))
    payload = json.dumps(score, ensure_ascii=False, indent=2)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(payload, encoding="utf-8")
        print(f"Wrote {args.output_json}")
    else:
        print(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
