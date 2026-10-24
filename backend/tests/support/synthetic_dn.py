"""SYNTHETIC copy of the real Degree Navigator audit PDF layout (Phase 7.3 Step 3).

Measured on the developer's own Chrome "Save as PDF" audit (never committed):
text layer; Chrome print header; "Degree Audit"; the "not an official"
disclaimer; requirement headings "Requirement R1 : Title [CODE]" with a status
ICON (an image, not text) on the same baseline; course rows
"<course> ( credits, Season YYYY, grade )" - numbered lists put TWO courses on
one baseline; Core rule text "{ ... [AHo] ( <course> ) }"; "Needs:" lines; a
course legend. Icon sizes/colours copy DN's legend images (check 13x9 blue,
x 13x9 red, current 10x8, planned 10x9, in-progress-or-planned 14x11).

Every value here is invented: subject 999, terms in 2098, made-up titles.
"""

from __future__ import annotations

ICONS = {  # name: (width, height, rgb) - DN legend semantics
    "completed": (13, 9, (0, 49, 99)),
    "incomplete": (13, 9, (132, 0, 0)),
    "current": (10, 8, (40, 120, 40)),
    "planned": (10, 9, (90, 90, 160)),
    "in_progress_or_planned": (14, 11, (200, 140, 0)),
}


def audit_pages():
    """[(lines, icons)] per page: lines = (x, y, text); icons = (x, y, kind)."""
    p1 = [
        (24, 770, "01/01/98, 9:00 AM Degree Navigator"),
        (24, 740, "Degree Audit"),
        (24, 724, "Your Audit Report"),
        (24, 708, "Rutgers, The State University of New Jersey"),
        (24, 690, "Degree Navigator is an advising tool; it is not an official transcript of your"),
        (24, 678, "academic record. Verification of degree requirements can only be certified"),
        (24, 666, "by an academic advisor."),
        (40, 630, "Requirement R1 : Invented Studies Core [AHo]"),
        (40, 614, "{ Invented Topics [AHo] ( 01:999:101 ) }"),
        (40, 598, "01:999:101 ( 3, Fall 2098, A)"),
        (40, 570, "Requirement R2 : Invented Writing [WCr]"),
        (40, 554, "01:999:102 ( 3, Spring 2098, B+)"),
        (40, 526, "Requirement R3 : Invented Quantitative Reasoning [QQ]"),
        (40, 510, "Needs: 1 more course to satisfy this requirement."),
    ]
    i1 = [(24, 630, "completed"), (24, 570, "completed"), (24, 526, "incomplete")]
    p2 = [
        (40, 740, "Requirement R4 : Invented Major Courses [MAJ]"),
        (40, 724, "1. 01:999:201 ( 4, Fall 2098, A) 2. 01:999:202 ( 4, Spring 2098, B)"),
        (40, 708, "3. 01:999:203 ( 4, Fall 2099, current) "
                  "4. 01:999:204 ( 3, Spring 2099, planned)"),
        (40, 680, "Requirement R5 : Invented Electives [ELE]"),
        (40, 664, "Needs: In Progress: Some requirements are in progress."),
        (40, 636, "Requirement R6 : Invented Contemporary Challenges [CCD]"),
        (40, 620, "{ Invented Seminar [CCD] ( 01:999:301 ) }"),
        (40, 592, "Course Legend"),
        (40, 576, "Completed - course has been successfully completed"),
        (40, 562, "Current - course is in progress this term"),
        (40, 548, "Planned - course is planned"),
        (40, 534, "In Progress or Planned - requirement depends on current or planned courses"),
    ]
    i2 = [(24, 740, "in_progress_or_planned"), (24, 708, "current"), (24, 680, "planned"),
          (24, 636, "incomplete")]
    return [(p1, i1), (p2, i2)]


def build_audit_pdf(pages=None) -> bytes:
    pages = audit_pages() if pages is None else pages
    bodies: list[bytes] = []

    def add(body: bytes) -> int:
        bodies.append(body)
        return len(bodies)

    catalog, root = add(b""), add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    images = {}
    for name, (w, h, rgb) in ICONS.items():
        pixels = bytes(rgb) * (w * h)
        images[name] = add(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
            b"/BitsPerComponent 8 /Length %d >>\nstream\n" % (w, h, len(pixels))
            + pixels + b"\nendstream")
    kids = []
    for lines, icons in pages:
        ops = []
        for x, y, text in lines:
            esc = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /F1 9 Tf 1 0 0 1 {x} {y} Tm ({esc}) Tj ET")
        for x, y, kind in icons:
            w, h, _ = ICONS[kind]
            ops.append(f"q {w} 0 0 {h} {x} {y} cm /Im_{kind} Do Q")
        stream = "\n".join(ops).encode("ascii")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        xobjects = " ".join(f"/Im_{k} {v} 0 R" for k, v in images.items())
        kids.append(add((
            f"<< /Type /Page /Parent {root} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font} 0 R >> /XObject << {xobjects} >> >> "
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


__all__ = ["ICONS", "audit_pages", "build_audit_pdf"]
