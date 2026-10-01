"""Synthetic Coursedog catalog pages for registry tests.

Real archived pages live in data/raw/ (gitignored, ~400 KB each), so unit
tests build the smallest page that has the same SHAPE: a `__NUXT_DATA__`
script holding a flat array whose objects refer to each other by index -
navigation groups and links - plus HTML prose blocks.
"""

from __future__ import annotations

import json

FILLER = (" This sentence pads the block past the prose-block length threshold so that"
          " the extractor treats it as page content rather than a navigation label,"
          " exactly as the real Coursedog payload separates content from labels.")


def page(nav: dict[str, dict[str, list[tuple[str, str, str]]]], blocks: list[str]) -> str:
    """nav: {school_slug: {section label: [(title, url, page_id), ...]}}."""
    flat: list = []

    def put(value) -> int:
        flat.append(value)
        return len(flat) - 1

    for school, sections in nav.items():
        section_ids = []
        for label, links in sections.items():
            link_ids = [put({"type": "link", "label": t, "url": u, "pageId": pid,
                             "linkType": "internal"}) for t, u, pid in links]
            section_ids.append(put({"type": "group", "label": label, "url": "",
                                    "children": link_ids}))
        put({"type": "group", "label": school.upper(), "url": f"/schools/{school}",
             "children": section_ids})
    for block in blocks:
        put(block + FILLER)
    payload = json.dumps(flat)
    return ("<html><body><div id='app'></div>"
            f'<script type="application/json" id="__NUXT_DATA__">{payload}</script>'
            "</body></html>")


SAS_NAV = {"sas": {"Programs": [
    ("Mathematics 640", "/schools/sas/program-listing/mathematics-640", "pg-math"),
    ("Computer Science 198", "/schools/sas/program-listing/computer-science-198", "pg-cs"),
], "Policies": [("Academic Integrity", "/schools/sas/academic-integrity", "pg-ai")]}}

MATH_BLOCKS = [
    "<h3>Major Requirements</h3><p>The requirements for a math major are as follows:</p>"
    "<p><strong>Option A, Standard Mathematics (Curriculum Code 640)</strong></p>"
    "<p>to complete the standard mathematics major a student must pass eight courses.</p>"
    "<p><strong>Option B, Honors Mathematics</strong></p><p>honors text</p>"
    "<p><strong>Statistics-Mathematics Interdisciplinary Major (Curriculum Code 961)</strong></p>",
    "<h3>Minor Requirements</h3><p>A minor in mathematics.</p>"
    "<h3>Certificate in Applied Mathematics</h3><p>certificate text</p>"
    "<h3>Entry Requirements for the Major</h3><p>admission text</p>",
]
