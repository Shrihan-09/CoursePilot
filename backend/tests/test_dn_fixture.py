"""Phase 7.3 Step 3: the committed SYNTHETIC Degree Navigator audit fixture.

It copies the real Chrome "Save as PDF" audit layout (measured on the
developer's own export, never committed) with invented data. These tests pin
the layout characteristics a future DN importer must handle.
"""

from __future__ import annotations

import io
import pathlib
import re

from pypdf import PdfReader

from app.services.scheduling.pdf_worker import extract_layout

from .support.synthetic_dn import ICONS, build_audit_pdf

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "synthetic" / "dn_audit.SYNTHETIC.pdf"
COURSE = r"\d\d:\d{3}:\d{3}"


def _text():
    result = extract_layout(FIXTURE.read_bytes(), 40, 400000)
    assert result[0] == "ok"
    return "\n".join(result[1])


def test_fixture_is_regenerable_and_synthetic():
    assert FIXTURE.read_bytes() == build_audit_pdf()
    assert set(re.findall(COURSE, _text())) <= {f"01:999:{n}" for n in range(100, 1000)}


def test_status_lives_in_icons_beside_requirement_headings():
    """DN status is an IMAGE on the heading's baseline, not text."""
    kinds = {(w, h): name for name, (w, h, _) in ICONS.items() if (w, h) != (13, 9)}
    seen = []
    for page in PdfReader(io.BytesIO(FIXTURE.read_bytes())).pages:
        xo = page["/Resources"]["/XObject"]
        draws, ys = [], []

        def before(op, args, cm, tm, xo=xo, draws=draws):
            if op == b"Do":
                draws.append((xo[args[0]].get_object(), cm[5]))

        def vtext(t, cm, tm, font, size, ys=ys):
            if t.strip():
                ys.append(tm[5])

        page.extract_text(visitor_operand_before=before, visitor_text=vtext)
        for obj, y in draws:
            size = (obj["/Width"], obj["/Height"])
            seen.append(kinds.get(size, "check_or_x"))
            assert any(abs(y - t) <= 8 for t in ys), "every icon sits on a text baseline"
    assert {"current", "planned", "in_progress_or_planned", "check_or_x"} <= set(seen)


def test_two_numbered_course_rows_share_one_line():
    """The reading-order hazard: a line holds TWO courses; split on 'n.' items."""
    row = rf"{COURSE} \( \d+, (?:Fall|Spring) \d{{4}}, [^)]+\)"
    assert re.search(rf"\d\. {row} \d\. {row}", _text())
    assert re.search(rf"{COURSE} \( \d+, (?:Fall|Spring) \d{{4}}, current\)", _text())


def test_needs_core_rules_and_disclaimer_are_present():
    text = _text()
    assert re.search(r"^Requirement R\d : .+\[[A-Za-z]+\]$", text, re.M)
    assert re.search(r"\{ .*\[[A-Za-z]+\] \( " + COURSE + r" \) \}", text)
    assert re.search(r"^Needs: \d", text, re.M) and "Needs: In Progress:" in text
    assert "not an official transcript" in text
