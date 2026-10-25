"""The committed SYNTHETIC Degree Navigator audit fixture (Phase 7.3 Step 3, 7.4).

It copies the real Chrome "Save as PDF" audit layout (measured on the
developer's own export, never committed) with invented data. These tests pin
the layout characteristics the DN importer handles, and the parser's
accuracy on the fixture against the generator's own model.
"""

from __future__ import annotations

import pathlib
import re

from app.domain.degree_audit import parse_audit
from app.services.degree_audit.pdf_worker import extract_audit_layout

from .support.synthetic_dn import (
    ICONS,
    REQUIREMENT_ICON,
    audit_layout,
    build_audit_pdf,
    render_pdf,
    sas_core_model,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "synthetic" / "dn_audit.SYNTHETIC.pdf"
COURSE = r"\d\d:\d{3}:\d{3}"
STATUS = {v: k for k, v in REQUIREMENT_ICON.items()}


def _layout(data=None):
    out = extract_audit_layout(data or FIXTURE.read_bytes(), 40, 400_000)
    assert out[0] == "ok", out
    return out[3]


def test_fixture_is_regenerable_and_synthetic():
    assert FIXTURE.read_bytes() == build_audit_pdf()
    text = " ".join(f["text"] for page in _layout() for f in page["fragments"])
    assert set(re.findall(COURSE, text)) <= {f"01:999:{n}" for n in range(100, 1000)}
    assert "TEST STUDENT" in text and "000000000" in text          # invented identity


def test_status_lives_in_icons_on_heading_baselines():
    """DN status is an IMAGE on the heading's baseline, not text; legend icons
    are the same images (same pixel digest) as the ones they explain."""
    layout = _layout()
    sizes = {(i["width"], i["height"]) for page in layout for i in page["images"]}
    assert sizes == {(w, h) for w, h, _ in ICONS.values()}
    for page in layout:
        heads = [f for f in page["fragments"] if re.match(r"^R\d+ : ", f["text"])]
        for head in heads:
            assert [i for i in page["images"] if abs(i["y"] - head["y"]) < 1.5
                    and i["x"] < head["x"]], "every heading has its icon on its baseline"


def test_two_numbered_course_rows_share_one_line():
    page = next(p for p in _layout() if any(f["text"] == "Courses Used for this Program"
                                            for f in p["fragments"]))
    numbers = [f for f in page["fragments"] if f["text"] in ("1.", "2.")]
    assert len(numbers) >= 2 and abs(numbers[0]["y"] - numbers[1]["y"]) < 1.5


def test_parser_reads_every_field_of_the_fixture():
    """Extraction accuracy on the synthetic fixture = 100% of the model's fields."""
    model = sas_core_model()
    block = parse_audit(_layout()).blocks[0]
    assert (block.subject, block.version_label, block.printed_at, block.degree_id) == (
        model["subject"], model["version"], model["printed_at"], model["degree_id"])
    assert block.report_date == model["report_date"]
    assert len(block.requirements) == len(model["requirements"])
    for req, want in zip(block.requirements, model["requirements"], strict=True):
        assert req.title == want["title"]
        assert req.icon == want["status"]
        assert req.problems == []
        assert [(c.course_code, c.term_label, c.grade or "current") for c in req.courses] == \
            [(c, t, g) for c, _, t, g in want["courses"]]
        assert [(g.code, g.courses) for g in req.goals] == \
            [(code, list(courses)) for _, code, courses in want["goals"]]
    listed = {(c.list_kind, c.course_code) for c in block.courses}
    assert listed == {("used", c[0]) for c in model["used"]} | \
        {("unused", c[0]) for c in model["unused"]} | \
        {("below_minimum_grade", c[0]) for c in model["below_minimum"]}


def test_identity_lines_are_never_returned():
    parsed = parse_audit(_layout())
    dump = repr(parsed)
    assert "TEST STUDENT" not in dump and "000000000" not in dump


def test_an_icon_that_matches_no_legend_icon_is_unknown():
    pages = audit_layout(sas_core_model())
    # SYNTHETIC: drop the legend's icons; the heading icons then match nothing.
    first = pages[0]
    first["icons"] = [i for i in first["icons"] if i[1] < 470 or i[0] not in (37.5, 314.2)]
    block = parse_audit(_layout(render_pdf(pages))).blocks[0]
    assert {r.icon for r in block.requirements} == {"unknown"}
    assert "LEGEND_INCOMPLETE" in block.warnings
    assert all("ICON_UNKNOWN" in r.problems for r in block.requirements)
