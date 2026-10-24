"""Pre-commit guard (Phase 7.3 trust boundary 7): refuse to commit private material.

Fails if any staged path is under data/private/, ends in .har, or is a
*state*.json file (a saved browser/session state). Reads staged NAMES only.
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys

PATTERNS = ("data/private/*", "*/data/private/*", "*.har", "*state*.json")


def blocked(paths: list[str]) -> list[str]:
    hits = []
    for path in paths:
        name = path.replace("\\", "/")
        base = name.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(base, p) for p in PATTERNS):
            hits.append(name)
    return hits


def main() -> int:
    staged = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                            capture_output=True, text=True, check=True).stdout.split()
    hits = blocked(staged)
    if hits:
        print("Refusing to commit private material (PHASE_7_3_PLAN.md trust boundary 7):")
        for path in hits:
            print(f"  {path}")
        print("Unstage these files: git restore --staged <path>")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
