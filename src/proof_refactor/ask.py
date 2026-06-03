"""
Generic external ask runner (OpenAI-compatible endpoint).

Usage:
  proof-refactor ask <profile> <input_file>

Profiles:
  - extract
  - design
  - prove
  - repair

The input file may be Markdown, plain text, or JSON. This script does not parse
the file structurally; it reads the file as UTF-8 text and inserts that text
into the selected prompt template.

Calls automatically inherit prior context for the same profile and input path.
For `*.reask.input.md`, the session is shared with the matching `*.input.md`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import openai

from .config import bundled_prompts_dir, load_project_dotenv


ASK_SESSION_DIR_ENV = "PROOF_REFACTOR_ASK_SESSION_DIR"
load_project_dotenv()
PROFILE_NAMES = ("extract", "design", "prove", "repair")

ChatMessage = dict[str, str]


def load_prompt_template(profile: str, prompts_dir: str | Path = "") -> str:
    if profile not in PROFILE_NAMES:
        valid = ", ".join(PROFILE_NAMES)
        raise RuntimeError(f"Unknown ask profile `{profile}`. Expected one of: {valid}")

    prompt_root = Path(prompts_dir).resolve() if prompts_dir else bundled_prompts_dir()
    prompt_path = prompt_root / "informal" / f"{profile}.md"
    if not prompt_path.exists():
        raise RuntimeError(f"Prompt file not found for profile `{profile}`: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8").strip()


def normalize_response(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:markdown|md|text|json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def canonical_session_path(input_path: Path) -> Path:
    resolved = input_path.resolve()
    if resolved.name.endswith(".reask.input.md"):
        base_name = resolved.name.removesuffix(".reask.input.md") + ".input.md"
        return resolved.with_name(base_name)
    return resolved


def ask_session_dir() -> Path:
    """Resolve managed ask sessions, falling back to a local standalone cache."""
    configured = os.environ.get(ASK_SESSION_DIR_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.cwd().resolve() / ".ask_sessions"


def session_file(profile: str, input_path: Path) -> Path:
    canonical_path = canonical_session_path(input_path)
    key = f"{profile}\0{canonical_path}".encode("utf-8")
    digest = hashlib.sha256(key).hexdigest()[:24]
    return ask_session_dir() / profile / f"{digest}.json"


def load_session_messages(path: Path) -> list[ChatMessage]:
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ask session file is invalid JSON: {path}") from exc

    messages = data.get("messages")
    if not isinstance(messages, list):
        raise RuntimeError(f"Ask session file is missing a message list: {path}")

    validated: list[ChatMessage] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise RuntimeError(f"Ask session message #{index} is not an object: {path}")
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant", "system"} or not isinstance(content, str):
            raise RuntimeError(f"Ask session message #{index} is malformed: {path}")
        validated.append({"role": role, "content": content})
    return validated


def save_session_messages(path: Path, profile: str, input_path: Path, messages: list[ChatMessage]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "profile": profile,
        "input_path": str(canonical_session_path(input_path)),
        "messages": messages,
    }
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def format_failure(profile: str, error: str) -> str:
    return "\n".join(
        [
            "# Ask Failed",
            "",
            f"profile: {profile}",
            "status: failed",
            f"error: {error}",
        ]
    )


def build_prompt(profile: str, ask_input: str, prompts_dir: str | Path = "") -> str:
    prompt_template = load_prompt_template(profile, prompts_dir)
    return prompt_template.replace("{{ASK_INPUT}}", ask_input)


def call_messages(messages: list[ChatMessage]) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set")

    base_url = os.environ.get("BASE_URL")
    if not base_url:
        raise RuntimeError("BASE_URL environment variable is not set")
    model = os.environ.get("MODEL")

    client = openai.OpenAI(
        api_key=api_key,
        base_url=base_url,
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
        )
    except openai.OpenAIError as exc:
        raise RuntimeError(f"Gemini API error: {exc}") from exc

    message = response.choices[0].message.content
    if not message:
        raise RuntimeError("Gemini returned an empty response")
    return normalize_response(message)


def call_profile_from_path(profile: str, input_path: Path, prompts_dir: str | Path = "") -> str:
    if not input_path.exists():
        raise RuntimeError(f"Input file not found: {input_path}")

    ask_input = input_path.read_text(encoding="utf-8")
    path = session_file(profile, input_path)
    prior_messages = load_session_messages(path)
    prompt = build_prompt(profile, ask_input, prompts_dir)
    request_messages = [*prior_messages, {"role": "user", "content": prompt}]

    result = call_messages(request_messages)
    save_session_messages(
        path,
        profile,
        input_path,
        [*request_messages, {"role": "assistant", "content": result}],
    )
    return result


def run_profile_cli(profile: str, input_file: str | Path, prompts_dir: str | Path = "") -> int:
    """Run one ask profile and print its response."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    profile = profile.strip()
    input_path = Path(input_file)
    try:
        result = call_profile_from_path(profile, input_path, prompts_dir)
    except RuntimeError as exc:
        print(format_failure(profile, str(exc)), file=sys.stderr)
        return 1

    print(result)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one packaged external-ask profile over an input file.")
    parser.add_argument("profile")
    parser.add_argument("input_file")
    parser.add_argument("--prompts-dir", default="", help="External prompt root override")
    args = parser.parse_args()
    raise SystemExit(run_profile_cli(args.profile, args.input_file, args.prompts_dir))


if __name__ == "__main__":
    main()
