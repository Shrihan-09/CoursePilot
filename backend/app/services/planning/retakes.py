"""The retakes the planner detects, and WHEN the student may retake each (10a follow-up).

Read-only. The planner already decides which courses on the record must be
taken again (an attempt that earned no credit, or a grade below what a later
course needs) and marks them `PlannedCourse.retake`. This module runs that
same plan - the student's confirmed preferences, WITHOUT their retake pins, so
the baseline is the plan they would get with no choice made - and lists:

* each retake: course, title, why, and what it unlocks (later courses whose
  prerequisite needs it and that the plan places or its targets require);
* the terms they may choose (the Musts step's "When do you want to retake it?"):

  | option | term | shown |
  |---|---|---|
  | asap | earliest Fall/Spring from the plan's start with offering evidence | always |
  | winter | the next Winter session | only with offering evidence for it |
  | next_semester | the plan's first Fall/Spring term | always |
  | summer | the next Summer session | only with offering evidence for it |

  Offering evidence is the planner's own (`OfferingIndex`): CONFIRMED only when
  that term's SOC lists the course; otherwise "historically offered", labelled.
* each option's impact on what the retake unlocks: the earliest Fall/Spring
  after the option's term with offering evidence for the unlocked course,
  against the baseline plan's term for it - "on track" or "delays".

Nothing here is a cost: winter and summer are billed separately, and the
student is pointed at Rutgers' tuition page - never a dollar amount.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.domain.planning import OfferingEvidenceKind, PlanResult, WorkloadLevel
from app.domain.planning_profile import PlanningProfileInput, to_constraints
from app.models import Student
from app.services.planning.calendar import current_term, default_start
from app.services.planning.offerings import OfferingIndex
from app.services.planning.profile import get_profile, profile_input
from app.services.planning.service import generate_plan, latest_history_term
from app.services.planning.terms import label

COST_NOTE = ("Winter and summer courses are billed separately — check Rutgers' tuition page.")
OPTIONS = ("asap", "winter", "next_semester", "summer")
#: How far ahead ASAP and an unlocked course's earliest term are looked for.
LOOKAHEAD_REGULAR_TERMS = 6


@dataclass(frozen=True, slots=True)
class Impact:
    course: str
    #: The earliest Fall/Spring term the unlocked course could follow this
    #: option in (None: no offering evidence in the look-ahead).
    term_code: str | None
    #: The baseline plan's term for it (None: not placed in the baseline).
    planned_term: str | None
    delayed: bool
    message: str


@dataclass(frozen=True, slots=True)
class RetakeOption:
    key: str
    term_code: str | None
    label: str
    #: confirmed | historical | none
    evidence: str
    evidence_label: str
    recommended: bool
    impacts: tuple[Impact, ...] = ()


@dataclass(frozen=True, slots=True)
class Unlock:
    course: str
    title: str | None
    planned_term: str | None
    #: Its published prerequisite names the retake itself (not via another
    #: course) - only these get an impact line: a chain's later links move
    #: with their own prerequisites, which this read-only check does not re-plan.
    direct: bool = False


@dataclass(frozen=True, slots=True)
class Retake:
    course: str
    title: str
    reason: str
    unlocks: tuple[Unlock, ...]
    #: It unlocks a course the baseline plan needs in its next two terms.
    blocking: bool
    default: str
    options: tuple[RetakeOption, ...]
    #: The student's saved choice: (option key, term) from a retake pin.
    chosen_term: str | None = None


@dataclass(frozen=True, slots=True)
class RetakesView:
    start_term: str | None
    retakes: tuple[Retake, ...] = field(default_factory=tuple)
    cost_note: str = COST_NOTE


def _next_regular(term: str) -> str:
    year, s = int(term[:4]), term[4]
    return f"{year}9" if s in ("0", "1", "7") else f"{year + 1}1"


def _regular_from(term: str, count: int) -> list[str]:
    first = term if term[4] in ("1", "9") else _next_regular(term)
    out = [first]
    while len(out) < count:
        out.append(_next_regular(out[-1]))
    return out


def _next_season(after: str, digit: str) -> str:
    """The first term of season `digit` (0 winter, 7 summer) after `after`."""
    year = int(after[:4])
    candidate = f"{year}{digit}"
    return candidate if candidate > after else f"{year + 1}{digit}"


def _evidence(index: OfferingIndex, course: str, term: str) -> tuple[str, str]:
    ev = index.evidence(course, term)
    if ev.kind is OfferingEvidenceKind.CONFIRMED_IN_TERM:
        return "confirmed", f"Listed in the {label(term)} Schedule of Classes"
    if ev.kind is OfferingEvidenceKind.HISTORICAL_SAME_SEASON:
        seen = ", ".join(label(t) for t in ev.same_season_terms[-3:])
        return "historical", f"Historically offered ({seen}) — not yet confirmed for {label(term)}"
    return "none", f"No offering on record for {label(term)}"


def _earliest_after(index: OfferingIndex, course: str, term: str) -> str | None:
    for t in _regular_from(_next_regular(term) if term[4] in "19" else term,
                           LOOKAHEAD_REGULAR_TERMS):
        if t > term and _evidence(index, course, t)[0] != "none":
            return t
    return None


def _impacts(index: OfferingIndex, term: str | None, unlocks: tuple[Unlock, ...],
             option_name: str) -> tuple[Impact, ...]:
    if term is None:
        return ()
    out = []
    for u in (u for u in unlocks if u.direct):
        baseline = u.planned_term
        earliest = _earliest_after(index, u.course, term)
        if baseline is not None and baseline > term and (earliest is None or baseline < earliest):
            # The plan already uses a winter/summer term for it (the student
            # plans those seasons): still possible after this option.
            earliest = baseline
        delayed = earliest is None or (baseline is not None and earliest > baseline)
        if earliest is None:
            msg = f"No later offering of {u.course} is on record yet"
        elif delayed:
            msg = f"{option_name} delays {u.course} to {label(earliest)}"
        else:
            msg = f"This keeps {u.course} on track for {label(baseline or earliest)}"
        out.append(Impact(u.course, earliest, baseline, delayed, msg))
    return tuple(out)


def _baseline(session: Session, student: Student, start: str) -> PlanResult:
    row = get_profile(session, student)
    # No confirmed preferences yet (mid-setup): the same defaults the
    # Preferences page offers.
    profile = (profile_input(row) if row is not None
               else PlanningProfileInput(workload_level=WorkloadLevel.BALANCED))
    data = profile.model_dump()
    data["pins"] = [p for p in data["pins"] if not p.get("retake")]
    return generate_plan(session, student, start_term=start,
                         constraints=to_constraints(PlanningProfileInput.model_validate(data)))


def detect_retakes(session: Session, student: Student, on: date | None = None) -> RetakesView:
    start = default_start(latest_history_term(session, student), on)
    result = _baseline(session, student, start)
    planned = {pc.course: pc for term in result.terms for pc in term.courses}
    retakes = [pc for pc in planned.values() if pc.retake]
    if not retakes:
        return RetakesView(start_term=start)
    row = get_profile(session, student)
    chosen = {p["course"]: p["term_code"] for p in (row.pins or []) if p.get("retake")} \
        if row is not None else {}
    keys = {pc.course for pc in retakes} | {u for pc in retakes for u in pc.unlocks}
    index = OfferingIndex(session, keys)
    now = current_term(on)
    floor = max(now, latest_history_term(session, student) or now)
    regular = _regular_from(start, LOOKAHEAD_REGULAR_TERMS)
    out = []
    for pc in sorted(retakes, key=lambda p: (p.term_code, p.course)):
        unlocks = tuple(Unlock(u, planned[u].title if u in planned else None,
                               planned[u].term_code if u in planned else None,
                               direct=u in planned and pc.course in {
                                   d.course for d in planned[u].prerequisite.conditional_on})
                        for u in pc.unlocks)
        asap = next((t for t in regular if _evidence(index, pc.course, t)[0] != "none"), None)
        # Blocking: the plan needs something it unlocks within the two
        # Fall/Spring terms after the earliest retake - ASAP is then the default.
        soon = _regular_from(_next_regular(asap), 2)[-1] if asap else None
        blocking = soon is not None and any(
            u.planned_term is not None and u.planned_term <= soon for u in unlocks)
        winter = _next_season(floor, "0")
        summer = _next_season(floor, "7")
        options = []
        for key, term, name in (("asap", asap, "ASAP"), ("winter", winter, "Winter session"),
                                ("next_semester", start, "Next semester"),
                                ("summer", summer, "Summer")):
            if term is None:
                ev, ev_label = "none", "No Fall/Spring offering on record in the next three years"
            else:
                ev, ev_label = _evidence(index, pc.course, term)
            if key in ("winter", "summer") and ev == "none":
                continue                  # shown only when it is offered then
            options.append(RetakeOption(
                key=key, term_code=term, label=label(term) if term else "Not available",
                evidence=ev, evidence_label=ev_label, recommended=key == "asap",
                impacts=_impacts(index, term, unlocks, name)))
        out.append(Retake(course=pc.course, title=pc.title, reason=pc.retake, unlocks=unlocks,
                          blocking=blocking, default="asap" if blocking else "next_semester",
                          options=tuple(options), chosen_term=chosen.get(pc.course)))
    return RetakesView(start_term=start, retakes=tuple(out))


__all__ = ["COST_NOTE", "OPTIONS", "Impact", "Retake", "RetakeOption", "RetakesView",
           "Unlock", "detect_retakes"]
