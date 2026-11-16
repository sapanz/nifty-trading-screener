"""Global macro backdrop for a stock: which global factors matter to its sector, which way each is moving, and whether that helps the trade.

Factors (daily closes from Yahoo's chart API, Stooq as a fallback; Nifty 50 and India VIX come from Upstox in the build script):
  S&P 500, Nasdaq 100, US 10-year yield, Dollar index, Brent crude, USD/INR, Nifty 50, India VIX.
A factor's trend is "up" when it is above its 50-session average AND higher than 20 sessions ago, "down" when below and lower, else "flat".

The sector links are RULES OF THUMB (an exporter earns in dollars, cement burns fuel ...), each with its reason in words. They are context for
reading a setup - they are not in the probability and have not been backtested. Everything here fails soft: a factor that cannot be fetched is
shown as "no data" and counts neither for nor against.
"""
from __future__ import annotations

import io
import logging

import pandas as pd
import requests

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Accept": "application/json,text/csv,*/*"}
# key: (label, Yahoo symbol, Stooq symbol, unit)
SOURCES = {"spx": ("S&P 500", "^GSPC", "^spx", ""), "ndx": ("Nasdaq 100", "^NDX", "^ndx", ""), "us10y": ("US 10-year yield", "^TNX", "10usy.b", "%"),
           "dxy": ("Dollar index", "DX-Y.NYB", "dx.f", ""), "brent": ("Brent crude", "BZ=F", "cb.f", "$"), "usdinr": ("USD/INR", "INR=X", "usdinr", "₹")}
LABELS = {**{k: v[0] for k, v in SOURCES.items()}, "nifty": "Nifty 50", "vix": "India VIX"}

# (factor, the direction that helps a LONG, why) - applied to every stock
GENERAL = [("spx", "up", "global equities rising means foreign investors are taking risk, which lifts flows into Indian stocks"),
           ("dxy", "down", "a weaker dollar sends money towards emerging markets; a stronger one pulls it out"),
           ("us10y", "down", "falling US yields make equities, and emerging markets in particular, more attractive"),
           ("nifty", "up", "most stocks move with the Indian market's own trend"),
           ("vix", "down", "a falling India VIX means less fear and smaller swings against the position")]
# sector (the Nifty 500 list's Industry) -> the factors that matter to it. "mixed" = it cuts both ways inside the sector.
SECTOR = {
    "Information Technology": [("usdinr", "up", "IT companies bill in dollars, so a weaker rupee lifts their revenue and margins"),
                               ("ndx", "up", "US technology strength goes with clients' technology spending")],
    "Healthcare": [("usdinr", "up", "pharma exporters earn in dollars, so a weaker rupee lifts their revenue")],
    "Textiles": [("usdinr", "up", "textile exporters earn in dollars, so a weaker rupee lifts their revenue")],
    "Automobile and Auto Components": [("brent", "down", "cheaper fuel supports vehicle demand and lowers input and freight costs")],
    "Fast Moving Consumer Goods": [("brent", "down", "cheaper crude lowers packaging and freight costs")],
    "Consumer Durables": [("brent", "down", "cheaper crude lowers plastics and freight costs")],
    "Chemicals": [("brent", "down", "crude is the feedstock for most chemicals, so cheaper crude widens margins")],
    "Construction Materials": [("brent", "down", "cement and building materials burn a lot of fuel, so cheaper energy widens margins")],
    "Consumer Services": [("brent", "down", "cheaper fuel lowers travel, delivery and retail logistics costs")],
    "Services": [("brent", "down", "cheaper fuel lowers transport and logistics costs")],
    "Metals & Mining": [("dxy", "down", "metals are priced in dollars, so a weaker dollar lifts metal prices and miners' earnings")],
    "Financial Services": [("us10y", "down", "lower global yields ease funding costs and bring foreign flows, which favour banks and lenders first")],
    "Realty": [("us10y", "down", "property is rate-sensitive, so lower yields help demand and funding")],
    "Oil Gas & Consumable Fuels": [("brent", "mixed", "crude cuts both ways here: producers gain when it rises, refiners and fuel retailers when it falls - check which this company is")],
}


def _yahoo(symbol: str, session: requests.Session) -> pd.Series | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol)}?range=1y&interval=1d"
    data = session.get(url, headers=HEADERS, timeout=20)
    data.raise_for_status()
    result = data.json()["chart"]["result"][0]
    s = pd.Series(result["indicators"]["quote"][0]["close"], index=pd.to_datetime(result["timestamp"], unit="s").normalize()).dropna()
    return s if len(s) >= 60 else None


def _stooq(symbol: str, session: requests.Session) -> pd.Series | None:
    text = session.get(f"https://stooq.com/q/d/l/?s={requests.utils.quote(symbol)}&i=d", headers=HEADERS, timeout=20).text
    df = pd.read_csv(io.StringIO(text))
    if "Close" not in df or "Date" not in df:
        return None
    s = pd.Series(df["Close"].to_numpy(float), index=pd.to_datetime(df["Date"])).dropna().tail(260)
    return s if len(s) >= 60 else None


def fetch_ticker(symbol: str, session: requests.Session | None = None) -> pd.Series | None:
    """One Yahoo ticker's daily closes (a year), or None - never raises. Used for the global sector funds and peer companies."""
    try:
        return _yahoo(symbol, session or requests.Session())
    except Exception as exc:  # noqa: BLE001
        logger.info("Ticker %s unavailable: %s", symbol, type(exc).__name__)
        return None


def fetch_global() -> tuple[dict[str, pd.Series], dict[str, str]]:
    """({factor: closes}, {factor: where it came from or why it is missing}). Never raises."""
    session = requests.Session()
    series, status = {}, {}
    for key, (_, ysym, ssym, _) in SOURCES.items():
        for name, fn, sym in (("yahoo", _yahoo, ysym), ("stooq", _stooq, ssym)):
            try:
                s = fn(sym, session)
                if s is not None:
                    series[key], status[key] = s, name
                    break
                status[key] = f"{name}: too little data"
            except Exception as exc:  # noqa: BLE001 - context is optional; the build must not fail on it
                status[key] = f"{name}: {type(exc).__name__}"
        if key not in series:
            logger.warning("Macro factor %s unavailable (%s)", key, status.get(key))
    return series, status


def reading(key: str, closes: pd.Series) -> dict | None:
    """{label, last, chg20 (% over 20 sessions), above50, trend, text} for one factor, or None with too little data."""
    s = pd.Series(closes).dropna()
    if len(s) < 51:
        return None
    last, then, avg = float(s.iloc[-1]), float(s.iloc[-21]), float(s.iloc[-50:].mean())
    chg = (last / then - 1) * 100
    above = last > avg
    trend = "up" if above and chg > 0 else "down" if not above and chg < 0 else "flat"
    words = {"up": "rising", "down": "falling", "flat": "going sideways"}[trend]
    return {"key": key, "label": LABELS.get(key, key), "last": round(last, 2), "chg20": round(chg, 1), "above50": bool(above), "trend": trend,
            "text": f"{LABELS.get(key, key)} is {words}: {last:,.2f}, {chg:+.1f}% in 20 sessions, {'above' if above else 'below'} its 50-session average"}


def readings(series: dict[str, pd.Series]) -> dict[str, dict]:
    out = {}
    for key, s in series.items():
        r = reading(key, s)
        if r:
            out[key] = r
    return out


def assess(sector: str | None, side: str, factors: dict[str, dict]) -> dict:
    """The macro backdrop for one trade: each relevant factor with its reading, whether it is for or against the trade and why, and an overall
    verdict (Supportive / Mixed / Against / No data)."""
    rules = {f: (want, why, False) for f, want, why in GENERAL}
    for f, want, why in SECTOR.get(sector or "", []):
        rules[f] = (want, why, True)                                # the sector's own reason replaces the general one
    items, score, known = [], 0, 0
    for f, (want, why, specific) in rules.items():
        r = factors.get(f)
        if r is None:
            items.append({"factor": LABELS.get(f, f), "verdict": "nodata", "text": f"{LABELS.get(f, f)}: no data", "why": why, "sector": specific})
            continue
        if want == "mixed" or r["trend"] == "flat":
            verdict = "neutral"
        else:
            helps_long = r["trend"] == want
            verdict = "for" if helps_long == (side != "short") else "against"
            score += 1 if verdict == "for" else -1
        known += 1
        items.append({"factor": r["label"], "verdict": verdict, "text": r["text"], "why": why, "sector": specific})
    n_for, n_against = sum(i["verdict"] == "for" for i in items), sum(i["verdict"] == "against" for i in items)
    verdict = "nodata" if not known else "supportive" if score >= 2 else "against" if score <= -2 else "mixed"
    summary = {"nodata": "No global data could be fetched for this build.",
               "supportive": f"Global backdrop is supportive: {n_for} of {known} factors are with this {'short' if side == 'short' else 'long'}, {n_against} against.",
               "against": f"Global backdrop is against it: {n_against} of {known} factors work against this {'short' if side == 'short' else 'long'}, {n_for} for.",
               "mixed": f"Global backdrop is mixed: {n_for} of {known} factors for this {'short' if side == 'short' else 'long'}, {n_against} against."}[verdict]
    items.sort(key=lambda i: (not i["sector"], {"against": 0, "for": 1, "neutral": 2, "nodata": 3}[i["verdict"]]))
    return {"verdict": verdict, "summary": summary, "items": items}
