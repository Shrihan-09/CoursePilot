"""Section selection as a constraint-satisfaction problem (Phase 6.6) - pure.

    variables  one SLOT per requested course (plus one per required companion
               component, e.g. 01:750:193's lab record)
    domains    that slot's candidate sections that passed every UNARY hard
               constraint (time window, avoided days, restriction not failed)
    binary     no two chosen sections may have conflicting meetings (every
               meeting of both, date-aware, with the student's buffer), and a
               section may not be chosen together with its own cross-listing
               (the same class under two course codes)

## Algorithm

Depth-first backtracking with forward checking:

  1. Slots are ordered most-constrained first: (domain size, -timed
     meetings per section, slot key). Fixed for the whole search.
  2. Choosing a section filters every later slot's domain to the sections
     compatible with it; an emptied domain prunes the branch immediately
     (forward checking) - a conflict is never discovered only after a
     complete schedule has been built.
  3. Within a slot, sections are tried in the caller's canonical order.
  4. Pairwise compatibility is memoized per (index, index).

## Ranked search (branch and bound)

With `keep=K` and a `full_key`, only the K smallest complete assignments
are kept. The first four ranking components (needs-confirmation sections,
unknown-time sections, preference misses, class days) can only grow as
sections are added, so each Candidate carries its contribution and a
partial assignment whose prefix is already STRICTLY worse than the K-th
kept key is pruned - no completion of it could enter the top K. The
result is exact for the objective; only the node limit can cut it short,
and that is reported.

Worst case is exponential (the product of domain sizes); forward checking
makes it proportional to the number of PARTIAL assignments that are still
consistent, which is what `SearchStats` measures against the Cartesian
product. Hard limits on explored nodes and collected solutions stop the
search deterministically, and `limit_reached` says so - "no schedule
exists" is never reported when the search was stopped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.schedule import MeetingInterval
from app.services.scheduling.meetings import sections_conflict


@dataclass(frozen=True)
class Candidate:
    """A section, as the search sees it."""

    slot: str                          # "01:198:336" or "01:750:193#companion"
    index: str
    meetings: tuple[MeetingInterval, ...]
    cross_listed: frozenset[str] = frozenset()
    #: Monotone ranking contributions: (needs_confirmation, unknown_time, preference_miss)
    weight: tuple[int, int, int] = (0, 0, 0)
    days: frozenset[str] = frozenset()


@dataclass
class SearchOutcome:
    solutions: list[tuple[Candidate, ...]] = field(default_factory=list)
    slot_order: list[str] = field(default_factory=list)
    nodes: int = 0
    pruned: int = 0
    bound_pruned: int = 0
    complete_seen: int = 0
    limit_reached: bool = False


class Compatibility:
    """Memoized pairwise compatibility (the binary constraints)."""

    def __init__(self, buffer: int = 0) -> None:
        self.buffer = buffer
        self._memo: dict[tuple[str, str], bool] = {}

    def __call__(self, a: Candidate, b: Candidate) -> bool:
        key = (a.index, b.index) if a.index <= b.index else (b.index, a.index)
        hit = self._memo.get(key)
        if hit is None:
            hit = not (b.index in a.cross_listed or a.index in b.cross_listed
                       or a.index == b.index
                       or sections_conflict(list(a.meetings), list(b.meetings), self.buffer))
            self._memo[key] = hit
        return hit


def order_slots(domains: dict[str, list[Candidate]]) -> list[str]:
    def timed(slot):
        cands = domains[slot]
        return sum(len(c.meetings) for c in cands) / max(len(cands), 1)
    return sorted(domains, key=lambda s: (len(domains[s]), -timed(s), s))


def prefix(chosen) -> tuple[int, int, int, int]:
    days: set[str] = set()
    a = b = c = 0
    for x in chosen:
        a, b, c = a + x.weight[0], b + x.weight[1], c + x.weight[2]
        days |= x.days
    return a, b, c, len(days)


def search(domains: dict[str, list[Candidate]], *, buffer: int = 0, node_limit: int = 200_000,
           solution_limit: int = 2_000, keep: int | None = None,
           full_key=None) -> SearchOutcome:
    """Consistent assignments in deterministic order.

    Without `keep`: every one, up to `solution_limit`. With `keep` and
    `full_key(tuple) -> tuple` (whose first four elements are `prefix`):
    the `keep` smallest by that key, found by branch and bound.
    """
    out = SearchOutcome(slot_order=order_slots(domains))
    if any(not domains[s] for s in out.slot_order):
        return out
    compatible = Compatibility(buffer)
    order = out.slot_order
    best: list[tuple[tuple, tuple[Candidate, ...]]] = []

    def dfs(depth: int, chosen: list[Candidate], remaining: dict[str, list[Candidate]]) -> bool:
        if depth == len(order):
            out.complete_seen += 1
            if keep is None:
                out.solutions.append(tuple(chosen))
                return len(out.solutions) < solution_limit
            key = full_key(tuple(chosen))
            if len(best) < keep or key < best[-1][0]:
                best.append((key, tuple(chosen)))
                best.sort(key=lambda kv: kv[0])
                del best[keep:]
            return True
        slot = order[depth]
        for cand in remaining[slot]:
            out.nodes += 1
            if out.nodes > node_limit:
                out.limit_reached = True
                return False
            if (keep is not None and len(best) == keep
                    and prefix([*chosen, cand]) > best[-1][0][:4]):
                out.bound_pruned += 1
                continue
            filtered: dict[str, list[Candidate]] = {}
            dead = False
            for later in order[depth + 1:]:
                fits = [c for c in remaining[later] if compatible(cand, c)]
                if not fits:
                    dead = True
                    break
                filtered[later] = fits
            if dead:
                out.pruned += 1
                continue
            chosen.append(cand)
            go_on = dfs(depth + 1, chosen, filtered)
            chosen.pop()
            if not go_on:
                return False
        return True

    finished = dfs(0, [], {s: domains[s] for s in order})
    if keep is not None:
        out.solutions = [sol for _, sol in best]
    elif not finished and len(out.solutions) >= solution_limit:
        out.limit_reached = True
    return out


def incompatible_pairs(domains: dict[str, list[Candidate]],
                       buffer: int = 0) -> list[tuple[str, str]]:
    """Slot pairs with NO compatible section pair - the smallest explanation
    of an impossible request when one exists."""
    compatible = Compatibility(buffer)
    slots = sorted(domains)
    out = []
    for i, a in enumerate(slots):
        for b in slots[i + 1:]:
            if not any(compatible(x, y) for x in domains[a] for y in domains[b]):
                out.append((a, b))
    return out


__all__ = ["Candidate", "Compatibility", "SearchOutcome", "incompatible_pairs", "order_slots",
           "prefix", "search"]
