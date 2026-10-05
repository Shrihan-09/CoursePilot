"""Phase 6.4: how much of the published prerequisite-condition and co-requisite
text CoursePilot now interprets, measured on the archived SOC terms.

Offline and read-only: archives + the course table (for short-code
resolution only). Prints counts and every distinct interpretation so a human
can check for over-parsing.

Usage:  DATABASE_URL_SYNC=... python scripts/measure_condition_coverage.py [--show N]
"""

from __future__ import annotations

import glob
import json
import os
import pathlib
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "ingestion")]

from sqlalchemy import create_engine, select  # noqa: E402

from app.domain.conditions import clean, interpret  # noqa: E402
from app.domain.corequisites import parse_note  # noqa: E402
from app.domain.prerequisites import condition_kinds, course_keys, parse  # noqa: E402
from app.models import Course  # noqa: E402

SHOW = int(sys.argv[sys.argv.index("--show") + 1]) if "--show" in sys.argv else 0


def main() -> None:
    url = os.environ.get("DATABASE_URL_SYNC",
                         "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot")
    with create_engine(url).connect() as c:
        strings = [r[0] for r in c.execute(select(Course.course_string)
                                           .where(Course.supplement_code == ""))]
    by_sn: dict = defaultdict(list)
    for s in strings:
        u, sub, num = s.split(":")
        by_sn[(sub, num)].append(s)

    def resolver(unit):
        def resolve(subject, number):
            options = by_sn.get((subject, number), [])
            own = f"{unit}:{subject}:{number}"
            return own if own in options else (options[0] if len(options) == 1 else None)
        return resolve

    cond = Counter()
    coreq = Counter()
    grade_scopes = Counter()
    distinct_grade: dict = {}
    distinct_alt: dict = {}
    distinct_coreq: dict = defaultdict(dict)
    for f in sorted(glob.glob(str(ROOT / "data" / "raw" / "soc_courses_*_NB.json"))):
        for raw in json.loads(pathlib.Path(f).read_text(encoding="utf-8")):
            unit = raw["offeringUnitCode"]
            resolve = resolver(unit)
            note = raw.get("courseNotes")
            note = note if condition_kinds(note) else None
            sections = raw.get("sections") or []
            texts = {(s.get("sectionNotes") or "").strip() for s in sections}
            section = texts.pop() if len(texts) == 1 and sections else None
            section = section if section and condition_kinds(section) else None
            if note or section:
                cond["offerings_with_condition_text"] += 1
                result = parse(raw.get("preReqNotes")) if (raw.get("preReqNotes") or "").strip() else None
                exprs = set(course_keys(result.expression)) if result and result.expression else set()
                out = interpret([note, section], exprs, resolve)
                if section:
                    cond["from_uniform_section_note"] += 1
                if out["minimum_grade"]:
                    cond["minimum_grade_interpreted"] += 1
                    grade_scopes[out["minimum_grade"]["scope"]] += 1
                    distinct_grade.setdefault(out["minimum_grade"]["text"], (raw["courseString"], out["minimum_grade"]))
                if out["alternatives"]:
                    cond["alternatives_interpreted"] += 1
                    for a in out["alternatives"]:
                        distinct_alt.setdefault(a["text"], (raw["courseString"], a["kinds"]))
                if out["uninterpreted"]:
                    cond["still_uninterpreted"] += 1
                    for k in out["uninterpreted"]:
                        cond[f"uninterpreted:{k}"] += 1
                else:
                    cond["fully_interpreted"] += 1
            parses = parse_note(raw.get("courseNotes"), resolve)
            source = "courseNotes"
            if not parses and sections:
                per = [parse_note(s.get("sectionNotes"), resolve) for s in sections]
                canon = {tuple((p.classification, p.canonical_text or "") for p in ps) for ps in per}
                if len(canon) == 1 and per[0]:
                    parses, source = per[0], "sectionNotes:all"
                elif any(per):
                    coreq["section_level_only_not_loaded"] += 1
            if parses:
                coreq["offerings_with_corequisite"] += 1
                coreq[f"source:{source}"] += 1
                for p in parses:
                    coreq[f"{p.kind}:{p.classification}"] += 1
                    key = p.canonical_text or f"UNSUPPORTED {p.detail}"
                    distinct_coreq[p.classification].setdefault(key, raw["courseString"])
    print("CONDITIONS", dict(cond))
    print("minimum-grade scopes", dict(grade_scopes))
    print("COREQUISITES", dict(coreq))
    print("distinct grade interpretations", len(distinct_grade),
          "| alternatives", len(distinct_alt),
          "| coreq parsed", len(distinct_coreq["parsed"]),
          "| coreq unsupported", len(distinct_coreq["unsupported"]))
    if SHOW:
        for text, (course, g) in list(distinct_grade.items())[:SHOW]:
            print(f"  GRADE {course} {g['grade']} {g['scope']} {g['courses']} <- {text[:120]}")
        for text, (course, kinds) in list(distinct_alt.items())[:SHOW]:
            print(f"  ALT   {course} {kinds} <- {text[:120]}")
        for cls in ("parsed", "unsupported"):
            for canon, course in list(distinct_coreq[cls].items())[:SHOW]:
                print(f"  COREQ {cls:11} {course} {canon[:130]}")


if __name__ == "__main__":
    main()
