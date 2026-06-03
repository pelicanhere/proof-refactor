#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


def remove_lean_comments(code: str) -> str:
    """
    Remove Lean comments from code.

    Supports:
    - line comments: -- ...
    - nested block comments: /- ... -/

    Comment markers inside string literals are ignored.
    """
    result = []
    i = 0
    n = len(code)

    in_string = False
    escape = False

    while i < n:
        c = code[i]

        if in_string:
            result.append(c)

            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False

            i += 1
            continue

        # enter string
        if c == '"':
            in_string = True
            result.append(c)
            i += 1
            continue

        # line comment
        if i + 1 < n and code[i:i + 2] == "--":
            # replace comment by whitespace/newline
            i += 2
            while i < n and code[i] != "\n":
                i += 1
            if i < n and code[i] == "\n":
                result.append("\n")
                i += 1
            else:
                result.append(" ")
            continue

        # block comment, possibly nested
        if i + 1 < n and code[i:i + 2] == "/-":
            depth = 1
            i += 2
            result.append(" ")

            while i < n and depth > 0:
                if i + 1 < n and code[i:i + 2] == "/-":
                    depth += 1
                    i += 2
                elif i + 1 < n and code[i:i + 2] == "-/":
                    depth -= 1
                    i += 2
                else:
                    # preserve newlines as whitespace boundaries
                    if code[i] == "\n":
                        result.append("\n")
                    i += 1

            result.append(" ")
            continue

        result.append(c)
        i += 1

    return "".join(result)


def count_words_in_lean_file(path: Path) -> int:
    code = path.read_text(encoding="utf-8")
    code_without_comments = remove_lean_comments(code)
    return len(code_without_comments.split())


def count_folder(folder: Path, recursive: bool = True, use_stem: bool = True) -> dict[str, int]:
    pattern = "**/*.lean" if recursive else "*.lean"
    result = {}

    for path in sorted(folder.glob(pattern)):
        if not path.is_file():
            continue

        # use_stem=True:  Theorem1.lean -> Theorem1
        # use_stem=False: Theorem1.lean -> Theorem1.lean
        name = path.stem if use_stem else path.name
        result[name] = count_words_in_lean_file(path)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Count whitespace-separated words in Lean files, excluding comments."
    )

    parser.add_argument(
        "folder",
        type=Path,
        help="Folder containing .lean files."
    )

    parser.add_argument(
        "output",
        type=Path,
        help="Output JSON path."
    )

    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Only count .lean files directly under the folder."
    )

    parser.add_argument(
        "--keep-extension",
        action="store_true",
        help="Use full filename like Foo.lean instead of stem Foo."
    )

    args = parser.parse_args()

    if not args.folder.exists():
        raise FileNotFoundError(f"Folder does not exist: {args.folder}")

    if not args.folder.is_dir():
        raise NotADirectoryError(f"Not a folder: {args.folder}")

    counts = count_folder(
        folder=args.folder,
        recursive=not args.no_recursive,
        use_stem=not args.keep_extension,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)

    with args.output.open("w", encoding="utf-8") as f:
        json.dump(counts, f, ensure_ascii=False, indent=2)

    print(f"Counted {len(counts)} Lean files.")
    print(f"Output written to: {args.output}")


if __name__ == "__main__":
    main()