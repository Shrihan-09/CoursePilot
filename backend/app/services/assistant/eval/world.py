"""Synthetic students for the assistant eval (Prompt 11 Part B, B4).

Everything here is FABRICATED: the students, their records, the catalog
entries, descriptions, sections and index numbers. No real student data is
used. Course keys follow Rutgers' UU:SSS:CCC shape so the grounding gate is
exercised realistically, but titles and descriptions are written for the
eval and are not Rutgers' text.

`SyntheticTools` implements the same `AssistantTools` protocol and the same
result shapes as `DbAssistantTools`, with a deliberately small planning model:
it knows offerings per term, prerequisites, credit caps, locks and
graduation, which is what the eval's swaps, moves, impossible requests and
side effects need. It is not the planning engine; Part A's tests cover that.
"""

from __future__ import annotations

import copy
import itertools
from decimal import Decimal

from app.domain.plan_edits import PlanChange
from app.services.assistant.actions import StaleProposal
from app.services.assistant.contracts import term_label

S27, F27, S28 = "20271", "20279", "20281"
SU27 = "20277"
H = "historically offered"
C = "confirmed"


def _sec(index, number, meetings, *, campus="College Avenue", status="open",
         instructors=("STAFF",), delivery="in person", special=False):
    return {"index_number": index, "section_number": number, "campus": campus,
            "instructors": list(instructors), "open_status": status, "delivery": delivery,
            "special_permission": special,
            "meetings": [{"day": d, "start_minute": s, "end_minute": e, "campus": campus}
                         for d, s, e in meetings]}


ASYNC = [{"day": None, "kind": "async"}]


def _course(title, credits, codes, names, *, desc=None, prereq=(), offered=None,
            delivery=("in_person",), weight="1.0", hours="3", status="open", sections=(),
            forces_move=None, prereq_text=None):
    return {"title": title, "credits": Decimal(str(credits)), "requirement_codes": list(codes),
            "requirements": list(names), "description": desc, "prereq": list(prereq),
            "prereq_text": prereq_text, "offered": offered or {S27: C, F27: H, S28: H},
            "delivery": list(delivery), "workload_weight": Decimal(weight),
            "contact_hours": Decimal(hours), "open_status": status,
            "sections": list(sections), "forces_move": forces_move}


AH, HST, SCL, CCD, NS, QR, WCD = ("Arts and Humanities", "Historical Analysis",
                                  "Social Analysis", "Diversities and Social Inequalities",
                                  "Natural Sciences", "Quantitative and Formal Reasoning",
                                  "Writing and Communication (Discipline)")

CATALOG: dict[str, dict] = {
    "01:640:151": _course("Calculus I", 4, ["MATH_CALC1"], ["Calculus I"]),
    "01:640:152": _course(
        "Calculus II", 4, ["MATH_CALC2"], ["Calculus II"], prereq=["01:640:151"],
        prereq_text="01:640:151 or placement", weight="1.4", hours="4",
        desc="Techniques and applications of integration, sequences and series. Continues "
             "Calculus I.",
        sections=[_sec("08811", "01", [("M", 600, 680), ("W", 600, 680)]),
                  _sec("08812", "02", [("T", 600, 680), ("H", 600, 680)])]),
    "01:198:111": _course("Introduction to Computer Science", 4, ["CS_CORE"], ["CS core"]),
    "01:198:112": _course("Data Structures", 4, ["CS_CORE"], ["CS core"],
                          prereq=["01:198:111"]),
    "01:198:205": _course("Introduction to Discrete Structures I", 4, ["CS_CORE"], ["CS core"],
                          prereq=["01:198:111", "01:640:151"]),
    "01:198:206": _course(
        "Introduction to Discrete Structures II", 4, ["CS_CORE"], ["CS core"],
        prereq=["01:198:205", "01:640:152"], prereq_text="01:198:205 and 01:640:152",
        offered={S27: C, F27: H, S28: H}, weight="1.5", hours="4",
        desc="Probability, counting and graph theory for computer science."),
    "01:198:211": _course(
        "Computer Architecture", 4, ["CS_CORE"], ["CS core"], prereq=["01:198:112"],
        prereq_text="01:198:112", weight="1.5", hours="4",
        sections=[_sec("10231", "01", [("M", 860, 940), ("W", 860, 940)])]),
    "01:198:344": _course("Design and Analysis of Computer Algorithms", 4, ["CS_CORE"],
                          ["CS core"], prereq=["01:198:206", "01:198:112"],
                          prereq_text="01:198:206 and 01:198:112", weight="1.6", hours="4"),
    "01:198:416": _course("Operating Systems Design", 4, ["CS_ELECTIVE"], ["CS electives"],
                          prereq=["01:198:211"], offered={S27: C, F27: H, S28: H},
                          weight="1.6", hours="4"),
    "01:198:439": _course("Introduction to Data Science", 4, ["CS_ELECTIVE"],
                          ["CS electives"], prereq=["01:198:112"]),
    "01:750:203": _course(
        "General Physics I", 3, ["NS", "PHYS_SEQ"], [NS, "Physics sequence"],
        prereq=["01:640:151"], offered={S27: C, F27: H, S28: H}, weight="1.3", hours="4",
        desc="Mechanics, heat and waves, with calculus.",
        sections=[_sec("11203", "01", [("T", 780, 860), ("H", 780, 860)])]),
    "01:082:105": _course(
        "Art History: Ancient to Medieval", 3, ["AHp"], [AH],
        desc="Painting, sculpture and architecture from the ancient world through the Middle "
             "Ages. Visits to the campus art museum."),
    # --- Arts and Humanities alternatives ---
    "01:730:105": _course(
        "Current Moral and Social Issues", 3, ["AHp"], [AH], weight="0.9",
        desc="Philosophical arguments about contested moral questions. Short weekly essays.",
        sections=[_sec("14105", "01", [("F", 600, 680), ("W", 600, 680)])]),
    "01:700:101": _course(
        "Introduction to Music", 3, ["AHp"], [AH], delivery=("asynchronous",), weight="0.8",
        desc="Listening skills and the history of Western and global music. Fully online, "
             "self-paced modules.",
        sections=[_sec("17101", "90", [], campus="Online", delivery="async",
                       instructors=("ROSS, A",)),
                  _sec("17102", "91", [], campus="Online", delivery="async", status="closed",
                       instructors=("LEE, M",))]),
    "01:082:106": _course(
        "Art History: Renaissance to Modern", 3, ["AHp"], [AH],
        desc="European and American art from the Renaissance to the present."),
    "01:358:201": _course(
        "Literature and Technology", 3, ["AHp"], [AH], delivery=("synchronous_online",),
        desc="How novels and films imagine computers, data and digital life. Online, with live "
             "weekly discussion.",
        sections=[_sec("13201", "90", [("M", 1140, 1220)], campus="Online",
                       delivery="online")]),
    "01:965:211": _course("Theater Appreciation", 3, ["AHp"], [AH], delivery=("hybrid",),
                          desc=None),
    "01:195:150": _course(
        "Great Books and Big Questions", 4, ["AHp", "WCd"], [AH, WCD],
        delivery=("asynchronous",), weight="1.2", forces_move="01:750:203",
        desc="Classic texts read across cultures, with a sequence of revised essays."),
    # --- Historical Analysis ---
    "01:510:101": _course("Development of Europe I", 3, ["HST"], [HST],
                          desc="Europe from antiquity to the 1600s."),
    "01:506:250": _course(
        "History of Technology", 3, ["HST"], [HST], delivery=("asynchronous",), weight="0.9",
        desc="From the printing press to the internet: how technology changed societies. "
             "Online."),
    "01:512:103": _course("Development of the United States I", 3, ["HST", "SCL"], [HST, SCL],
                          desc="The United States from colonization to 1877."),
    # --- other Core ---
    "01:920:101": _course("Introduction to Sociology", 3, ["SCL"], [SCL],
                          delivery=("asynchronous",), desc="Society, groups and institutions."),
    "01:220:102": _course("Introduction to Microeconomics", 3, ["SCL"], [SCL],
                          desc="Markets, prices and choices."),
    "01:988:130": _course("Women, Culture and Society", 3, ["CCD"], [CCD],
                          delivery=("asynchronous",)),
    "01:119:101": _course("General Biology I", 4, ["NS"], [NS], weight="1.3", hours="5"),
    "01:960:211": _course("Statistics I", 3, ["QR"], [QR]),
    "01:355:101": _course("College Writing", 3, ["WC"], ["Writing and Communication"]),
    "01:355:201": _course("Research in the Disciplines", 3, ["WCd"], [WCD]),
}
for key in CATALOG:
    CATALOG[key].setdefault("offered", {S27: C, F27: H, S28: H})


def _plan(*terms: tuple[str, list[tuple[str, str, str | None]]], graduation: str) -> dict:
    return {"revision": 1, "graduation_term": graduation,
            "terms": [{"term_code": t, "courses": [
                {"course": c, "state": "locked" if lock else "planner_selected", "role": role,
                 "locked_reason": lock} for c, role, lock in courses]} for t, courses in terms]}


RETAKE = "a needed retake of a course you did not pass"

STUDENTS: dict[str, dict] = {
    # CS B.S., undeclared, retaking Calc II, rushing.
    "alex": {
        "profile": {"program": {"name": "Computer Science B.S.", "support": "Beta supported"},
                    "catalog_year": "2026-2027", "declared": False, "pace": "rushing",
                    "preferences": {"delivery": None, "avoid_days": [], "earliest": None,
                                    "latest": None, "max_credits_per_term": 18,
                                    "blocked_times": [{"days": ["T", "H"], "start": "09:00",
                                                       "end": "12:00", "kind": "job"}]}},
        "completed": ["01:198:111", "01:198:112", "01:198:205", "01:640:151", "01:355:101"],
        "progress": {"source": "estimated", "source_label": "estimated", "gpa": "3.41",
                     "credits_completed": 19, "credits_required": 120, "requirements": [
                         {"code": "AHp", "name": AH, "status": "planned", "system": "core"},
                         {"code": "HST", "name": HST, "status": "open", "system": "core"},
                         {"code": "SCL", "name": SCL, "status": "open", "system": "core"},
                         {"code": "CCD", "name": CCD, "status": "open", "system": "core"},
                         {"code": "NS", "name": NS, "status": "planned", "system": "core"},
                         {"code": "QR", "name": QR, "status": "open", "system": "core"},
                         {"code": "WCd", "name": WCD, "status": "open", "system": "core"},
                         {"code": "CS_CORE", "name": "CS core", "status": "in_progress",
                          "system": "major"}]},
        "plan": _plan(
            (S27, [("01:640:152", "anchor", RETAKE), ("01:198:211", "major", None),
                   ("01:750:203", "multi_requirement", None), ("01:082:105", "filler", None)]),
            (F27, [("01:198:206", "major", None), ("01:510:101", "filler", None),
                   ("01:119:101", "filler", None), ("01:960:211", "filler", None)]),
            (S28, [("01:198:344", "major", None), ("01:198:416", "major", None),
                   ("01:988:130", "filler", None), ("01:220:102", "filler", None)]),
            graduation=S28),
    },
    # CS B.A. senior, graduating this spring; progress from a DN audit.
    "blair": {
        "profile": {"program": {"name": "Computer Science B.A.", "support": "Beta supported"},
                    "catalog_year": "2023-2024", "declared": True, "pace": "balanced",
                    "preferences": {"delivery": "in_person", "avoid_days": ["F"],
                                    "earliest": None, "latest": None,
                                    "max_credits_per_term": 18, "blocked_times": []}},
        "completed": ["01:198:111", "01:198:112", "01:198:205", "01:198:206", "01:198:211",
                      "01:640:151", "01:640:152", "01:355:101", "01:355:201", "01:510:101",
                      "01:920:101", "01:988:130", "01:119:101", "01:960:211"],
        "progress": {"source": "degree_navigator",
                     "source_label": "from your Degree Navigator audit",
                     "credits_completed": 109, "credits_required": 120, "requirements": [
                         {"code": "AHp", "name": AH, "status": "planned", "system": "core"},
                         {"code": "CS_ELECTIVE", "name": "CS electives", "status": "planned",
                          "system": "major"}]},
        "plan": _plan((S27, [("01:198:416", "major", None), ("01:198:439", "major", None),
                             ("01:082:105", "filler", None)]), graduation=S27),
    },
    # Freshman with only a WebReg schedule import; a Preview program.
    "casey": {
        "profile": {"program": {"name": "Economics B.A.",
                                "support": "Preview — not yet reviewed"},
                    "catalog_year": "2026-2027", "declared": False, "pace": "balanced",
                    "preferences": {"delivery": None, "avoid_days": [], "earliest": None,
                                    "latest": None, "max_credits_per_term": 17,
                                    "blocked_times": []}},
        "completed": [], "in_progress": ["01:198:111", "01:640:151", "01:355:101"],
        "progress": {"source": "estimated", "source_label": "estimated",
                     "credits_completed": 0, "credits_required": 120, "requirements": [
                         {"code": "AHp", "name": AH, "status": "open", "system": "core"},
                         {"code": "HST", "name": HST, "status": "open", "system": "core"},
                         {"code": "SCL", "name": SCL, "status": "planned", "system": "core"},
                         {"code": "NS", "name": NS, "status": "open", "system": "core"},
                         {"code": "WC", "name": "Writing and Communication",
                          "status": "in_progress", "system": "core"}]},
        "plan": _plan((S27, [("01:220:102", "major", None), ("01:640:152", "major", None),
                             ("01:198:112", "multi_requirement", None),
                             ("01:920:101", "filler", None)]), graduation="20301"),
    },
    # Degree Navigator and CoursePilot disagree about one requirement.
    "devon": {
        "profile": {"program": {"name": "Computer Science B.S.", "support": "Beta supported"},
                    "catalog_year": "2025-2026", "declared": True, "pace": "balanced",
                    "preferences": {"delivery": "online", "avoid_days": [], "earliest": None,
                                    "latest": None, "max_credits_per_term": 18,
                                    "blocked_times": []}},
        "completed": ["01:198:111", "01:198:112", "01:198:205", "01:640:151", "01:640:152",
                      "01:355:101", "01:355:201"],
        "progress": {"source": "degree_navigator",
                     "source_label": "from your Degree Navigator audit",
                     "credits_completed": 52, "credits_required": 120, "requirements": [
                         {"code": "WCd", "name": WCD, "status": "needs_review",
                          "label_text": "needs review", "system": "core",
                          "note": "Degree Navigator counts 01:355:201 here; Coursenav "
                                  "could not confirm it"},
                         {"code": "AHp", "name": AH, "status": "planned", "system": "core"},
                         {"code": "HST", "name": HST, "status": "open", "system": "core"},
                         {"code": "CS_CORE", "name": "CS core", "status": "in_progress",
                          "system": "major"}]},
        "plan": _plan((S27, [("01:198:206", "major", None), ("01:198:211", "major", None),
                             ("01:082:105", "filler", None)]),
                      (F27, [("01:198:344", "major", None), ("01:510:101", "filler", None)]),
                      graduation="20291"),
    },
}

_ids = itertools.count(1)


class SyntheticTools:
    """The AssistantTools protocol over one fabricated student."""

    def __init__(self, student: str) -> None:
        s = copy.deepcopy(STUDENTS[student])
        self.key = student
        self.profile = s["profile"]
        self.completed = set(s["completed"]) | set(s.get("in_progress", []))
        self.progress = s["progress"]
        self.plan = s["plan"]
        self.previews: dict[str, dict] = {}
        self.applied: list[dict] = []
        self.planned_sections: dict[tuple[str, str], str] = {}

    # -- helpers --------------------------------------------------------
    def plan_revision(self) -> int:
        return self.plan["revision"]

    def _where(self, plan: dict | None = None) -> dict[str, str]:
        return {c["course"]: t["term_code"] for t in (plan or self.plan)["terms"]
                for c in t["courses"]}

    def _line(self, c: dict, term: str) -> dict:
        info = CATALOG[c["course"]]
        return {"course": c["course"], "title": info["title"], "credits": info["credits"],
                "state": c["state"], "role": c.get("role"), "locked_reason": c.get("locked_reason"),
                "offering": info["offered"].get(term),
                "requirement_codes": info["requirement_codes"],
                "requirements": info["requirements"]}

    def _taken_before(self, term: str, plan: dict | None = None) -> set[str]:
        done = set(self.completed)
        for t in (plan or self.plan)["terms"]:
            if t["term_code"] < term:
                done |= {c["course"] for c in t["courses"]}
        return done

    def valid_option(self, course: str, term: str, *, ignore: str | None = None) -> bool:
        info = CATALOG.get(course)
        if info is None or term not in info["offered"] or course in self.completed:
            return False
        if course in self._where() and course != ignore:
            return False
        return all(p in self._taken_before(term) for p in info["prereq"])

    def _item(self, course: str, term: str) -> dict:
        info = CATALOG[course]
        return {"course": course, "title": info["title"], "credits": info["credits"],
                "requirement_codes": info["requirement_codes"],
                "requirements": info["requirements"], "offering": info["offered"][term],
                "offering_term": term, "delivery": info["delivery"],
                "workload_weight": info["workload_weight"],
                "contact_hours": info["contact_hours"],
                "open_status": info["open_status"] if term == S27 else "not_published",
                "fills_multiple": len(info["requirement_codes"]) > 1,
                "description": info["description"], "why": "fills "
                + " + ".join(info["requirement_codes"])}

    # -- read tools -----------------------------------------------------
    def get_profile_summary(self) -> dict:
        p = self.profile
        return {"program": p["program"], "catalog_year": p["catalog_year"],
                "declared": p["declared"], "pace": p["pace"], "preferences": p["preferences"],
                "next_term": self.plan["terms"][0]["term_code"],
                "labels": [p["program"]["support"]]}

    def get_progress(self, *, include_gpa: bool) -> dict:
        out = {k: v for k, v in self.progress.items() if k != "gpa"}
        out["program"] = self.profile["program"]["name"]
        out["support"] = self.profile["program"]["support"]
        if include_gpa and self.progress.get("gpa"):
            out["gpa"] = self.progress["gpa"]
        return copy.deepcopy(out)

    def get_plan(self) -> dict:
        terms = []
        for t in self.plan["terms"]:
            lines = [self._line(c, t["term_code"]) for c in t["courses"]]
            terms.append({"term_code": t["term_code"], "label": term_label(t["term_code"]),
                          "credits": sum((x["credits"] for x in lines), Decimal(0)),
                          "courses": lines})
        return {"revision": self.plan["revision"], "terms": terms,
                "graduation": term_label(self.plan["graduation_term"]),
                "graduation_term": self.plan["graduation_term"],
                "labels": [self.profile["program"]["support"]]}

    def explain_course_placement(self, course: str, term_code: str | None) -> dict:
        where = self._where()
        if course not in where:
            raise LookupError(f"{course} is not in your plan")
        term = where[course]
        info = CATALOG[course]
        c = next(x for t in self.plan["terms"] for x in t["courses"] if x["course"] == course)
        reasons = [f"it fills {', '.join(info['requirements'])}"]
        if info["prereq"]:
            reasons.append("its prerequisites (" + ", ".join(info["prereq"]) + ") are done or "
                           "planned earlier")
        if c.get("locked_reason"):
            reasons.append(f"it is {c['locked_reason']}")
        unlocks = sorted(k for k, v in CATALOG.items() if course in v["prereq"]
                         and k in where)
        return {"course": course, "title": info["title"], "term_code": term,
                "reasons": reasons, "offering": info["offered"].get(term),
                "prerequisite": {"text": info["prereq_text"] or ", ".join(info["prereq"])
                                 or "none published"},
                "unlocks": unlocks, "requirements": info["requirements"],
                "requirement_codes": info["requirement_codes"]}

    def list_course_options(self, course: str, term_code: str) -> dict:
        where = self._where()
        if where.get(course) != term_code:
            raise LookupError(f"{course} is not in your plan for {term_label(term_code)}")
        info = CATALOG[course]
        c = next(x for t in self.plan["terms"] for x in t["courses"] if x["course"] == course)
        out = {"course": course, "term_code": term_code, "title": info["title"],
               "state": c["state"], "locked": c["state"] == "locked",
               "locked_reason": c.get("locked_reason"), "requirements": info["requirements"],
               "groups": [], "move_terms": [t["term_code"] for t in self.plan["terms"]
                                            if t["term_code"] != term_code
                                            and t["term_code"] in info["offered"]],
               "can_remove": c["state"] != "locked"}
        if out["locked"]:
            return out
        for code, name in zip(info["requirement_codes"], info["requirements"]):
            opts = [self._item(k, term_code) for k, v in CATALOG.items()
                    if code in v["requirement_codes"] and k != course
                    and self.valid_option(k, term_code)]
            if opts:
                out["groups"].append({"kind": "same_requirement", "requirement_code": code,
                                      "requirement_name": name, "options": opts})
        open_codes = [r for r in self.progress["requirements"] if r["status"] == "open"]
        for r in open_codes[:2]:
            opts = [self._item(k, term_code) for k, v in CATALOG.items()
                    if r["code"] in v["requirement_codes"] and self.valid_option(k, term_code)]
            if opts:
                out["groups"].append({"kind": "different_requirement",
                                      "requirement_code": r["code"],
                                      "requirement_name": r["name"], "options": opts[:3]})
        return out

    def search_courses(self, *, text: str, term_code: str | None,
                       requirements: list[list[str]] | None, topic: str | None,
                       filters: dict | None, exclude: list[str], limit: int) -> dict:
        term = term_code or self.plan["terms"][0]["term_code"]
        f = filters or {}
        stop = {"the", "and", "course", "courses", "class", "about", "something", "with",
                "for", "that", "this", "called", "named"}
        words = [w for w in (topic or "").lower().split() if len(w) >= 3 and w not in stop]
        hits = []
        for key, info in CATALOG.items():
            if key in exclude or not self.valid_option(key, term):
                continue
            if requirements and not all(set(g) & set(info["requirement_codes"])
                                        for g in requirements):
                continue
            d = set(info["delivery"])
            if f.get("online") and not d & {"asynchronous", "synchronous_online"}:
                continue
            if f.get("async_only") and "asynchronous" not in d:
                continue
            if f.get("in_person") and "in_person" not in d:
                continue
            if f.get("fills_two") and len(info["requirement_codes"]) < 2:
                continue
            if f.get("avoid_days") and term == S27:
                days = {m["day"] for s in info["sections"] for m in s["meetings"]}
                if info["sections"] and days & set(f["avoid_days"]):
                    continue
            if words:
                hay = (info["title"] + " " + (info["description"] or "")).lower()
                if not any(w[:5] in hay for w in words):
                    continue
            item = self._item(key, term)
            if words:
                item["why"] += " · matches the topic"
            hits.append(item)
        return {"interpretation": {"term_code": term, "requirements": requirements or [],
                                   "topic": topic, "online": bool(f.get("online")),
                                   "exclude": exclude, "notes": []},
                "results": hits[:limit], "not_available": []}

    def get_course_details(self, course: str) -> dict:
        info = CATALOG.get(course)
        if info is None:
            raise LookupError(f"{course} is not in the catalog data Coursenav has")
        return {"course": course, "title": info["title"], "credits": info["credits"],
                "description": info["description"],
                "prerequisite_text": info["prereq_text"] or (", ".join(info["prereq"]) or None),
                "offerings": [{"term_code": t, "offering": lbl}
                              for t, lbl in info["offered"].items()]}

    # -- previews -------------------------------------------------------
    def _diff(self, before: dict, after: dict, *, forced: list[dict], lost: list[str],
              summary: str) -> dict:
        terms = []
        codes = sorted({t["term_code"] for t in before["terms"]} |
                       {t["term_code"] for t in after["terms"]})
        for code in codes:
            b = next((t for t in before["terms"] if t["term_code"] == code), {"courses": []})
            a = next((t for t in after["terms"] if t["term_code"] == code), {"courses": []})
            bl = [self._line(c, code) for c in b["courses"]]
            al = [self._line(c, code) for c in a["courses"]]
            bk, ak = {x["course"] for x in bl}, {x["course"] for x in al}
            if bk == ak:
                continue
            terms.append({"term_code": code, "label": term_label(code),
                          "before": [{k: x[k] for k in ("course", "title", "credits", "state")}
                                     for x in bl],
                          "after": [{k: x[k] for k in ("course", "title", "credits", "state")}
                                    for x in al],
                          "added": sorted(ak - bk), "removed": sorted(bk - ak),
                          "credits_before": sum((x["credits"] for x in bl), Decimal(0)),
                          "credits_after": sum((x["credits"] for x in al), Decimal(0))})
        gb, ga = before["graduation_term"], after["graduation_term"]
        over = any(t["credits_after"] > self.profile["preferences"]["max_credits_per_term"]
                   for t in terms)
        big = bool(forced) or ga > gb or bool(lost) or over
        tail = "graduation unchanged" if ga == gb else \
            f"graduation moves to {term_label(ga)}"
        return {"terms": terms, "forced_moves": forced,
                "graduation_before": term_label(gb), "graduation_after": term_label(ga),
                "requirements_lost": lost, "requirements_gained": [],
                "new_issues": ["over the credit maximum"] if over else [],
                "summary": f"{summary}; {tail}", "big": big}

    def _refuse(self, change: PlanChange, *errors: str) -> dict:
        return {"preview_id": "", "base_revision": self.plan["revision"],
                "change": change.model_dump(mode="json", exclude_none=True), "valid": False,
                "errors": list(errors), "diff": None}

    def _term(self, plan: dict, code: str) -> dict:
        t = next((t for t in plan["terms"] if t["term_code"] == code), None)
        if t is None:
            t = {"term_code": code, "courses": []}
            plan["terms"].append(t)
            plan["terms"].sort(key=lambda x: x["term_code"])
        return t

    def preview_plan_change(self, change: PlanChange) -> dict:
        after = copy.deepcopy(self.plan)
        where = self._where()
        k = change.kind
        name = lambda c: f"{c} {CATALOG[c]['title']}" if c in CATALOG else c  # noqa: E731
        forced: list[dict] = []
        lost: list[str] = []
        if k == "choose_alternative":
            return self._refuse(change, "not_supported: no one-of choice is open")
        course, term = change.course, change.term_code
        if k in ("swap", "move", "remove", "lock", "unlock") and where.get(course) != term:
            return self._refuse(change, f"course_not_in_term: {course} is not planned in "
                                        f"{term_label(term)}")
        entry = next((c for t in after["terms"] for c in t["courses"]
                      if c["course"] == course), None)
        if k in ("swap", "move", "remove") and entry and entry["state"] == "locked":
            return self._refuse(change, f"locked: {course} stays because it is "
                                        f"{entry.get('locked_reason') or 'locked'}")
        if k == "swap":
            rep = change.replacement
            if rep not in CATALOG:
                return self._refuse(change, f"unknown_course: {rep} is not in the catalog data")
            if not self.valid_option(rep, term):
                return self._refuse(change, f"not_valid: {rep} cannot be taken in "
                                            f"{term_label(term)}")
            t = self._term(after, term)
            t["courses"] = [c for c in t["courses"] if c["course"] != course]
            t["courses"].append({"course": rep, "state": "user_selected", "role": "selected",
                                 "locked_reason": None})
            moved = CATALOG[rep]["forces_move"]
            if moved and where.get(moved) == term:
                t["courses"] = [c for c in t["courses"] if c["course"] != moved]
                nxt = next(x["term_code"] for x in after["terms"] if x["term_code"] > term)
                self._term(after, nxt)["courses"].append(
                    {"course": moved, "state": "planner_selected", "role": "multi_requirement",
                     "locked_reason": None})
                forced.append({"course": moved, "title": CATALOG[moved]["title"],
                               "from_term": term, "to_term": nxt,
                               "reason": "the new course's time conflicts with it"})
            summary = f"{name(course)} out of {term_label(term)}; {name(rep)} in"
        elif k == "move":
            to = change.to_term
            info = CATALOG[course]
            if to not in info["offered"]:
                return self._refuse(change, f"not_offered: {course} is not offered in "
                                            f"{term_label(to)}")
            src = self._term(after, term)
            src["courses"] = [c for c in src["courses"] if c["course"] != course]
            self._term(after, to)["courses"].append({**entry, "state": "user_selected"})
            for t in list(after["terms"]):
                for c in list(t["courses"]):
                    if course in CATALOG[c["course"]]["prereq"] and t["term_code"] <= to:
                        t["courses"].remove(c)
                        later = [x["term_code"] for x in after["terms"] if x["term_code"] > to]
                        dest = later[0] if later else str(int(to[:4]) + (to[4] == "9")) + \
                            ("1" if to[4] == "9" else "9")
                        self._term(after, dest)["courses"].append(c)
                        forced.append({"course": c["course"],
                                       "title": CATALOG[c["course"]]["title"],
                                       "from_term": t["term_code"], "to_term": dest,
                                       "reason": f"it needs {course} first"})
            summary = f"{name(course)} moves to {term_label(to)}"
        elif k == "remove":
            t = self._term(after, term)
            t["courses"] = [c for c in t["courses"] if c["course"] != course]
            lost = list(CATALOG[course]["requirements"])
            summary = f"{name(course)} out of {term_label(term)}"
        elif k == "add":
            if course not in CATALOG:
                return self._refuse(change, f"unknown_course: {course} is not in the catalog "
                                            "data Coursenav has")
            info = CATALOG[course]
            if course in self.completed or course in where:
                return self._refuse(change, f"already_planned: {course} is already in your "
                                            "record or plan")
            if term not in info["offered"]:
                return self._refuse(change, f"not_offered: {course} is not offered in "
                                            f"{term_label(term)}")
            missing = [p for p in info["prereq"] if p not in self._taken_before(term)]
            if missing:
                return self._refuse(change, f"prerequisite_not_met: {course} needs "
                                            f"{', '.join(missing)} first")
            self._term(after, term)["courses"].append(
                {"course": course, "state": "user_selected", "role": "selected",
                 "locked_reason": None})
            summary = f"{name(course)} added to {term_label(term)}"
        elif k == "lighten_term":
            t = self._term(after, term)
            movable = [c for c in t["courses"] if c["role"] in ("filler", "multi_requirement")
                       and c["state"] != "locked"]
            if not movable:
                return self._refuse(change, f"nothing_to_move: every course in "
                                            f"{term_label(term)} is required there")
            c = movable[-1]
            t["courses"].remove(c)
            later = [x for x in after["terms"] if x["term_code"] > term and len(x["courses"]) < 5]
            if later:
                later[0]["courses"].append(c)
                dest = later[0]["term_code"]
            else:
                last = after["terms"][-1]["term_code"]
                dest = str(int(last[:4]) + 1) + "1" if last[4] == "9" else last[:4] + "9"
                self._term(after, dest)["courses"].append(c)
                after["graduation_term"] = dest
            summary = f"{name(c['course'])} moves from {term_label(term)} to {term_label(dest)}"
        elif k in ("lock", "unlock"):
            entry["state"] = "locked" if k == "lock" else "planner_selected"
            entry["locked_reason"] = "you locked it" if k == "lock" else None
            summary = f"{name(course)} {'locked' if k == 'lock' else 'unlocked'}"
        else:
            return self._refuse(change, f"not_supported: {k}")
        last = max(t["term_code"] for t in after["terms"] if t["courses"])
        after["graduation_term"] = max(after["graduation_term"], last)
        pid = f"pv-{self.key}-{next(_ids)}"
        diff = self._diff(self.plan, after, forced=forced, lost=lost, summary=summary)
        self.previews[pid] = {"base": self.plan["revision"], "after": after}
        return {"preview_id": pid, "base_revision": self.plan["revision"],
                "change": change.model_dump(mode="json", exclude_none=True), "valid": True,
                "errors": [], "diff": diff}

    def find_sections(self, course: str, term_code: str, limit: int) -> dict:
        info = CATALOG.get(course)
        if info is None:
            raise LookupError(course)
        prefs = self.profile["preferences"]
        blocks = [(d, int(b["start"][:2]) * 60 + int(b["start"][3:]),
                   int(b["end"][:2]) * 60 + int(b["end"][3:]))
                  for b in prefs.get("blocked_times") or [] for d in b["days"]]
        out = []
        for s in info["sections"] if term_code == S27 else []:
            clash = any(m["day"] == d and m["start_minute"] < e and s0 < m["end_minute"]
                        for m in s["meetings"] for d, s0, e in blocks)
            avoid = any(m["day"] in (prefs.get("avoid_days") or []) for m in s["meetings"])
            if not clash and not avoid:
                out.append(s)
        out.sort(key=lambda s: s["open_status"] != "open")
        notes = [] if info["sections"] or term_code != S27 else ["No sections are published."]
        if term_code != S27:
            notes = [f"Sections for {term_label(term_code)} are not published yet."]
        return {"course": course, "title": info["title"], "term_code": term_code,
                "sections": copy.deepcopy(out[:limit]), "notes": notes,
                "blocked_times_respected": True}

    def preview_preference(self, preference: dict) -> dict:
        before = {k: self.profile["preferences"].get(k) for k in preference}
        parts = [f"{k.replace('_', ' ')}: {before[k]} -> {v}" for k, v in preference.items()]
        return {"before": before, "after": dict(preference), "summary": "; ".join(parts)}

    # -- the action executor's ------------------------------------------
    def apply_confirmed(self, preview_id: str) -> dict:
        p = self.previews.pop(preview_id, None)
        if p is None:
            raise LookupError("that preview does not exist")
        if p["base"] != self.plan["revision"]:
            raise StaleProposal(preview_id)
        p["after"]["revision"] = self.plan["revision"] + 1
        self.plan = p["after"]
        self.applied.append({"preview_id": preview_id})
        return {"revision": self.plan["revision"]}

    def apply_preference(self, preference: dict) -> dict:
        self.profile["preferences"].update(preference)
        return {"summary": ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in
                                     preference.items())}


__all__ = ["CATALOG", "STUDENTS", "SyntheticTools"]
