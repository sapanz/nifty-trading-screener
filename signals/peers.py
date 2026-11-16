"""Peers for a stock: how its sector is doing WORLDWIDE (global sector funds and listed global peer companies) and how its INDIAN peers are doing.

Each stock belongs to a curated peer group (private banks, IT services, steel ...). A stock outside every group falls back to its broad Nifty 500
sector - the page says which basis was used. The global side uses a sector fund plus a few well-known listed companies that do the same business;
the Indian side is measured from the dashboard's own candles. Verdicts are plain rules (trend of each series, share of peers above their 50-session
average, return against the market) with the numbers behind them. Context, not part of the probability, and the groups are rules of thumb.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from signals import macro

# key: (label, Indian members, [(global ticker, name)], (sector fund ticker, what it is))
_G = {
    "it_services": ("IT services", "COFORGE HCLTECH INFY LTM LTIM MPHASIS PERSISTENT TECHM TCS WIPRO SAGILITY",
                    [("ACN", "Accenture"), ("CTSH", "Cognizant"), ("EPAM", "EPAM Systems"), ("IBM", "IBM"), ("GLOB", "Globant")], ("XLK", "US technology sector fund")),
    "it_software": ("software and engineering R&D", "OFSS TATAELXSI KPITTECH", [("PTC", "PTC"), ("ADSK", "Autodesk"), ("CDNS", "Cadence"), ("SNPS", "Synopsys")], ("XLK", "US technology sector fund")),
    "banks_private": ("private banks", "AUBANK AXISBANK BANDHANBNK CUB FEDERALBNK HDFCBANK ICICIBANK IDFCFIRSTB INDUSINDBK KARURVYSYA KOTAKBANK RBLBANK YESBANK UJJIVANSFB",
                      [("JPM", "JPMorgan"), ("BAC", "Bank of America"), ("WFC", "Wells Fargo"), ("C", "Citigroup")], ("KBE", "US banks fund")),
    "banks_psu": ("public-sector banks", "BANKBARODA BANKINDIA CANBK INDIANB MAHABANK PNB SBIN UNIONBANK", [("JPM", "JPMorgan"), ("BAC", "Bank of America"), ("WFC", "Wells Fargo"), ("C", "Citigroup")], ("KBE", "US banks fund")),
    "nbfc": ("lenders and NBFCs", "ABCAPITAL BAJAJFINSV BAJFINANCE CHOLAFIN LICHSGFIN LTF MANAPPURAM MUTHOOTFIN PNBHOUSING SHRIRAMFIN SBICARD JIOFIN",
             [("COF", "Capital One"), ("AXP", "American Express"), ("SYF", "Synchrony"), ("ALLY", "Ally Financial")], ("XLF", "US financials fund")),
    "infra_finance": ("infrastructure lenders", "IREDA IRFC PFC RECLTD", [("JPM", "JPMorgan"), ("BAC", "Bank of America")], ("XLF", "US financials fund")),
    "insurance": ("insurers", "HDFCLIFE ICICIGI ICICILIFE LICI MFSL SBILIFE", [("MET", "MetLife"), ("PRU", "Prudential"), ("AIG", "AIG"), ("ALL", "Allstate"), ("TRV", "Travelers")], ("KIE", "US insurance fund")),
    "capital_markets": ("brokers, exchanges and asset managers", "360ONE ANANDRATHI ANGELONE BSE CAMS CDSL HDFCAMC IEX KFINTECH MCX MOTILALOFS NAM-INDIA",
                        [("CME", "CME Group"), ("ICE", "Intercontinental Exchange"), ("NDAQ", "Nasdaq"), ("SCHW", "Schwab"), ("BLK", "BlackRock")], ("KCE", "US capital markets fund")),
    "pharma": ("pharmaceuticals", "ALKEM AUROPHARMA BIOCON CIPLA DIVISLAB DRREDDY GLENMARK LAURUSLABS LUPIN MANKIND SUNPHARMA TORNTPHARM ZYDUSLIFE",
               [("TEVA", "Teva"), ("VTRS", "Viatris"), ("PFE", "Pfizer"), ("MRK", "Merck")], ("PPH", "global pharmaceuticals fund")),
    "hospitals": ("hospitals", "APOLLOHOSP FORTIS MAXHEALTH", [("HCA", "HCA Healthcare"), ("UHS", "Universal Health"), ("THC", "Tenet")], ("IHF", "US healthcare providers fund")),
    "auto_oem": ("vehicle makers", "BAJAJ-AUTO EICHERMOT FORCEMOT HEROMOTOCO HYUNDAI M&M MARUTI TMPV TVSMOTOR ATHERENERG ASHOKLEY TATAMOTORS",
                 [("TM", "Toyota"), ("HMC", "Honda"), ("GM", "General Motors"), ("F", "Ford"), ("TSLA", "Tesla")], ("CARZ", "global auto fund")),
    "auto_parts": ("auto components", "BHARATFORG BOSCHLTD MOTHERSON SONACOMS TIINDIA UNOMINDA", [("APTV", "Aptiv"), ("BWA", "BorgWarner"), ("LEA", "Lear"), ("ALV", "Autoliv")], ("CARZ", "global auto fund")),
    "fmcg": ("packaged consumer goods", "BRITANNIA COLPAL DABUR GODFRYPHLP GODREJCP HINDUNILVR ITC MARICO NESTLEIND PATANJALI TATACONSUM",
             [("UL", "Unilever"), ("PG", "Procter & Gamble"), ("KO", "Coca-Cola"), ("MDLZ", "Mondelez"), ("CL", "Colgate-Palmolive")], ("XLP", "US consumer staples fund")),
    "beverages": ("beverages", "VBL RADICO UNITDSPR", [("KO", "Coca-Cola"), ("PEP", "PepsiCo"), ("DEO", "Diageo"), ("STZ", "Constellation Brands")], ("XLP", "US consumer staples fund")),
    "durables": ("appliances and electricals", "AMBER BLUESTARCO CROMPTON DIXON HAVELLS PGEL VOLTAS WHIRLPOOL", [("WHR", "Whirlpool"), ("CARR", "Carrier"), ("LII", "Lennox"), ("TT", "Trane")], ("XHB", "US home-building and goods fund")),
    "paints": ("paints and adhesives", "ASIANPAINT PIDILITIND BERGEPAINT", [("SHW", "Sherwin-Williams"), ("PPG", "PPG"), ("RPM", "RPM International")], ("XLB", "US materials fund")),
    "retail": ("jewellery and retail", "TITAN KALYANKJIL TRENT DMART VMM", [("COST", "Costco"), ("WMT", "Walmart"), ("TJX", "TJX"), ("TGT", "Target")], ("XRT", "US retail fund")),
    "internet": ("consumer internet", "ETERNAL SWIGGY NYKAA NAUKRI POLICYBZR", [("AMZN", "Amazon"), ("DASH", "DoorDash"), ("UBER", "Uber"), ("MELI", "MercadoLibre"), ("SE", "Sea Ltd")], ("XLY", "US consumer discretionary fund")),
    "hotels_qsr": ("hotels and restaurants", "INDHOTEL JUBLFOOD", [("MAR", "Marriott"), ("HLT", "Hilton"), ("DPZ", "Domino's"), ("MCD", "McDonald's")], ("PEJ", "US leisure and entertainment fund")),
    "steel": ("steel", "JINDALSTEL JSWSTEEL SAIL TATASTEEL APLAPOLLO", [("NUE", "Nucor"), ("STLD", "Steel Dynamics"), ("MT", "ArcelorMittal"), ("PKX", "POSCO"), ("TX", "Ternium")], ("SLX", "global steel fund")),
    "nonferrous": ("aluminium, copper, zinc and mining", "HINDALCO HINDZINC NATIONALUM NMDC VEDL HINDCOPPER", [("AA", "Alcoa"), ("FCX", "Freeport-McMoRan"), ("SCCO", "Southern Copper"), ("BHP", "BHP"), ("RIO", "Rio Tinto")], ("XME", "US metals and mining fund")),
    "coal": ("coal", "COALINDIA", [("BTU", "Peabody"), ("AMR", "Alpha Metallurgical"), ("ARLP", "Alliance Resource")], ("XLE", "US energy sector fund")),
    "oil_producers": ("oil and gas producers", "ONGC OIL", [("XOM", "Exxon Mobil"), ("CVX", "Chevron"), ("COP", "ConocoPhillips"), ("OXY", "Occidental")], ("XOP", "US oil and gas exploration fund")),
    "refiners": ("refiners and fuel retailers", "BPCL HINDPETRO IOC MRPL CHENNPETRO", [("VLO", "Valero"), ("MPC", "Marathon Petroleum"), ("PSX", "Phillips 66")], ("XLE", "US energy sector fund")),
    "gas": ("gas transmission and distribution", "GAIL PETRONET IGL MGL GUJGASLTD", [("LNG", "Cheniere"), ("WMB", "Williams"), ("KMI", "Kinder Morgan")], ("XLE", "US energy sector fund")),
    "power": ("power utilities", "NTPC POWERGRID TATAPOWER ADANIPOWER JSWENERGY NHPC ADANIENSOL TORNTPOWER", [("NEE", "NextEra"), ("DUK", "Duke Energy"), ("SO", "Southern Company"), ("AEP", "American Electric Power")], ("XLU", "US utilities fund")),
    "renewables": ("renewable energy", "ADANIGREEN INOXWIND SUZLON WAAREEENER PREMIERENE", [("ENPH", "Enphase"), ("FSLR", "First Solar"), ("NEE", "NextEra")], ("ICLN", "global clean energy fund")),
    "electricals": ("electrical equipment and capital goods", "ABB SIEMENS BHEL CGPOWER CUMMINSIND POWERINDIA GVT&D KEI POLYCAB KAYNES SYRMA", [("ETN", "Eaton"), ("EMR", "Emerson"), ("ROK", "Rockwell"), ("HUBB", "Hubbell")], ("XLI", "US industrials fund")),
    "defence": ("defence", "BEL HAL BDL MAZDOCK COCHINSHIP DATAPATTNS", [("LMT", "Lockheed Martin"), ("RTX", "RTX"), ("NOC", "Northrop Grumman"), ("GD", "General Dynamics")], ("ITA", "US aerospace and defence fund")),
    "cement": ("cement", "AMBUJACEM GRASIM SHREECEM ULTRACEMCO ACC DALMIACEM", [("CX", "Cemex"), ("VMC", "Vulcan Materials"), ("MLM", "Martin Marietta"), ("EXP", "Eagle Materials")], ("XLB", "US materials fund")),
    "chemicals": ("specialty chemicals", "SRF NAVINFLUOR SOLARINDS DEEPAKNTR TATACHEM", [("ALB", "Albemarle"), ("CE", "Celanese"), ("DOW", "Dow"), ("LYB", "LyondellBasell")], ("XLB", "US materials fund")),
    "agrochem": ("agrochemicals and fertilisers", "COROMANDEL PIIND UPL CHAMBLFERT", [("CTVA", "Corteva"), ("FMC", "FMC"), ("MOS", "Mosaic"), ("NTR", "Nutrien")], ("XLB", "US materials fund")),
    "realty": ("real-estate developers", "DLF GODREJPROP LODHA OBEROIRLTY PHOENIXLTD PRESTIGE", [("DHI", "D.R. Horton"), ("LEN", "Lennar"), ("PHM", "PulteGroup"), ("TOL", "Toll Brothers")], ("XHB", "US home-building fund")),
    "telecom": ("telecom", "BHARTIARTL IDEA INDUSTOWER", [("T", "AT&T"), ("VZ", "Verizon"), ("TMUS", "T-Mobile"), ("AMX", "America Movil")], ("IYZ", "US telecom fund")),
    "logistics": ("logistics and ports", "ADANIPORTS CONCOR DELHIVERY", [("UPS", "UPS"), ("FDX", "FedEx"), ("XPO", "XPO")], ("IYT", "US transportation fund")),
    "airlines": ("airlines and airports", "INDIGO GMRAIRPORT", [("DAL", "Delta"), ("UAL", "United Airlines"), ("LUV", "Southwest")], ("JETS", "global airlines fund")),
    "construction": ("construction and infrastructure", "LT NBCC RVNL IRCON", [("PWR", "Quanta Services"), ("J", "Jacobs"), ("FLR", "Fluor")], ("PAVE", "US infrastructure fund")),
}
GROUPS = {k: {"label": v[0], "members": v[1].split(), "global": v[2], "fund": v[3]} for k, v in _G.items()}
SYMBOL_GROUP = {sym: key for key, g in GROUPS.items() for sym in g["members"]}
# broad-sector fallback when a stock is in no group: (global sector fund, what it is)
SECTOR_FUND = {"Information Technology": ("XLK", "US technology sector fund"), "Financial Services": ("XLF", "US financials fund"), "Healthcare": ("XLV", "US healthcare fund"),
               "Automobile and Auto Components": ("CARZ", "global auto fund"), "Fast Moving Consumer Goods": ("XLP", "US consumer staples fund"), "Metals & Mining": ("XME", "US metals and mining fund"),
               "Oil Gas & Consumable Fuels": ("XLE", "US energy sector fund"), "Capital Goods": ("XLI", "US industrials fund"), "Power": ("XLU", "US utilities fund"),
               "Chemicals": ("XLB", "US materials fund"), "Construction Materials": ("XLB", "US materials fund"), "Consumer Durables": ("XHB", "US home-building and goods fund"),
               "Consumer Services": ("XLY", "US consumer discretionary fund"), "Realty": ("XHB", "US home-building fund"), "Telecommunication": ("IYZ", "US telecom fund"),
               "Services": ("IYT", "US transportation fund"), "Construction": ("PAVE", "US infrastructure fund"), "Textiles": ("XRT", "US retail fund")}
MIN_PEERS = 3


def group_of(symbol: str, sector: str | None) -> dict:
    """{key, label, basis ("peer group" | "sector" | None), global: [(ticker, name)], fund: (ticker, what)} for a stock."""
    key = SYMBOL_GROUP.get(symbol)
    if key:
        g = GROUPS[key]
        return {"key": key, "label": g["label"], "basis": "peer group", "global": g["global"], "fund": g["fund"]}
    fund = SECTOR_FUND.get(sector or "")
    return {"key": None, "label": sector or "its sector", "basis": "sector" if sector else None, "global": [], "fund": fund}


def tickers_for(symbol: str, sector: str | None) -> list[str]:
    g = group_of(symbol, sector)
    return ([g["fund"][0]] if g["fund"] else []) + [t for t, _ in g["global"]]


# ---------------------------------------------------------------- Indian peers
def _ret(c: np.ndarray, n: int) -> float:
    return float(c[-1] / c[-1 - n] - 1) if len(c) > n and c[-1 - n] > 0 else float("nan")


def india_peers(symbol: str, sector: str | None, frames: dict[str, pd.DataFrame], sectors: dict[str, str]) -> dict:
    """How the stock's Indian peers are doing: their 1- and 3-month returns against the market, the share above their 50-session average, and where
    the stock stands among them. Peers come from the curated group when it has enough members in the data, otherwise from the broad sector."""
    g = group_of(symbol, sector)
    pool = [s for s in GROUPS[g["key"]]["members"] if s != symbol and s in frames] if g["key"] else []
    basis, label = "peer group", g["label"]
    if len(pool) < MIN_PEERS:
        pool = [s for s, sec in sectors.items() if sec and sec == sector and s != symbol and s in frames]
        basis, label = "sector", sector or "its sector"
    if len(pool) < MIN_PEERS or symbol not in frames:
        return {"verdict": "nodata", "summary": "Too few Indian peers in the data to compare.", "reasons": [], "n": len(pool), "basis": basis, "label": label}
    closes = {s: frames[s]["close"].to_numpy(float) for s in pool + [symbol]}
    r1, r3 = {s: _ret(c, 21) for s, c in closes.items()}, {s: _ret(c, 63) for s, c in closes.items()}
    above = {s: bool(len(c) >= 50 and c[-1] > c[-50:].mean()) for s, c in closes.items()}
    market3 = float(np.nanmedian([_ret(frames[s]["close"].to_numpy(float), 63) for s in frames]))
    peers3 = [r3[s] for s in pool if not np.isnan(r3[s])]
    if len(peers3) < MIN_PEERS or np.isnan(r3[symbol]):
        return {"verdict": "nodata", "summary": "Not enough price history to compare with peers.", "reasons": [], "n": len(pool), "basis": basis, "label": label}
    med1, med3 = float(np.nanmedian([r1[s] for s in pool])), float(np.median(peers3))
    share = sum(above[s] for s in pool) / len(pool) * 100
    rel = (med3 - market3) * 100
    ranked = sorted(pool + [symbol], key=lambda s: -(r3[s] if not np.isnan(r3[s]) else -9))
    place = ranked.index(symbol) + 1
    leaders = [f"{s} {r3[s] * 100:+.0f}%" for s in ranked[:3] if s != symbol and not np.isnan(r3[s])]
    laggards = [f"{s} {r3[s] * 100:+.0f}%" for s in ranked[::-1][:2] if s != symbol and not np.isnan(r3[s])]
    verdict = "strong" if rel > 2 and share >= 60 else "weak" if rel < -2 and share <= 40 else "mixed"
    reasons = [f"{len(pool)} {basis == 'peer group' and 'peers in the group' or 'stocks in the sector'} ({label}): median {med3 * 100:+.1f}% over 3 months against {market3 * 100:+.1f}% for the market "
               f"({rel:+.1f} points), {med1 * 100:+.1f}% over the last month.",
               f"{share:.0f}% of them trade above their 50-session average" + (" - the group is in an uptrend." if share >= 60 else " - the group is in a downtrend." if share <= 40 else ".")]
    reasons.append(f"The stock is {r3[symbol] * 100:+.1f}% over 3 months: {place} of {len(ranked)} " + ("(leading its peers)." if place <= max(2, len(ranked) // 4) else "(lagging its peers)." if place > len(ranked) * 0.75 else "(in the middle of its peers)."))
    if leaders:
        reasons.append("Strongest peers: " + ", ".join(leaders) + (". Weakest: " + ", ".join(laggards) + "." if laggards else "."))
    summary = {"strong": f"Indian peers are strong: {label} is beating the market and most of it is in an uptrend.",
               "weak": f"Indian peers are weak: {label} is lagging the market and most of it is in a downtrend.",
               "mixed": f"Indian peers are mixed: {label} is neither clearly ahead of nor behind the market."}[verdict]
    return {"verdict": verdict, "summary": summary, "reasons": reasons, "n": len(pool), "basis": basis, "label": label, "rel3m": round(rel, 1), "share_above50": round(share),
            "stock_place": place, "of": len(ranked)}


# ---------------------------------------------------------------- the sector worldwide
def global_view(symbol: str, sector: str | None, series: dict[str, pd.Series], spx: pd.Series | None = None) -> dict:
    """How the sector is doing globally, from the sector fund and the listed global peers: each one's trend (above its 50-session average and higher than
    20 sessions ago), its 3-month change, and the fund against the S&P 500. `series` = {ticker: daily closes}; missing tickers are shown as no data."""
    g = group_of(symbol, sector)
    wanted = ([(g["fund"][0], g["fund"][1])] if g["fund"] else []) + list(g["global"])
    if not wanted:
        return {"verdict": "nodata", "summary": "No global peer set is defined for this stock.", "items": [], "label": g["label"]}
    items, trends = [], []
    for k, (ticker, name) in enumerate(wanted):
        s = series.get(ticker)
        r = macro.reading(ticker, s) if s is not None else None
        is_fund = k == 0 and bool(g["fund"])
        if r is None:
            items.append({"ticker": ticker, "name": name, "fund": is_fund, "trend": None, "text": f"{name} ({ticker}): no data"})
            continue
        c = pd.Series(s).dropna().to_numpy(float)
        chg63 = _ret(c, 63) * 100
        trends.append((is_fund, r["trend"], chg63))
        items.append({"ticker": ticker, "name": name, "fund": is_fund, "trend": r["trend"], "chg20": r["chg20"], "chg63": round(chg63, 1), "above50": r["above50"],
                      "text": f"{name} ({ticker}) is {dict(up='rising', down='falling', flat='going sideways')[r['trend']]}: {chg63:+.1f}% over 3 months, {r['chg20']:+.1f}% over 20 sessions, "
                              f"{'above' if r['above50'] else 'below'} its 50-session average" + (" - the sector fund" if is_fund else "")})
    if not trends:
        return {"verdict": "nodata", "summary": "Global sector data could not be fetched for this build.", "items": items, "label": g["label"]}
    up = sum(t == "up" for _, t, _ in trends)
    down = sum(t == "down" for _, t, _ in trends)
    fund = next((t for f, t, _ in trends if f), None)
    verdict = "strong" if up / len(trends) >= 0.6 and fund != "down" else "weak" if down / len(trends) >= 0.6 and fund != "up" else "mixed"
    note = ""
    if spx is not None and g["fund"] and series.get(g["fund"][0]) is not None:
        f, m = pd.Series(series[g["fund"][0]]).dropna().to_numpy(float), pd.Series(spx).dropna().to_numpy(float)
        if len(f) > 63 and len(m) > 63:
            note = f" The {g['fund'][1]} is {(_ret(f, 63) - _ret(m, 63)) * 100:+.1f} points against the S&P 500 over 3 months."
    summary = {"strong": f"The sector is strong worldwide: {up} of {len(trends)} global {g['label']} references are in an uptrend.",
               "weak": f"The sector is weak worldwide: {down} of {len(trends)} global {g['label']} references are in a downtrend.",
               "mixed": f"The sector is mixed worldwide: {up} of {len(trends)} global {g['label']} references up, {down} down."}[verdict] + note
    return {"verdict": verdict, "summary": summary, "items": items, "label": g["label"], "basis": g["basis"]}
