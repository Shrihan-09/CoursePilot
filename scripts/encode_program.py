"""The encoding pipeline for a new major: archive -> encode -> validate -> packet -> load.

Thin entry point; see ingestion/coursepilot_ingestion/encoding/cli.py and
docs/ENCODING_MAJORS.md.

    python scripts/encode_program.py validate data/programs/rutgers/nb-undergrad/2026-2027/components/*.json
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "ingestion"), str(ROOT / "backend")]

from coursepilot_ingestion.encoding.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
