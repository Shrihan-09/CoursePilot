"""The course knowledge index: build, freshness, and query (Prompt 08 B1).

    build(session)             one immutable build from the current inputs
    ensure_fresh(session)      reuse the newest READY build whose input
                               manifest still matches; otherwise rebuild
    KnowledgeView(...)         per program version and term: what each course
                               could fill, with labels - read by Search,
                               Options, the semester builder and the assistant

WHAT A MEMBERSHIP IS. "This course, offered in this term, can fill this
requirement node of this owner" - structural eligibility only. It never says
the student needs it, may enroll, meets a grade, is admitted, or can double
count. Sources:

  * a node with a curated list or query: its reconciled
    `requirement_course_option` rows (the SAME rows the Degree Engine audits
    with), for each loaded term in which the course is offered;
  * a node certified per term (`certification_codes`, e.g. SOEHS, CCD): the
    term's own SOC `offering_core_code` rows - never a union across years.

COURSE STATUS for one program context and term (`classify`):

    mapped               at least one membership
    excluded             a composed course-exclusion rule names it
    free_elective_only   ONLY when the context's coverage is complete
    unknown              everything else - a missing row never means
                         "free elective only" (unreviewed components, open
                         conflicts, unknown course lists, no certifications)

Freshness: the manifest hashes every input (definitions, lifecycle states,
eligibility rows, offerings, certifications, builder version). Identical
inputs give the same hash; any change makes the build stale. Loaders call
`mark_dirty`-free: staleness is DERIVED from the inputs, so no in-memory
callback can be missed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.models import (
    Course,
    CourseOffering,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    SchoolRequirementSetVersion,
)
from app.models.core_codes import OfferingCoreCode
from app.models.knowledge import (
    KnowledgeBuild,
    KnowledgeContext,
    KnowledgeCoverage,
    KnowledgeMembership,
)

BUILDER_VERSION = "knowledge/1"
#: Ready builds kept (the newest is served; one older for comparison).
KEEP_READY = 2
_BATCH = 5000


# --------------------------------------------------------------------------
# keys and the input manifest
# --------------------------------------------------------------------------


def program_key(version: ProgramVersion) -> str:
    p = version.program
    school = p.school.code if p.school else "?"
    parts = [school, p.code, p.degree_type] + ([p.variant] if p.variant else []) + (
        [version.curriculum_key] if version.curriculum_key else [])
    return "-".join(x.lower() for x in parts)


def _digest(rows) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(json.dumps(row, default=str, separators=(",", ":")).encode())
        h.update(b"\n")
    return h.hexdigest()


def manifest(session: Session) -> dict:
    """Every input the build reads, hashed. Same inputs -> same manifest."""
    versions = sorted(
        (str(v.id), v.definition_sha256 or "", v.lifecycle_state, v.admission_stage or "",
         v.curriculum_key)
        for v in session.scalars(select(ProgramVersion)))
    components = sorted(
        (str(c.id), c.code, c.definition_sha256, c.lifecycle_state, c.kind, c.composition)
        for c in session.scalars(select(SchoolRequirementSetVersion)))
    nodes = sorted(
        (str(r.id), r.code, r.name, str(r.parent_id), r.requirement_system,
         json.dumps(r.certification_codes), json.dumps(r.unresolved_conflicts),
         r.available_after_stage or "", r.min_grade or "")
        for r in session.scalars(select(Requirement)))
    eligibility = sorted(
        (str(a), str(b), c) for a, b, c in session.execute(select(
            RequirementCourseOption.requirement_id, RequirementCourseOption.course_id,
            RequirementCourseOption.category)))
    offerings = sorted((t, str(c)) for t, c in session.execute(
        select(CourseOffering.term_code, CourseOffering.course_id).distinct()))
    certifications = sorted((t, str(c), k) for t, c, k in session.execute(
        select(OfferingCoreCode.term_code, OfferingCoreCode.course_id,
               OfferingCoreCode.code).distinct()))
    from app.models import ProgramRule, ProgramVersionComponent
    rules = sorted((str(r.id), r.rule_type, r.excluded_course_strings or "")
                   for r in session.scalars(select(ProgramRule)))
    links = sorted((str(a), str(b)) for a, b in session.execute(select(
        ProgramVersionComponent.program_version_id, ProgramVersionComponent.component_id)))
    parts = {
        "builder_version": BUILDER_VERSION,
        "program_versions": _digest(versions),
        "components": _digest(components),
        "links": _digest(links),
        "requirements": _digest(nodes),
        "eligibility": _digest(eligibility),
        "offerings": _digest(offerings),
        "certifications": _digest(certifications),
        "rules": _digest(rules),
    }
    counts = {"program_versions": len(versions), "components": len(components),
              "requirements": len(nodes), "eligibility_rows": len(eligibility),
              "offerings": len(offerings), "certifications": len(certifications)}
    sha = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
    return {"sha256": sha, "parts": parts, "counts": counts}


def current_build(session: Session) -> KnowledgeBuild | None:
    return session.scalar(select(KnowledgeBuild).where(KnowledgeBuild.state == "ready")
                          .order_by(KnowledgeBuild.completed_at.desc()).limit(1))


def is_fresh(session: Session, build: KnowledgeBuild | None, m: dict | None = None) -> bool:
    if build is None:
        return False
    return build.manifest_sha256 == (m or manifest(session))["sha256"]


def ensure_fresh(session: Session) -> KnowledgeBuild:
    m = manifest(session)
    build = current_build(session)
    if build is not None and build.manifest_sha256 == m["sha256"]:
        return build
    return build_index(session, m)


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------


def _owners(session: Session):
    """(kind, id, key, lifecycle, nodes) for every program version and component."""
    out = []
    for v in session.scalars(select(ProgramVersion)):
        nodes = list(session.scalars(select(Requirement).where(
            Requirement.program_version_id == v.id)))
        out.append(("program_version", v.id, program_key(v), v.lifecycle_state, nodes, v))
    for c in session.scalars(select(SchoolRequirementSetVersion)):
        nodes = list(session.scalars(select(Requirement).where(
            Requirement.school_requirement_set_version_id == c.id)))
        out.append(("component", c.id, c.code, c.lifecycle_state, nodes, c))
    return out


def _path_labels(nodes, owner_obj):
    """Per node: inherited conflicts, stage, strictest minimum grade."""
    from app.domain.grades import LETTERS
    from app.services.academic_context import node_stages

    by_id = {n.id: n for n in nodes}
    version = owner_obj if isinstance(owner_obj, ProgramVersion) else None
    stages = node_stages(nodes, version)
    out = {}
    for n in nodes:
        conflicts, grades, cur, seen = set(), [], n, set()
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            conflicts |= set(cur.unresolved_conflicts or ())
            if cur.min_grade:
                grades.append(cur.min_grade)
            cur = by_id.get(cur.parent_id)
        grade = min(grades, key=LETTERS.index) if grades else None
        out[n.id] = (sorted(conflicts) or None, stages.get(n.code), grade)
    return out


def _cert_codes(node, rco_categories: dict) -> list[str] | None:
    """A node's term-scoped certification codes. SAS Core nodes loaded before
    B1 carry none; their eligibility is Core-managed (no stored rule) and its
    rows' categories ARE the goal codes, so those are used."""
    if node.certification_codes:
        return list(node.certification_codes)
    if node.eligibility_rule is None and rco_categories.get(node.id):
        return sorted(rco_categories[node.id])
    return None


def build_index(session: Session, m: dict | None = None) -> KnowledgeBuild:
    m = m or manifest(session)
    build = KnowledgeBuild(id=uuid.uuid4(), manifest_sha256=m["sha256"], manifest=m,
                           builder_version=BUILDER_VERSION, state="building",
                           started_at=datetime.now(UTC))
    session.add(build)
    session.flush()
    try:
        counts = _populate(session, build)
    except Exception as exc:
        session.rollback()
        failed = KnowledgeBuild(manifest_sha256=m["sha256"], manifest=m,
                                builder_version=BUILDER_VERSION, state="failed",
                                started_at=build.started_at, completed_at=datetime.now(UTC),
                                error=f"{type(exc).__name__}: {exc}"[:2000])
        session.add(failed)
        session.flush()
        raise
    build.state = "ready"
    build.completed_at = datetime.now(UTC)
    build.counts = counts
    session.flush()
    _prune(session, keep=build.id)
    return build


def _prune(session: Session, keep) -> None:
    ready = list(session.scalars(select(KnowledgeBuild.id).where(KnowledgeBuild.state == "ready")
                                 .order_by(KnowledgeBuild.completed_at.desc())))
    stale = [b for b in ready[KEEP_READY:] if b != keep]
    stale += list(session.scalars(select(KnowledgeBuild.id).where(
        KnowledgeBuild.state == "failed").order_by(KnowledgeBuild.started_at.desc()).offset(3)))
    if stale:
        session.execute(delete(KnowledgeBuild).where(KnowledgeBuild.id.in_(stale)))
        session.flush()


def _populate(session: Session, build: KnowledgeBuild) -> dict:
    offered: dict[str, set] = defaultdict(set)              # term -> course ids
    for term, course_id in session.execute(
            select(CourseOffering.term_code, CourseOffering.course_id).distinct()):
        offered[term].add(course_id)
    course_string = dict(session.execute(select(Course.id, Course.course_string)).all())
    certified: dict[tuple[str, str], set] = defaultdict(set)  # (term, code.casefold) -> ids
    for term, course_id, code in session.execute(select(
            OfferingCoreCode.term_code, OfferingCoreCode.course_id,
            OfferingCoreCode.code).distinct()):
        certified[(term, code.casefold())].add(course_id)
    certified_terms = {t for t, _ in certified}
    rco: dict = defaultdict(list)                           # requirement id -> (course, category)
    rco_categories: dict = defaultdict(set)
    for rid, cid, cat in session.execute(select(
            RequirementCourseOption.requirement_id, RequirementCourseOption.course_id,
            RequirementCourseOption.category)):
        rco[rid].append((cid, cat))
        if cat:
            rco_categories[rid].add(cat)

    owners = _owners(session)
    owner_meta = {}
    rows, total = [], 0
    for kind, owner_id, key, lifecycle, nodes, obj in owners:
        labels = _path_labels(nodes, obj)
        uses_certs, unknown_lists = False, []
        for n in nodes:
            conflicts, stage, grade = labels[n.id]
            base = dict(build_id=build.id, owner_kind=kind, owner_id=owner_id, owner_key=key,
                        requirement_id=n.id, requirement_code=n.code, requirement_name=n.name,
                        requirement_system=n.requirement_system, source_id=n.source_id,
                        lifecycle_state=lifecycle, conflicts=conflicts,
                        available_after_stage=stage, min_grade=grade)
            codes = _cert_codes(n, rco_categories)
            if codes:
                uses_certs = True
                for term in sorted(offered):
                    for code in codes:
                        for cid in certified.get((term, code.casefold()), ()):
                            rows.append(dict(base, id=uuid.uuid4(), term_code=term, course_id=cid,
                                             course_string=course_string.get(cid, "?"),
                                             category=code, basis="term_certification",
                                             certified_term=term))
            else:
                query = (n.eligibility_rule or {}).get("query")
                named = set((n.eligibility_rule or {}).get("courses", ())) | {
                    c for v in (n.eligibility_rule or {}).get("course_categories", {}).values()
                    for c in v}
                pairs = rco.get(n.id, [])
                if n.requirement_type in ("course", "choose_n") and not pairs \
                        and not (n.eligibility_rule or {}):
                    unknown_lists.append(n.code)
                for cid, cat in pairs:
                    cs = course_string.get(cid, "?")
                    basis = "curated_query" if query and cs not in named else "curated_list"
                    for term, ids in offered.items():
                        if cid in ids:
                            rows.append(dict(base, id=uuid.uuid4(), term_code=term, course_id=cid,
                                             course_string=cs, category=cat or "", basis=basis,
                                             certified_term=None))
            if len(rows) >= _BATCH:
                session.execute(insert(KnowledgeMembership), _dedupe(rows))
                total += len(rows)
                rows = []
        owner_meta[owner_id] = {
            "kind": kind, "key": key, "lifecycle": lifecycle, "uses_certs": uses_certs,
            "unknown_lists": unknown_lists,
            "conflicts": sorted({c for n in nodes for c in (n.unresolved_conflicts or ())}),
        }
    if rows:
        session.execute(insert(KnowledgeMembership), _dedupe(rows))
        total += len(rows)
    session.flush()

    contexts = coverage_rows = 0
    from app.services.academic_context import composed_rules, school_requirement_sets

    for version in session.scalars(select(ProgramVersion)):
        pkey = program_key(version)
        members = [("program_version", version.id, pkey, "own")]
        school_ids = {s.id for s in session.scalars(select(SchoolRequirementSetVersion).where(
            SchoolRequirementSetVersion.school_id == version.program.school_id,
            SchoolRequirementSetVersion.catalog_year == version.catalog_year,
            SchoolRequirementSetVersion.composition == "school"))}
        for comp in school_requirement_sets(session, version):
            members.append(("component", comp.id, comp.code,
                            "school_gen_ed" if comp.id in school_ids else "linked"))
        for kind, oid, okey, via in members:
            session.add(KnowledgeContext(build_id=build.id, program_version_id=version.id,
                                         program_key=pkey, owner_kind=kind, owner_id=oid,
                                         owner_key=okey, via=via))
            contexts += 1
        excluded = set()
        for rule in composed_rules(session, version):
            if rule.rule_type == "course_exclusion" and rule.is_evaluable:
                excluded |= set(rule.excluded_courses)
        excluded_ids = {cid for cid, cs in course_string.items() if cs in excluded}
        owner_ids = [oid for _, oid, _, _ in members]
        reasons_static = _incomplete_reasons(version, [owner_meta[o] for o in owner_ids
                                                       if o in owner_meta], session)
        mapped_by_term = defaultdict(set)
        for term, cid in session.execute(
                select(KnowledgeMembership.term_code, KnowledgeMembership.course_id).where(
                    KnowledgeMembership.build_id == build.id,
                    KnowledgeMembership.owner_id.in_(owner_ids)).distinct()):
            mapped_by_term[term].add(cid)
        uses_certs = any(owner_meta.get(o, {}).get("uses_certs") for o in owner_ids)
        for term, ids in sorted(offered.items()):
            reasons = list(reasons_static)
            if uses_certs and term not in certified_terms:
                reasons.append(f"No SOC certifications are loaded for {term}.")
            session.add(KnowledgeCoverage(
                build_id=build.id, program_version_id=version.id, program_key=pkey,
                term_code=term, offered_courses=len(ids),
                mapped_courses=len(mapped_by_term.get(term, set()) & ids),
                excluded_courses=len(excluded_ids & ids), complete=not reasons,
                incomplete_reasons=reasons))
            coverage_rows += 1
    session.flush()
    return {"memberships": total, "contexts": contexts, "coverage_rows": coverage_rows,
            "terms": len(offered), "owners": len(owners)}


def _dedupe(rows):
    seen, out = set(), []
    for r in rows:
        k = (r["term_code"], r["course_id"], r["requirement_id"], r["category"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _incomplete_reasons(version: ProgramVersion, owners: list[dict], session) -> list[str]:
    """Why "no membership" cannot yet mean "free elective only" for this context."""
    from app.services.planning.degree_total import degree_total_for

    reasons = []
    for o in owners:
        if o["lifecycle"] != "published":
            reasons.append(f"{o['key']} is not published (state: {o['lifecycle']}).")
        if o["unknown_lists"]:
            reasons.append(f"{o['key']}: course lists unknown for "
                           f"{', '.join(o['unknown_lists'])}.")
        if o["conflicts"]:
            reasons.append(f"{o['key']}: open source conflicts {', '.join(o['conflicts'])}.")
    if not any(o["kind"] == "component" for o in owners):
        reasons.append("No school requirements (gen-ed) are composed with this program.")
    if degree_total_for(session, version) is None:
        reasons.append("No sourced degree credit total: free-elective credit cannot be bounded.")
    return reasons


# --------------------------------------------------------------------------
# query
# --------------------------------------------------------------------------


@dataclass
class Fill:
    requirement_code: str
    requirement_name: str
    requirement_system: str
    owner_key: str
    category: str
    basis: str
    certified_term: str | None
    lifecycle_state: str
    conflicts: list[str] = field(default_factory=list)
    after_stage: str | None = None
    min_grade: str | None = None

    @property
    def pending_review(self) -> bool:
        return self.lifecycle_state != "published"


@dataclass
class CourseKnowledge:
    course: str
    term_code: str
    status: str                     # mapped | excluded | free_elective_only | unknown
    fills: list[Fill]
    reasons: list[str]              # why unknown / why not complete
    build_id: str | None
    stale: bool

    def as_dict(self) -> dict:
        return {"course": self.course, "term_code": self.term_code, "status": self.status,
                "fills": [f.__dict__ | {"pending_review": f.pending_review} for f in self.fills],
                "reasons": self.reasons, "build_id": self.build_id, "stale": self.stale}


class KnowledgeView:
    """What courses could fill for ONE program version, in ONE term.

    Reads the newest ready build. If it is stale (inputs changed since), the
    answers are still given but marked `stale`, and absence ("free elective
    only") is never claimed from a stale build."""

    def __init__(self, session: Session, version: ProgramVersion, term_code: str, *,
                 build: KnowledgeBuild | None = None, check_fresh: bool = True) -> None:
        self.session, self.version, self.term = session, version, term_code
        self.build = build or current_build(session)
        self.stale = self.build is None or (check_fresh and not is_fresh(session, self.build))
        self._fills: dict[str, list[Fill]] = defaultdict(list)
        self._coverage = None
        self._excluded: set[str] = set()
        from app.services.academic_context import composed_rules

        for rule in composed_rules(session, version):
            if rule.rule_type == "course_exclusion" and rule.is_evaluable:
                self._excluded |= set(rule.excluded_courses)
        if self.build is None:
            return
        owners = list(session.scalars(select(KnowledgeContext.owner_id).where(
            KnowledgeContext.build_id == self.build.id,
            KnowledgeContext.program_version_id == version.id)))
        self._coverage = session.scalar(select(KnowledgeCoverage).where(
            KnowledgeCoverage.build_id == self.build.id,
            KnowledgeCoverage.program_version_id == version.id,
            KnowledgeCoverage.term_code == term_code))
        if not owners:
            return
        for m in session.scalars(select(KnowledgeMembership).where(
                KnowledgeMembership.build_id == self.build.id,
                KnowledgeMembership.term_code == term_code,
                KnowledgeMembership.owner_id.in_(owners))):
            self._fills[m.course_string].append(Fill(
                requirement_code=m.requirement_code, requirement_name=m.requirement_name,
                requirement_system=m.requirement_system, owner_key=m.owner_key,
                category=m.category, basis=m.basis, certified_term=m.certified_term,
                lifecycle_state=m.lifecycle_state, conflicts=list(m.conflicts or []),
                after_stage=m.available_after_stage, min_grade=m.min_grade))

    def offered(self, course: str) -> bool:
        return self.session.scalar(
            select(func.count()).select_from(CourseOffering).join(
                Course, Course.id == CourseOffering.course_id).where(
                Course.course_string == course, CourseOffering.term_code == self.term)) > 0

    def judge(self, course: str) -> CourseKnowledge:
        fills = sorted(self._fills.get(course, []), key=lambda f: (f.requirement_code, f.category))
        bid = str(self.build.id) if self.build else None
        if self.build is None:
            return CourseKnowledge(course, self.term, "unknown", [], [
                "The course knowledge index has not been built yet."], None, True)
        if fills:
            return CourseKnowledge(course, self.term, "mapped", fills, [], bid, self.stale)
        if course in self._excluded:
            return CourseKnowledge(course, self.term, "excluded", [], [
                "A program rule says this course earns no credit for this program."], bid,
                self.stale)
        cov = self._coverage
        if cov is None:
            return CourseKnowledge(course, self.term, "unknown", [], [
                f"The index has no coverage for this program in {self.term}."], bid, self.stale)
        if cov.complete and not self.stale:
            return CourseKnowledge(course, self.term, "free_elective_only", [], [], bid, False)
        reasons = list(cov.incomplete_reasons) + (
            ["The index is older than the current requirement data."] if self.stale else [])
        return CourseKnowledge(course, self.term, "unknown", [], reasons, bid, self.stale)

    def judge_many(self, courses) -> dict[str, CourseKnowledge]:
        return {c: self.judge(c) for c in courses}


def coverage_report(session: Session, build: KnowledgeBuild | None = None) -> list[dict]:
    build = build or current_build(session)
    if build is None:
        return []
    out = []
    for cov in session.scalars(select(KnowledgeCoverage).where(
            KnowledgeCoverage.build_id == build.id).order_by(
            KnowledgeCoverage.program_key, KnowledgeCoverage.term_code)):
        out.append({"program": cov.program_key, "term": cov.term_code,
                    "offered": cov.offered_courses, "mapped": cov.mapped_courses,
                    "excluded": cov.excluded_courses, "complete": cov.complete,
                    "reasons": cov.incomplete_reasons})
    return out


__all__ = ["BUILDER_VERSION", "CourseKnowledge", "Fill", "KnowledgeView", "build_index",
           "coverage_report", "current_build", "ensure_fresh", "is_fresh", "manifest",
           "program_key"]
