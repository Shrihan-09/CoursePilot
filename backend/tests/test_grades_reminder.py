"""10a follow-up: the "Your grades are in" reminder rule (pure; no database).

Dates are the published Registrar final-grades dates in app.core.grade_calendar:
Summer 2026 grades August 14, 2026; Fall 2026 grades January 4, 2027.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.grades_reminder import decide

BEFORE_FALL26_GRADES = datetime(2026, 12, 20, tzinfo=UTC)    # latest graded: Summer 2026
AFTER_FALL26_GRADES = datetime(2027, 1, 10, tzinfo=UTC)      # latest graded: Fall 2026
OCT_2026 = datetime(2026, 10, 1, tzinfo=UTC)
FEB_2027 = datetime(2027, 2, 1, tzinfo=UTC)


def _r(now, *, dismissed=None, first="20259", transcript=OCT_2026, dn=OCT_2026):
    return decide(now, dismissed_term=dismissed, first_term=first,
                  confirmed={"transcript": transcript, "degree_navigator": dn})


def test_it_appears_only_after_the_grades_date_and_only_for_older_imports():
    # October imports are newer than Summer 2026's grades (Aug 14): nothing to do.
    assert not _r(BEFORE_FALL26_GRADES).show
    # After Fall 2026's grades (Jan 4, 2027) the October imports are out of date.
    after = _r(AFTER_FALL26_GRADES)
    assert after.show and after.term_code == "20269"
    assert after.kinds == ["transcript", "degree_navigator"]
    assert str(after.grades_date) == "2027-01-04" and "nbregistrar" in after.source_url
    # A transcript uploaded after the grades date is current; only DN is asked for.
    assert _r(AFTER_FALL26_GRADES, transcript=FEB_2027).kinds == ["degree_navigator"]
    assert not _r(AFTER_FALL26_GRADES, transcript=FEB_2027, dn=FEB_2027).show


def test_dismissing_hides_it_until_the_next_terms_grades_date():
    assert not _r(AFTER_FALL26_GRADES, dismissed="20269").show
    # Dismissed for Summer 2026: Fall 2026's grades bring it back.
    assert _r(AFTER_FALL26_GRADES, dismissed="20267").show


def test_a_first_year_without_grades_yet_is_not_reminded():
    nothing = dict(transcript=None, dn=None)
    # Started Fall 2026: Summer 2026 grades are not theirs.
    assert not _r(BEFORE_FALL26_GRADES, first="20269", **nothing).show
    # Fall 2026 grades are in: upload the first transcript / audit.
    assert _r(AFTER_FALL26_GRADES, first="20269", **nothing).kinds == [
        "transcript", "degree_navigator"]
    # No import and no stated first semester: nothing to anchor on.
    assert not _r(AFTER_FALL26_GRADES, first=None, **nothing).show
