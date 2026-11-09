"""Which planned course a message means ("Art History", "CS 206", "physics").

Deterministic and conservative. It only ever picks among courses a tool
returned (the student's own plan), so a miss means "ask which one", never a
guess outside the plan.
"""

from __future__ import annotations

import re

from app.services.assistant.contracts import COURSE_KEY

#: Common short names -> Rutgers subject codes (NB). Used only to narrow a
#: "CS 206"-style mention to plan courses; never to name a course.
SUBJECT_ALIASES = {
    "cs": "198", "comp sci": "198", "computer science": "198", "math": "640", "calc": "640",
    "calculus": "640", "physics": "750", "phys": "750", "chem": "160", "chemistry": "160",
    "expos": "355", "writing": "355", "econ": "220", "economics": "220", "psych": "830",
    "psychology": "830", "stats": "960", "statistics": "960", "soc": "920",
    "sociology": "920", "art history": "082", "history": "510", "philosophy": "730",
    "phil": "730", "bio": "119", "biology": "119", "linguistics": "615", "music": "700",
}
_WORD_ALIASES = {"calc": "calculus", "intro": "introduction", "phys": "physics",
                 "chem": "chemistry", "stats": "statistics", "econ": "economics",
                 "psych": "psychology", "bio": "biology", "hist": "history", "expos": "expository",
                 "comp": "computer", "sci": "science", "ii": "ii", "2": "ii", "1": "i"}
_STOP = {"the", "and", "for", "take", "taking", "want", "dont", "don't", "not", "course",
         "class", "this", "that", "what", "better", "know", "but", "with", "something",
         "rather", "would", "instead", "spring", "fall", "summer", "winter", "semester",
         "term", "next", "make", "move", "swap", "drop", "remove", "into", "from", "lighter",
         "online", "options", "option", "why", "is", "in", "to", "of", "a", "an", "my", "me",
         "do", "it", "i", "i'd", "i'm", "id", "im", "again", "fail", "if", "happens"}


def _norm_words(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return [_WORD_ALIASES.get(w, w) for w in words]


def _roman(words: list[str]) -> list[str]:
    return ["ii" if w == "2" else "i" if w == "1" else w for w in words]


def resolve(text: str, candidates: list[dict]) -> list[dict]:
    """The best-matching candidates ({course, title, term_code, ...}); [] if
    nothing matches, several if it is a tie."""
    keys = COURSE_KEY.findall(text)
    if keys:
        hit = [c for c in candidates if c["course"] in keys]
        if hit:
            return hit
    low = text.lower()
    for alias, subject in sorted(SUBJECT_ALIASES.items(), key=lambda x: -len(x[0])):
        m = re.search(rf"\b{re.escape(alias)}\.? ?(\d{{3}})\b", low)
        if m:
            hit = [c for c in candidates if c["course"].endswith(f"{subject}:{m.group(1)}")]
            if hit:
                return hit
    m = re.search(r"\b(\d{3})\b", low)
    if m:
        hit = [c for c in candidates if c["course"].endswith(f":{m.group(1)}")]
        if len(hit) == 1:
            return hit
    wanted = [w for w in _roman(_norm_words(text)) if w not in _STOP and len(w) > 1]
    if not wanted:
        return []
    scored = []
    for c in candidates:
        title = set(_roman(_norm_words(c.get("title") or "")))
        subject = c["course"].split(":")[1] if ":" in c["course"] else ""
        score = sum(2 if len(w) > 3 else 1 for w in wanted if w in title)
        for alias, code in SUBJECT_ALIASES.items():
            if code == subject and re.search(rf"\b{re.escape(alias)}\b", low):
                score += 2
        if score:
            scored.append((score, c))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [c for s, c in scored if s == best]


def plan_courses(plan: dict) -> list[dict]:
    return [{**c, "term_code": t["term_code"]} for t in plan.get("terms") or []
            for c in t.get("courses") or []]


__all__ = ["SUBJECT_ALIASES", "plan_courses", "resolve"]
