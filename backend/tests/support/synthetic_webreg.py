"""Invented positioned WebReg-style PDFs. No real document content."""

from __future__ import annotations


def objects(*, term="Fall 2099", records=None, arrangement=None):
    result = [
        (40, 765, "WebReg | View / Print Schedule"),
        (40, 740, term),
        (40, 720, "School 01 - Total Credits 12.0"),
    ]
    for x, day in [
        (110, "Monday"),
        (210, "Tuesday"),
        (310, "Wednesday"),
        (410, "Thursday"),
        (510, "Friday"),
    ]:
        result.append((x, 680, day))
    result += (
        records
        if records is not None
        else [(170, 610, "01:999:101:01-91234 (4.0)"), (470, 580, "01:999:101:01-91234 (4.0)")]
    )
    if arrangement is not None:
        result.append((40, 300, "By Arrangement, Off-campus, or Other Courses"))
        for i, record in enumerate(arrangement):
            result.extend(
                [
                    (40, 275 - i * 50, record),
                    (40, 260 - i * 50, "Credits: 3.0 Location: Asynchronous Content (Online)"),
                ]
            )
    return result


def extracted_layout(items):
    return [
        {
            "width": 612,
            "height": 792,
            "fragments": [{"x": x, "y": y, "text": text, "font_size": 10} for x, y, text in items],
        }
    ]


def build_schedule_pdf(items=None, *, raster_only=False):
    items = objects() if items is None else items
    bodies = []

    def add(body):
        bodies.append(body)
        return len(bodies)

    catalog, pages = add(b""), add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    ops = []
    for x, y, text in [] if raster_only else items:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"BT /F1 9 Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET")
    if raster_only:
        # Painted geometry has no text objects, exactly the no-text-layer path.
        ops.append("0.5 g 40 40 500 700 re f")
    stream = "\n".join(ops).encode("ascii")
    content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    page = add(
        (
            f"<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>"
        ).encode()
    )
    bodies[catalog - 1] = f"<< /Type /Catalog /Pages {pages} 0 R >>".encode()
    bodies[pages - 1] = f"<< /Type /Pages /Kids [{page} 0 R] /Count 1 >>".encode()
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(bodies, 1):
        offsets.append(len(output))
        output.extend(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(bodies) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        (
            f"trailer\n<< /Size {len(bodies) + 1} /Root {catalog} 0 R >>"
            f"\nstartxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(output)
