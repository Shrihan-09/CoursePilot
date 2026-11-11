"""The school's degree credit total (spread task): a requirement of every plan.

A plan is complete only when cumulative degree credits - completed and in
progress on the record, plus planned courses, plus planned free-elective
credits - reach the total. The total is a published school rule, cited here
with the exact wording, retrieval date and archive hash (the archive is
local: data/raw/ is not committed).

SAS, 2026-2027 catalog, "Requirements (SAS)", tab "Credits and Residency":

    "All students must earn at least 120 credits, with a minimum cumulative
    grade-point average of 2.000."

Only (school, catalog year) pairs whose rule CoursePilot has read are listed;
any other program has no modelled total and its plans say so - a total is
never assumed.

FREE ELECTIVES. Once a term's requirement courses are placed, the credits it
still needs toward the total are free-elective credits: any degree-credit
course the student chooses. CoursePilot plans the CREDITS, never a course,
and they carry no workload weight.

How they are spread over the plan's terms:

  * every term that holds courses is topped up to its credit floor;
  * unless pace = Early, the rest is spread over the terms up to the
    graduation target: an empty term at its floor while the total still needs
    a whole floor, then the lowest terms raised one credit at a time (never
    above a maximum). A plan that needs fewer terms ends sooner rather than
    leave a last term below its floor;
  * pace = Early fills each term toward its maximum, in order.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DegreeTotalSource:
    school_code: str
    catalog_year: str
    credits: Decimal
    title: str
    url: str
    quote: str
    retrieved_at: str
    archive: str
    archive_sha256: str


SAS_2026_2027 = DegreeTotalSource(
    school_code="SAS", catalog_year="2026-2027", credits=Decimal(120),
    title="Requirements (SAS) - Credits and Residency, 2026-2027 Rutgers catalog",
    url="https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/pages/xv72Yj9vsrh3FCdUV1kI",
    quote=("All students must earn at least 120 credits, with a minimum cumulative "
           "grade-point average of 2.000."),
    retrieved_at="2026-11-03",
    archive="data/raw/catalog/catalog_sas-requirements_2026_2027.html",
    archive_sha256="981a914cebc86bce8dc723aa349acbf6f8a6b30852c2ae525ab10d5c94da3a81")

TOTALS: dict[tuple[str, str], DegreeTotalSource] = {
    (SAS_2026_2027.school_code, SAS_2026_2027.catalog_year): SAS_2026_2027,
}

#: The requirement code the plan's projections use for the total.
REQUIREMENT_CODE = "DEGREE_CREDITS"


def degree_total(school_code: str | None, catalog_year: str | None) -> DegreeTotalSource | None:
    return TOTALS.get((school_code or "", catalog_year or ""))


def degree_total_for(session, version) -> DegreeTotalSource | None:
    """The sourced degree total that governs `version` (Prompt 08 B1).

    In order: the program's own total (School of Engineering: "120 to 132
    credits ... depending on the major"), then a composed school component's
    total (RBS, SEBS: each school's "Credits and Residency" page), then the
    registry above. None when no source states one - a total is never assumed.
    """
    from app.services.academic_context import school_requirement_sets

    school = version.program.school if version.program else None
    code = school.code if school else ""
    if version.degree_credits_min is not None:
        return _from_record(code, version.catalog_year, version.degree_credits_min,
                            version.degree_credits_source, version.source_url)
    for component in school_requirement_sets(session, version):
        if component.degree_credits_min is not None:
            return _from_record(code, version.catalog_year, component.degree_credits_min,
                                component.degree_credits_source, component.source_url)
    return degree_total(code, version.catalog_year)


def _from_record(school_code, catalog_year, credits, source, url) -> DegreeTotalSource:
    source = source or {}
    return DegreeTotalSource(
        school_code=school_code, catalog_year=catalog_year, credits=Decimal(credits),
        title=source.get("title", "Degree credit total"), url=source.get("url") or url or "",
        quote=source.get("quote", ""), retrieved_at=source.get("retrieved_at", ""),
        archive=source.get("archive", ""), archive_sha256=source.get("archive_sha256", ""))


def spread_electives(terms: list[tuple[Decimal, Decimal, Decimal]], need: Decimal, *,
                     early: bool) -> list[Decimal]:
    """Free-elective credits per term.

    `terms` is (course credits, floor, cap) per term in order; `need` the
    credits still short of the total after every planned course. Returns
    the elective credits per term (an unused empty term gets 0). The sum
    falls short of `need` only when every term is at its cap."""
    out = [Decimal(0)] * len(terms)
    totals = [c for c, _, _ in terms]
    # Every term that holds courses reaches its floor.
    for i, (courses, floor, cap) in enumerate(terms):
        if courses > 0 and courses < floor:
            out[i] = min(floor, cap) - courses
            totals[i] += out[i]
    need -= sum(out, Decimal(0))
    if need <= 0:
        return out
    if early:
        for i, (_, _, cap) in enumerate(terms):
            add = min(max(Decimal(0), cap - totals[i]), need)
            out[i] += add
            totals[i] += add
            need -= add
            if need <= 0:
                break
        return out
    # Empty terms at their floor, in order, while the total still needs a
    # whole floor; the rest raises the terms in use evenly toward their caps.
    # A further empty term only when every term in use is at its cap.
    used = [i for i, (c, _, _) in enumerate(terms) if c > 0]
    empty = [i for i, (c, _, _) in enumerate(terms) if c == 0]
    while empty and min(terms[empty[0]][1], terms[empty[0]][2]) <= need:
        i = empty.pop(0)
        out[i] = totals[i] = min(terms[i][1], terms[i][2])
        need -= out[i]
        used.append(i)
    need = _level_up(terms, used, out, totals, need)
    while need > 0 and empty:
        i = empty.pop(0)
        out[i] = totals[i] = min(terms[i][2], need)
        need -= out[i]
        used.append(i)
    return out


def _level_up(terms, used, out, totals, need: Decimal) -> Decimal:
    """Raise the lowest of `used` one credit at a time, never past a cap."""
    while need > 0:
        room = [i for i in sorted(used) if totals[i] < terms[i][2]]
        if not room:
            break
        i = min(room, key=lambda j: (totals[j], j))
        add = min(Decimal(1), terms[i][2] - totals[i], need)
        out[i] += add
        totals[i] += add
        need -= add
    return need

__all__ = ["REQUIREMENT_CODE", "SAS_2026_2027", "TOTALS", "DegreeTotalSource", "degree_total",
           "degree_total_for",
           "spread_electives"]
