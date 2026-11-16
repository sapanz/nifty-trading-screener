"""Dated catalysts from NSE: board meetings (results dates) and corporate actions (ex-dates). Best effort - NSE's JSON endpoints refuse some
servers, so every call fails soft and the page says when the calendar could not be read."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import requests

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar"}
CALENDAR_URL = "https://www.nseindia.com/api/event-calendar"
ACTIONS_URL = "https://www.nseindia.com/api/corporates-corporateActions?index=equities"


def _date(text: str | None) -> date | None:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((text or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_calendar(rows: list[dict]) -> dict[str, list[dict]]:
    """{symbol: [{date, what}]} from the event calendar (board meetings and what they are for)."""
    out: dict[str, list[dict]] = {}
    for r in rows or []:
        d, sym = _date(r.get("date")), (r.get("symbol") or "").strip()
        if d and sym:
            out.setdefault(sym, []).append({"date": d, "what": (r.get("purpose") or r.get("bm_desc") or "Board meeting").strip()})
    return out


def parse_actions(rows: list[dict]) -> dict[str, list[dict]]:
    """{symbol: [{date (ex-date), what}]} from the corporate actions list."""
    out: dict[str, list[dict]] = {}
    for r in rows or []:
        d, sym = _date(r.get("exDate")), (r.get("symbol") or "").strip()
        if d and sym:
            out.setdefault(sym, []).append({"date": d, "what": (r.get("subject") or "Corporate action").strip()})
    return out


def fetch() -> tuple[dict[str, list[dict]], dict[str, list[dict]], dict[str, str]]:
    """(board meetings by symbol, corporate actions by symbol, {source: status}). Never raises."""
    session = requests.Session()
    status = {}
    try:
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=20)
    except requests.RequestException as exc:
        logger.warning("NSE cookies: %s", exc)
    results = []
    for name, url, parse in (("calendar", CALENDAR_URL, parse_calendar), ("actions", ACTIONS_URL, parse_actions)):
        try:
            resp = session.get(url, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            parsed = parse(resp.json())
            status[name] = f"ok: {sum(len(v) for v in parsed.values())} rows, {len(parsed)} symbols"
            results.append(parsed)
        except Exception as exc:  # noqa: BLE001
            status[name] = f"failed: {type(exc).__name__} {str(exc)[:80]}"
            results.append({})
    return results[0], results[1], status


def for_symbol(symbol: str, meetings: dict, actions: dict, today: date, ahead: int = 21, back: int = 7) -> list[dict]:
    """The dated events of one stock from `back` days ago to `ahead` days from now, soonest first, each with why it matters."""
    out = []
    for kind, source in (("meeting", meetings), ("action", actions)):
        for ev in source.get(symbol, []):
            days = (ev["date"] - today).days
            if not (-back <= days <= ahead):
                continue
            results = "result" in ev["what"].lower()
            if kind == "meeting" and days >= 0:
                why = ("results can gap the stock straight through a stop: size down, or wait until they are out" if results
                       else "a board decision can move the stock on the day")
            elif kind == "meeting":
                why = "the results are out, so that uncertainty is behind the stock" if results else "the board has met; the outcome is known"
            else:
                why = "the price adjusts on the ex-date, which can look like a gap on the chart" if days >= 0 else "the price already adjusted for it"
            out.append({"date": ev["date"].isoformat(), "days": days, "kind": kind, "upcoming": days >= 0,
                        "text": f"{'Board meeting' if kind == 'meeting' else 'Ex-date'} {ev['date'].strftime('%d %b')}: {ev['what']}", "why": why})
    return sorted(out, key=lambda e: e["date"])


def window(today: date, ahead: int = 21, back: int = 7) -> tuple[date, date]:
    return today - timedelta(days=back), today + timedelta(days=ahead)
