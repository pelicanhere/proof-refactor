"""Lean source helpers used by phase prompt construction."""

import re
from typing import Any

_TOP_LEVEL_DECL_START_RE = re.compile(
    r"^(?:(?:private|protected|noncomputable|unsafe|partial|opaque)\s+)*"
    r"(theorem|lemma|def|abbrev|instance|example|class|structure|inductive|coinductive)\b"
)

_TOP_LEVEL_THEOREM_LEMMA_RE = re.compile(
    r"^(?:(?:private|protected|noncomputable|unsafe|partial|opaque)\s+)*"
    r"(theorem|lemma)\s+(«[^»]+»|[^\s:({\[=]+)"
)

_SECTION_START_RE = re.compile(r"^(namespace|section)\b(?:\s+([^\s]+))?")
_SECTION_END_RE = re.compile(r"^end\b(?:\s+([^\s]+))?")


def _strip_comments_preserve_lines(code: str) -> list[str]:
    """Remove comments while preserving original line numbering."""
    masked = re.sub(
        r"/-.*?(--/|-/)",
        lambda match: "\n" * match.group(0).count("\n"),
        code,
        flags=re.DOTALL,
    )
    return [re.split(r"--", line, maxsplit=1)[0].rstrip() for line in masked.splitlines()]


def extract_top_level_theorem_lemma_index(code: str) -> list[dict[str, Any]]:
    """Parse top-level theorem and lemma declarations from Lean source text."""
    raw_lines = code.splitlines()
    cleaned_lines = _strip_comments_preserve_lines(code)
    decl_starts: list[dict[str, Any]] = []
    section_stack: list[str] = []

    def current_section() -> str:
        return section_stack[-1] if section_stack else "Main"

    for idx, cleaned in enumerate(cleaned_lines, start=1):
        if not cleaned or cleaned != cleaned.lstrip():
            continue

        stripped = cleaned.strip()
        section_match = _SECTION_START_RE.match(stripped)
        if section_match:
            section_stack.append(section_match.group(2) or "Main")
            continue

        if _SECTION_END_RE.match(stripped):
            if section_stack:
                section_stack.pop()
            continue

        decl_match = _TOP_LEVEL_DECL_START_RE.match(stripped)
        if not decl_match:
            continue

        kind = decl_match.group(1)
        if kind not in {"theorem", "lemma"}:
            decl_starts.append(
                {
                    "name": None,
                    "kind": kind,
                    "start_line": idx,
                    "section": current_section(),
                }
            )
            continue

        theorem_match = _TOP_LEVEL_THEOREM_LEMMA_RE.match(stripped)
        if theorem_match:
            decl_starts.append(
                {
                    "name": theorem_match.group(2),
                    "kind": kind,
                    "start_line": idx,
                    "section": current_section(),
                }
            )

    all_decl_starts = [decl["start_line"] for decl in decl_starts]
    top_level_index: list[dict[str, Any]] = []
    total_lines = len(raw_lines)

    for index, decl in enumerate(decl_starts):
        if decl["kind"] not in {"theorem", "lemma"} or decl["name"] is None:
            continue

        next_start = all_decl_starts[index + 1] if index + 1 < len(all_decl_starts) else total_lines + 1
        top_level_index.append(
            {
                "name": decl["name"],
                "kind": decl["kind"],
                "start_line": decl["start_line"],
                "end_line": next_start - 1,
                "section": decl["section"],
            }
        )

    return top_level_index
