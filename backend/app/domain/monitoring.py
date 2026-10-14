"""Section monitoring (Phase 6.8) - pure rules, no I/O.

```
Rutgers --ONE request per term per refresh--> availability refresh (6.7)
                                                   |
                                      section_availability_change (system, append-only)
                                                   |  closed -> open only
                                      event processor (set-based, idempotent)
                                                   |  x active watches of THAT section
                                      notification_event (one per watch + change)
```

The number of watches never changes how often Rutgers is asked: a watch
is a row CoursePilot joins against the changes the single global refresh
already recorded. Delivery (in-app, push, email) is a later phase that will
read events; nothing here sends anything.

## What a watch is

One user, one term-scoped section (`course_section.id`, with the term and
index kept beside it - Rutgers reuses indexes across terms, so the index
alone never identifies a target). Not a course ("any usable section"
mixes eligibility, restrictions and fit - three axes Phase 6.7 keeps apart)
and not a bundle (SOC links no lecture index to a lab index). Those are
later, higher-level watch types built on this one.

## Which transitions are an opening

Only `closed -> open`, observed by an ACCEPTED refresh:

    closed  -> open     EVENT    "index I was observed open at T, closed before"
    NULL    -> open     none     first observation of the section (the first
                                 refresh of a term records ~8,500 of these)
    unknown -> open     none     CoursePilot did not know it was closed
    open    -> closed   none     (the watch stays active for the next opening)
    anything else       none

An event states what was OBSERVED and when ("opened at T"); it never says
"open now" (by the time anyone reads it, it may have closed again) and never
"you can register" (holds, time tickets and special permission are not
availability). For a section with a required registration component (an
LB lab), the event also carries the component's state at processing time,
so "lecture opened, every lab still closed" is never presented as a course
that can be registered.
"""

from __future__ import annotations

from enum import StrEnum

#: CoursePilot settings, not Rutgers policy.
MAX_ACTIVE_WATCHES_PER_USER = 50          # abuse bound on one account's active watches
MAX_EVENTS_PAGE = 100
DISPATCH_BATCH = 500                      # qualifying changes handled per transaction


class EventKind(StrEnum):
    SECTION_OPENED = "section_opened"


class DeactivationReason(StrEnum):
    USER_CANCELLED = "user_cancelled"
    TERM_ENDED = "term_ended"             # an operator closed the term's monitoring
    TARGET_REMOVED = "target_removed"     # the section left Rutgers' schedule


class WatchStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


def is_opening(from_status: str | None, to_status: str) -> bool:
    """The ONLY transition that creates an event."""
    return from_status == "closed" and to_status == "open"


__all__ = ["DISPATCH_BATCH", "MAX_ACTIVE_WATCHES_PER_USER", "MAX_EVENTS_PAGE",
           "DeactivationReason", "EventKind", "WatchStatus", "is_opening"]
