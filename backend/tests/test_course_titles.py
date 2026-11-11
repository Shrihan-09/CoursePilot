"""No ALL-CAPS course titles reach a student (SOC publishes many in caps)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.plan_edits import CourseLine, ForcedMove
from app.domain.text import display_title


@pytest.mark.parametrize(("raw", "shown"), [
    ("HISTORY OF THE PRINTED BOOK", "History of the Printed Book"),
    ("INTRO TO MUSIC", "Intro to Music"),
    ("CALCULUS II", "Calculus II"),
    ("WOMEN'S HISTORY IN THE U.S.", "Women's History in the U.S."),
    ("SOCIO-CULTURAL ANTHROPOLOGY", "Socio-Cultural Anthropology"),
    ("DATA 101: INTRO TO DATA", "Data 101: Intro to Data"),
    ("HANDS-ON AI FOR STEM", "Hands-On AI for STEM"),
    ("THE WORLD OF THE BIBLE", "The World of the Bible"),
    ("C++ PROGRAMMING", "C++ Programming"),
    ("O'NEILL AND THE AMERICAN STAGE", "O'Neill and the American Stage"),
])
def test_all_caps_titles_become_title_case(raw, shown):
    assert display_title(raw) == shown


@pytest.mark.parametrize("title", [
    "Stories of the Sea in Film",   # catalog case is kept as written
    "Introduction to Computer Science",
    "iOS Development",
    "",
    "101",
])
def test_titles_that_are_not_all_caps_are_unchanged(title):
    assert display_title(title) == title


def test_preview_lines_never_carry_an_all_caps_title():
    line = CourseLine(course="01:111:222", title="HISTORY OF THE PRINTED BOOK", credits=Decimal(3))
    move = ForcedMove(course="01:111:222", title="HISTORY OF THE PRINTED BOOK",
                      from_term=None, to_term="20271", reason="now planned")
    assert line.title == move.title == "History of the Printed Book"


def test_a_saved_plan_is_shown_in_title_case_without_rewriting_it():
    """Titles are cased when a plan is read, so plans saved before this change
    display correctly and stored data is never rewritten."""
    from app.domain.planning import PlannedCourse

    fields = PlannedCourse.model_fields
    assert "title" in fields
    raw = {"course": "01:111:222", "title": "HISTORY OF THE PRINTED BOOK"}
    # Validate only the title field's type through a minimal model.
    from pydantic import create_model

    from app.domain.text import CourseTitle
    probe = create_model("Probe", course=(str, ...), title=(CourseTitle, ...))
    assert probe.model_validate(raw).title == "History of the Printed Book"
    assert fields["title"].annotation is str
    assert any(getattr(m, "func", None) is display_title for m in fields["title"].metadata)
