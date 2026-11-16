"""Recent news for a stock and whether it is the kind that attracts buyers or sellers.

Headlines come from public news RSS feeds (Google News first, Yahoo Finance as a fallback), for the last NEWS_DAYS days. Each headline is read by plain
keyword rules - an analyst upgrade, a profit beat, an order win, a buyback on the positive side; a downgrade, a probe, a profit fall, a senior exit on the
negative side - and the matched cue and the reason it matters are shown with it. This is a crude reader of headlines, not of the stories: it can miss
nuance and misread a headline, so the page shows the headlines themselves. No headlines found is "no data", never bad news. Fails soft everywhere.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

import requests

logger = logging.getLogger(__name__)

NEWS_DAYS = 14
MAX_ITEMS = 8
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Accept": "application/rss+xml, application/xml, text/xml, */*"}

# (category, regex, why it matters). First match wins inside a side; positive cues are checked before negative ones only if no negative cue matched.
POSITIVE = [
    ("analyst", r"\b(upgrad\w*|raises? (its )?(target|price target)|target price (raised|hiked|increased)|(price )?target (raised|hiked)|initiates? (coverage )?with (a )?buy|outperform|overweight|top pick|buy rating|maintains? buy)\b",
     "an analyst upgrade or a higher target price brings in buyers who follow ratings"),
    ("results", r"\b(profit (rises?|jumps?|surges?|soars?|climbs?|grows?|up)|net profit (up|rises?|jumps?|surges?)|record (profit|revenue|earnings|sales|quarter)|beats? (estimates|expectations|street)|better[- ]than[- ]expected|strong (q[1-4]|results|earnings|numbers)|revenue (rises?|jumps?|grows?)|margin expan\w*)\b",
     "better-than-expected or growing profit is the most direct reason to own a stock"),
    ("orders", r"\b(bags?|wins?|secures?|lands?|receives?|gets?|bagged|won|clinch\w*)\b[^;:]{0,40}\b(order|contract|deal|mandate|project)s?\b|\border (win|book|inflow)s?\b|\bnew (order|contract)s?\b",
     "a new order or contract adds to future revenue"),
    ("corporate", r"\b(buyback|share repurchase|special dividend|interim dividend|dividend (hike|payout)|bonus (issue|shares)|stock split|promoters? (buy|buys|increase|raises?) (stake)?|stake (buy|purchase|hike)|fii[s]? (buy|buying|stake)|block deal.{0,20}buy)\b",
     "a buyback, dividend or insider or institutional buying returns cash or signals confidence"),
    ("approval", r"\b(usfda (approval|nod)|fda (approval|nod|clearance)|receives? (regulatory )?approval|gets? (regulatory )?approval|approval (for|to)|launches?|unveils?|commissions?|commissioned|clinical trial (success|results))\b",
     "an approval or launch can open a new source of revenue"),
    ("expansion", r"\b(capacity expansion|expands?|expansion plan|capex plan|partnership|joint venture|tie[- ]up|acquires?|acquisition of|to acquire|stake in)\b",
     "expansion, a partnership or an acquisition points to growth - though acquisitions can also cost money"),
    ("momentum", r"\b(52[- ]week high|all[- ]time high|record high|hits? (a )?(new )?high|rall(y|ies|ied)|surges?|soars?|jumps?|zooms?|gains?)\b",
     "a strong move or a new high draws momentum buyers"),
]
NEGATIVE = [
    ("analyst", r"\b(downgrad\w*|cuts? (its )?(target|price target)|target price (cut|lowered|slashed)|(price )?target (cut|lowered)|sell rating|underperform|underweight|reduce rating)\b",
     "an analyst downgrade or a lower target price takes buyers away"),
    ("results", r"\b(profit (falls?|drops?|declines?|slumps?|plunges?|down)|net profit (down|falls?|drops?|slumps?)|loss (widens?|narrows?)|net loss|posts? (a )?loss|misses? (estimates|expectations|street)|weak (q[1-4]|results|earnings|numbers)|revenue (falls?|drops?|declines?)|margin (pressure|contraction))\b",
     "falling profit or a loss is the most direct reason to sell"),
    ("probe", r"\b(probe|raids?|fraud|penalty|penalised|fined?|show[- ]cause|sebi (order|notice|bans?)|ed (raid|summons)|investigation|lawsuit|sued|default(s|ed)?|insolvency|bankruptcy|warning letter|recall|ban(s|ned)?)\b",
     "a probe, penalty, fraud allegation or product problem can hit the price and the company's standing"),
    ("management", r"\b(resigns?|resignation|steps? down|quits?|exits?|ceo (exit|quits?)|cfo (exit|quits?)|auditor resign\w*)\b",
     "a senior exit raises questions about the business"),
    ("momentum", r"\b(52[- ]week low|all[- ]time low|hits? (a )?(new )?low|plunges?|slumps?|tumbles?|crashes?|sinks?|falls? \d|drops? \d|selloff|sell-off)\b",
     "a sharp fall or a new low draws sellers"),
]
_POS = [(c, re.compile(r, re.I), w) for c, r, w in POSITIVE]
_NEG = [(c, re.compile(r, re.I), w) for c, r, w in NEGATIVE]


def classify(title: str) -> dict:
    """{"score": +1 / -1 / 0, "cue": the matched words, "category", "why"} for one headline. A negative cue beats a positive one (bad news outweighs
    a hopeful word in the same headline)."""
    for side, rules, score in (("neg", _NEG, -1), ("pos", _POS, 1)):
        for cat, rx, why in rules:
            m = rx.search(title)
            if m:
                return {"score": score, "cue": m.group(0).strip(), "category": cat, "why": why}
    return {"score": 0, "cue": None, "category": None, "why": None}


# ---------------------------------------------------------------- reading the feeds
def _parse_date(text: str | None) -> date | None:
    try:
        return parsedate_to_datetime(text).date() if text else None
    except (TypeError, ValueError):
        return None


def parse_rss(xml_text: str) -> list[dict]:
    """[{title, source, date, link}] from an RSS document; items without a title or a readable date are dropped."""
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        d = _parse_date(it.findtext("pubDate"))
        if not title or d is None:
            continue
        source = (it.findtext("source") or "").strip()
        if not source and " - " in title:
            title, source = title.rsplit(" - ", 1)
        elif source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)]
        out.append({"title": title.strip(), "source": source.strip(), "date": d, "link": (it.findtext("link") or "").strip()})
    return out


_SUFFIX = {"ltd", "limited", "corporation", "corp", "company", "co", "inc", "plc"}


def key_phrase(name: str, symbol: str) -> str:
    """What a headline must contain to be about this company: the first two words of its name (so "Tata Steel" does not match every "Tata" story),
    or the one word of a one-word name; the symbol alone when there is no name."""
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", name or "") if w.lower() not in _SUFFIX]
    if not words:
        return symbol.lower()
    return " ".join(words[:2]) if len(words) > 1 and len(words[0]) < 12 else words[0]


def mentions(title: str, name: str, symbol: str) -> bool:
    """The headline contains the company's key phrase, or its symbol as an uppercase whole word (TCS, SBIN)."""
    norm = " ".join(re.findall(r"[a-z0-9]+", title.lower()))
    return key_phrase(name, symbol) in norm or bool(re.search(rf"\b{re.escape(symbol)}\b", title))


def relevant(items: list[dict], name: str, symbol: str, today: date, days: int = NEWS_DAYS) -> list[dict]:
    """Recent items that mention the company, newest first, de-duplicated by headline."""
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["date"], reverse=True):
        age = (today - it["date"]).days
        norm = re.sub(r"\W+", " ", it["title"].lower()).strip()
        if age < 0 or age > days or norm in seen or not mentions(it["title"], name, symbol):
            continue
        seen.add(norm)
        out.append(it)
    return out


def fetch(name: str, symbol: str, today: date, session: requests.Session | None = None) -> tuple[list[dict], str]:
    """(recent relevant headlines, where they came from or why none). Never raises."""
    session = session or requests.Session()
    q = requests.utils.quote(f'"{name or symbol}" stock when:{NEWS_DAYS}d')
    urls = [("google", f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"),
            ("yahoo", f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={requests.utils.quote(symbol)}.NS&region=IN&lang=en-IN")]
    last = "no feed answered"
    for label, url in urls:
        try:
            resp = session.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            items = relevant(parse_rss(resp.text), name, symbol, today)
            if items:
                return items[:MAX_ITEMS * 2], label
            last = f"{label}: no recent headlines"
        except Exception as exc:  # noqa: BLE001 - news is optional context
            last = f"{label}: {type(exc).__name__}"
    return [], last


def assess(items: list[dict], side: str, source: str, today: date) -> dict:
    """Verdict (positive / negative / mixed / quiet / nodata) for the trade's side, with every headline, its matched cue and why it matters."""
    rows = []
    for it in items[:MAX_ITEMS]:
        c = classify(it["title"])
        rows.append({"title": it["title"], "source": it["source"], "date": it["date"].isoformat(), "days": (today - it["date"]).days, "link": it["link"], **c})
    if not rows:
        return {"verdict": "nodata", "for": None, "summary": f"No news headlines found for the last {NEWS_DAYS} days ({source}). That is not bad news - it means the stock is quiet in the press.", "items": [],
                "pos": 0, "neg": 0}
    pos, neg = sum(r["score"] > 0 for r in rows), sum(r["score"] < 0 for r in rows)
    net = pos - neg
    verdict = "positive" if net >= 2 else "negative" if net <= -2 else "mixed" if (pos or neg) else "quiet"
    long = side != "short"
    helps = None if verdict in ("mixed", "quiet") else (verdict == "positive") == long
    summary = {"positive": f"News is positive: {pos} of {len(rows)} recent headlines carry a buying cue and {neg} a selling cue.",
               "negative": f"News is negative: {neg} of {len(rows)} recent headlines carry a selling cue and {pos} a buying cue.",
               "mixed": f"News is mixed: {pos} headlines with a buying cue and {neg} with a selling cue out of {len(rows)}.",
               "quiet": f"News is quiet: {len(rows)} recent headlines, none with a clear buying or selling cue."}[verdict]
    return {"verdict": verdict, "for": helps, "summary": summary, "items": rows, "pos": pos, "neg": neg, "source": source}
