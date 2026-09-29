"""Phase 6.1: observe what Rutgers systems expose, anonymously and politely.

INVESTIGATION TOOLING - not production architecture. It never logs in, never
submits a form, never sends a cookie it did not receive in the same response,
and stops at the first authentication boundary it meets. Every URL it touches
is either taken from an official Rutgers page already archived by CoursePilot
(the catalog links nbdn.rutgers.edu, sims.rutgers.edu/csp/, webreg.rutgers.edu
and classes.rutgers.edu/soc/) or found inside a public page this script
fetched - never guessed.

Outputs:
  data/raw/probes/                         raw public pages (gitignored)
  docs/investigations/evidence/phase-6-1-probes.json   the observations

Usage:
  python scripts/probe_rutgers_sources.py
"""

from __future__ import annotations

import json
import pathlib
import re
import time
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import httpx

UA = "CoursePilot/0.1 (Rutgers student academic planning project; investigation)"
ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "probes"
EVIDENCE = ROOT / "docs" / "investigations" / "evidence" / "phase-6-1-probes.json"
DELAY_SECONDS = 1.5

CATALOG_HOSTS = {
    "2026-2027": "https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu",
    "2025-2026": "https://newbrunswick-25-26-undergrad-archive.catalogs.rutgers.edu",
}
# The 11 school paths linked from the archived catalog page (root_26-27.html).
SCHOOLS = ["ejbsppp", "eng", "honors-college", "mgsa", "pharmacy", "rbsnb",
           "sas", "sci", "sebs", "smlr", "ssw"]
# Official system entry points, as linked by the Rutgers catalog itself.
SYSTEMS = {
    "degree_navigator": "http://nbdn.rutgers.edu",
    "csp": "https://sims.rutgers.edu/csp/",
    "webreg": "http://webreg.rutgers.edu",
    "soc_app": "https://classes.rutgers.edu/soc/",
}
LOGIN_MARKERS = re.compile(
    r"(cas\.rutgers\.edu|/cas/login|idp\.rutgers\.edu|shibboleth|saml|netid|"
    r"password|login|sign in|duo)", re.I)
NUXT = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)

observations: dict = {"generated_at": datetime.now(UTC).isoformat(), "user_agent": UA,
                      "systems": {}, "catalog": {}, "soc": {}}


def get(client: httpx.Client, url: str) -> httpx.Response | None:
    time.sleep(DELAY_SECONDS)
    try:
        return client.get(url)
    except httpx.HTTPError as exc:
        print(f"  {url}: {type(exc).__name__}: {exc}")
        return None


def chain(response: httpx.Response) -> list[dict]:
    hops = [{"status": r.status_code, "url": str(r.url)} for r in response.history]
    hops.append({"status": response.status_code, "url": str(response.url)})
    return hops


def describe_system(client, name, url) -> None:
    """One anonymous GET. Record where it lands; never go past a login."""
    print(f"[{name}] {url}")
    response = get(client, url)
    if response is None:
        observations["systems"][name] = {"entry": url, "error": "unreachable"}
        return
    text = response.text
    hosts = sorted({urlparse(h["url"]).netloc for h in chain(response)})
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', text)
    forms = re.findall(r'<form[^>]*action="([^"]*)"', text, re.I)
    observations["systems"][name] = {
        "entry": url,
        "redirect_chain": chain(response),
        "hosts_visited": hosts,
        "final_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
        "title": (re.search(r"<title>(.*?)</title>", text, re.I | re.S) or [None, None])[1],
        "login_markers": sorted({m.lower() for m in LOGIN_MARKERS.findall(text)})[:12],
        "password_field": bool(re.search(r'type="password"', text, re.I)),
        "form_actions": forms[:5],
        "script_srcs": scripts[:20],
        "server_rendered_text_chars": len(re.sub(r"<[^>]+>|\s+", " ", text)),
    }
    (RAW / f"system_{name}.html").write_text(text, encoding="utf-8")


def soc_endpoints(client) -> None:
    """Which JSON endpoints does the public SOC app's OWN code reference?"""
    page = (RAW / "system_soc_app.html")
    if not page.exists():
        return
    html = page.read_text(encoding="utf-8")
    base = observations["systems"]["soc_app"]["redirect_chain"][-1]["url"]
    found: dict[str, list[str]] = {}
    sources = [("page", html)]
    for src in re.findall(r'<script[^>]+src="([^"]+)"', html)[:12]:
        full = urljoin(base, src)
        if urlparse(full).netloc.endswith("rutgers.edu"):
            r = get(client, full)
            if r is not None and r.status_code == 200:
                sources.append((full, r.text))
    for label, text in sources:
        for ep in set(re.findall(r'[\w./-]*api/[\w./-]+\.json', text)):
            found.setdefault(ep, []).append(label)
    observations["soc"]["endpoints_referenced_by_soc_app"] = found
    print(f"[soc] endpoints referenced: {sorted(found)}")

    # Probe ONLY what the app itself references, with the parameters the
    # existing, verified courses.json call uses. Record shape, not payload.
    probes = {}
    for ep in sorted(found):
        if "courses.json" in ep:
            continue                      # already ingested; 30 MB; archived
        url = urljoin("https://classes.rutgers.edu/soc/", ep.split("soc/")[-1])
        url += "?year=2026&term=9&campus=NB"
        r = get(client, url)
        if r is None:
            continue
        entry = {"url": url, "status": r.status_code,
                 "content_type": r.headers.get("content-type"), "bytes": len(r.content)}
        try:
            data = r.json()
            entry["json_type"] = type(data).__name__
            entry["length"] = len(data) if hasattr(data, "__len__") else None
            entry["sample"] = (data[:3] if isinstance(data, list)
                               else {k: data[k] for k in list(data)[:5]})
            (RAW / f"soc_{pathlib.Path(ep).stem}_2026_9_NB.json").write_text(
                json.dumps(data), encoding="utf-8")
        except ValueError:
            entry["json_type"] = None
        probes[ep] = entry
        print(f"  {url} -> {r.status_code} {entry.get('json_type')} len={entry.get('length')}")
    observations["soc"]["probes"] = probes


def catalog_discovery(client) -> None:
    """Program pages per school per catalog year, from each school's page."""
    for year, host in CATALOG_HOSTS.items():
        per_school = {}
        for school in SCHOOLS:
            url = f"{host}/schools/{school}"
            r = get(client, url)
            if r is None:
                per_school[school] = {"error": "unreachable"}
                continue
            html = r.text
            paths = sorted(set(re.findall(
                rf"/schools/{re.escape(school)}/program-listing/[a-z0-9-]+", html)))
            other = sorted(set(re.findall(r"/schools/[a-z0-9-]+/program-listing/[a-z0-9-]+", html))
                           - set(paths))
            (RAW / f"catalog_school_{school}_{year}.html").write_text(html, encoding="utf-8")
            per_school[school] = {
                "status": r.status_code, "bytes": len(r.content),
                "has_nuxt_payload": bool(NUXT.search(html)),
                "program_listing_paths": paths,
                "foreign_program_paths": other[:10],
            }
            print(f"[catalog {year}] {school:15} {r.status_code} programs={len(paths)}")
        observations["catalog"][year] = per_school


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for name, url in SYSTEMS.items():
            describe_system(client, name, url)
        soc_endpoints(client)
        catalog_discovery(client)
    EVIDENCE.write_text(json.dumps(observations, indent=1, default=str), encoding="utf-8")
    print(f"\nevidence -> {EVIDENCE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
