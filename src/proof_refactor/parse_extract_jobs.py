"""
Parse extract markdown fragments into lean_extract_batch jobs.

`block` is treated as an opaque source substring payload. It is decoded later by
`lean_extract_batch`; this parser does not remap branch/case hosts.

Usage:
  proof-refactor parse_extract_jobs <extract_output.md>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _strip_code_ticks(text: str) -> str:
    text = text.strip()
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        return text[1:-1]
    return text


def _parse_extraction_bullet(bullet: str) -> dict[str, str]:
    marker = " | scaffold="
    if not bullet.startswith("block=") or marker not in bullet:
        raise ValueError(f"Invalid extraction bullet: {bullet}")

    block_part, rest = bullet.split(marker, 1)
    block = block_part[len("block=") :]
    scaffold = rest.split(" | ", 1)[0].strip()
    if not block or not scaffold:
        raise ValueError(f"Invalid extraction bullet: {bullet}")
    return {"block": block, "name": scaffold}


def parse_extract_text(text: str) -> list[dict[str, object]]:
    jobs: list[dict[str, object]] = []
    lines = text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]
        if not line.startswith("### "):
            i += 1
            continue

        owner_decl = line[4:].strip()
        i += 1
        extraction_bullets: list[str] = []

        while i < len(lines) and not lines[i].startswith("### "):
            current = lines[i].strip()
            if current == "- **extract_suggestion**:":
                i += 1
                while i < len(lines) and lines[i].startswith("  - "):
                    extraction_bullets.append(_strip_code_ticks(lines[i][4:].strip()))
                    i += 1
                continue
            i += 1

        if not extraction_bullets:
            raise ValueError(
                f"Declaration `{owner_decl}` is missing an extract_suggestion block."
            )

        extractions = [
            _parse_extraction_bullet(bullet)
            for bullet in extraction_bullets
            if bullet != "(none)"
        ]
        if extractions:
            jobs.append({"owner_decl": owner_decl, "extractions": extractions})

    return jobs


def parse_extract_jobs_cli(extract_output: str | Path) -> int:
    """Parse extract Markdown from one path and print JSON jobs."""
    extract_path = Path(extract_output)
    if not extract_path.exists():
        print(f"Error: extract file not found: {extract_path}", file=sys.stderr)
        return 1

    try:
        jobs = parse_extract_text(extract_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"Parse failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"jobs": jobs}, ensure_ascii=True, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse extract Markdown into JSON jobs.")
    parser.add_argument("extract_output", help="Path to extract_<theorem>.md")
    args = parser.parse_args()
    raise SystemExit(parse_extract_jobs_cli(args.extract_output))


if __name__ == "__main__":
    main()
