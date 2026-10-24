"""Per-course WORKLOAD WEIGHT and delivery mode from published SOC facts (Phase 7.3).

A planning WEIGHT, not a difficulty rating: it is computed only from facts
Rutgers publishes, every component is shown to the student, and no wording
calls a course easy or hard (docs/SOURCE_AUTHORITY.md: difficulty claims need
first-party feedback; these are not difficulty claims).

    weight = credits
           + 0.25 x weekly contact hours ABOVE the credits
           + 1.0  if a section meets as a LAB (SOC meeting mode 05)
           + 0.5  x (course level - 1)        100-level 0 ... 400-level 1.5
           + 1.0  if technical: the major's own subject, 01:640 mathematics,
                  or certified SAS Core Natural Science (NS)

Contact hours come from the SOC meetings of the course's sections in the
planned term when Rutgers has published it, else from its most recent loaded
term with sections (`basis_term` says which). A course's sections usually
share one structure (lecture + recitation + lab); the most common weekly
total is used, ties to the larger. Untimed meetings (online asynchronous,
research, TBA) add no contact hours.

Delivery mode, per section, from the SOC meeting mode: `async` - every
meeting is ONLINE INSTRUCTION (mode 90) with no day/time; `online` - all
mode 90, some timed; `in_person` - anything else.

The four workload levels are CoursePilot planning settings, not Rutgers rules.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.planning import WorkloadLevel
from app.models import Course, CourseOffering, CourseSection, SectionMeeting

ONLINE_MODE = "90"
LAB_MODE = "05"
MATH_SUBJECT = "640"

#: (credit floor, credit maximum, workload budget) per level.
LEVELS: dict[WorkloadLevel, tuple[Decimal, Decimal, Decimal]] = {
    WorkloadLevel.LIGHT: (Decimal(12), Decimal(14), Decimal(16)),
    WorkloadLevel.BALANCED: (Decimal(14), Decimal(17), Decimal(20)),
    WorkloadLevel.SEMI_RIGOROUS: (Decimal(16), Decimal(19), Decimal(24)),
    WorkloadLevel.CHALLENGING: (Decimal(17), Decimal(21), Decimal(28)),
}

_Q = Decimal("0.01")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_Q, rounding=ROUND_HALF_UP)


@dataclass(slots=True)
class CourseWorkload:
    course: str
    credits: Decimal
    contact_hours: Decimal | None        # None: no published sections anywhere
    has_lab: bool
    level: int                           # 1..4 (course-number hundreds, capped)
    technical: str | None                # why it counts as technical, or None
    weight: Decimal
    components: dict[str, Decimal] = field(default_factory=dict)
    delivery: list[str] = field(default_factory=list)   # async | online | in_person
    basis_term: str | None = None


def _minutes(hhmm: str | None) -> int | None:
    if not hhmm or len(hhmm) != 4 or not hhmm.isdigit():
        return None
    return int(hhmm[:2]) * 60 + int(hhmm[2:])


def course_level(course_key: str) -> int:
    number = course_key.split(":")[-1]
    hundreds = int(number[0]) if number[:1].isdigit() else 1
    return min(max(hundreds, 1), 4)


class WorkloadIndex:
    """Weights for many courses at once - one meetings query per term batch."""

    def __init__(self, session: Session, *, major_subject: str | None,
                 natural_science: set[str]) -> None:
        self.session = session
        self.major_subject = major_subject
        self.natural_science = natural_science
        self._sections: dict[tuple[str, str], dict] = {}      # (course, term) -> facts
        self._latest: dict[str, str | None] = {}               # course -> latest term w/ sections
        self._cache: dict[tuple[str, str], CourseWorkload] = {}

    def _load(self, keys: set[str]) -> None:
        need = sorted(k for k in keys if k not in self._latest)
        if not need:
            return
        sections: dict[tuple[str, str], dict] = defaultdict(lambda: defaultdict(list))
        for key, term, section_id, mode, day, start, end in self.session.execute(
                select(Course.course_string, CourseSection.term_code, CourseSection.id,
                       SectionMeeting.meeting_mode_code, SectionMeeting.meeting_day,
                       SectionMeeting.start_time_military, SectionMeeting.end_time_military)
                .join(CourseOffering, CourseOffering.course_id == Course.id)
                .join(CourseSection, CourseSection.offering_id == CourseOffering.id)
                .join(SectionMeeting, SectionMeeting.section_id == CourseSection.id)
                .where(Course.course_string.in_(need), Course.supplement_code == "")):
            sections[(key, term)][section_id].append((mode, day, start, end))
        for key in need:
            self._latest[key] = None
        for (key, term), by_section in sections.items():
            self._sections[(key, term)] = by_section
            if self._latest[key] is None or term > self._latest[key]:
                self._latest[key] = term

    def weight(self, course: Course, term: str) -> CourseWorkload:
        key = course.course_string
        if (key, term) in self._cache:
            return self._cache[(key, term)]
        self._load({key})
        basis = term if (key, term) in self._sections else self._latest.get(key)
        hours, has_lab, delivery = None, False, []
        if basis is not None:
            totals, modes = [], set()
            for meetings in self._sections[(key, basis)].values():
                minutes = 0
                for mode, day, start, end in meetings:
                    a, b = _minutes(start), _minutes(end)
                    if day and a is not None and b is not None and b > a:
                        minutes += b - a
                    has_lab = has_lab or mode == LAB_MODE
                totals.append(minutes)
                online = all(m[0] == ONLINE_MODE for m in meetings)
                untimed = all(not m[1] for m in meetings)
                modes.add("async" if online and untimed else "online" if online else "in_person")
            common = Counter(totals).most_common()
            top = max(n for _, n in common)
            hours = _q(Decimal(max(t for t, n in common if n == top)) / 60)
            delivery = sorted(modes)
        credits = Decimal(course.credits or 0)
        level = course_level(key)
        subject = key.split(":")[1] if key.count(":") == 2 else ""
        major = bool(self.major_subject) and subject == self.major_subject
        technical = ("major subject " + subject if major
                     else "mathematics (01:640)" if subject == MATH_SUBJECT
                     else "SAS Core natural science" if key in self.natural_science else None)
        above = max(Decimal(0), (hours or credits) - credits)
        components = {
            "credits": credits,
            "contact_above_credits": _q(Decimal("0.25") * above),
            "lab": Decimal(1) if has_lab else Decimal(0),
            "level": _q(Decimal("0.5") * (level - 1)),
            "technical": Decimal(1) if technical else Decimal(0),
        }
        result = CourseWorkload(course=key, credits=credits, contact_hours=hours,
                                has_lab=has_lab, level=level, technical=technical,
                                weight=_q(sum(components.values(), Decimal(0))),
                                components=components, delivery=delivery, basis_term=basis)
        self._cache[(key, term)] = result
        return result


def filler_rank(delivery: list[str], preference: str) -> int:
    """0 first. 'flexible': async, then online, then in person; 'in_person':
    in-person sections first; no preference: all equal."""
    if preference == "flexible":
        return 0 if "async" in delivery else 1 if "online" in delivery else 2
    if preference == "in_person":
        return 0 if "in_person" in delivery else 1
    return 0


__all__ = ["LEVELS", "CourseWorkload", "WorkloadIndex", "course_level", "filler_rank"]
