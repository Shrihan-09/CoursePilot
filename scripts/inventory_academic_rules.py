"""Phase 6.4: inventory of academic-rule prose in REAL archived Rutgers evidence.

INVESTIGATION TOOLING - reads archives only, writes one evidence file, loads
nothing. Answers "which academic rules does Rutgers actually publish, and how
often?" so that grade/GPA/sequence/co-requisite semantics are designed from
observed text rather than from hypothetical examples.

Sources (all public, all archived first):
  * Catalog program pages for the 2026-27 SAS wave (data/raw/catalog/)
  * SOC courses.json for five terms (data/raw/soc_courses_*_NB.json):
      courseNotes (course-level) and sectionNotes (section-level)
  * Curated definitions (data/programs/...): rules_not_yet_modeled

Each sentence/note is classified into at most the FIRST matching class of
each family; counts are occurrences (catalog: sentences; SOC: course x term
x distinct text). The classifier is deliberately broad - it finds candidates
for a human to read, it does not interpret anything.

Usage:  python scripts/inventory_academic_rules.py
"""

from __future__ import annotations

import glob
import html
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "ingestion")]

from coursepilot_ingestion.catalog_registry import page_prose  # noqa: E402

OUT = ROOT / "docs" / "investigations" / "evidence" / "phase-6-4-rule-inventory.json"
PROGRAM_PAGES = ["computer-science-198", "mathematics-640", "economics-220", "psychology-830",
                 "statistics-960", "philosophy-730", "sociology-920", "linguistics-615",
                 "data-science-219", "physics-750", "political-science-790"]

CATALOG_CLASSES = [
    ("admission_to_major", re.compile(
        r"\bto declare\b|\bdeclar(e|ing) the major\b|prerequisite for declaring|\badmission\b|"
        r"\bapply (to|for) the\b|entry requirements", re.I)),
    ("gpa", re.compile(r"grade-point average|\bGPA\b", re.I)),
    ("grade_quota", re.compile(
        r"(no more than|only|at most) (one|two|three|\d) (elective )?(course with a )?grades? of [A-D]|"
        r"\bone (elective )?course with a grade of D\b|\bone D grade\b", re.I)),
    ("degree_minimum_grade", re.compile(
        r"\b[A-D][+]? or (better|higher)\b|grades? of [A-D][+]? or|minimum grade", re.I)),
    ("sequence", re.compile(r"\b\d{2}:\d{3}:\d{3}-\d{3}\b|\b\d{3}-\d{3}\b")),
    ("option_or_track", re.compile(r"\bOption [A-Z]\b|\btrack\b|\bconcentration\b", re.I)),
    ("corequisite", re.compile(r"co-?requisite|concurrent", re.I)),
    ("residency_or_location", re.compile(r"taken at Rutgers|at Rutgers University-New Brunswick|"
                                         r"in New Brunswick|outside (of )?Rutgers", re.I)),
]
SOC_CLASSES = [
    ("prerequisite_minimum_grade", re.compile(
        r"\b[A-D][+]? or (better|higher|above)\b|grades? of [A-D]\b|below an? '?[A-D]\b|"
        r"WITH (A )?GRADE OF\b", re.I)),
    ("gpa", re.compile(r"\bG ?PA\b|grade[- ]point", re.I)),
    ("corequisite", re.compile(r"co-?req|corequisite|concurrent", re.I)),
    ("placement", re.compile(r"placement test|placement into|OR PLACEMENT\b", re.I)),
    ("program_restriction", re.compile(r"\bmajors? only\b|declared majors|restricted to|open only to",
                                       re.I)),
]
PREREQ_CONTEXT = re.compile(r"pre-?req|prerequisite|co-?req|corequisite|concurrent|G ?PA\b|grade", re.I)


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z0-9(])", text) if s.strip()]


def catalog_inventory() -> dict:
    counts: Counter = Counter()
    per_page: dict = defaultdict(Counter)
    examples: dict = defaultdict(list)
    for slug in PROGRAM_PAGES:
        path = ROOT / "data" / "raw" / "catalog" / f"catalog_{slug}_2026_2027.html"
        if not path.exists():
            continue
        for sentence in sentences(page_prose(path.read_text(encoding="utf-8"))):
            for name, pattern in CATALOG_CLASSES:
                if pattern.search(sentence):
                    counts[name] += 1
                    per_page[slug][name] += 1
                    if len(examples[name]) < 8:
                        examples[name].append({"page": slug, "text": sentence[:300]})
    return {"counts": dict(counts), "per_page": {k: dict(v) for k, v in per_page.items()},
            "examples": examples}


def soc_inventory() -> dict:
    counts: Counter = Counter()
    by_field: dict = defaultdict(Counter)
    distinct: dict = defaultdict(set)
    uniform: Counter = Counter()
    examples: dict = defaultdict(list)
    terms = []
    for f in sorted(glob.glob(str(ROOT / "data" / "raw" / "soc_courses_*_NB.json"))):
        term = pathlib.Path(f).stem.replace("soc_courses_", "")
        terms.append(term)
        for course in json.loads(pathlib.Path(f).read_text(encoding="utf-8")):
            sections = course.get("sections") or []
            texts = [("courseNotes", clean(course.get("courseNotes")), None)]
            section_notes = Counter(clean(s.get("sectionNotes")) for s in sections)
            texts += [("sectionNotes", t, n == len(sections)) for t, n in section_notes.items()]
            for field, text, is_uniform in texts:
                if not text or not PREREQ_CONTEXT.search(text):
                    continue
                for name, pattern in SOC_CLASSES:
                    if pattern.search(text):
                        counts[name] += 1
                        by_field[name][field] += 1
                        distinct[name].add(text)
                        if is_uniform:
                            uniform[name] += 1
                        if len(examples[name]) < 8 and all(e["text"] != text[:300]
                                                           for e in examples[name]):
                            examples[name].append({"course": course.get("courseString"),
                                                   "term": term, "field": field,
                                                   "text": text[:300]})
    return {"terms": terms, "counts": dict(counts),
            "by_field": {k: dict(v) for k, v in by_field.items()},
            "distinct_texts": {k: len(v) for k, v in distinct.items()},
            "section_notes_uniform_across_all_sections": dict(uniform),
            "examples": examples}


def curated_inventory() -> dict:
    out = []
    for f in sorted((ROOT / "data" / "programs").rglob("*.json")):
        if "review" in f.parts:
            continue
        d = json.loads(f.read_bytes())
        for r in d.get("rules_not_yet_modeled", []):
            out.append({"definition": f.name, "rule": r["rule"], "why_not": r["why_not"]})
    return {"rules_not_yet_modeled": out, "count": len(out)}


def main() -> None:
    evidence = {"phase": "6.4", "catalog": catalog_inventory(), "soc": soc_inventory(),
                "curated": curated_inventory()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("catalog:", evidence["catalog"]["counts"])
    print("soc:", evidence["soc"]["counts"])
    print("soc distinct:", evidence["soc"]["distinct_texts"])
    print("soc uniform sectionNotes:", evidence["soc"]["section_notes_uniform_across_all_sections"])
    print("curated rules_not_yet_modeled:", evidence["curated"]["count"])
    print("wrote", OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
