"""The Schedule Engine (Phase 6.6): actual sections for exact requested courses.

```
requested courses (exact) ─► candidates.load_term      batched: sections, meetings,
   + preferences                                        instructors, cross-lists, restrictions
                           ─► unary hard constraints    window, avoided days, failed restriction
                           ─► search.search             backtracking + forward checking
                           ─► rank                      lexicographic objective (below)
                           ─► ScheduleResult            options + structured evidence/issues
```

It never adds, drops or substitutes a course: every option has exactly one
choice per requested course (plus required companion components), or the
result says why no option exists.

## Ranking objective (lexicographic, smallest first)

    1. needs_confirmation               sections whose restriction is UNKNOWN
    2. unknown_time_sections            sections with a TBA / arranged / malformed meeting
    3. closed_sections_if_current       sections CLOSED as of a CURRENT observation
    4. unknown_availability_if_current  sections UNKNOWN while availability is current
    5. preference_misses                sections meeting outside the preferred campuses (soft)
    6. class_days                       distinct weekdays with a timed meeting
    7. gap_minutes                      idle minutes between consecutive classes, per day
    8. the requested courses' index numbers (canonical tie-break)

Options are "ranked schedule options" under this objective - never "best".

## Availability (Phase 6.7)

Availability is a ranking and filtering dimension, never a structural rule:

  * prefer_open (default) - nothing is excluded; with CURRENT availability an
    option whose bundles are open ranks before one with a closed or unknown
    section (3-4 above). STALE availability is reported on every choice and
    option but never reorders anything - an old "open" is not a reason to
    prefer a section now.
  * require_open - a unary hard constraint: only sections OPEN as of a
    CURRENT observation enter the search. A course whose required lab has no
    open section is reported (NO_OPEN_SECTION on that component), never
    silently scheduled without its lab.

Each option reports availability per REGISTRATION BUNDLE (primary +
required companion indexes, `bundle_status`), per requested course - an
academic co-requisite is a separate course and a separate bundle.

## Interchangeable sections

Sections of one course with identical meetings (day, time, dates, campus),
restriction evidence, time verification and cross-listing are equivalent
for every structural rule and every ranking signal. The search runs over one
representative per class (the lowest index; since Phase 6.7 the lowest among
those OPEN as of a current observation, if any) and each choice lists the
others in `equivalent_sections` with their own availability and
instructors. This removes duplicate options that differ only by an
interchangeable index, without dropping any distinct schedule.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from app.domain.availability import CURRENT_WINDOW, bundle_status, oldest, weakest
from app.domain.schedule import (
    TIME_UNKNOWN_KINDS,
    WEEKDAYS,
    AvailabilityFreshness,
    AvailabilityMode,
    AvailabilityState,
    ComponentAvailability,
    CourseAvailability,
    CourseRelationship,
    EquivalentSection,
    MeetingKind,
    OptionAvailability,
    Reason,
    RestrictionOutcome,
    ScheduleIssue,
    ScheduleMetadata,
    ScheduleOption,
    SchedulePreferences,
    ScheduleResult,
    ScheduleScore,
    ScheduleStatus,
    SearchStats,
    SectionChoice,
    Severity,
)
from app.services.scheduling.candidates import SectionCandidate, TermData
from app.services.scheduling.meetings import hhmm, violates_window
from app.services.scheduling.search import Candidate, incompatible_pairs, search

RANKING_OBJECTIVE = ["needs_confirmation", "unknown_time_sections",
                     "closed_sections_if_current", "unknown_availability_if_current",
                     "preference_misses", "class_days", "gap_minutes", "index_numbers"]
NODE_LIMIT = 200_000
SOLUTION_LIMIT = 2_000
ONLINE_CAMPUSES = frozenset({"ONL", "**"})


def _domains(term: TermData, prefs: SchedulePreferences):
    """Apply the UNARY hard constraints; return domains and what excluded what."""
    earliest, latest = hhmm(prefs.earliest_start), hhmm(prefs.latest_end)
    domains: dict[str, list[SectionCandidate]] = {}
    excluded: dict[str, dict[str, list[str]]] = {}
    for slot in sorted(term.candidates):
        keep, why = [], defaultdict(list)
        for c in term.candidates[slot]:
            if c.restriction.outcome is RestrictionOutcome.NOT_SATISFIED:
                why["restriction_not_satisfied"].append(c.index_number)
                continue
            if prefs.availability is AvailabilityMode.REQUIRE_OPEN:
                a = c.availability
                if a.freshness is not AvailabilityFreshness.CURRENT:
                    why["availability_not_current"].append(c.index_number)
                    continue
                if a.state is not AvailabilityState.OPEN:
                    why[f"availability_{a.state.value}"].append(c.index_number)
                    continue
            broken = violates_window(c.meetings, earliest=earliest, latest=latest,
                                     avoid_days=prefs.avoid_days)
            if broken:
                why[broken].append(c.index_number)
                continue
            keep.append(c)
        # Canonical order inside a slot - the objective's own order: clear
        # restriction, verified time, open as of a CURRENT observation, index.
        # (Good first schedules make the ranking bound prune early.)
        keep.sort(key=lambda c: (c.restriction.outcome is RestrictionOutcome.UNKNOWN,
                                 not c.time_verified, _availability_weight(c), c.index_number))
        domains[slot] = keep
        excluded[slot] = dict(why)
    return domains, excluded


def _current(c: SectionCandidate) -> bool:
    return c.availability.freshness is AvailabilityFreshness.CURRENT


def _availability_weight(c: SectionCandidate) -> tuple[int, int]:
    """(closed, unknown) - counted only for CURRENT observations."""
    if not _current(c):
        return 0, 0
    return (int(c.availability.state is AvailabilityState.CLOSED),
            int(c.availability.state is AvailabilityState.UNKNOWN))


def _signature(c: SectionCandidate) -> tuple:
    """Everything a structural rule can see. Sections with the same signature
    are interchangeable; only the FIRST in slot order is searched - the lowest
    index among those with the best CURRENT availability, so an open twin is
    always chosen over a closed one (Phase 6.7). Availability is deliberately
    not part of it: splitting twins by state only adds dominated branches."""
    meetings = tuple(sorted((m.kind.value, m.day or "", m.start_minute or -1,
                             m.end_minute or -1, str(m.start_date), str(m.end_date),
                             m.campus or "") for m in c.meetings))
    r = c.restriction
    return (meetings, r.outcome.value, tuple((e.kind, e.code, e.unit_code) for e in r.entries),
            r.eligibility_text, r.special_permission, c.time_verified,
            tuple(c.cross_listed), c.supplement_code, c.meeting_text_in_notes or "")


def _group(domains):
    """slot -> [(representative, [equivalent candidates])], canonical order kept."""
    out = {}
    for slot, cands in domains.items():
        groups: dict[tuple, list[SectionCandidate]] = {}
        for c in cands:
            groups.setdefault(_signature(c), []).append(c)
        out[slot] = [(g[0], g[1:]) for g in groups.values()]
    return out


def _misses(c: SectionCandidate, prefs: SchedulePreferences) -> int:
    return int(bool(prefs.preferred_campuses) and any(
        m.kind is MeetingKind.TIMED and m.campus not in ONLINE_CAMPUSES
        and m.campus not in prefs.preferred_campuses for m in c.meetings))


def _as_search(domains, prefs: SchedulePreferences):
    """Search over group representatives (a plain domain is a list of candidates)."""
    out = {}
    for slot, items in domains.items():
        reps = [i[0] if isinstance(i, tuple) else i for i in items]
        out[slot] = [Candidate(
            slot=slot, index=c.index_number, meetings=tuple(c.meetings),
            cross_listed=frozenset(c.cross_listed),
            weight=(int(c.restriction.outcome is RestrictionOutcome.UNKNOWN),
                    int(not c.time_verified), *_availability_weight(c), _misses(c, prefs)),
            days=frozenset(m.day for m in c.meetings if m.kind is MeetingKind.TIMED))
            for c in reps]
    return out


def _score(choices: list[SectionCandidate], prefs: SchedulePreferences) -> ScheduleScore:
    by_day: dict[str, list[tuple[int, int]]] = defaultdict(list)
    misses = closed = unknown = 0
    for c in choices:
        for m in c.meetings:
            if m.kind is MeetingKind.TIMED:
                by_day[m.day].append((m.start_minute, m.end_minute))
        misses += _misses(c, prefs)
        was_closed, was_unknown = _availability_weight(c)
        closed, unknown = closed + was_closed, unknown + was_unknown
    gaps = 0
    for spans in by_day.values():
        spans.sort()
        end = spans[0][1]
        for s, e in spans[1:]:
            if s > end:
                gaps += s - end
            end = max(end, e)
    return ScheduleScore(
        needs_confirmation=sum(c.restriction.outcome is RestrictionOutcome.UNKNOWN
                               for c in choices),
        unknown_time_sections=sum(not c.time_verified for c in choices),
        closed_sections_if_current=closed,
        unknown_availability_if_current=unknown,
        preference_misses=misses,
        class_days=len(by_day),
        gap_minutes=gaps)


def _key(score: ScheduleScore, choices: list[SectionCandidate]) -> tuple:
    return (score.needs_confirmation, score.unknown_time_sections,
            score.closed_sections_if_current, score.unknown_availability_if_current,
            score.preference_misses, score.class_days, score.gap_minutes,
            tuple(c.index_number for c in sorted(choices, key=lambda c: c.slot)))


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _option_availability(choices: list[SectionCandidate]) -> OptionAvailability:
    """Bundle availability per requested course, then for the whole option."""
    by_course: dict[str, list[SectionCandidate]] = defaultdict(list)
    for c in choices:
        by_course[c.course].append(c)
    courses = []
    for course in sorted(by_course):
        parts = sorted(by_course[course], key=lambda c: (c.component != "primary", c.slot))
        comps = [ComponentAvailability(
            component=c.component, supplement_code=c.supplement_code,
            index_number=c.index_number, state=c.availability.state,
            freshness=c.availability.freshness, observed_at=c.availability.observed_at)
            for c in parts]
        fresh = weakest(x.freshness for x in comps)
        reasons = [f"{x.component}_{x.state.value}:{x.index_number}" for x in comps
                   if x.state is not AvailabilityState.OPEN]
        if fresh is not AvailabilityFreshness.CURRENT:
            reasons.append(f"availability_{fresh.value}")
        as_of = oldest(_when(x.observed_at) for x in comps)
        courses.append(CourseAvailability(
            course=course, state=bundle_status(x.state for x in comps), freshness=fresh,
            as_of=as_of.isoformat() if as_of else None, components=comps, reasons=reasons))
    state = bundle_status(c.state for c in courses)
    fresh = weakest(c.freshness for c in courses)
    as_of = oldest(_when(c.as_of) for c in courses)
    return OptionAvailability(
        state=state, freshness=fresh, as_of=as_of.isoformat() if as_of else None,
        all_open_now=(state is AvailabilityState.OPEN
                      and fresh is AvailabilityFreshness.CURRENT),
        courses=courses)


def _choice(c: SectionCandidate, equivalents: list[SectionCandidate]) -> SectionChoice:
    return SectionChoice(
        equivalent_sections=[EquivalentSection(
            index_number=e.index_number, section_number=e.section_number,
            availability=e.availability, instructors=e.instructors) for e in equivalents],
        course=c.course, component=c.component, course_string=c.course_string,
        supplement_code=c.supplement_code, title=c.title, section_number=c.section_number,
        index_number=c.index_number, campus_code=c.campus_code, meetings=c.meetings,
        instructors=c.instructors, availability=c.availability, restriction=c.restriction,
        cross_listed_indexes=c.cross_listed, time_verified=c.time_verified, notes=c.notes,
        credits=c.credits, meeting_components=c.meeting_components,
        component_evidence=c.component_evidence,
        meeting_text_in_notes=c.meeting_text_in_notes)


_SECTION_REF = re.compile(r"(?:\d{2}:)?(\d{3}:\d{3}):([0-9A-Z]{2})\b")


def _option_issues(choices: list[SectionCandidate], prefs: SchedulePreferences) -> list:
    issues = []
    requested = {c.course.split(":", 1)[1]: c.course for c in choices}   # "160:314" -> key
    for c in choices:
        a = c.availability
        if a.state is AvailabilityState.CLOSED and _current(c):
            companion = c.component == "required_companion"
            issues.append(ScheduleIssue(
                severity=Severity.WARNING,
                code="REQUIRED_COMPONENT_CLOSED" if companion else "SECTION_CLOSED",
                message=(f"{c.course} index {c.index_number}"
                         + (f" (required {c.supplement_code} component)" if companion else "")
                         + f" is CLOSED as of {a.observed_at}; the schedule is structurally "
                         "valid but cannot be registered as is."),
                courses=[c.course], indexes=[c.index_number],
                details={"component": c.component, "observed_at": a.observed_at,
                         "source": a.source, "since": a.since}))
        elif a.state is AvailabilityState.UNKNOWN:
            issues.append(ScheduleIssue(
                severity=Severity.WARNING, code="AVAILABILITY_UNKNOWN",
                message=(f"{c.course} index {c.index_number}: CoursePilot cannot tell whether "
                         "it is open or closed."),
                courses=[c.course], indexes=[c.index_number],
                details={"component": c.component, "reasons": a.reasons,
                         "observed_at": a.observed_at}))
        if c.meeting_text_in_notes:
            issues.append(ScheduleIssue(
                severity=Severity.NEEDS_CONFIRMATION, code="MEETING_TIME_IN_NOTES",
                message=(f"{c.course} index {c.index_number}: Rutgers describes meeting times "
                         "in the section note that CoursePilot cannot verify against the "
                         "published meetings; conflict freedom is not verified."),
                courses=[c.course], indexes=[c.index_number],
                details={"note": c.meeting_text_in_notes}))
        for m in _SECTION_REF.finditer(c.notes or ""):
            other = requested.get(m.group(1))
            if other and other != c.course:
                issues.append(ScheduleIssue(
                    severity=Severity.NEEDS_CONFIRMATION,
                    code="SECTION_NOTE_REFERENCES_REQUESTED_COURSE",
                    message=(f"{c.course} index {c.index_number}'s note names section "
                             f"{m.group(2)} of {other}, also requested; CoursePilot cannot "
                             "verify which sections Rutgers allows together."),
                    courses=sorted({c.course, other}), indexes=[c.index_number],
                    details={"note": c.notes}))
        if c.restriction.outcome is RestrictionOutcome.UNKNOWN:
            issues.append(ScheduleIssue(
                severity=Severity.NEEDS_CONFIRMATION, code="SECTION_RESTRICTION_UNKNOWN",
                message=(f"{c.course} index {c.index_number}: CoursePilot cannot confirm the "
                         "student may register for this section."),
                courses=[c.course], indexes=[c.index_number],
                details={"reasons": c.restriction.reasons,
                         "open_to": c.restriction.open_to_text,
                         "eligibility": c.restriction.eligibility_text,
                         "special_permission": c.restriction.special_permission}))
        unknown = [m.kind.value for m in c.meetings if m.kind in TIME_UNKNOWN_KINDS]
        if unknown:
            issues.append(ScheduleIssue(
                severity=Severity.NEEDS_CONFIRMATION, code="SCHEDULE_TIME_UNKNOWN",
                message=(f"{c.course} index {c.index_number} has a meeting with no usable time; "
                         "conflict freedom cannot be fully verified."),
                courses=[c.course], indexes=[c.index_number], details={"kinds": unknown}))
            if prefs.time_constraints():
                issues.append(ScheduleIssue(
                    severity=Severity.WARNING, code="TIME_CONSTRAINT_UNVERIFIABLE",
                    message="A meeting without a time cannot be checked against your time limits.",
                    courses=[c.course], indexes=[c.index_number]))
        if c.cross_listed:
            issues.append(ScheduleIssue(
                severity=Severity.WARNING, code="CROSS_LISTED_SECTION",
                message=(f"{c.course} index {c.index_number} is cross-listed; register for ONE "
                         "of the listed indexes only."),
                courses=[c.course], indexes=[c.index_number, *c.cross_listed]))
    # Campus changes between consecutive classes - reported, never judged.
    by_day = defaultdict(list)
    for c in choices:
        for m in c.meetings:
            if m.kind is MeetingKind.TIMED and m.campus and m.campus not in ONLINE_CAMPUSES:
                by_day[m.day].append((m.start_minute, m.end_minute, m.campus, c.course))
    for day in WEEKDAYS:
        spans = sorted(by_day.get(day, []))
        for (_s1, e1, c1, k1), (s2, _e2, c2, k2) in zip(spans, spans[1:], strict=False):
            if c1 != c2 and s2 >= e1:
                issues.append(ScheduleIssue(
                    severity=Severity.WARNING, code="CAMPUS_CHANGE_BETWEEN_CLASSES",
                    message=(f"{day}: {k1} ({c1}) ends {s2 - e1} min before {k2} ({c2}). "
                             "CoursePilot does not model travel time."),
                    courses=[k1, k2], details={"day": day, "gap_minutes": s2 - e1,
                                               "from": c1, "to": c2}))
    return issues


def _reasons(choices, score: ScheduleScore, prefs: SchedulePreferences,
             availability: OptionAvailability) -> list[Reason]:
    out = [Reason.NO_TIME_CONFLICTS]
    if score.unknown_time_sections == 0:
        out.append(Reason.ALL_MEETING_TIMES_VERIFIED)
    if prefs.hard_constraints():
        out.append(Reason.ALL_HARD_CONSTRAINTS_SATISFIED)
    if score.needs_confirmation == 0:
        out.append(Reason.ALL_RESTRICTIONS_SATISFIED_OR_ABSENT)
    if availability.all_open_now:
        out.append(Reason.ALL_SECTIONS_OPEN_CURRENT)
    elif availability.state is AvailabilityState.OPEN:
        out.append(Reason.ALL_SECTIONS_OPEN_WHEN_LAST_OBSERVED)
    if any(c.component == "required_companion" for c in choices):
        out.append(Reason.REQUIRED_COMPONENTS_INCLUDED)
    if prefs.preferred_campuses and score.preference_misses == 0:
        out.append(Reason.MATCHES_PREFERRED_CAMPUS)
    return out


def _search(term, prefs, keep: int | None = None):
    """keep=None: existence only (stops at the first schedule)."""
    domains, excluded = _domains(term, prefs)
    grouped = _group(domains)
    by_index = {r.index_number: r for items in grouped.values() for r, _ in items}

    def full_key(solution):
        choices = [by_index[c.index] for c in solution]
        return _key(_score(choices, prefs), choices)

    outcome = search(_as_search(grouped, prefs), buffer=prefs.minimum_minutes_between_classes,
                     node_limit=NODE_LIMIT, solution_limit=1 if keep is None else SOLUTION_LIMIT,
                     keep=keep, full_key=full_key)
    return grouped, excluded, outcome


def schedule(term: TermData, courses: list[str], prefs: SchedulePreferences, max_results: int,
             base_issues: list[ScheduleIssue],
             relationships: list[CourseRelationship] = ()) -> ScheduleResult:
    issues = list(base_issues)
    links = sorted([*relationships, *(CourseRelationship(
        kind=k, course=c, related=r, evidence=e, source=src)
        for k, c, r, e, src in term.relationships)],
        key=lambda x: (x.course, x.kind, x.related))
    meta = dict(term_code=term.term_code, section_dataset=term.dataset,
                availability_freshness=term.availability_freshness,
                availability_observed_at=term.availability_observed_at,
                availability_source=term.availability_source,
                availability_refresh_id=term.availability_refresh_id,
                availability_mode=prefs.availability,
                availability_current_window_seconds=int(CURRENT_WINDOW.total_seconds()),
                ranking_objective=RANKING_OBJECTIVE)

    def result(status, options=(), stats=None):
        return ScheduleResult(
            term_code=term.term_code, status=status, requested_courses=courses,
            preferences=prefs, options=list(options), relationships=links,
            issues=sorted(issues, key=lambda i: (i.severity.value, i.code, i.courses, i.indexes)),
            metadata=ScheduleMetadata(**meta, search=stats or SearchStats(
                node_limit=NODE_LIMIT, solution_limit=SOLUTION_LIMIT)))

    if not term.published:
        issues.append(ScheduleIssue(
            severity=Severity.BLOCKER, code="TERM_SCHEDULE_NOT_PUBLISHED",
            message=(f"CoursePilot has no Rutgers section data for {term.term_code}. Sections "
                     "from earlier terms are never used to build a schedule.")))
        return result(ScheduleStatus.TERM_SCHEDULE_NOT_PUBLISHED)

    if term.availability_freshness is not AvailabilityFreshness.CURRENT:
        required = prefs.availability is AvailabilityMode.REQUIRE_OPEN
        issues.append(ScheduleIssue(
            severity=Severity.BLOCKER if required else Severity.WARNING,
            code="AVAILABILITY_NOT_CURRENT",
            message=(f"CoursePilot's latest open/closed information for {term.term_code} is "
                     + (f"from {term.availability_observed_at} ({term.availability_source})"
                        if term.availability_observed_at else "missing")
                     + ", not current. "
                     + ("require_open cannot be met on it; no section was scheduled as open."
                        if required else
                        "States are shown with that time and do not reorder the options.")),
            details={"freshness": term.availability_freshness.value
                     if term.availability_freshness else None,
                     "observed_at": term.availability_observed_at,
                     "source": term.availability_source}))

    for key in term.not_offered:
        partial = term.coverage != "complete"
        issues.append(ScheduleIssue(
            severity=Severity.NEEDS_CONFIRMATION if partial else Severity.BLOCKER,
            code="COURSE_NOT_IN_PARTIAL_TERM_DATA" if partial else "COURSE_NOT_OFFERED",
            message=(f"{key} has no sections in CoursePilot's {term.term_code} data"
                     + (" (the term's data is partial)." if partial else ".")),
            courses=[key]))
    for key, sups in sorted(term.unverified_components.items()):
        issues.append(ScheduleIssue(
            severity=Severity.NEEDS_CONFIRMATION, code="LINKED_COMPONENT_UNVERIFIED",
            message=(f"{key} has other registration records ({', '.join(sups)}) whose "
                     "relationship to it Rutgers does not state; they were not scheduled."),
            courses=[key], details={"supplement_codes": sups}))
    for a, b in term.cross_listed_requests:
        issues.append(ScheduleIssue(
            severity=Severity.WARNING, code="REQUESTED_COURSES_CROSS_LISTED",
            message=(f"{a} and {b} are cross-listed: the same class under two codes. They are "
                     "never scheduled as the same class twice."),
            courses=[a, b]))
    if term.not_offered:
        return result(ScheduleStatus.NO_VALID_SCHEDULE)

    grouped, excluded, outcome = _search(term, prefs, keep=max_results)
    sizes = {s: len(term.candidates[s]) for s in sorted(term.candidates)}
    patterns = {s: len(grouped[s]) for s in sorted(grouped)}
    stats = SearchStats(nodes=outcome.nodes, pruned=outcome.pruned,
                        bound_pruned=outcome.bound_pruned,
                        solutions_considered=outcome.complete_seen, node_limit=NODE_LIMIT,
                        solution_limit=SOLUTION_LIMIT, limit_reached=outcome.limit_reached,
                        candidates_per_course=sizes, distinct_patterns_per_course=patterns,
                        cartesian_product=_product(sizes.values()),
                        pattern_product=_product(patterns.values()))
    if outcome.limit_reached:
        issues.append(ScheduleIssue(
            severity=Severity.WARNING, code="SEARCH_LIMIT_REACHED",
            message=("The search stopped at its node limit; options are ranked among the "
                     f"{outcome.complete_seen} schedules reached, not every possible one."),
            details={"nodes": outcome.nodes, "schedules_reached": outcome.complete_seen}))

    by_index = {rep_.index_number: (rep_, eq) for items in grouped.values() for rep_, eq in items}
    ranked = []
    for solution in outcome.solutions:           # already the top K, in key order
        choices = sorted((by_index[c.index][0] for c in solution), key=lambda c: c.slot)
        score = _score(choices, prefs)
        ranked.append((_key(score, choices), choices, score))
    ranked.sort(key=lambda r: r[0])
    options = []
    for rank, (_k, choices, score) in enumerate(ranked[:max_results], start=1):
        credits = [c.credits for c in choices]
        availability = _option_availability(choices)
        options.append(ScheduleOption(
            rank=rank, choices=[_choice(c, by_index[c.index_number][1]) for c in choices],
            total_credits=(sum((Decimal(x) for x in credits), Decimal(0))
                           if all(x is not None for x in credits) else None),
            score=score,
            reasons=_reasons(choices, score, prefs, availability),
            issues=_option_issues(choices, prefs),
            days=sorted({m.day for c in choices for m in c.meetings
                         if m.kind is MeetingKind.TIMED}, key=WEEKDAYS.index),
            availability=availability))
    if options:
        return result(ScheduleStatus.OPTIONS_FOUND, options, stats)
    if outcome.limit_reached:
        return result(ScheduleStatus.SEARCH_LIMIT_REACHED, stats=stats)

    issues.extend(_diagnose(term, prefs, grouped, excluded))
    return result(ScheduleStatus.NO_VALID_SCHEDULE, stats=stats)


def _product(values) -> int:
    out = 1
    for n in values:
        out *= max(n, 1)
    return out


def _diagnose(term: TermData, prefs: SchedulePreferences, domains, excluded) -> list:
    out = []
    empty = [s for s in sorted(domains) if not domains[s]]
    for slot in empty:
        course = slot.split("#")[0]
        if not term.candidates[slot]:
            out.append(ScheduleIssue(severity=Severity.BLOCKER, code="NO_SECTIONS",
                                     message=f"{slot} has no sections in this term.",
                                     courses=[course]))
            continue
        why = excluded[slot]
        details = {"excluded_by": {k: len(v) for k, v in sorted(why.items())}}
        if set(why) == {"restriction_not_satisfied"}:
            code = "SECTION_RESTRICTION_NOT_SATISFIED"
            message = f"Every section of {slot} is excluded ({', '.join(sorted(why))})."
        elif all(k.startswith("availability_") for k in why):
            # require_open: name the bundle component that has no open section.
            component = "required_companion" if "#" in slot else "primary"
            code = "NO_OPEN_SECTION"
            message = (f"No section of {slot}"
                       + (" (a required registration component)" if "#" in slot else "")
                       + " is open as of a current observation ("
                       + ", ".join(f"{k.removeprefix('availability_')}: {len(v)}"
                                   for k, v in sorted(why.items())) + ").")
            details["component"] = component
        else:
            code = "USER_CONSTRAINTS_EXCLUDE_ALL_SECTIONS"
            message = f"Every section of {slot} is excluded ({', '.join(sorted(why))})."
        out.append(ScheduleIssue(severity=Severity.BLOCKER, code=code, message=message,
                                 courses=[course], details=details))
    if not empty:
        pairs = incompatible_pairs(_as_search(domains, prefs),
                                   prefs.minimum_minutes_between_classes)
        for a, b in pairs:
            out.append(ScheduleIssue(
                severity=Severity.BLOCKER, code="ALL_SECTIONS_CONFLICT",
                message=f"Every available section of {a} conflicts with every one of {b}.",
                courses=sorted({a.split('#')[0], b.split('#')[0]}),
                details={"slots": [a, b]}))
        if not pairs:
            out.append(ScheduleIssue(
                severity=Severity.BLOCKER, code="NO_COMPATIBLE_COMBINATION",
                message=("Each pair of requested courses fits together, but no combination of "
                         "all of them does."),
                courses=sorted({s.split('#')[0] for s in domains})))
    # Which of the student's own hard constraints are responsible (never relaxed).
    names = prefs.hard_constraints()
    if names:
        _d, _e, loose = _search(term, SchedulePreferences(
            preferred_campuses=prefs.preferred_campuses))
        if loose.solutions:
            blocking = [n for n in names if _search(term, prefs.without(n))[2].solutions]
            out.append(ScheduleIssue(
                severity=Severity.BLOCKER, code="USER_CONSTRAINTS_TOO_STRICT",
                message=("Schedules exist without your hard constraints; they were NOT relaxed. "
                         + (f"Removing {', '.join(blocking)} alone would allow one."
                            if blocking else "No single constraint is responsible.")),
                details={"constraints": names, "individually_blocking": blocking}))
    return out


__all__ = ["NODE_LIMIT", "RANKING_OBJECTIVE", "SOLUTION_LIMIT", "schedule"]
