#!/usr/bin/env python3
"""Compare two files line by line and report where they differ."""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FILE_A = SCRIPT_DIR / "examples" / "sample_a.txt"
DEFAULT_FILE_B = SCRIPT_DIR / "examples" / "sample_b.txt"


def load_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def compare_files(path_a: Path, path_b: Path) -> int:
    lines_a = load_lines(path_a)
    lines_b = load_lines(path_b)
    max_len = max(len(lines_a), len(lines_b))

    diff_count = 0
    for i in range(max_len):
        line_a = lines_a[i] if i < len(lines_a) else None
        line_b = lines_b[i] if i < len(lines_b) else None

        if line_a != line_b:
            diff_count += 1
            print(f"Line {i + 1}:")
            print(f"  - {path_a.name}: {line_a if line_a is not None else '<no line>'}")
            print(f"  - {path_b.name}: {line_b if line_b is not None else '<no line>'}")

    if diff_count == 0:
        print(f"No differences found between {path_a.name} and {path_b.name}.")
    else:
        print(f"\n{diff_count} differing line(s) out of {max_len}.")

    return diff_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "file_a", nargs="?", type=Path, default=DEFAULT_FILE_A,
        help=f"First file to compare (default: {DEFAULT_FILE_A})",
    )
    parser.add_argument(
        "file_b", nargs="?", type=Path, default=DEFAULT_FILE_B,
        help=f"Second file to compare (default: {DEFAULT_FILE_B})",
    )
    args = parser.parse_args()

    for path in (args.file_a, args.file_b):
        if not path.is_file():
            print(f"Error: file not found: {path}", file=sys.stderr)
            return 2

    diff_count = compare_files(args.file_a, args.file_b)
    return 1 if diff_count else 0


if __name__ == "__main__":
    sys.exit(main())
