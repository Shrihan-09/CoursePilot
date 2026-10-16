"""SYNTHETIC transcript documents for Phase 7.0 tests.

Everything here is invented. No line, name, course history or layout below
comes from a real student or a real Rutgers transcript - no such sample was
available, and the parser's layout support is labelled UNVERIFIED for that
reason (`app.domain.transcript.PARSER_VERSION`). Course codes are either
real catalog codes present in the test database (so normalization can match
them) or the synthetic codes the test world creates.

`build_pdf` writes a minimal, valid, text-layer PDF by hand - one Helvetica
text object per page, one `Tj` per line - so the tests exercise the real
extractor (pypdf, in its child process) without a PDF-writing dependency.
"""

from __future__ import annotations


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(pages: list[list[str]], *, encrypt_marker: bool = False) -> bytes:
    """A PDF with one text line per entry of each page."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog_id = add(b"")              # placeholder, filled below
    pages_id = add(b"")
    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids = []
    for lines in pages:
        ops = ["BT", "/F1 10 Tf", "12 TL", "40 760 Td"]
        for line in lines:
            ops.append(f"({_escape(line)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content_id = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream
                         + b"\nendstream")
        page_id = add((f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
                       f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                       f"/Contents {content_id} 0 R >>").encode())
        kids.append(page_id)
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
    trailer = f"<< /Size {len(objects) + 1} /Root {catalog_id} 0 R"
    if encrypt_marker:
        # A trailer that DECLARES encryption. Enough for a reader to treat the
        # document as encrypted; nothing here is real cryptography.
        trailer += " /Encrypt << /Filter /Standard /V 1 /R 2 /O <00> /U <00> /P -4 >>" \
                   " /ID [<00112233445566778899aabbccddeeff> <00112233445566778899aabbccddeeff>]"
    out += f"trailer\n{trailer} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def transcript_lines(courses: dict[str, list[str]], *, transfer: list[str] = (),
                     exam: list[str] = (), cumulative_gpa: str | None = "3.412") -> list[str]:
    """A SYNTHETIC transcript page: a header, term blocks, optional transfer /
    exam sections, a cumulative GPA line. `courses` maps a term heading
    ("FALL 2024") to course lines ("01:198:111 INTRO COMPUTER SCI 4.0 A")."""
    lines = ["SYNTHETIC UNOFFICIAL TRANSCRIPT - TEST DATA ONLY",
             "NAME: TEST STUDENT          ID: 000000000"]
    if transfer:
        lines += ["TRANSFER CREDIT", *transfer]
    if exam:
        lines += ["ADVANCED PLACEMENT CREDIT", *exam]
    for heading, rows in courses.items():
        lines += [heading, *rows, "TERM CREDITS 7.0  TERM GPA 3.500"]
    if cumulative_gpa:
        lines.append(f"CUMULATIVE GPA {cumulative_gpa}")
    return lines
