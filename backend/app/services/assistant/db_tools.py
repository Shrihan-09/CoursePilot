"""The assistant's tools over the signed-in student's real records (Prompt 11 Part B).

Each method wraps an existing service and reshapes its result into the shared
tool shape (tools.py); no academic decision is made here:

    get_plan / explain_course_placement   the saved plan (services.planning.saved)
    get_progress                          the Degree Engine + the confirmed DN overlay
    list_course_options                   services.planning.options.course_options
    search_courses                        services.planning.course_search.search_courses
    preview_plan_change / apply_confirmed services.planning.edits (preview / apply_preview)
    find_sections                         services.scheduling.service.generate_schedule
    preview_preference / apply_preference services.planning.profile

The object is bound to ONE student, resolved from the credential by the
route. No method takes a student id, so no tool call can reach another
student's plan.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.plan_edits import PlanChange
from app.models import Course, Student
from app.services.assistant.actions import StaleProposal
from app.services.assistant.contracts import term_label
from app.services.assistant.grounding import CORE_CODES

_TAG = re.compile(r"<[^>]+>")


def _offering(evidence) -> str:
    from app.domain.planning import OfferingEvidenceKind

    kind = getattr(evidence, "kind", None)
    if kind is OfferingEvidenceKind.CONFIRMED_IN_TERM:
        return "confirmed"
    if kind is OfferingEvidenceKind.HISTORICAL_SAME_SEASON:
        return "historically offered"
    return str(getattr(kind, "value", kind) or "unknown").replace("_", " ")


def _reason_text(value: str) -> str:
    return value.replace("_", " ")


class DbAssistantTools:
    def __init__(self, session: Session, student: Student) -> None:
        self.session = session
        self.student = student
        self._ctx = None

    # -- shared ---------------------------------------------------------
    def _context(self):
        if self._ctx is None:
            from app.domain.planning import PlanningConstraints
            from app.services.planning.edits import NoSavedPlan, _row
            from app.services.planning.options import OptionsContext
            from app.services.planning.saved import _decode

            try:
                row = _row(self.session, self.student)
            except NoSavedPlan:
                raise LookupError("you have no saved plan yet - build one on the Plan "
                                  "page") from None
            self._ctx = OptionsContext(self.session, self.student, _decode(row.result_blob),
                                       PlanningConstraints.model_validate(row.constraints))
            self._revision = row.revision
        return self._ctx

    def _descriptions(self, keys: list[str]) -> dict[str, str]:
        """Rutgers catalog descriptions (catalog_course_entry), newest catalog first."""
        from app.models.requirements import CatalogCourseEntry

        if not keys:
            return {}
        out: dict[str, str] = {}
        rows = self.session.execute(
            select(Course.course_string, CatalogCourseEntry.description,
                   CatalogCourseEntry.catalog_year)
            .join(CatalogCourseEntry, CatalogCourseEntry.course_id == Course.id)
            .where(Course.course_string.in_(keys), CatalogCourseEntry.description.is_not(None))
            .order_by(CatalogCourseEntry.catalog_year.desc())).all()
        for key, desc, _year in rows:
            if desc and desc.strip():
                out.setdefault(key, _TAG.sub(" ", desc).strip())
        return out

    def _with_descriptions(self, items: list[dict]) -> list[dict]:
        desc = self._descriptions([i["course"] for i in items])
        for i in items:
            i["description"] = desc.get(i["course"])
        return items

    def plan_revision(self) -> int | None:
        from app.services.planning.edits import NoSavedPlan, _row

        try:
            return _row(self.session, self.student).revision
        except NoSavedPlan:
            return None

    def _support(self) -> str:
        from app.models.requirements import ProgramVersion

        v = self.session.get(ProgramVersion, self.student.program_version_id)
        if v is not None and v.lifecycle_state == "published" and \
                v.publication_basis == "human_review":
            return "Beta supported"
        return "Preview — not yet reviewed"

    # -- read tools -----------------------------------------------------
    def get_profile_summary(self) -> dict:
        from app.services.planning.profile import get_profile, profile_input
        from app.services.student_context import build_student_context

        context = build_student_context(self.session, self.student)
        profile = get_profile(self.session, self.student)
        prefs: dict = {}
        if profile is not None:
            answers = profile_input(profile)
            s = answers.schedule
            preferred = s.modality.preferred.value if s.modality.preferred else None
            prefs = {"delivery": {"asynchronous": "async", "synchronous_online": "online",
                                  "hybrid": "hybrid", "in_person": "in_person"}.get(
                                      preferred or "", None),
                     "avoid_days": list(s.avoid_days), "earliest": s.earliest_start,
                     "latest": s.latest_end,
                     "blocked_times": [b.model_dump() for b in s.blocked_times],
                     "max_credits_per_term": answers.max_credits_per_term,
                     "pace": answers.pace.value,
                     "workload_level": answers.workload_level.value
                     if answers.workload_level else None}
        support = self._support()
        try:
            next_term = self._context().plan.terms[0].term_code
        except (LookupError, IndexError):
            next_term = None
        return {"program": {"name": context.program.program_name, "support": support},
                "catalog_year": context.program.catalog_year,
                "declared": bool(getattr(self.student, "declared", False)),
                "preferences": prefs, "next_term": next_term, "labels": [support]}

    def get_progress(self, *, include_gpa: bool) -> dict:
        from app.domain.audit import RequirementStatus
        from app.services.audit.cached_audit import audit_with_cache
        from app.services.degree_audit.overlay import active_overlay, apply_overlay

        result, _ = audit_with_cache(self.session, self.student)
        overlay = active_overlay(self.session, self.student)
        result = apply_overlay(result.model_copy(deep=True), overlay)
        planned: set[str] = set()
        try:
            planned = {r.requirement_code for r in self._context().plan.requirements
                       if r.status_after in ("satisfied", "provisionally_satisfied")
                       and r.status_before not in ("satisfied", "provisionally_satisfied")}
        except LookupError:
            pass
        reqs: list[dict] = []

        def leaf(node) -> None:
            if node.children:
                for c in node.children:
                    leaf(c)
                return
            dn = overlay.statuses.get(node.requirement_code) if overlay else None
            review = bool(dn and dn.disagrees)
            status = {RequirementStatus.SATISFIED: "complete",
                      RequirementStatus.PROVISIONALLY_SATISFIED: "in_progress",
                      RequirementStatus.INDETERMINATE: "cannot_check",
                      RequirementStatus.NOT_EVALUABLE: "cannot_check"}.get(node.status, "open")
            if status == "open" and node.requirement_code in planned:
                status = "planned"
            if review:
                status = "needs_review"
            entry = {"code": node.requirement_code, "name": node.requirement_name,
                     "status": status,
                     "system": "core" if node.requirement_code in CORE_CODES else "major",
                     "source_label": "from your Degree Navigator audit" if dn else "estimated"}
            if review:
                entry["label_text"] = "needs review"
                entry["note"] = "Degree Navigator and CoursePilot disagree; confirm with your " \
                                "advisor"
            reqs.append(entry)

        for root in result.requirements:
            leaf(root)
        out = {"source": "degree_navigator" if overlay else "estimated",
               "source_label": ("from your Degree Navigator audit" if overlay
                                else "estimated"),
               "program": result.program_name, "support": self._support(),
               "credits_completed": result.credits_completed,
               "credits_required": result.credits_required_min,
               "credits_remaining": result.credits_remaining,
               "requirements": reqs[:60]}
        if include_gpa:
            # CoursePilot does not compute a GPA for display; it never guesses one.
            out["gpa"] = None
            out["gpa_note"] = "CoursePilot does not calculate your GPA; Degree Navigator shows it."
        return out

    def _line(self, pc, term: str) -> dict:
        ctx = self._context()
        state = ctx.states.get((pc.course, term))
        return {"course": pc.course, "title": pc.title, "credits": pc.credits,
                "state": state.state.value if state else "planner_selected",
                "locked_reason": state.locked_reason if state else None,
                "role": pc.role, "offering": _offering(pc.offering),
                "requirement_codes": [r.requirement_code for r in pc.requirements],
                "requirements": [r.requirement_name for r in pc.requirements]}

    def get_plan(self) -> dict:
        ctx = self._context()
        terms = [{"term_code": t.term_code, "label": t.label, "credits": t.credits,
                  "courses": [self._line(pc, t.term_code) for pc in t.courses]}
                 for t in ctx.plan.terms]
        last = max((t["term_code"] for t in terms if t["courses"]), default=None)
        return {"revision": self._revision, "terms": terms,
                "graduation": term_label(last), "graduation_term": last,
                "status": ctx.plan.status.value,
                "issues": [i.message for i in ctx.plan.issues][:6],
                "labels": [self._support()]}

    def explain_course_placement(self, course: str, term_code: str | None) -> dict:
        ctx = self._context()
        for t in ctx.plan.terms:
            for pc in t.courses:
                if pc.course == course and (term_code in (None, t.term_code)):
                    reasons = [_reason_text(r.value) for r in pc.reasons]
                    if pc.role_reason:
                        reasons.insert(0, pc.role_reason)
                    prereq = pc.prerequisite
                    return {"course": course, "title": pc.title, "term_code": t.term_code,
                            "reasons": reasons, "offering": _offering(pc.offering),
                            "prerequisite": {"status": str(getattr(prereq.state, "value", prereq.state)),
                                             "text": getattr(prereq, "raw_text", None)},
                            "unlocks": list(pc.unlocks),
                            "requirements": [r.requirement_name for r in pc.requirements],
                            "requirement_codes": [r.requirement_code for r in pc.requirements]}
        raise LookupError(f"{course} is not in your saved plan")

    def list_course_options(self, course: str, term_code: str) -> dict:
        from app.services.planning.change_intent import CourseNotInPlan
        from app.services.planning.options import course_options

        try:
            o = course_options(self._context(), course, term_code)
        except CourseNotInPlan as exc:
            raise LookupError(str(exc)) from None
        out = vars(o).copy()
        out["groups"] = [{**{k: v for k, v in vars(g).items() if k != "options"},
                          "options": self._with_descriptions([vars(x).copy()
                                                              for x in g.options])}
                         for g in o.groups]
        return out

    def search_courses(self, *, text: str, term_code: str | None,
                       requirements: list[list[str]] | None, topic: str | None,
                       filters: dict | None, exclude: list[str], limit: int) -> dict:
        from app.services.planning.course_search import search_courses

        r = search_courses(self._context(), text, term_code=term_code,
                           requirements=requirements, topic=topic, filters=filters,
                           limit=min(25, limit + len(exclude)))
        term = r.interpretation.term_code
        hits = [{**vars(h), "term_code": term} for h in r.results
                if h.course not in exclude][:limit]
        return {"interpretation": vars(r.interpretation), "results":
                self._with_descriptions(hits), "not_available": r.not_available}

    def get_course_details(self, course: str) -> dict:
        from app.models.academic import CourseOffering

        row = self.session.scalar(select(Course).where(Course.course_string == course))
        if row is None:
            raise LookupError(f"{course} is not in the catalog data CoursePilot has")
        terms = sorted({t for (t,) in self.session.execute(
            select(CourseOffering.term_code).where(CourseOffering.course_id == row.id))},
            reverse=True)[:6]
        prereq = _TAG.sub(" ", row.prereq_notes_raw or "").strip() or None
        return {"course": course, "title": row.title, "credits": row.credits,
                "description": self._descriptions([course]).get(course),
                "prerequisite_text": prereq,
                "offerings": [{"term_code": t, "label_text": term_label(t)} for t in terms],
                "requirement_fit": self._requirement_fit(course, terms)}

    def _requirement_fit(self, course: str, terms: list[str]) -> dict | None:
        """Prompt 08 B1: what the course could fill for THIS student's program
        (course knowledge index, newest offered term), with the student's own
        status per requirement computed now from their saved plan. Structural
        membership never implies the student needs it or may enroll."""
        from app.models import ProgramVersion
        from app.services.knowledge import KnowledgeView, current_build

        if (not terms or self.student.program_version_id is None
                or current_build(self.session) is None):
            return None
        version = self.session.get(ProgramVersion, self.student.program_version_id)
        verdict = KnowledgeView(self.session, version, terms[0]).judge(course)
        status = {}
        try:
            from app.services.planning.saved import current_plan
            saved = current_plan(self.session, self.student)
            if saved is not None:
                status = {r.requirement_code:
                          str(getattr(r.status_before, "value", r.status_before))
                          for r in saved.result.requirements}
        except Exception:       # noqa: BLE001 - status is an overlay; never fail the tool
            status = {}
        return {"term_code": terms[0], "status": verdict.status, "stale": verdict.stale,
                "reasons": verdict.reasons[:4],
                "fills": [{"requirement_code": f.requirement_code,
                           "requirement_name": f.requirement_name,
                           "after_stage": f.after_stage, "pending_review": f.pending_review,
                           "open_conflicts": f.conflicts, "minimum_grade": f.min_grade,
                           "your_status": status.get(f.requirement_code, "unknown")}
                          for f in verdict.fills]}

    # -- previews -------------------------------------------------------
    def preview_plan_change(self, change: PlanChange) -> dict:
        from app.services.planning.edits import NoSavedPlan, PlanOutOfDate, preview_change

        try:
            out = preview_change(self.session, self.student, change)
        except NoSavedPlan as exc:
            raise LookupError(str(exc)) from None
        except PlanOutOfDate as exc:
            raise ValueError(str(exc)) from None
        self.session.commit()
        return out.model_dump(mode="json")

    def find_sections(self, course: str, term_code: str, limit: int) -> dict:
        from app.domain.schedule import SchedulePreferences
        from app.services.planning.profile import get_profile, profile_input
        from app.services.scheduling.service import InvalidScheduleRequest, generate_schedule

        prefs = SchedulePreferences()
        profile = get_profile(self.session, self.student)
        if profile is not None:
            s = profile_input(profile).schedule
            prefs = SchedulePreferences(earliest_start=s.earliest_start, latest_end=s.latest_end,
                                        avoid_days=list(s.avoid_days),
                                        blocked_times=list(s.blocked_times),
                                        preferred_campuses=list(s.preferred_campuses))
        try:
            result = generate_schedule(self.session, self.student, term_code=term_code,
                                       courses=[course], preferences=prefs, max_results=10)
        except InvalidScheduleRequest as exc:
            raise ValueError(str(exc)) from None
        self.session.rollback()          # generation writes nothing; keep it that way
        seen: set[str] = set()
        sections = []
        for option in result.options:
            for ch in option.choices:
                if ch.index_number in seen or ch.component != "primary":
                    continue
                seen.add(ch.index_number)
                sections.append({
                    "index_number": ch.index_number, "section_number": ch.section_number,
                    "campus": ch.campus_code, "instructors": ch.instructors,
                    "open_status": ch.availability.state.value,
                    "delivery": ch.modality.classification.value.replace("_", " "),
                    "special_permission": bool(ch.restriction.special_permission),
                    "meetings": [{"day": m.day, "start_minute": m.start_minute,
                                  "end_minute": m.end_minute, "campus": m.campus,
                                  "kind": m.kind.value} for m in ch.meetings]})
        title = ""
        for option in result.options:
            title = next((c.title for c in option.choices if c.title), title)
        return {"course": course, "title": title, "term_code": term_code,
                "sections": sections[:limit],
                "notes": [i.message for i in result.issues][:3],
                "blocked_times_respected": True}

    def _profile_with(self, preference: dict):
        from app.domain.modality import Modality
        from app.services.planning.profile import get_profile, profile_input

        profile = get_profile(self.session, self.student)
        if profile is None:
            raise ValueError("confirm your planning preferences first")
        answers = profile_input(profile)
        data = answers.model_dump()
        sched = data["schedule"]
        if "delivery" in preference:
            sched["modality"]["preferred"] = {
                "online": Modality.SYNCHRONOUS_ONLINE, "async": Modality.ASYNCHRONOUS,
                "hybrid": Modality.HYBRID, "in_person": Modality.IN_PERSON,
                "no_preference": None}[preference["delivery"]]
        for key in ("avoid_days", "earliest_start", "latest_end"):
            if key in preference:
                sched[key] = preference[key]
        if "max_credits_per_term" in preference:
            data["max_credits_per_term"] = Decimal(preference["max_credits_per_term"])
        return answers, type(answers).model_validate(data)

    def preview_preference(self, preference: dict) -> dict:
        before, after = self._profile_with(preference)
        b = before.model_dump(mode="json")
        flat_before = {k: b["schedule"].get(k, b.get(k)) for k in preference}
        parts = [f"{k.replace('_', ' ')}: {flat_before.get(k)} -> {v}"
                 for k, v in preference.items()]
        return {"before": flat_before, "after": dict(preference), "summary": "; ".join(parts)}

    # -- the action executor's ------------------------------------------
    def apply_confirmed(self, preview_id: str) -> dict:
        from app.services.planning.edits import NoSavedPlan, StalePreview, apply_preview

        try:
            saved = apply_preview(self.session, self.student, preview_id,
                                  now=datetime.now(UTC))
        except StalePreview as exc:
            self.session.rollback()
            raise StaleProposal(str(exc)) from None
        except NoSavedPlan as exc:
            raise LookupError(str(exc)) from None
        self.session.commit()
        self._ctx = None
        return {"revision": saved.revision}

    def apply_preference(self, preference: dict) -> dict:
        from app.services.planning.profile import confirm_profile

        _, after = self._profile_with(preference)
        confirm_profile(self.session, self.student, after, datetime.now(UTC))
        self.session.commit()
        return {"summary": ", ".join(f"{k.replace('_', ' ')} {v}"
                                     for k, v in preference.items())}


__all__ = ["DbAssistantTools"]
