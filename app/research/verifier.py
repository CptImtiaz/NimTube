"""Evidence verification: a fact is VERIFIED only if its quoted sentence is actually present on the fetched
source page (normalised / near-exact match) AND the claimed number appears inside that quote."""
from __future__ import annotations

import re
import unicodedata

NUM_RE = re.compile(r"\d[\d,\.\s]*\d|\d")


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ")
    text = re.sub(r"\[\d+\]", "", text)            # wiki footnote markers
    text = re.sub(r"[^\w\s\.\,\-%$']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def numbers_in(text: str) -> list[float]:
    out = []
    for m in NUM_RE.finditer(text):
        raw = m.group(0).replace(" ", "")
        # treat 1,234 as thousands; 1.234,5 rare - ignore
        if raw.count(",") and raw.count(".") <= 1:
            raw = raw.replace(",", "")
        try:
            out.append(float(raw.rstrip(".")))
        except ValueError:
            continue
    return out


def number_matches(value: float, text: str, tolerance: float = 0.006) -> bool:
    """True if value (or value expressed in thousands/millions/billions) appears in text."""
    if value is None:
        return True
    cands = numbers_in(text)
    scales = [1, 1e3, 1e6, 1e9, 1e12]
    low = text.lower()
    for c in cands:
        for sc in scales:
            if sc > 1 and not any(w in low for w in ("thousand", "million", "billion", "trillion", "bn", "mn", "k ")):
                continue
            v = c * sc
            if value == 0 and v == 0:
                return True
            if value and abs(v - value) / abs(value) <= tolerance:
                return True
    return False


def quote_in_page(quote: str, page_text: str, min_ratio: float = 0.9) -> bool:
    q, p = normalise(quote), normalise(page_text)
    if len(q) < 25:
        return False                 # too short to be meaningful evidence
    if q in p:
        return True
    # near-exact: sliding window over page words, ≥90% of quote tokens in order-insensitive window
    qt = q.split()
    pt = p.split()
    n = len(qt)
    if n == 0 or len(pt) < n:
        return False
    need = set(qt)
    first_tokens = {qt[0], qt[1] if n > 1 else qt[0]}
    for i in range(len(pt) - n + 1):
        if pt[i] not in first_tokens:
            continue
        window = set(pt[i:i + n + 3])
        if len(need & window) / len(need) >= min_ratio:
            return True
    return False


def verify_fact(fact: dict, page: dict) -> tuple[bool, str]:
    if page.get("status") != "ok":
        return False, f"source unavailable ({page.get('status')})"
    quote = fact.get("quote") or ""
    if not quote_in_page(quote, page["text"]):
        return False, "quote not found on source page"
    if fact.get("value") is not None and not number_matches(float(fact["value"]), quote):
        return False, "claimed value not present in quote"
    return True, "verified"


def repair_quote(name: str, value, page_text: str) -> str | None:
    """When a model paraphrases instead of copying, find the REAL sentence on the page that names the entry and
    contains the claimed number. Returns that verbatim sentence (so the evidence is still genuinely on the page)."""
    if value is None or not page_text:
        return None
    toks = [t for t in normalise(name).split() if t not in ("the", "a", "an", "of") and len(t) > 2]
    if not toks:
        return None
    sentences = re.split(r"(?<=[\.\!\?])\s+|\n+|\s\|\s", page_text)
    best = None
    for sent in sentences:
        sent = sent.strip()
        if not (25 <= len(sent) <= 400):
            continue
        ns = normalise(sent)
        if sum(t in ns.split() for t in toks) < max(1, (len(toks) + 1) // 2):
            continue
        if number_matches(float(value), sent):
            if best is None or len(sent) < len(best):
                best = sent
    return best
