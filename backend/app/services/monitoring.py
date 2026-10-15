"""Section watches and notification events (Phase 6.8).

Two boundaries, never mixed:

* the WATCH API (user requests) - create / list / deactivate watches, list
  events. Never fetches Rutgers, never processes transitions.
* the EVENT PROCESSOR (`process_openings`, system) - turns committed
  closed -> open change rows into notification events. Run after each
  availability refresh, and again by itself to recover from any crash.

## Exactly one logical event per (watch, opening)

    1. pending = openings with no availability_change_dispatch row
                 (ORDER BY observed_at, id; PostgreSQL: FOR UPDATE SKIP LOCKED)
    2. INSERT notification_event ... ON CONFLICT (watch_id, change_id) DO NOTHING
    3. INSERT availability_change_dispatch ... ON CONFLICT (change_id) DO NOTHING
    4. COMMIT  (events and marker together)

A crash before 4 leaves the opening pending: the next run redoes it. A
second worker, a rerun or a replay hits the unique keys and inserts nothing.
The database, not an "if not exists" check, is the final authority. This is
exactly-once LOGICAL event creation; a later delivery step will be
at-least-once and use the event id as its idempotency key.

## Which watches match an opening

    watch.section_id = change.section_id   (term-scoped identity - never the bare index)
    AND watch.term_code = change.term_code
    AND watch is active
    AND watch.created_at <= change.observed_at   (no event for an opening observed
                                                  before the watch existed)

One set-based statement per batch - never one query per watcher.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.availability import AvailabilityStatus, ResolvedAvailability, bundle_status
from app.domain.monitoring import (
    DISPATCH_BATCH,
    MAX_ACTIVE_WATCHES_PER_USER,
    DeactivationReason,
    EventKind,
)
from app.models import Course, CourseOffering, CourseSection, DataSource
from app.models.availability import SectionAvailabilityChange
from app.models.monitoring import AvailabilityChangeDispatch, NotificationEvent, SectionWatch
from app.services.availability import SectionRef, lookup
from app.services.live import create_inbox_items, end_activities_for_watches
from app.services.scheduling.candidates import StudentAttributes, load_term


class WatchError(ValueError):
    """A watch request that cannot be honoured. `code` is client-safe."""

    code = "WATCH_ERROR"


class TargetNotFound(WatchError):
    code = "SECTION_NOT_FOUND"


class TargetRemoved(WatchError):
    code = "SECTION_REMOVED"


class TooManyWatches(WatchError):
    code = "TOO_MANY_ACTIVE_WATCHES"


@dataclass(frozen=True)
class Target:
    """What a client renders about a watched section (natural keys only)."""

    term_code: str
    index_number: str
    course_string: str | None
    supplement_code: str | None
    section_number: str | None
    removed: bool = False


# --------------------------------------------------------------------------
# targets
# --------------------------------------------------------------------------


def _sections(session: Session, section_ids):
    """section_id -> (CourseSection, course_string, supplement_code), one query."""
    ids = sorted({i for i in section_ids if i is not None}, key=str)
    if not ids:
        return {}
    rows = session.execute(
        select(CourseSection, Course.course_string, Course.supplement_code)
        .join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
        .join(Course, Course.id == CourseOffering.course_id)
        .where(CourseSection.id.in_(ids))).all()
    return {s.id: (s, cs, sup) for s, cs, sup in rows}


def removed_section_ids(session: Session, term_code: str, section_ids) -> set[uuid.UUID]:
    """Sections the term's newest COMPLETE schedule download no longer contains.

    Section ingestion never deletes a section that disappears from Rutgers'
    schedule; it just stops re-stamping its source. So: if the newest
    download any section of the term came from is a complete one, a section
    stamped with an older download was not in it. If the newest download is
    not complete (a --limit / --subject run), removal cannot be proven and
    nothing is called removed.
    """
    ids = [i for i in section_ids if i is not None]
    if not ids:
        return set()
    newest = session.execute(
        select(DataSource.id, DataSource.coverage)
        .where(DataSource.id.in_(select(CourseSection.source_id)
                                 .where(CourseSection.term_code == term_code).distinct()))
        .order_by(DataSource.retrieved_at.desc(), DataSource.id).limit(1)).first()
    if newest is None or newest.coverage != "complete":
        return set()
    return {sid for sid, src in session.execute(
        select(CourseSection.id, CourseSection.source_id).where(CourseSection.id.in_(ids)))
        if src != newest.id}


def _target(watch_or_event, sections, removed=frozenset()) -> Target:
    hit = sections.get(watch_or_event.section_id)
    if hit is None:
        return Target(watch_or_event.term_code, watch_or_event.index_number, None, None, None,
                      removed=True)
    section, course_string, supplement = hit
    return Target(section.term_code, section.index_number, course_string,
                  supplement.strip(), section.section_number,
                  removed=section.id in removed)


# --------------------------------------------------------------------------
# watch API (user requests)
# --------------------------------------------------------------------------


def _active_watch(session: Session, account_id, term_code: str, index_number: str):
    return session.scalar(select(SectionWatch).where(
        SectionWatch.user_account_id == account_id, SectionWatch.term_code == term_code,
        SectionWatch.index_number == index_number, SectionWatch.deactivated_at.is_(None)))


def create_watch(session: Session, account_id, *, term_code: str, index_number: str,
                 now: datetime) -> tuple[SectionWatch, bool]:
    """(watch, created). Idempotent: an identical active watch is returned,
    never duplicated - by the partial unique index, not only by this lookup.
    Any availability state may be watched (an open section is armed for its
    NEXT closed -> open); the caller reports the current state beside it."""
    section = session.scalar(select(CourseSection).where(
        CourseSection.term_code == term_code, CourseSection.index_number == index_number))
    if section is None:
        raise TargetNotFound(f"No section {index_number} in CoursePilot's {term_code} data.")
    if section.id in removed_section_ids(session, term_code, [section.id]):
        raise TargetRemoved(f"Section {index_number} is no longer in Rutgers' {term_code} "
                            "schedule; it can never open.")
    existing = _active_watch(session, account_id, term_code, index_number)
    if existing is not None:
        return existing, False
    active = session.scalar(select(func.count()).select_from(SectionWatch).where(
        SectionWatch.user_account_id == account_id, SectionWatch.deactivated_at.is_(None)))
    if active >= MAX_ACTIVE_WATCHES_PER_USER:
        raise TooManyWatches(f"At most {MAX_ACTIVE_WATCHES_PER_USER} active watches.")
    watch = SectionWatch(id=uuid.uuid4(), user_account_id=account_id, section_id=section.id,
                         term_code=term_code, index_number=index_number, created_at=now,
                         updated_at=now)
    try:
        with session.begin_nested():
            session.add(watch)
    except IntegrityError:
        # A concurrent identical request won the unique index: return its watch.
        existing = _active_watch(session, account_id, term_code, index_number)
        if existing is None:
            raise
        return existing, False
    return watch, True


def deactivate_watch(session: Session, account_id, watch_id, *, now: datetime,
                     reason: DeactivationReason = DeactivationReason.USER_CANCELLED):
    """The owner's watch, deactivated (idempotent), or None when the watch does
    not exist OR belongs to someone else - indistinguishable on purpose."""
    watch = session.scalar(select(SectionWatch).where(
        SectionWatch.id == watch_id, SectionWatch.user_account_id == account_id))
    if watch is not None and watch.deactivated_at is None:
        watch.deactivated_at, watch.deactivation_reason = now, reason.value
        end_activities_for_watches(session, [watch.id], now=now)       # Phase 6.9
        session.flush()
    return watch


def list_watches(session: Session, account_id, *, include_inactive: bool = False):
    query = select(SectionWatch).where(SectionWatch.user_account_id == account_id)
    if not include_inactive:
        query = query.where(SectionWatch.deactivated_at.is_(None))
    return session.scalars(query.order_by(SectionWatch.created_at.desc(),
                                          SectionWatch.term_code, SectionWatch.index_number,
                                          SectionWatch.id)).all()


def describe_watches(session: Session, watches, reference_time: datetime):
    """watch.id -> (Target, ResolvedAvailability | None) in a fixed number of
    queries: one for the sections, a 2-3 query availability lookup per term."""
    sections = _sections(session, (w.section_id for w in watches))
    out = {}
    by_term: dict[str, list] = defaultdict(list)
    for w in watches:
        if w.section_id in sections:
            by_term[w.term_code].append(sections[w.section_id][0])
    resolved: dict[tuple[str, str], ResolvedAvailability] = {}
    removed: set = set()
    for term, secs in sorted(by_term.items()):
        found, _ = lookup(session, term, [SectionRef(s.id, s.index_number, s.open_status,
                                                     s.source_id) for s in secs],
                          reference_time)
        resolved.update({(term, i): r for i, r in found.items()})
        removed |= removed_section_ids(session, term, [s.id for s in secs])
    for w in watches:
        out[w.id] = (_target(w, sections, removed), resolved.get((w.term_code, w.index_number)))
    return out


def list_events(session: Session, account_id, *, limit: int):
    return session.scalars(
        select(NotificationEvent).where(NotificationEvent.user_account_id == account_id)
        .order_by(NotificationEvent.observed_at.desc(), NotificationEvent.term_code,
                  NotificationEvent.index_number, NotificationEvent.created_at,
                  NotificationEvent.id)
        .limit(limit)).all()


# --------------------------------------------------------------------------
# event processor (system)
# --------------------------------------------------------------------------


@dataclass
class ProcessStats:
    openings: int = 0                   # closed -> open changes dispatched this run
    matches: int = 0                    # (watch, opening) pairs found
    events_created: int = 0             # rows actually inserted (0 on a rerun)
    batches: int = 0
    by_term: dict[str, int] = field(default_factory=dict)


def _insert_ignoring_conflicts(session: Session, table, rows: list[dict], keys: list[str],
                               returning=None):
    """INSERT ... ON CONFLICT (keys) DO NOTHING on PostgreSQL and SQLite.
    The count of rows really inserted - or, with `returning`, those rows."""
    if not rows:
        return [] if returning is not None else 0
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    # RETURNING counts what was really inserted (psycopg reports rowcount -1
    # for a multi-row INSERT ... ON CONFLICT).
    result = session.execute(insert(table).values(rows).on_conflict_do_nothing(
        index_elements=keys).returning(*(returning or table.primary_key.columns)))
    return result.all() if returning is not None else len(result.all())


def _bundle_context(session: Session, term_code: str, course: str, section_id, now: datetime):
    """(state, open, total, as_of) of the OTHER required registration record(s)
    of the course around `section_id` - None when the course has none.

    Uses the Schedule Engine's own component detection (Phase 6.6.1) and
    availability (6.7), so "lecture opened, every LB lab closed" carries
    state CLOSED, 0 of 8 - never a course that looks registrable."""
    term = load_term(session, term_code, [course], StudentAttributes(), now)
    slots = term.slots_of.get(course, [])
    mine = next((s for s in slots if any(c.section_id == section_id
                                         for c in term.candidates[s])), None)
    others = [s for s in slots if s != mine]
    if mine is None or not others:
        return None, None, None, None
    states, opened, total = [], 0, 0
    for slot in others:
        cands = term.candidates[slot]
        n_open = sum(c.availability.state is AvailabilityStatus.OPEN for c in cands)
        n_closed = sum(c.availability.state is AvailabilityStatus.CLOSED for c in cands)
        opened, total = opened + n_open, total + len(cands)
        states.append(AvailabilityStatus.OPEN if n_open else AvailabilityStatus.CLOSED
                      if n_closed == len(cands) else AvailabilityStatus.UNKNOWN)
    as_of = term.availability_observed_at
    return (bundle_status(states).value, opened, total,
            datetime.fromisoformat(as_of) if as_of else None)


def _pending(session: Session, batch: int):
    change = SectionAvailabilityChange
    query = (select(change)
             .outerjoin(AvailabilityChangeDispatch,
                        AvailabilityChangeDispatch.change_id == change.id)
             .where(change.from_status == "closed", change.to_status == "open",
                    AvailabilityChangeDispatch.change_id.is_(None))
             .order_by(change.observed_at, change.term_code, change.index_number, change.id)
             .limit(batch))
    if session.get_bind().dialect.name == "postgresql":
        # Two workers take different batches; the unique keys stay the guarantee.
        query = query.with_for_update(skip_locked=True, of=change)
    return session.scalars(query).all()


def process_openings(session: Session, *, now: datetime, batch: int = DISPATCH_BATCH,
                     commit: bool = True) -> ProcessStats:
    """Turn every pending closed -> open change into events, batch by batch.
    Safe to run any number of times, by any number of workers."""
    stats = ProcessStats()
    handled: set = set()
    while True:
        openings = _pending(session, batch)
        if not openings:
            break
        if {c.id for c in openings} <= handled:
            # Progress markers are not being recorded; never spin forever.
            raise RuntimeError("openings stay pending after processing: dispatch markers "
                               "were not recorded")
        handled |= {c.id for c in openings}
        stats.batches += 1
        stats.openings += len(openings)
        ids = [c.id for c in openings]
        watch, change = SectionWatch, SectionAvailabilityChange
        pairs = session.execute(
            select(watch.id, watch.user_account_id, change.id)
            .join(change, and_(change.section_id == watch.section_id,
                               change.term_code == watch.term_code))
            .where(change.id.in_(ids), watch.deactivated_at.is_(None),
                   watch.created_at <= change.observed_at)
            .order_by(change.observed_at, change.term_code, change.index_number,
                      watch.created_at, watch.id)).all()
        by_change = {c.id: c for c in openings}
        sections = _sections(session, {by_change[cid].section_id for _, _, cid in pairs})
        context = {}
        for cid in sorted({cid for _, _, cid in pairs}, key=lambda i: (
                by_change[i].observed_at, by_change[i].term_code, by_change[i].index_number)):
            c = by_change[cid]
            _section, course_string, _sup = sections[c.section_id]
            context[cid] = _bundle_context(session, c.term_code, course_string,
                                           c.section_id, now)
        rows = []
        for watch_id, account_id, cid in pairs:
            c = by_change[cid]
            section, course_string, supplement = sections[c.section_id]
            state, n_open, total, as_of = context[cid]
            rows.append({
                "id": uuid.uuid4(), "user_account_id": account_id, "watch_id": watch_id,
                "change_id": cid, "refresh_id": c.refresh_id,
                "kind": EventKind.SECTION_OPENED.value, "section_id": c.section_id,
                "term_code": c.term_code, "index_number": c.index_number,
                "course_string": course_string, "supplement_code": supplement.strip(),
                "section_number": section.section_number, "from_status": c.from_status,
                "to_status": c.to_status, "observed_at": c.observed_at,
                "required_component_state": state, "required_components_open": n_open,
                "required_components_total": total, "context_as_of": as_of,
                "created_at": now, "updated_at": now})
        created = _insert_ignoring_conflicts(
            session, NotificationEvent.__table__, rows, ["watch_id", "change_id"],
            returning=[NotificationEvent.__table__.c.id,
                       NotificationEvent.__table__.c.user_account_id])
        inserted = len(created)
        # Phase 6.9: the inbox item for each NEW event, in the same transaction.
        create_inbox_items(session, [(e, a) for e, a in created], now)
        per_change = defaultdict(int)
        for _w, _a, cid in pairs:
            per_change[cid] += 1
        _insert_ignoring_conflicts(
            session, AvailabilityChangeDispatch.__table__,
            [{"change_id": cid, "events_created": per_change[cid], "created_at": now,
              "updated_at": now} for cid in ids], ["change_id"])
        stats.matches += len(pairs)
        stats.events_created += inserted
        for c in openings:
            stats.by_term[c.term_code] = stats.by_term.get(c.term_code, 0) + 1
        if commit:
            session.commit()
        else:
            session.flush()
    return stats


def deactivate_removed_targets(session: Session, *, now: datetime) -> int:
    """Active watches whose section is gone (deleted, or not in the term's
    newest complete download) become inactive with reason target_removed -
    kept as history, never retargeted to another section or index."""
    active = session.scalars(select(SectionWatch).where(
        SectionWatch.deactivated_at.is_(None))
        .order_by(SectionWatch.term_code, SectionWatch.index_number, SectionWatch.id)).all()
    by_term: dict[str, list] = defaultdict(list)
    for w in active:
        by_term[w.term_code].append(w)
    count = 0
    for term, watches in sorted(by_term.items()):
        removed = removed_section_ids(session, term, [w.section_id for w in watches])
        for w in watches:
            if w.section_id is None or w.section_id in removed:
                w.deactivated_at = now
                w.deactivation_reason = DeactivationReason.TARGET_REMOVED.value
                end_activities_for_watches(session, [w.id], now=now)   # Phase 6.9
                count += 1
    session.flush()
    return count


def expire_term(session: Session, term_code: str, *, now: datetime) -> int:
    """End monitoring for a term (an operator decision - Rutgers publishes no
    term end for monitoring). Watches stay as history; an index Rutgers
    reuses next term is a different section, so they can never match it."""
    watches = session.scalars(select(SectionWatch).where(
        SectionWatch.term_code == term_code, SectionWatch.deactivated_at.is_(None))).all()
    for w in watches:
        w.deactivated_at, w.deactivation_reason = now, DeactivationReason.TERM_ENDED.value
    end_activities_for_watches(session, [w.id for w in watches], now=now)  # Phase 6.9
    session.flush()
    return len(watches)


def pending_openings(session: Session) -> int:
    change = SectionAvailabilityChange
    return session.scalar(
        select(func.count()).select_from(change)
        .outerjoin(AvailabilityChangeDispatch, AvailabilityChangeDispatch.change_id == change.id)
        .where(change.from_status == "closed", change.to_status == "open",
               AvailabilityChangeDispatch.change_id.is_(None))) or 0


__all__ = ["ProcessStats", "Target", "TargetNotFound", "TargetRemoved", "TooManyWatches",
           "WatchError", "create_watch", "deactivate_removed_targets", "deactivate_watch",
           "describe_watches", "expire_term", "list_events", "list_watches",
           "pending_openings", "process_openings", "removed_section_ids"]

