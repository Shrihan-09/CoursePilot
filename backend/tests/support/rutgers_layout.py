"""SANITIZED Rutgers-layout transcript generator for Phase 7.0 tests.

The STRUCTURE was learned from one real Rutgers NB unofficial transcript; the
CONTENT here is entirely fabricated - identity, student number, record date,
courses, sections, grades and averages are all invented. Nothing was copied
from the source document, which never entered the repository.

What it reproduces:

  * iText-style "encryption": permissions only, EMPTY user password;
  * a monospaced fixed-width table - TITLE SCH DEPT CRS SUP SEC CRED PR GRADE -
    with course codes as three columns ("98 263 101"), never "98:263:101";
  * page 1 in TWO COLUMNS, every line its own positioned text run. The
    generator writes the left column then the right (`order="columns"`, as the
    real generator does) or both columns line by line, top to bottom
    (`order="interleaved"`: the reading-order hazard);
  * term heading "<Season> <Year> SCHOOL OF ...", MAJOR:/CLASS: lines, an
    optional SUB TOPIC continuation line, TOTAL CREDITS ATTEMPTED, then
    DEGREE CREDITS EARNED / TERM AVG / CUMULATIVE AVG (blank for a term that
    is not graded yet);
  * a diagonal watermark "UNOFFICIAL COPY OF <name>" drawn as rotated text;
  * "Last Term Information" continuing onto page 2, and the end marker.
"""

from __future__ import annotations

import io

FAKE_NAME = "TESTFIXTURE PERSONA"
FAKE_STUDENT_NUMBER = "000000000"
HEADER = "              TITLE             SCH  DEPT CRS  SUP SEC  CRED  PR GRADE"
END_MARKER = "                      *** END OF TRANSCRIPT ***"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def course_line(title: str, code: str, section: str, credits: str, grade: str = "",
                supplement: str = "", prefix: str = "") -> str:
    """One fixed-width course row, columns as the real layout spaces them."""
    sch, dept, crs = code.split(":")
    return (f"  {title[:20]:<30}{sch}  {dept}  {crs}  {supplement:>3} {section:>3}   "
            f"{credits:>4}  {prefix:>2}    {grade}").rstrip()


def term_block(season: str, year: int, rows: list[tuple], *, term_avg: str | None,
               cum_avg: str | None, attempted: str | None = None,
               sub_topic_after: int | None = None) -> list[str]:
    """`rows` = (title, code, section, credits[, grade[, supplement[, prefix]]]).
    TOTAL CREDITS ATTEMPTED is the rows' sum unless `attempted` overrides it."""
    total = attempted or f"{sum(float(r[3]) for r in rows):.1f}"
    lines = [f"{season:<6} {year} SCHOOL OF EXAMPLE STUDIES",
             "MAJOR: UNDECLARED (EXAMPLE)", "CLASS: 99"]
    for i, r in enumerate(rows):
        lines.append(course_line(*r))
        if sub_topic_after == i:
            lines.append("    SUB TOPIC: EXAMPLE TOPIC, ANOTHER TOPIC")
    lines.append(f"  TOTAL CREDITS ATTEMPTED:                      {total:>8}")
    if term_avg is None:
        lines.append("DEGREE CREDITS EARNED:       TERM AVG:        CUMULATIVE AVG:")
    else:
        lines.append(f"DEGREE CREDITS EARNED: {total}   TERM AVG: {term_avg}  "
                     f"CUMULATIVE AVG: {cum_avg}")
    lines.append("                                                                     .")
    return lines


def last_term_block(credit_hours: str, gpa_credits: str, points: str,
                    cum_credits: str) -> list[str]:
    return ["Last Term Information",
            f"  LAST TERM CREDIT   HOURS:                 {credit_hours}",
            f"  LAST TERM CREDITS IN GPA:                 {gpa_credits}",
            f"  LAST TERM POINTS  IN GPA:                 {points}",
            f"  LAST TERM CUMULATIVE CREDITS IN GPA:      {cum_credits}",
            "                     ** CONTINUED ON NEXT PAGE **"]


def _positioned_pdf(pages: list[list[tuple[float, float, str]]], watermark: bool) -> bytes:
    """A PDF whose page content is one positioned Courier run per tuple."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog_id, pages_id = add(b""), add(b"")
    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    bold_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    kids = []
    for runs in pages:
        ops = []
        if watermark:
            for i, text in enumerate(("UNOFFICIAL COPY OF", FAKE_NAME)):
                ops += ["BT", "0.85 g", "/F2 48 Tf",
                        f"0.866 0.5 -0.5 0.866 {99 + 54 * i} {380 - 130 * i} Tm",
                        f"({_escape(text)}) Tj", "ET"]
        ops.append("0 g")
        for x, y, text in runs:
            ops += ["BT", "/F1 7 Tf", f"1 0 0 1 {x} {y} Tm", f"({_escape(text)}) Tj", "ET"]
        stream = "\n".join(ops).encode("latin-1")
        content_id = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add((f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 792 612] "
                         f"/Resources << /Font << /F1 {font_id} 0 R /F2 {bold_id} 0 R >> >> "
                         f"/Contents {content_id} 0 R >>").encode()))
    objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    objects[pages_id - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
                             f"/Count {len(kids)} >>").encode()
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def _permissions_only(pdf: bytes, user_password: str = "") -> bytes:
    """Encrypt as the real generator does: an EMPTY user password (opens with
    no prompt) plus an owner password restricting permissions. A non-empty
    `user_password` makes a genuinely password-protected document."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    writer.encrypt(user_password=user_password, owner_password="fixture-owner-password",
                   algorithm="RC4-128")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def build_rutgers_pdf(left: list[str], right: list[str], page2: list[str] | None = None, *,
                      order: str = "columns", encrypt: bool = True, user_password: str = "",
                      watermark: bool = True, end_marker: bool = True) -> bytes:
    """A SYNTHETIC two-column Rutgers-layout transcript. `left` and `right` are
    the two columns' lines below the header block; `page2` continues the
    document (the end marker is appended unless `end_marker=False`)."""
    header = [f"    RECORD OF: {FAKE_NAME}", f"STUDENT NUMBER: {FAKE_STUDENT_NUMBER}",
              "   RECORD DATE: 01/01/30      PAGE: 1"]
    runs_left = [(10, 570 - 20 * i, t) for i, t in enumerate(header)]
    runs_left.append((10, 486, HEADER))
    runs_left += [(10, 466 - 10 * i, t) for i, t in enumerate(left)]
    runs_right = [(430, 510, HEADER)] + [(430, 490 - 10 * i, t) for i, t in enumerate(right)]
    if order == "columns":
        page1 = runs_left + runs_right
    elif order == "interleaved":
        page1 = sorted(runs_left + runs_right, key=lambda r: (-r[1], r[0]))
    else:
        raise ValueError(order)
    tail = list(page2 or []) + ([END_MARKER] if end_marker else [])
    pages = [page1]
    if tail:
        pages.append([(10, 570, f"    RECORD OF: {FAKE_NAME}"),
                      (10, 550, f"STUDENT NUMBER: {FAKE_STUDENT_NUMBER}"), (10, 486, HEADER)]
                     + [(10, 466 - 12 * i, t) for i, t in enumerate(tail)])
    pdf = _positioned_pdf(pages, watermark)
    return _permissions_only(pdf, user_password) if encrypt else pdf


def standard_fixture(codes: dict[str, str]) -> dict:
    """The default fabricated history over the given course codes (keys A..E):

    left : Fall 2027  A(3.0 A, sub-topic)  B(4.0 B+)          avg 3.429 / 3.429
           Spring 2028 C(3.0 F)  D(3.0 PA)                     avg 0.000 / 2.400
    right: Fall 2028  E(4.0 C+)  C(3.0 B)  <- the retake of C  avg 2.429 / 2.429
           Spring 2029 A(3.0)  B(4.0) <- no grades yet          blank
           Last Term Information (continues on page 2)
    """
    c = codes
    left = (term_block("Fall", 2027, [("EXAMPLE COURSE A", c["A"], "01", "3.0", "A"),
                                      ("EXAMPLE COURSE B", c["B"], "H2", "4.0", "B+")],
                       term_avg="3.429", cum_avg="3.429", sub_topic_after=0)
            + term_block("Spring", 2028, [("EXAMPLE COURSE C", c["C"], "7Q", "3.0", "F"),
                                          ("EXAMPLE COURSE D", c["D"], "02", "3.0", "PA")],
                         term_avg="0.000", cum_avg="2.400"))
    right = (term_block("Fall", 2028, [("EXAMPLE COURSE E", c["E"], "05", "4.0", "C+"),
                                       ("EXAMPLE COURSE C", c["C"], "09", "3.0", "B")],
                        term_avg="2.429", cum_avg="2.429")
             + term_block("Spring", 2029, [("EXAMPLE COURSE A", c["A"], "03", "3.0"),
                                           ("EXAMPLE COURSE B", c["B"], "11", "4.0")],
                          term_avg=None, cum_avg=None)
             + last_term_block("7.0", "7.0", "17.0", "20.0"))
    page2 = ["  LAST TERM CUMULATIVE POINTS  IN GPA:      48.5", "     ."]
    expected = [("20279", c["A"], "01", "3.0", "A"), ("20279", c["B"], "H2", "4.0", "B+"),
                ("20281", c["C"], "7Q", "3.0", "F"), ("20281", c["D"], "02", "3.0", "PA"),
                ("20289", c["E"], "05", "4.0", "C+"), ("20289", c["C"], "09", "3.0", "B"),
                ("20291", c["A"], "03", "3.0", None), ("20291", c["B"], "11", "4.0", None)]
    return {"left": left, "right": right, "page2": page2, "expected": expected,
            "reported_cumulative": "2.429"}
