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

    1. needs_confirmation       sections whose restriction is UNKNOWN
    2. unknown_time_sections    sections with a TBA / arranged / malformed meeting
    3. preference_misses        sections meeting outside the preferred campuses (soft)
    4. class_days               distinct weekdays with a timed meeting
    5. gap_minutes              idle minutes between consecutive classes, per day
    6. closed_sections_if_live  closed sections - counted ONLY for live availability;
                                an archived snapshot does not reorder options
    7. the requested courses' index numbers (canonical tie-break)

Options are "ranked schedule options" under this objective - never "best".

## Interchangeable sections

Sections of one course with identical meetings (day, time, dates, campus),
restriction evidence, time verification and cross-listing are equivalent
for every structural rule and every ranking signal. The search runs over one
representative per class (the lowest index) and each choice lists the
others in `equivalent_sections` with their own availability and
instructors. This removes duplicate options that differ only by an
interchangeable index, without dropping any distinct schedule.
"""

from __future__ import annotations

from collections import defaultdict

from app.domain.schedule import (
    TIME_UNKNOWN_KINDS,
    WEEKDAYS,
    AvailabilityFreshness,
    AvailabilityState,
    EquivalentSection,
    MeetingKind,
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

RANKING_OBJECTIVE = ["needs_confirmation", "unknown_time_sections", "preference_misses",
                     "class_days", "gap_minutes", "closed_sections_if_live",
                     "index_numbers"]
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
            broken = violates_window(c.meetings, earliest=earliest, latest=latest,
                                     avoid_days=prefs.avoid_days)
            if broken:
                why[broken].append(c.index_number)
                continue
            keep.append(c)
        # Canonical order inside a slot: clear restriction, verified time, index.
        keep.sort(key=lambda c: (c.restriction.outcome is RestrictionOutcome.UNKNOWN,
                                 not c.time_verified, c.index_number))
        domains[slot] = keep
        excluded[slot] = dict(why)
    return domains, excluded


def _signature(c: SectionCandidate, live: bool) -> tuple:
    """Everything a structural rule or the ranking can see. Sections with the
    same signature are interchangeable; only the lowest index is searched."""
    meetings = tuple(sorted((m.kind.value, m.day or "", m.start_minute or -1,
                             m.end_minute or -1, str(m.start_date), str(m.end_date),
                             m.campus or "") for m in c.meetings))
    r = c.restriction
    return (meetings, r.outcome.value, tuple((e.kind, e.code, e.unit_code) for e in r.entries),
            r.eligibility_text, r.special_permission, c.time_verified,
            tuple(c.cross_listed), c.supplement_code,
            c.availability.state.value if live else "")


def _group(domains, live: bool):
    """slot -> [(representative, [equivalent candidates])], canonical order kept."""
    out = {}
    for slot, cands in domains.items():
        groups: dict[tuple, list[SectionCandidate]] = {}
        for c in cands:
            groups.setdefault(_signature(c, live), []).append(c)
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
                    int(not c.time_verified), _misses(c, prefs)),
            days=frozenset(m.day for m in c.meetings if m.kind is MeetingKind.TIMED))
            for c in reps]
    return out


def _score(choices: list[SectionCandidate], prefs: SchedulePreferences,
           live: bool) -> ScheduleScore:
    by_day: dict[str, list[tuple[int, int]]] = defaultdict(list)
    misses = 0
    for c in choices:
        for m in c.meetings:
            if m.kind is MeetingKind.TIMED:
                by_day[m.day].append((m.start_minute, m.end_minute))
        misses += _misses(c, prefs)
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
        preference_misses=misses,
        class_days=len(by_day),
        gap_minutes=gaps,
        closed_sections_if_live=sum(c.availability.state is AvailabilityState.CLOSED
                                    for c in choices) if live else 0)


def _key(score: ScheduleScore, choices: list[SectionCandidate]) -> tuple:
    return (score.needs_confirmation, score.unknown_time_sections, score.preference_misses,
            score.class_days, score.gap_minutes, score.closed_sections_if_live,
            tuple(c.index_number for c in sorted(choices, key=lambda c: c.slot)))


def _choice(c: SectionCandidate, equivalents: list[SectionCandidate]) -> SectionChoice:
    return SectionChoice(
        equivalent_sections=[EquivalentSection(
            index_number=e.index_number, section_number=e.section_number,
            availability=e.availability, instructors=e.instructors) for e in equivalents],
        course=c.course, component=c.component, course_string=c.course_string,
        supplement_code=c.supplement_code, title=c.title, section_number=c.section_number,
        index_number=c.index_number, campus_code=c.campus_code, meetings=c.meetings,
        instructors=c.instructors, availability=c.availability, restriction=c.restriction,
        cross_listed_indexes=c.cross_listed, time_verified=c.time_verified, notes=c.notes)


def _option_issues(choices: list[SectionCandidate], prefs: SchedulePreferences) -> list:
    issues = []
    for c in choices:
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
            if prefs.hard_constraints():
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


def _reasons(choices, score: ScheduleScore, prefs: SchedulePreferences) -> list[Reason]:
    out = [Reason.NO_TIME_CONFLICTS]
    if score.unknown_time_sections == 0:
        out.append(Reason.ALL_MEETING_TIMES_VERIFIED)
    if prefs.hard_constraints():
        out.append(Reason.ALL_HARD_CONSTRAINTS_SATISFIED)
    if score.needs_confirmation == 0:
        out.append(Reason.ALL_RESTRICTIONS_SATISFIED_OR_ABSENT)
    if all(c.availability.state is AvailabilityState.OPEN for c in choices):
        out.append(Reason.ALL_SECTIONS_OPEN_IN_SNAPSHOT)
    if any(c.component == "required_companion" for c in choices):
        out.append(Reason.REQUIRED_COMPONENTS_INCLUDED)
    if prefs.preferred_campuses and score.preference_misses == 0:
        out.append(Reason.MATCHES_PREFERRED_CAMPUS)
    return out


def _search(term, prefs, live: bool = False, keep: int | None = None):
    """keep=None: existence only (stops at the first schedule)."""
    domains, excluded = _domains(term, prefs)
    grouped = _group(domains, live)
    by_index = {r.index_number: r for items in grouped.values() for r, _ in items}

    def full_key(solution):
        choices = [by_index[c.index] for c in solution]
        return _key(_score(choices, prefs, live), choices)

    outcome = search(_as_search(grouped, prefs), buffer=prefs.minimum_minutes_between_classes,
                     node_limit=NODE_LIMIT, solution_limit=1 if keep is None else SOLUTION_LIMIT,
                     keep=keep, full_key=full_key)
    return grouped, excluded, outcome


def schedule(term: TermData, courses: list[str], prefs: SchedulePreferences, max_results: int,
             base_issues: list[ScheduleIssue]) -> ScheduleResult:
    issues = list(base_issues)
    live = False                      # SOC downloads are archived snapshots (see candidates)
    meta = dict(term_code=term.term_code, section_dataset=term.dataset,
                availability_freshness=AvailabilityFreshness.ARCHIVED if term.dataset else None,
                availability_observed_at=term.observed_at, ranking_objective=RANKING_OBJECTIVE)

    def result(status, options=(), stats=None):
        return ScheduleResult(
            term_code=term.term_code, status=status, requested_courses=courses,
            preferences=prefs, options=list(options),
            issues=sorted(issues, key=lambda i: (i.severity.value, i.code, i.courses, i.indexes)),
            metadata=ScheduleMetadata(**meta, search=stats or SearchStats(
                node_limit=NODE_LIMIT, solution_limit=SOLUTION_LIMIT)))

    if not term.published:
        issues.append(ScheduleIssue(
            severity=Severity.BLOCKER, code="TERM_SCHEDULE_NOT_PUBLISHED",
            message=(f"CoursePilot has no Rutgers section data for {term.term_code}. Sections "
                     "from earlier terms are never used to build a schedule.")))
        return result(ScheduleStatus.TERM_SCHEDULE_NOT_PUBLISHED)

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

    grouped, excluded, outcome = _search(term, prefs, live, keep=max_results)
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
        score = _score(choices, prefs, live)
        ranked.append((_key(score, choices), choices, score))
    ranked.sort(key=lambda r: r[0])
    options = []
    for rank, (_k, choices, score) in enumerate(ranked[:max_results], start=1):
        options.append(ScheduleOption(
            rank=rank, choices=[_choice(c, by_index[c.index_number][1]) for c in choices],
            score=score,
            reasons=_reasons(choices, score, prefs), issues=_option_issues(choices, prefs),
            days=sorted({m.day for c in choices for m in c.meetings
                         if m.kind is MeetingKind.TIMED}, key=WEEKDAYS.index),
            closed_sections_in_snapshot=sorted(
                c.index_number for c in choices
                if c.availability.state is AvailabilityState.CLOSED)))
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
        code = ("SECTION_RESTRICTION_NOT_SATISFIED" if set(why) == {"restriction_not_satisfied"}
                else "USER_CONSTRAINTS_EXCLUDE_ALL_SECTIONS")
        out.append(ScheduleIssue(
            severity=Severity.BLOCKER, code=code,
            message=f"Every section of {slot} is excluded ({', '.join(sorted(why))}).",
            courses=[course], details={"excluded_by": {k: len(v) for k, v in sorted(why.items())}}))
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
                message=("Schedules exist without your time constraints; they were NOT relaxed. "
                         + (f"Removing {', '.join(blocking)} alone would allow one."
                            if blocking else "No single constraint is responsible.")),
                details={"constraints": names, "individually_blocking": blocking}))
    return out


__all__ = ["NODE_LIMIT", "RANKING_OBJECTIVE", "SOLUTION_LIMIT", "schedule"]
