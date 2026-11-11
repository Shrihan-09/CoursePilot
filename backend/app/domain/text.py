"""Display text for course titles.

Rutgers' Schedule of Classes publishes many titles in ALL CAPS
("HISTORY OF THE PRINTED BOOK", "INTRO TO MUSIC"). Students should never read a
shouted title, so every course title the API returns passes through
`display_title`: an ALL-CAPS title becomes title case ("Structure of
Information", "Intro to Music"); a title that already has lowercase letters
(catalog titles such as "Stories of the Sea in Film") is
returned unchanged. Stored data is never rewritten.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator

#: Lowercase inside a title (never as its first or last word).
_SMALL = frozenset({"a", "an", "and", "as", "at", "but", "by", "en", "for", "from", "in",
                    "into", "nor", "of", "on", "or", "per", "the", "to", "via", "vs", "with"})
#: Kept as written: Roman numerals and common academic abbreviations.
_UPPER = frozenset({"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII",
                    "AI", "AIDS", "CS", "DNA", "EMS", "ESL", "EU", "GIS", "HIV", "ID", "IT",
                    "LGBT", "LGBTQ", "LGBTQ+", "NJ", "NYC", "RNA", "SAS", "STEM", "UK", "UN",
                    "US", "USA", "U.S.", "U.S.A.", "UX", "UI", "3D", "2D"})
_TOKEN = re.compile(r"[A-Za-z0-9.'’+&]+|[^A-Za-z0-9.'’+&]+")


def _word(word: str, *, first: bool, last: bool) -> str:
    bare = word.strip(".")
    if word in _UPPER or bare in _UPPER or re.fullmatch(r"(?:[A-Z]\.){2,}", word):
        return word
    if any(ch.isdigit() for ch in word):
        return word                    # "C++", "3D", "101" keep their form
    low = word.lower()
    if low in _SMALL and not first and not last:
        return low
    # "WOMEN'S" -> "Women's"; "O'NEILL" -> "O'Neill"
    parts = re.split(r"(['’])", low)
    out = parts[0][:1].upper() + parts[0][1:]
    for i in range(1, len(parts), 2):
        tail = parts[i + 1]
        if len(tail) > 1:              # O'Neill, not Women'S
            tail = tail[:1].upper() + tail[1:]
        out += parts[i] + tail
    return out


def display_title(title: str) -> str:
    """Title case for an ALL-CAPS course title; anything else unchanged."""
    if not title or not any(ch.isalpha() for ch in title) or any(ch.islower() for ch in title):
        return title
    tokens = _TOKEN.findall(title.strip())
    words = [i for i, t in enumerate(tokens) if re.match(r"[A-Za-z0-9]", t)]
    out = []
    after_colon = False
    for i, tok in enumerate(tokens):
        if i not in words:
            out.append(tok)
            after_colon = after_colon or ":" in tok
            continue
        # Hyphenated words are cased per part ("SOCIO-CULTURAL" -> "Socio-Cultural").
        out.append(_word(tok, first=(i == words[0] or after_colon), last=(i == words[-1])))
        after_colon = False
    text = "".join(out)
    return re.sub(r"(?<=-)([a-z])", lambda m: m.group(1).upper(), text)


def _optional(title: str | None) -> str | None:
    return display_title(title) if title else title


#: A course title as students see it (see module docstring).
CourseTitle = Annotated[str, AfterValidator(display_title)]
OptionalCourseTitle = Annotated[str | None, AfterValidator(_optional)]

__all__ = ["CourseTitle", "OptionalCourseTitle", "display_title"]
