"""SYNTHETIC Degree Navigator audit PDFs in the real Chrome "Save as PDF" layout (Phase 7.4).

The layout copies the developer's own audit (measured locally, never committed;
docs/investigations/phase-7-3-csp-dn.md section 4):

* Chrome draws text in a flipped coordinate system (`cm 0.75 0 0 -0.75 0 792`,
  `Tm 1 0 0 -1 x y`) - positions only make sense after composing both;
* every label and value is its OWN fragment ("Requirement" | "R1 : Title [CC]",
  "Total Courses:" | "2", "1." | "01:999:101" | "( 3, Fall 2025, A)");
* requirement status is an ICON on the heading's baseline; course status icons
  sit right after a course code inside the rule text; DN's legend (two columns)
  shows each icon next to its meaning;
* numbered course lists put two courses on one baseline;
* a Chrome header (print timestamp) and footer (URL with degreeID, "n / N")
  repeat on every page; a requirement can start at the bottom of one page and
  continue on the next;
* placement records read "( 0, Placement, PL, PL )".

Every value is invented: subject 999, titles, the identity block
("TEST STUDENT", RUID 000000000), the degreeID. Program headings and Core goal
codes are Rutgers' public names - they are what a mapping is keyed on.

`build_audit_pdf(model)` renders a model; `audit_layout(model)` exposes the
positioned layout so `scripts/inject_dn_failures.py` can damage it.
"""

from __future__ import annotations

import copy

ICONS = {  # name: (width, height, rgb) - DN legend images' sizes; invented colours
    "completed": (13, 9, (0, 49, 99)),
    "incomplete": (13, 9, (132, 0, 0)),
    "current": (10, 8, (40, 120, 40)),
    "planned": (10, 9, (90, 90, 160)),
    "in_progress_or_planned": (14, 11, (200, 140, 0)),
}
#: Requirement status -> the icon DN draws; course status -> its icon.
REQUIREMENT_ICON = {"fulfilled": "completed", "not_fulfilled": "incomplete",
                    "in_progress_or_planned": "in_progress_or_planned"}
COURSE_ICON = {"completed": "completed", "current": "current", "planned": "planned"}
NEEDS = {"fulfilled": "This requirement is (will be) satisfied.",
         "in_progress_or_planned": "In Progress: This requirement will be satisfied."}

TOP, BOTTOM, LINE = 750.0, 60.0, 12.8


def sas_core_model() -> dict:
    """A SAS Core audit like the real one, with invented courses (subject 999)."""
    return {
        "printed_at": "10/30/26, 9:15 AM",
        "report_date": "October 30 2026",
        "degree_id": "9999",
        "subject": "The SAS Core Curriculum (NB)",
        "version": "Summer 2019",
        "identity": ("TEST STUDENT", "000000000"),
        "requirements": [
            {"title": "Contemporary Challenges [CC]", "status": "in_progress_or_planned",
             "total": 2, "intro": "The required number of courses from 2 of the following "
                                  "sets of courses:", "join": "and",
             "goals": [("Contemporary Challenges: Our Common Future Courses", "CCO",
                        [("01:999:101", "completed")]),
                       ("Contemporary Challenges: Diversity and Social Inequalities Courses",
                        "CCD", [("01:999:102", "current")])],
             "courses": [("01:999:101", 3, "Spring 2026", "A"),
                         ("01:999:102", 3, "Fall 2026", "current")]},
            {"title": "Areas of Inquiry: Natural Sciences [NS]", "status": "not_fulfilled",
             "total": 2, "intro": "A total of 2 courses from", "join": None,
             "needs": "1 more course is needed to satisfy this requirement.",
             "goals": [("Natural Sciences", "NS", [("01:999:201", "completed")])],
             "courses": [("01:999:201", 4, "Fall 2025", "B+")]},
            {"title": "Areas of Inquiry: Social [SCL] and Historical [HST] Analysis",
             "status": "not_fulfilled", "total": 2,
             "intro": "1 courses from 2 of the following sets of courses:", "join": "and",
             "needs": "1 more course is needed to satisfy this requirement.",
             "goals": [("Historical Analysis", "HST", []),
                       ("Social Analysis", "SCL", [("01:999:301", "completed")])],
             "courses": [("01:999:301", 3, "Fall 2025", "B")]},
            {"title": "Areas of Inquiry: Arts and Humanities [AHo], [AHp], [AHq], [AHr]",
             "status": "fulfilled", "total": 2,
             "intro": "1 courses from 2 of the following sets of courses:", "join": "or",
             "goals": [("Human Experience", "AHo", [("01:999:401", "completed")]),
                       ("Arts and/or Literatures", "AHp", []),
                       ("Nature of Languages", "AHq", []),
                       ("Critical Creative Expression", "AHr", [("01:999:402", "completed")])],
             "courses": [("01:999:401", 3, "Fall 2025", "A"),
                         ("01:999:402", 3, "Spring 2026", "B+")]},
            {"title": "Cognitive Skills and Processes: Writing and Communication "
                      "[WC], [WCr], [WCd]", "status": "not_fulfilled", "total": 3,
             "intro": "1 courses from 3 of the following sets of courses:", "join": "and",
             "needs": "2 more courses are needed to satisfy this requirement.",
             "goals": [("College Writing", "WC", [("01:999:501", "completed")]),
                       ("Revision-Based Writing and Communication", "WCr", []),
                       ("Discipline-Based Writing and Communication", "WCd", [])],
             "courses": [("01:999:501", 3, "Spring 2026", "A")]},
            {"title": "Cognitive Skills and Processes: Quantitative and Formal Reasoning "
                      "[QQ], [QR]", "status": "fulfilled", "total": 2,
             "intro": "1 courses from 2 of the following sets of courses:", "join": "and",
             "goals": [("Quantitative Information", "QQ", [("01:999:601", "completed")]),
                       ("Mathematical or Formal Reasoning", "QR", [("01:999:602", "completed")])],
             "courses": [("01:999:601", 4, "Fall 2025", "A"),
                         ("01:999:602", 4, "Fall 2025", "B")]},
        ],
        "used": [("01:999:101", 3, "Spring 2026", "A"), ("01:999:102", 3, "Fall 2026", "current"),
                 ("01:999:201", 4, "Fall 2025", "B+"), ("01:999:301", 3, "Fall 2025", "B"),
                 ("01:999:401", 3, "Fall 2025", "A"), ("01:999:402", 3, "Spring 2026", "B+"),
                 ("01:999:501", 3, "Spring 2026", "A"), ("01:999:601", 4, "Fall 2025", "A"),
                 ("01:999:602", 4, "Fall 2025", "B")],
        "unused": [("01:999:701", 4, "Spring 2026", "B"), ("01:999:702", 3, "Fall 2026", "current"),
                   ("XX:999:ABC", 0, "Placement", "PL, PL")],
        "below_minimum": [("01:999:801", 4, "Spring 2026", "F")],
    }


def major_model() -> dict:
    """A SYNTHETIC major block (Computer Science heading, invented courses)."""
    return {
        "printed_at": "10/30/26, 9:16 AM", "report_date": "October 30 2026",
        "degree_id": "9998", "subject": "Computer Science - B.A. (NB)", "version": "Fall 2024",
        "identity": ("TEST STUDENT", "000000000"),
        "requirements": [
            {"title": "Introductory Courses", "status": "fulfilled", "total": 2,
             "intro": "2 courses from", "join": None,
             "goals": [("Introductory Computer Science", "", [("01:999:111", "completed"),
                                                              ("01:999:112", "completed")])],
             "courses": [("01:999:111", 4, "Fall 2025", "A"),
                         ("01:999:112", 4, "Spring 2026", "B")]},
        ],
        "used": [("01:999:111", 4, "Fall 2025", "A"), ("01:999:112", 4, "Spring 2026", "B")],
        "unused": [], "below_minimum": [],
    }


class _Pages:
    def __init__(self, model: dict) -> None:
        self.model, self.pages, self.y = model, [], TOP
        self.new_page()

    def new_page(self) -> None:
        self.pages.append({"lines": [], "icons": []})
        self.y = TOP

    def need(self, height: float) -> None:
        if self.y - height < BOTTOM:
            self.new_page()

    def line(self, *frags, gap: float = LINE, size: float = 11) -> float:
        self.need(gap)
        y = self.y
        for x, text in frags:
            self.pages[-1]["lines"].append((x, y, text, size))
        self.y -= gap
        return y

    def icon(self, x: float, y: float, kind: str) -> None:
        self.pages[-1]["icons"].append((x, y, kind))


def _course_detail(credits, term, grade) -> str:
    return f"( {credits}, {term}, {grade} )" if term == "Placement" else \
        f"( {credits}, {term}, {grade})"


def audit_layout(model: dict | None = None) -> list[dict]:
    """[{lines: [(x, y, text, size)], icons: [(x, y, kind)]}] in composed (user) space."""
    m = model or sas_core_model()
    p = _Pages(m)
    p.line((38.2, "Degree Audit"), gap=20, size=14)
    p.line((38.2, "Full Audit Report"), gap=16, size=12)
    p.line((38.2, "Rutgers, the State University of New Jersey"), gap=16)
    for text in ("Degree Navigator is an advising tool designed to help students make "
                 "informed decisions.", "The Degree Navigator report is not an official "
                 "transcript of your academic record.", "Verification of college and degree "
                 "requirements can only be certified by an academic adviser."):
        p.line((38.2, text), gap=10)
    p.line((38.2, f"Date:  {m['report_date']}"), gap=14)
    name, ruid = m["identity"]
    p.line((38.2, name), gap=14)
    p.line((38.2, "Student RUID:"), (98.4, ruid), gap=14)
    p.line((38.2, "Report for Subject:"), (126.8, f"{m['subject']} ({m['version']})"), gap=14)
    p.line((38.2, "Completed Credits:"), (126.8, "N/A"), gap=14)
    done = sum(r["status"] != "not_fulfilled" for r in m["requirements"])
    p.line((38.2, "Completed Requirements:"), (169.4, f"{done} of {len(m['requirements'])}"),
           gap=24)
    p.line((38.2, "Legend"), gap=25, size=12)
    p.line((37.5, "Course Legend"), (314.1, "Requirement Legend"), gap=15.7)
    legend = [("Indicates course has been successfully completed", "completed",
               "Indicates requirement has been fulfilled", "completed"),
              ("Indicates course is currently in progress", "current",
               "Indicates requirement has not been fulfilled", "incomplete"),
              ("Indicates course is in the course plan", "planned",
               "Indicates requirement will be fulfilled with successful",
               "in_progress_or_planned")]
    for left, licon, right, ricon in legend:
        y = p.line((50.2, left), (327.6, right), gap=12.7)
        p.icon(37.5, y, licon)
        # The tall "will be fulfilled" icon is centred on its two-line phrase.
        p.icon(314.2, y - 5.3 if ricon == "in_progress_or_planned" else y, ricon)
    p.line((327.6, "completion of in-progress or planned courses"), gap=26)
    p.line((38.2, "Requirements"), gap=18, size=12)

    for n, req in enumerate(m["requirements"], 1):
        p.need(80)
        y = p.line((48.7, "Requirement"), (108.6, f"R{n} : {req['title']}"), gap=21.7)
        p.icon(38.2, y, REQUIREMENT_ICON[req["status"]])
        completed = len(req["courses"])
        p.line((59.2, "Total Courses:"), (116.5, str(req["total"])))
        p.line((59.2, "Completed:"), (116.5, str(completed)), gap=12)
        needs = req.get("needs") or NEEDS[req["status"]]
        p.line((59.2, "Needs:"), (116.5, needs), gap=27.7)
        p.line((39.7, req["intro"]), gap=16)
        for gi, (title, code, courses) in enumerate(req["goals"]):
            if gi and req["join"]:
                p.line((54.7, req["join"]), gap=14)
            label = f"{title} [{code}]" if code else title
            if not courses:
                p.line((46.4, "{"), (51.6, label), (260.7, "}"), gap=16)
                continue
            p.line((46.4, "{"), (51.6, label), gap=10)
            x = 43.5
            frags = [(x, "(")]
            icons = []
            x += 3.7
            for course, status in courses:
                frags.append((x, course))
                icons.append((x + 49.5, COURSE_ICON[status]))
                x += 62
            frags += [(x, ")"), (x + 3.7, "}")]
            y = p.line(*frags, gap=16)
            for ix, kind in icons:
                p.icon(ix, y, kind)
        p.line((39.7, "Notes:"), gap=16)
        p.line((66.7, "Students will be able to meet an invented learning goal."), gap=20)
        if req["courses"]:
            p.line((39.7, "Completed Courses:"), gap=12)
            for course, credits, term, grade in req["courses"]:
                p.line((39.7, course), (91.5, _course_detail(credits, term, grade)), gap=12)
        p.y -= 28

    p.line((38.2, "Conditions"), gap=16, size=12)
    p.line((39.7, "Condition C13"), gap=12)
    p.line((39.7, f"(Applies to requirement {{R1 - {m['requirements'][0]['title']}}})"), gap=12)
    p.line((39.7, "Students will be able to meet an invented learning goal."), gap=20)
    p.line((38.2, "Reuse"), gap=16, size=12)
    for n, req in enumerate(m["requirements"], 1):
        p.line((57.7, f"R{n} - {req['title']}"), gap=14)
    p.y -= 10
    p.line((38.2, "Courses"), gap=16.5, size=12)

    def numbered(title, rows, second_x=240.6):
        p.line((36.7, title), gap=15)
        for i in range(0, len(rows), 2):
            frags = []
            for j, (course, credits, term, grade) in enumerate(rows[i:i + 2]):
                x = 38.2 if j == 0 else second_x
                num = f"{i + j + 1}."
                frags += [(x, num), (x + 10.5 + (5.3 if len(num) > 2 else 0), course),
                          (x + 62.3, _course_detail(credits, term, grade))]
            p.line(*frags, gap=15)

    numbered("Courses Used for this Program", m["used"])
    if m["unused"]:
        numbered("Unused Courses for this Program", m["unused"], second_x=250.2)
    if m["below_minimum"]:
        p.line((36.7, "Unused Courses for this Program - Courses Do Not Meet Minimum Grade "
                      "Requirement(s)"), gap=13.5)
        p.line((36.7, "The following courses may not be used in this program as specified "
                      "by requirement"), (385.8, "C12 - University-Wide Minimum Passing"),
               gap=9.8)
        p.line((36.7, "Grade"), (61.5, ":"), gap=15)
        numbered("", m["below_minimum"])
    p.line((233.0, "Powered by"), (283.8, "Decision Academic Inc."))

    total = len(p.pages)
    url = f"https://dn.rutgers.edu/DN/Audit/DegreeAudit.aspx?pageid=audit&degreeID={m['degree_id']}"
    for i, page in enumerate(p.pages, 1):
        page["lines"] = [ln for ln in page["lines"] if ln[2]]
        page["lines"] += [(24.0, 769.5, m["printed_at"], 10.7),
                          (322.1, 769.5, "Degree Navigator", 10.7),
                          (24.0, 16.5, url, 10.7), (576.9, 16.5, str(i), 10.7),
                          (581.3, 16.5, "/", 10.7), (583.5, 16.5, str(total), 10.7)]
    return p.pages


def render_pdf(pages: list[dict], *, images: bool = True) -> bytes:
    """Write a layout as Chrome does: flipped text space, upright image space.
    `images=False` drops every icon (the status layer gone)."""
    bodies: list[bytes] = []

    def add(body: bytes) -> int:
        bodies.append(body)
        return len(bodies)

    catalog, root = add(b""), add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    xobjects = {}
    for name, (w, h, rgb) in ICONS.items():
        pixels = bytes(rgb) * (w * h)
        xobjects[name] = add(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
            b"/BitsPerComponent 8 /Length %d >>\nstream\n" % (w, h, len(pixels))
            + pixels + b"\nendstream")
    kids = []
    for page in pages:
        ops = ["q 0.75 0 0 -0.75 0 792 cm"]
        for x, y, text, size in page["lines"]:
            esc = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /F1 {size} Tf 1 0 0 -1 {x / 0.75:.2f} {(792 - y) / 0.75:.2f} Tm "
                       f"({esc}) Tj ET")
        ops.append("Q")
        if images:
            for x, y, kind in page["icons"]:
                w, h, _ = ICONS[kind]
                ops.append(f"q {w * 0.75:.2f} 0 0 {h * 0.75:.2f} {x} {y} cm /Im_{kind} Do Q")
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        names = " ".join(f"/Im_{k} {v} 0 R" for k, v in xobjects.items())
        kids.append(add((
            f"<< /Type /Page /Parent {root} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font} 0 R >> /XObject << {names} >> >> "
            f"/Contents {content} 0 R >>").encode()))
    bodies[catalog - 1] = f"<< /Type /Catalog /Pages {root} 0 R >>".encode()
    bodies[root - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
                        f"/Count {len(kids)} >>").encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(bodies, 1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(bodies) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(f"trailer\n<< /Size {len(bodies) + 1} /Root {catalog} 0 R >>\n"
               f"startxref\n{xref}\n%%EOF\n".encode())
    return bytes(out)


def build_audit_pdf(model: dict | None = None, *, extra: list[dict] | None = None) -> bytes:
    """One audit (and optionally more blocks appended, as a multi-degree print)."""
    pages = audit_layout(model)
    for other in extra or []:
        pages += audit_layout(other)
    return render_pdf(pages)


def variant(**changes) -> dict:
    model = copy.deepcopy(sas_core_model())
    model.update(changes)
    return model


__all__ = ["COURSE_ICON", "ICONS", "REQUIREMENT_ICON", "audit_layout", "build_audit_pdf",
           "major_model", "render_pdf", "sas_core_model", "variant"]
