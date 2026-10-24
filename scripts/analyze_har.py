"""Sanitized endpoint inventory from a discovery HAR (Phase 7.3 Step 2).

    python scripts/analyze_har.py data/private/har/<file>.har

Prints, per endpoint SHAPE: method, host, path pattern (digits -> {n},
long tokens -> {id}), query parameter NAMES, status codes, response content
types, size range and count. It NEVER prints header values, cookies, query
parameter values, POST bodies or response bodies - a HAR holds live session
material and, for DN, a student's record.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from urllib.parse import parse_qsl, urlsplit

STATIC = re.compile(r"\.(css|js|png|gif|jpe?g|svg|ico|woff2?|ttf|map)(\?|$)", re.I)


def shape(path: str) -> str:
    path = re.sub(r"/[0-9A-Fa-f-]{16,}(?=/|$)", "/{id}", path)
    return re.sub(r"\d+", "{n}", path)


def main() -> int:
    har = json.load(open(sys.argv[1], encoding="utf-8"))
    rows: dict[tuple, dict] = defaultdict(lambda: {"status": set(), "types": set(),
                                                   "sizes": [], "count": 0, "params": set(),
                                                   "body": False})
    static = 0
    for entry in har["log"]["entries"]:
        req, res = entry["request"], entry["response"]
        url = urlsplit(req["url"])
        if STATIC.search(url.path):
            static += 1
            continue
        key = (req["method"], url.netloc, shape(url.path))
        row = rows[key]
        row["count"] += 1
        row["status"].add(res.get("status"))
        row["types"].add((res.get("content", {}).get("mimeType") or "").split(";")[0])
        row["sizes"].append(res.get("content", {}).get("size") or 0)
        row["params"] |= {k for k, _ in parse_qsl(url.query, keep_blank_values=True)}
        row["body"] |= bool(req.get("postData"))
    print(f"entries: {len(har['log']['entries'])}  static assets skipped: {static}")
    for (method, host, path), r in sorted(rows.items(), key=lambda kv: (kv[0][1], kv[0][2])):
        sizes = r["sizes"]
        print(f"{method:6} {host}{path}  x{r['count']}  status={sorted(r['status'])}  "
              f"type={sorted(t for t in r['types'] if t)}  size={min(sizes)}-{max(sizes)}B  "
              f"params={sorted(r['params'])}{'  +body' if r['body'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
