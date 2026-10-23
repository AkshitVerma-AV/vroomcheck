"""Narrative cleaning shared by embedding and keyword extraction.

NHTSA call-centre narratives are wrapped in boilerplate ("THE CONTACT OWNS A
2019 FORD EXPLORER ... THE MANUFACTURER WAS NOT NOTIFIED"). Left in, that
boilerplate dominates sentence embeddings and every complaint looks alike, so
we strip it before embedding.
"""
from __future__ import annotations

import re

_BOILERPLATE = [
    r"the contact owns? an? \d{4} [a-z0-9 \-/]+?\.",
    r"the contact (stated|indicated|mentioned|related) that",
    r"the (contact|consumer|caller) (was )?(not )?(contacted|notified|informed)[^.]*\.",
    r"the (manufacturer|dealer|local dealer|independent mechanic)[^.]*(was|were) (not )?(contacted|notified|informed|made aware)[^.]*\.",
    r"the (approximate )?failure mileage was[^.]*\.",
    r"the vin was (not )?(available|provided)[^.]*\.",
    r"\*[a-z]{1,3}\b",          # analyst initials like *TR *JB
    r"\b(tl|jb|tt|dt|ld|jc|jm|ad|kmj|mlr)\*",
    r"information redacted pursuant to the freedom of information act \(foia\), 5 u\.s\.c\. 552\(b\)\(6\)",
    r"\bxxx+\b",
]
_BOILER_RE = re.compile("|".join(_BOILERPLATE))
_WS = re.compile(r"\s+")


def clean_narrative(text: str | None, max_chars: int = 1200) -> str:
    if not text:
        return ""
    t = text.lower()
    t = _BOILER_RE.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    return t[:max_chars]


_RECALL_INTRO = re.compile(r"^.{0,250}?\bis recalling\b.*?(\.\s|$)", re.I | re.S)


def clean_recall(text: str | None, max_chars: int = 1500) -> str:
    """Drop the 'Acme is recalling certain 2019-2021 Foo vehicles ...' opener that every
    Part 573 summary starts with; the defect itself is described after it."""
    t = _RECALL_INTRO.sub("", text or "", count=1)
    return clean_narrative(t, max_chars)


# Domain stop-words for cluster labels (generic complaint vocabulary).
LABEL_STOPWORDS = {
    "vehicle", "car", "contact", "dealer", "dealership", "manufacturer", "issue", "problem",
    "stated", "said", "told", "time", "times", "just", "like", "did", "does", "got", "went",
    "mph", "miles", "mileage", "approximately", "repair", "repaired", "fixed", "owner",
    "failure", "failed", "recall", "nhtsa", "number", "campaign", "remedy", "notified",
    "ford", "chevrolet", "toyota", "honda", "nissan", "jeep", "ram", "tesla", "kia", "hyundai",
    "subaru", "dodge", "chrysler", "gmc", "bmw", "volkswagen", "mazda", "buick", "cadillac",
    "lexus", "acura", "audi", "mercedes", "benz", "volvo", "lincoln", "infiniti", "mitsubishi",
    "model", "year", "new", "day", "days", "week", "weeks", "month", "months", "later",
    "again", "also", "would", "could", "still", "back", "even", "one", "two", "get", "going",
    "taken", "experienced", "received", "notification", "reasonable", "exceeded", "consumer",
    "safety", "diagnosed", "available", "informed", "independent", "mechanic", "contacted",
}


def strip_recall_intro(text: str | None) -> str:
    """Same as clean_recall but keeps the original casing, for display."""
    return _RECALL_INTRO.sub("", text or "", count=1).strip()
