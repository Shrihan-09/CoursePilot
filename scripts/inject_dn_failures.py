"""Damage a SYNTHETIC Degree Navigator audit and show the importer catches it (Phase 7.4).

Three failures a real print (or a bad export) could produce:

  flipped_marker   one requirement's status ICON swapped (x -> check): the
                   icon no longer agrees with its "Needs:" line, and the
                   audit's "Completed Requirements: k of n" no longer adds up
  dropped_allocation  one course line under a requirement's "Completed
                   Courses:" missing: "Completed: n" no longer matches
  shifted_column   one course row's detail fragment pushed off its baseline:
                   the code and its "( credits, term, grade )" land on
                   different lines

Each must become NEEDS REVIEW - never a silently different status or course.

Usage:  python scripts/inject_dn_failures.py [--out DIR]
Writes the damaged PDFs to DIR (default: the system temp dir) and prints
what the parser flagged for each. Uses only invented data.
"""

from __future__ import annotations

import argparse
import copy
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend" / "tests")]

from app.domain.degree_audit import parse_audit  # noqa: E402
from app.services.degree_audit.pdf_worker import extract_audit_layout  # noqa: E402
from support.synthetic_dn import audit_layout, render_pdf, sas_core_model  # noqa: E402


def _heading(pages, number):
    for p, page in enumerate(pages):
        for x, y, text, _ in page["lines"]:
            if text.startswith(f"R{number} : "):
                return p, y
    raise LookupError(f"R{number} not in layout")


def _course_lines(pages, number):
    """(page, y) of each course row under R<number>'s "Completed Courses:"."""
    p, y0 = _heading(pages, number)
    out, inside = [], False
    for q in range(p, len(pages)):
        for x, y, text, _ in sorted(pages[q]["lines"], key=lambda ln: (-ln[1], ln[0])):
            if q == p and y >= y0:
                continue
            if text.startswith(f"R{number + 1} : ") or text in ("Conditions", "Reuse"):
                return out
            if text == "Completed Courses:":
                inside = True
            elif inside and x < 60 and ":" in text and text[:2].isdigit():
                out.append((q, y))
    return out


def flipped_marker(pages, number=2, to="completed"):
    pages = copy.deepcopy(pages)
    p, y = _heading(pages, number)
    pages[p]["icons"] = [(x, iy, to if abs(iy - y) < 1 and x < 45 else kind)
                         for x, iy, kind in pages[p]["icons"]]
    return pages


def dropped_allocation(pages, number=1):
    pages = copy.deepcopy(pages)
    q, y = _course_lines(pages, number)[0]
    pages[q]["lines"] = [ln for ln in pages[q]["lines"] if abs(ln[1] - y) > 0.5]
    return pages


def shifted_column(pages, number=2, dy=6.0):
    pages = copy.deepcopy(pages)
    q, y = _course_lines(pages, number)[0]
    pages[q]["lines"] = [(x, ly - dy if abs(ly - y) < 0.5 and text.startswith("(") else ly,
                          text, size) for x, ly, text, size in pages[q]["lines"]]
    return pages


FAILURES = {"flipped_marker": flipped_marker, "dropped_allocation": dropped_allocation,
            "shifted_column": shifted_column}


def parse_pdf(data: bytes):
    out = extract_audit_layout(data, 40, 400_000)
    assert out[0] == "ok", out
    return parse_audit(out[3])


def flagged(parsed) -> dict:
    """requirement number -> problem codes; plus course-list problems."""
    block = parsed.blocks[0]
    out = {f"R{r.number}": sorted(set(r.problems)) for r in block.requirements if r.problems}
    for c in block.courses:
        if c.problems:
            out.setdefault("course_lists", []).extend(c.problems)
    if block.warnings:
        out["block"] = list(block.warnings)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=tempfile.gettempdir())
    args = parser.parse_args()
    base = audit_layout(sas_core_model())
    print("clean:", flagged(parse_pdf(render_pdf(base))) or "nothing flagged")
    for name, damage in FAILURES.items():
        data = render_pdf(damage(base))
        path = pathlib.Path(args.out) / f"dn_audit.SYNTHETIC.{name}.pdf"
        path.write_bytes(data)
        print(f"{name}: {flagged(parse_pdf(data))}  ({path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
