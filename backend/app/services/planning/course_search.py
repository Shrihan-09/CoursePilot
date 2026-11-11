"""Requirement-aware course search: what students mean, not course codes (Prompt 11 A3c).

    "a different AHo course in Spring 2027, online"
    "another CS elective that's not 14:332"
    "something about AI that counts for my major"
    "a writing course that also fills diversity"
    "Fridays off: what Core courses fit?"

One search, used by the plan's search box, the Options sheet and (Part B) the
assistant's `search_courses` tool.

## Words -> requirements, deterministically

`RequirementResolver` is a lookup built from the program's own encodings: the
SAS Core goal codes on the requirement pools (AHo, HST, WCd...), every
requirement's name, and a short curated alias list ("humanities" -> the AH
goals, "writing" -> WC/WCr/WCd, "CS elective" -> CS_ELECTIVES). A phrase with
two different meanings ("science": Core Natural Sciences or the B.S. science
sequence?) is never guessed - it comes back as "Did you mean". Words that
match no requirement are the TOPIC, searched with the existing BM25 course
index and its curated abbreviations ("AI" -> artificial intelligence;
app.services.search) and labelled as a topic match. There are no embeddings in
this corpus (DATA_MODEL section 22).

## Only valid courses

Every result passes every gate the planner applies for the chosen term
(`PlanningEngine.check_candidates`); a course that fails one is listed under
"Not available this term" with its reason, never mixed in.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.planning import OfferingEvidenceKind
from app.models import Course, CourseOffering, CourseSection
from app.models.sections import SectionMeeting
from app.services.planning.options import OptionsContext, Similarity
from app.services.planning.terms import label

DAY_WORDS = {"monday": "M", "mondays": "M", "tuesday": "T", "tuesdays": "T",
             "wednesday": "W", "wednesdays": "W", "thursday": "H", "thursdays": "H",
             "friday": "F", "fridays": "F", "saturday": "S", "saturdays": "S"}
SEASONS = {"spring": "1", "fall": "9", "summer": "7", "winter": "0"}

#: Curated aliases -> requirement selectors. A selector is a requirement code,
#: "goal:<GOAL>" (a SAS Core goal code on the pools), or a group keyword
#: (@core, @major, @degree). Each entry is a phrase students use; ambiguous
#: phrases map to SEVERAL distinct meanings and are asked back, never guessed.
ALIASES: dict[str, list[list[str]]] = {
    "aho": [["CORE_AH#AHo"]],
    "ahp": [["CORE_AH#AHp"]],
    "ahq": [["CORE_AH#AHq"]],
    "ahr": [["CORE_AH#AHr"]],
    "arts and humanities": [["CORE_AH"]],
    "arts & humanities": [["CORE_AH"]],
    "humanities": [["CORE_AH"]],
    "ah": [["CORE_AH"]],
    "history": [["CORE_HST"]],
    "historical analysis": [["CORE_HST"]],
    "historical": [["CORE_HST"]],
    "social analysis": [["CORE_SCL"]],
    "social science": [["CORE_SCL"]],
    "diversity": [["CORE_CCD"]],
    "diversities": [["CORE_CCD"]],
    "inequality": [["CORE_CCD"]],
    "our common future": [["CORE_CCO"]],
    "common future": [["CORE_CCO"]],
    "writing": [["CORE_COLLEGE_WRITING", "CORE_WCR", "CORE_WCD"]],
    "revision-based writing": [["CORE_WCR"]],
    "revision writing": [["CORE_WCR"]],
    "discipline-based writing": [["CORE_WCD"]],
    "college writing": [["CORE_COLLEGE_WRITING"]],
    "natural science": [["CORE_NS"]],
    "natural sciences": [["CORE_NS"]],
    "lab science": [["CORE_NS"]],
    "quantitative": [["CORE_QQ"]],
    "formal reasoning": [["CORE_QR"]],
    "logic": [["CORE_QR"]],
    "science": [["CORE_NS"], ["@science_sequence"]],
    "math": [["CORE_QR", "CORE_QQ"], ["CS_MATH"]],
    "cs elective": [["CS_ELECTIVES"]],
    "cs electives": [["CS_ELECTIVES"]],
    "computer science elective": [["CS_ELECTIVES"]],
    "computer science electives": [["CS_ELECTIVES"]],
    "major elective": [["CS_ELECTIVES"]],
    "science sequence": [["@science_sequence"]],
    "core": [["@core"]],
    "core course": [["@core"]],
    "core courses": [["@core"]],
    "major": [["@major"]],
    "my major": [["@major"]],
    "my degree": [["@degree"]],
    "degree": [["@degree"]],
}
_FILLER_WORDS = {
    "a", "an", "the", "another", "different", "other", "some", "something", "course",
    "courses", "class", "classes", "that", "which", "what", "counts", "count", "for",
    "toward", "towards", "my", "also", "fills", "fill", "fit", "fits", "and", "in", "to",
    "i", "can", "take", "want", "need", "with", "is", "are", "of", "or", "any", "anything",
    "about", "on", "related", "off", "no", "not", "only", "please", "show", "me", "find",
    "one", "it", "this", "next", "semester", "term", "be", "would", "like",
}


@dataclass
class Interpretation:
    term_code: str
    requirements: list[list[str]] = field(default_factory=list)   # groups, ANDed
    requirement_names: list[str] = field(default_factory=list)
    topic: str | None = None
    online: bool = False
    async_only: bool = False
    in_person: bool = False
    campus: str | None = None
    earliest: str | None = None
    latest: str | None = None
    avoid_days: list[str] = field(default_factory=list)
    level: int | None = None
    credits: Decimal | None = None
    open_only: bool = False
    fills_two: bool = False
    exclude: list[str] = field(default_factory=list)
    did_you_mean: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class SearchHit:
    course: str
    title: str
    credits: Decimal
    requirement_codes: list[str]
    requirements: list[str]
    offering: str                     # confirmed | historically offered
    delivery: list[str]
    workload_weight: Decimal | None
    contact_hours: Decimal | None
    open_status: str                  # open | closed | unknown | not_published
    why: str
    similar_to: str | None = None
    #: Already in the plan for a LATER term: choosing it moves it here.
    planned_term: str | None = None
    fills_multiple: bool = False
    #: Prompt 08 B1: the course knowledge index's verdict for the term.
    knowledge: dict | None = None


@dataclass
class SearchResult:
    interpretation: Interpretation
    results: list[SearchHit] = field(default_factory=list)
    not_available: list[dict] = field(default_factory=list)


class RequirementResolver:
    """Phrases -> requirement codes, from the program's encodings plus ALIASES."""

    def __init__(self, ctx: OptionsContext) -> None:
        self.ctx = ctx
        e = ctx.engine
        self.goals: dict[str, set[str]] = defaultdict(set)      # goal -> requirement codes
        for (code, _course), cats in e.pool_categories.items():
            for cat in cats:
                if cat:
                    self.goals[cat].add(code)
        self.by_name = {r.name.lower(): code for code, r in e.requirements.items()
                        if code in ctx.open}

    def expand(self, selector: str, *, include_closed: bool = False) -> list[str]:
        """A selector -> the OPEN requirement leaves it means (with
        `include_closed`, also the ones the record already meets)."""
        ctx, e = self.ctx, self.ctx.engine
        universe = ctx.open if not include_closed else sorted(
            c for c in e.pools if e.pools[c] and c not in ctx.inactive)
        if "#" in selector:
            code, _, goal = selector.partition("#")
            return [f"{code}#{goal}"] if code in universe else []
        if selector == "@core":
            return [c for c in universe if e._is_core(c)]
        if selector == "@major":
            return [c for c in universe if e._is_major_node(c)]
        if selector == "@degree":
            return list(universe)
        if selector == "@science_sequence":
            # The program's one-of science requirement (B.S. Physics/Chemistry):
            # leaves under a major any_of node.
            anyof = [code for code, r in e.requirements.items()
                     if r.requirement_type == "any_of" and e._is_major_node(code)]
            return [c for c in universe if any(self._under(c, a) for a in anyof)]
        if selector.startswith("goal:"):
            return sorted(self.goals.get(selector[5:], set()) & set(universe))
        return [c for c in universe if c == selector or self._under(c, selector)]

    def _under(self, code: str, ancestor: str) -> bool:
        e = self.ctx.engine
        node = e.requirements.get(code)
        while node is not None and node.parent_id in e.by_id_code:
            parent = e.by_id_code[node.parent_id]
            if parent == ancestor:
                return True
            node = e.requirements.get(parent)
        return False

    def goal_codes(self) -> dict[str, str]:
        """Lower-case goal code -> its spelling (AHo, WCd, ...)."""
        return {g.lower(): g for g in self.goals}


def _season_term(ctx: OptionsContext, season: str, year: str | None) -> str | None:
    digit = SEASONS[season]
    if year:
        return f"{year}{digit}"
    for t in ctx.plan.terms:
        if t.term_code.endswith(digit):
            return t.term_code
    return None


def interpret(ctx: OptionsContext, text: str, *, term_code: str | None = None) -> Interpretation:
    """Deterministic: the same words always mean the same thing."""
    resolver = RequirementResolver(ctx)
    q = " " + text.lower().replace("’", "'") + " "
    first = ctx.plan.terms[0].term_code if ctx.plan.terms else ""
    out = Interpretation(term_code=term_code or first)

    m = re.search(r"\b(spring|fall|summer|winter)\s*(20\d\d)?\b", q)
    if m and not term_code:
        found = _season_term(ctx, m.group(1), m.group(2))
        if found:
            out.term_code = found
        q = q.replace(m.group(0), " ")
    for code in re.findall(r"\bnot\s+(?:in\s+)?(\d{2}:\d{3}(?::\d{3})?|\d{3}:\d{3})\b", q):
        out.exclude.append(code)
    q = re.sub(r"\b(?:not|except|other than|besides)\s+(?:in\s+)?"
               r"(\d{2}:\d{3}(?::\d{3})?|\d{3}:\d{3})\b", " ", q)
    if re.search(r"\basync(hronous)?\b", q):
        out.async_only = out.online = True
    elif re.search(r"\b(online|remote)\b", q):
        out.online = True
    if re.search(r"\bin[- ]person\b", q):
        out.in_person, out.online, out.async_only = True, False, False
    q = re.sub(r"\b(asynchronous|async|online|remote|in[- ]person)\b", " ", q)
    for phrase, campus in {"college avenue": "CAC", "busch": "BUS", "livingston": "LIV",
                           "cook": "COOK", "douglass": "D/C"}.items():
        if re.search(rf"\b{phrase}\b", q):
            out.campus = campus
            q = re.sub(rf"\b(?:on\s+)?{phrase}(?:\s+campus)?\b", " ", q)
    for word, field_name in (("after", "earliest"), ("before", "latest")):
        m = re.search(rf"\b{word}\s+(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b", q)
        if m:
            hour = int(m[1]) % 12 + (12 if m[3] == "pm" else 0)
            setattr(out, field_name, f"{hour:02}:{int(m[2] or 0):02}")
            q = q.replace(m[0], " ")
    for word, day in DAY_WORDS.items():
        if re.search(rf"\b(no\s+{word}|{word}\s+off|not\s+on\s+{word}|without\s+{word})\b", q):
            out.avoid_days.append(day)
            q = re.sub(rf"\b(no\s+{word}|{word}\s+off|not\s+on\s+{word}|without\s+{word})\b",
                       " ", q)
    out.avoid_days = sorted(set(out.avoid_days))
    m = re.search(r"\b([1-4])00[- ]?level\b", q)
    if m:
        out.level = int(m.group(1))
        q = q.replace(m.group(0), " ")
    m = re.search(r"\b(\d)(?:\.0)?\s*[- ]?credits?\b", q)
    if m:
        out.credits = Decimal(m.group(1))
        q = q.replace(m.group(0), " ")
    if re.search(r"\bopen\s+(sections?|seats?)\b|\bnot\s+full\b", q):
        out.open_only = True
        q = re.sub(r"\bopen\s+(sections?|seats?)\b|\bnot\s+full\b", " ", q)
    if re.search(r"\b(counts?\s+twice|double[- ]count\w*|2\+|two\s+requirements)\b", q):
        out.fills_two = True
        q = re.sub(r"\b(counts?\s+twice|double[- ]count\w*|2\+|two\s+requirements)\b", " ", q)

    # Requirements: goal codes first (case-insensitive, whole word), then the
    # program's requirement names and the aliases, longest phrase first.
    goals = resolver.goal_codes()
    for token in re.findall(r"\b[a-z]{2,3}\b", q):
        if token in goals and token not in ("ah",):
            # "CORE_AH#AHo": the requirement, narrowed to courses certified
            # for that one goal.
            goal = goals[token]
            codes = resolver.expand(f"goal:{goal}")
            if not codes:
                # Met or in progress on the record: still searchable, and said
                # so - such a course would not count toward it.
                codes = resolver.expand(f"goal:{goal}", include_closed=True)
                out.notes.append(f"{goal}: already met or in progress on your record - "
                                 "these would not count toward it.")
            out.requirements.append([f"{c}#{goal}" for c in codes])
            out.requirement_names.append(goal)
            q = re.sub(rf"\b{token}\b", " ", q, count=1)
    phrases = sorted({**{n: [[c]] for n, c in resolver.by_name.items()}, **ALIASES}.items(),
                     key=lambda kv: -len(kv[0]))
    for phrase, meanings in phrases:
        if not re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", q):
            continue
        resolved = [sorted({c for sel in meaning for c in resolver.expand(sel)})
                    for meaning in meanings]
        resolved = [r for r in resolved if r]
        q = re.sub(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", " ", q, count=1)
        if not resolved:
            closed = [sorted({c for sel in meaning
                              for c in resolver.expand(sel, include_closed=True)})
                      for meaning in meanings]
            closed = [r for r in closed if r]
            if len(closed) == 1:
                out.requirements.append(closed[0])
                out.requirement_names.append(phrase)
                out.notes.append(f"'{phrase}': already met or in progress on your record - "
                                 "these would not count toward it.")
            else:
                # Keep a recognized but empty requirement restrictive. Dropping
                # it would turn "writing and diversity" into unrelated options.
                out.requirements.append([])
                out.requirement_names.append(phrase)
                out.notes.append(f"'{phrase}': nothing of yours is still open there.")
        elif len(resolved) > 1:
            out.did_you_mean.append({
                "phrase": phrase,
                "options": [{"requirements": r, "names": [ctx.names.get(c, c) for c in r]}
                            for r in resolved]})
        else:
            out.requirements.append(resolved[0])
            out.requirement_names.append(phrase)
    words = [w for w in re.findall(r"[a-z0-9]+", q) if w not in _FILLER_WORDS and len(w) > 1]
    if words:
        out.topic = " ".join(words)
        if not out.requirements and not out.did_you_mean:
            out.notes.append(f"No requirement matches '{out.topic}', so it is searched as a "
                             "topic in course titles and descriptions.")
    return out


def _sections(session: Session, term: str, keys: list[str], attrs, honors):
    """course -> [(index, open, [meetings])] for a PUBLISHED term."""
    from app.services.scheduling.meetings import normalize
    from app.domain.schedule import RestrictionOutcome
    from app.services.scheduling.candidates import load_term, honors_only

    data = load_term(session, term, keys, attrs)
    availability = {c.index_number: c.availability for candidates in data.candidates.values()
                    for c in candidates}
    eligible = {c.index_number for candidates in data.candidates.values() for c in candidates
                if c.restriction.outcome in (RestrictionOutcome.UNRESTRICTED, RestrictionOutcome.SATISFIED)
                and (honors or not honors_only(c.restriction))}

    rows = session.execute(
        select(Course.course_string, CourseSection.id, CourseSection.index_number,
               CourseSection.open_status)
        .join(CourseOffering, CourseOffering.course_id == Course.id)
        .join(CourseSection, CourseSection.offering_id == CourseOffering.id)
        .where(Course.course_string.in_(keys), CourseSection.term_code == term,
               Course.supplement_code == "")).all()
    meetings = defaultdict(list)
    for m in session.scalars(select(SectionMeeting).where(
            SectionMeeting.section_id.in_([r[1] for r in rows]))) if rows else []:
        meetings[m.section_id].append(normalize(
            day=m.meeting_day, start=m.start_time_military, end=m.end_time_military,
            mode_code=m.meeting_mode_code, mode_desc=m.meeting_mode_desc,
            campus=m.campus_abbrev))
    out = defaultdict(list)
    for key, sid, index, _soc_open in rows:
        if index not in eligible:
            continue
        ev = availability.get(index)
        # Old SOC/openSections snapshots cannot promise a currently open seat.
        status = ev.state.value if ev is not None and ev.freshness.value == "current" else "unknown"
        out[key].append((index, status, meetings.get(sid, [])))
    return out


def _blocked(ctx: OptionsContext, term: str) -> list[tuple[str, int, int]]:
    from app.services.planning.profile import get_profile, profile_input
    from app.services.scheduling.meetings import hhmm

    profile = get_profile(ctx.session, ctx.student)
    if profile is None:
        return []
    answers = profile_input(profile)
    blocks = answers.schedule.blocked_times
    override = next((o for o in answers.term_overrides if o.term_code == term), None)
    if override is not None and override.blocked_times is not None:
        blocks = override.blocked_times
    return [(d, hhmm(b.start), hhmm(b.end)) for b in blocks for d in b.days]


def search_courses(ctx: OptionsContext, text: str = "", *, term_code: str | None = None,
                   requirements: list[list[str]] | None = None, topic: str | None = None,
                   filters: dict | None = None,
                   limit: int = 10) -> SearchResult:
    """Structured fields (requirements, topic) win over the same thing in `text`."""
    it = interpret(ctx, text, term_code=term_code) if text else Interpretation(
        term_code=term_code or (ctx.plan.terms[0].term_code if ctx.plan.terms else ""))
    if requirements:
        it.requirements = [[c for c in group if c.split("#")[0] in ctx.engine.requirements]
                           for group in requirements]
    if topic:
        it.topic = topic
    for key, value in (filters or {}).items():
        if key in {"online", "async_only", "in_person", "campus", "earliest", "latest",
                   "avoid_days", "level", "credits", "open_only", "fills_two"} and value is not None:
            setattr(it, key, value)
    explicit = term_code is not None or bool(
        re.search(r"\b(spring|fall|summer|winter)\b", (text or "").lower()))
    out = _search(ctx, it, limit)
    if not out.results and not explicit and not it.did_you_mean and len(ctx.plan.terms) > 1:
        # Nothing fits the plan's first term (often: prerequisites come
        # later). Try its later terms in order, and say so.
        first = ctx.plan.terms[0]
        for t in ctx.plan.terms[1:]:
            it.term_code = t.term_code
            later = _search(ctx, it, limit)
            if later.results:
                it.notes.append(f"Nothing fits {first.label} yet; showing {t.label}.")
                later.not_available = out.not_available
                return later
        it.term_code = first.term_code
    return out


def _section_fits(meetings, it: Interpretation, blocked) -> bool:
    """All requested filters must hold for the SAME section, with known times."""
    from app.domain.schedule import MeetingKind, TIME_UNKNOWN_KINDS
    from app.services.scheduling.meetings import hhmm, overlaps_blocked, violates_window

    time_filter = bool(it.avoid_days or blocked or it.earliest or it.latest)
    if time_filter and (not meetings or any(m.kind in TIME_UNKNOWN_KINDS for m in meetings)):
        return False
    online = [m.kind is MeetingKind.ASYNCHRONOUS or m.campus == "ONL"
              or "ONLINE" in (m.mode_desc or "").upper() for m in meetings]
    if it.async_only and (not meetings or any(m.kind is not MeetingKind.ASYNCHRONOUS for m in meetings)):
        return False
    if it.online and (not online or not all(online)):
        return False
    if it.in_person and (not meetings or any(online)):
        return False
    if it.campus and not any((m.campus or "").upper() == it.campus.upper() for m in meetings):
        return False
    return not violates_window(meetings, earliest=hhmm(it.earliest) if it.earliest else None,
                               latest=hhmm(it.latest) if it.latest else None,
                               avoid_days=it.avoid_days) and not overlaps_blocked(meetings, blocked)


def _goal_filters_match(ctx, verdict, it: Interpretation) -> bool:
    """A named goal must be certified by the actual offering evidence, not just a pool."""
    for group in it.requirements:
        if group and all("#" in entry for entry in group):
            wanted = {entry.partition("#")[2] for entry in group}
            evidence = ctx.engine._core_evidence(verdict.course, it.term_code, verdict.offering)
            if evidence is None or not wanted.intersection(evidence.codes):
                return False
    return True


def _search(ctx: OptionsContext, it: Interpretation, limit: int) -> SearchResult:

    out = SearchResult(interpretation=it)
    if it.did_you_mean and not it.requirements:
        return out                                  # ask back; never guess
    term = it.term_code
    pool: set[str] | None = None
    cats = ctx.engine.pool_categories
    for group in it.requirements:
        members = set()
        for entry in group:
            code, _, goal = entry.partition("#")
            members |= {k for k in ctx.engine.pools.get(code, ())
                        if not goal or goal in cats.get((code, k), set())}
        pool = members if pool is None else pool & members
    topic_score: dict[str, float] = {}
    if it.topic:
        from app.services.search.index_registry import get_searcher

        searcher, _ = get_searcher(ctx.session)
        for rank, hit in enumerate(searcher.search(it.topic, limit=300)):
            topic_score.setdefault(hit.course_key, -rank)
        pool = set(topic_score) if pool is None else pool & set(topic_score)
    if pool is None:
        pool = {k for c in ctx.open for k in ctx.engine.pools.get(c, ())}
    # Planned in this term or earlier: not a result. Planned LATER: a result
    # ("take it now"), labelled with where it is.
    pool -= {k for k, t in ctx.where.items() if t <= term}
    pool = {k for k in pool if not any(k.startswith(x) or k.endswith(x) for x in it.exclude)}
    index = ctx.engine.offerings
    ranked = sorted(pool, key=lambda k: (-topic_score.get(k, 0) if topic_score else 0,
                                         index.evidence(k, term).kind
                                         is not OfferingEvidenceKind.CONFIRMED_IN_TERM, k))[:240]
    verdicts = ctx.check(term, {k: ctx.fills(k) for k in ranked}, replacing=None)
    passed = [v for v in verdicts.values() if v.ok]
    out.not_available = [{"course": v.course, "reason": v.reason}
                         for v in verdicts.values() if not v.ok][:15]
    sections = _sections(ctx.session, term, [v.course for v in passed], ctx.engine.attributes, ctx.c.honors)
    blocked = _blocked(ctx, term)
    similarity = Similarity(ctx.engine, term, ctx.liked)
    hits = []
    for v in passed:
        info = ctx.engine.course_info[v.course]
        w = v.workload
        delivery = list(w.delivery) if w else []
        codes = list(v.requirements)
        # An OPEN requirement the student asked for must be certified for this
        # term; a met one was only a pool filter (labelled in the notes).
        bases = [{e.split("#")[0] for e in g} for g in it.requirements]
        if not all(b & set(codes) for b in bases if b & set(ctx.open)):
            continue                                  # the term's Core codes did not certify
        if not _goal_filters_match(ctx, v, it):
            continue
        if it.async_only and "async" not in delivery:
            continue
        if it.online and not {"async", "online"} & set(delivery):
            continue
        if it.in_person and "in_person" not in delivery:
            continue
        if it.level and not v.course.split(":")[2].startswith(str(it.level)):
            continue
        if it.credits is not None and info.credits != it.credits:
            continue
        if it.fills_two and ctx.engine._usable(codes) < 2:
            continue
        secs = sections.get(v.course, [])
        status = "not_published"
        if secs:
            fitting = [s for s in secs if _section_fits(s[2], it, blocked)]
            if not fitting:
                continue
            usable = fitting
            status = ("open" if any(s[1] == "open" for s in usable) else
                      "closed" if all(s[1] == "closed" for s in usable) else "unknown")
            if it.open_only and status != "open":
                continue
        elif it.open_only or it.avoid_days or blocked or it.campus or it.earliest or it.latest:
            continue                                   # times unknown: cannot promise a fit
        reasons = []
        if codes:
            reasons.append("fills " + " + ".join(ctx.names.get(c, c) for c in codes))
        for name, b in zip(it.requirement_names, bases, strict=False):
            if not b & set(ctx.open):
                reasons.append(f"{name} (already met)")
        if "async" in delivery:
            reasons.append("async")
        elif "online" in delivery:
            reasons.append("online")
        if it.topic and v.course in topic_score:
            reasons.append(f"matches '{it.topic}'")
        if it.avoid_days:
            reasons.append("no " + "/".join(it.avoid_days) + " meetings")
        _, note = similarity.best(v.course, info.title)
        hits.append(SearchHit(
            course=v.course, title=info.title, credits=info.credits, requirement_codes=codes,
            requirements=[ctx.names.get(c, c) for c in codes],
            offering=("confirmed" if v.offering.kind is OfferingEvidenceKind.CONFIRMED_IN_TERM
                      else "historically offered"),
            delivery=delivery, workload_weight=w.weight if w else None,
            contact_hours=w.contact_hours if w else None, open_status=status,
            why=" · ".join(reasons) or f"offered in {label(term)}", similar_to=note,
            planned_term=ctx.where.get(v.course), fills_multiple=ctx.engine._usable(codes) >= 2,
            knowledge=(ctx.knowledge_fields(v.course, term)
                       if hasattr(ctx, "knowledge_fields") else None)))
    pref = ctx.c.filler_preference.value

    def key(h: SearchHit):
        from app.services.planning.workload import filler_rank
        score, _ = similarity.best(h.course, h.title)
        return (-topic_score.get(h.course, 0) if it.topic and not it.requirements else 0,
                filler_rank(h.delivery, pref), -len(h.requirement_codes), -score,
                h.open_status != "open", h.workload_weight or 0, h.course)
    out.results = sorted(hits, key=key)[:limit]
    return out


__all__ = ["ALIASES", "Interpretation", "RequirementResolver", "SearchHit", "SearchResult",
           "interpret", "search_courses"]
