"""The stock-specific global backdrop: how the stock's SECTOR is doing worldwide, how its INDIAN PEERS are doing, what the NEWS says, and (as one background
vote) the market-wide global factors. Each part is shown with its numbers and reasons; the overall verdict counts how many are with the trade (flipped for a
short). Context, not part of the probability - the groups, thresholds and headline cues are rules of thumb and have not been backtested.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from signals import macro, news, peers


def _vote(verdict: str | None, good: tuple[str, ...], bad: tuple[str, ...], long: bool) -> int:
    if verdict in good:
        return 1 if long else -1
    if verdict in bad:
        return -1 if long else 1
    return 0


def assess_stock(symbol: str, sector: str | None, side: str, factors: dict[str, dict], series: dict[str, pd.Series] | None, frames: dict[str, pd.DataFrame],
                 sectors: dict[str, str], news_result: tuple[list[dict], str] | None, today: date) -> dict:
    """{verdict, summary, score, sections: {world, peers, news, backdrop}, items (the market-wide factors, for older pages)}."""
    long = side != "short"
    market = macro.assess(sector, side, factors)                                   # market-wide factors, each with its reason
    world = peers.global_view(symbol, sector, series or {}, (series or {}).get("SPX"))
    india = peers.india_peers(symbol, sector, frames, sectors)
    nws = news.assess(*(news_result or ([], "not fetched")), side, today) if news_result is not None else \
        {"verdict": "nodata", "for": None, "summary": "News could not be fetched for this build.", "items": [], "pos": 0, "neg": 0}
    sector_items = [i for i in market["items"] if i["sector"] and i["verdict"] in ("for", "against")]
    general = [i for i in market["items"] if not i["sector"] and i["verdict"] in ("for", "against")]
    g_for, g_against = sum(i["verdict"] == "for" for i in general), sum(i["verdict"] == "against" for i in general)
    votes = {"world": _vote(world["verdict"], ("strong",), ("weak",), long), "peers": _vote(india["verdict"], ("strong",), ("weak",), long),
             "news": 0 if nws.get("for") is None else (1 if nws["for"] else -1),
             "factor": sum(1 if i["verdict"] == "for" else -1 for i in sector_items), "backdrop": 1 if g_for - g_against >= 2 else -1 if g_against - g_for >= 2 else 0}
    known = [world["verdict"] != "nodata", india["verdict"] != "nodata", nws["verdict"] not in ("nodata",), bool(sector_items), bool(general)]
    score = sum(votes.values())
    verdict = "nodata" if not any(known) else "supportive" if score >= 2 else "against" if score <= -2 else "mixed"
    names = {"world": "sector worldwide", "peers": "Indian peers", "news": "news", "factor": "sector-specific global factors", "backdrop": "market-wide backdrop"}
    state = {"world": world["verdict"], "peers": india["verdict"], "news": nws["verdict"], "factor": None, "backdrop": None}
    with_t = [names[k] + (f" ({state[k]})" if state[k] and state[k] not in ('nodata',) else "") for k, v in votes.items() if v > 0]
    agst = [names[k] + (f" ({state[k]})" if state[k] and state[k] not in ('nodata',) else "") for k, v in votes.items() if v < 0]
    word = "short" if side == "short" else "long"
    summary = {"nodata": "No stock-specific global data could be fetched for this build.",
               "supportive": f"The backdrop for this {word} is supportive: " + "; ".join(with_t) + ("; against: " + ", ".join(agst) if agst else "") + ".",
               "against": f"The backdrop for this {word} is against it: " + ", ".join(agst) + ("; for: " + ", ".join(with_t) if with_t else "") + ".",
               "mixed": f"The backdrop for this {word} is mixed" + (": for - " + ", ".join(with_t) if with_t else "") + ("; against - " + ", ".join(agst) if agst else "") + "."}[verdict]
    world = {**world, "for": None if votes["world"] == 0 else votes["world"] > 0}
    india = {**india, "for": None if votes["peers"] == 0 else votes["peers"] > 0}
    return {"verdict": verdict, "summary": summary, "score": score, "items": market["items"],
            "sections": {"world": world, "peers": india, "news": nws, "backdrop": {"verdict": market["verdict"], "summary": market["summary"], "items": market["items"]}}}
