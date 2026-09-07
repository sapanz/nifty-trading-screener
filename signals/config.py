"""Central place for every tunable threshold used by the strategies.

Keeping these as plain module constants (rather than scattering magic
numbers through the strategy code) makes it easy to tune the screener
without hunting through logic.
"""

# --- Trend filter -----------------------------------------------------
SMA_LONG = 200          # "above 200 SMA" trend filter used by every strategy

# --- Candle quality -----------------------------------------------------
# A candle is a "proper close" when the upper wick is small relative to
# the candle's own range, i.e. price closed near its high instead of
# getting rejected and closing well below it.
MAX_UPPER_WICK_RATIO = 0.25  # (high - close) / (high - low) must be <= this

# --- Volume candle ---------------------------------------------------------
# Both remaining strategies (weekly breakout, monthly ATH breakout) gate on
# volume.
VOLUME_LOOKBACK = 20
WEEKLY_VOLUME_MULTIPLIER = 1.3
MONTHLY_VOLUME_LOOKBACK = 12
MONTHLY_VOLUME_MULTIPLIER = 1.3

# --- CIP (Change In Polarity), weekly only --------------------------------
# An old all-time-high *zone* - validated by multiple distinct swing-high
# peaks near it, not just a single exact price - approached repeatedly
# without breaking and then finally broken on strong volume, later gets
# retested from above; if a bullish candle holds that old-zone-turned-
# support level, that's the "change in polarity" - and since it's the
# stock's own all-time high, it's the strongest support level available.
# (A daily-timeframe version was tried and dropped: even after tightening
# its resistance-zone criteria hard, backtesting over 12 months showed a
# genuinely negative edge, not just noise.)
CIP_ZONE_TOLERANCE = 0.02        # how wide the resistance zone is below its own all-time high
CIP_VOLUME_MULTIPLIER = 1.5      # breakout candle's volume vs its trailing average
CIP_RISK_REWARD_TARGETS = (2, 3)  # T1/T2 as multiples of entry-to-SL risk

CIP_WEEKLY_TOUCH_LOOKBACK = 12      # weeks scanned for zone touches before a candidate breakout
CIP_WEEKLY_BREAKOUT_SEARCH = 26     # how many weeks back a qualifying breakout can still count
CIP_WEEKLY_MIN_ZONE_POINTS = 2      # the zone must show at least this many distinct swing-high peaks before breaking

# --- Daily Swing: Darvas Box + technical CANSLIM --------------------------
# Darvas Box: buy a stock making a fresh new high once it consolidates
# into a tight box, then breaks out above the TOP of that box on volume;
# the stop sits below the box bottom, per Darvas's own rule. Layered with
# the price/volume-derivable legs of O'Neil's CANSLIM: N (new high, built
# into the box top itself), S (volume-confirmed breakout - the box
# breakout already requires this), L (leadership - only the strongest
# stocks in the scanned universe by trailing relative return qualify),
# and M (a weak overall market, measured by breadth, stands the whole
# strategy down for the day). The fundamentals-only legs of CANSLIM (C, A,
# I - quarterly/annual earnings growth, institutional ownership) aren't
# available from the OHLCV-only Upstox feed this screener uses, so
# they're intentionally left out rather than faked.
#
# Note: this is a single point-in-time EOD scan, not Darvas's original
# stateful trailing-stop system (he raised the stop as new boxes formed
# on top of each other and let winners run rather than taking a fixed
# target) - targets here are risk-multiples instead, to fit this
# screener's existing entry/SL/target architecture.
DARVAS_NEW_HIGH_LOOKBACK = 252     # ~52 weeks: the box's top must be a new high over this many trading days
DARVAS_NEW_HIGH_TOLERANCE = 0.02   # how much the box top may sit below the actual 52-week high and still count as "new"
DARVAS_BOX_MIN_DAYS = 3            # minimum consecutive days price must hold inside the box to confirm it
DARVAS_BOX_MAX_DAYS = 15           # give up looking for a box for a given peak after this many days
DARVAS_BOX_TIGHTNESS = 0.12        # (box_top - box_bottom) / box_bottom must be <= 12%

DAILY_SWING_VOLUME_MULTIPLIER = 1.5    # breakout candle's volume vs its trailing average
DAILY_SWING_RISK_REWARD_TARGETS = (2, 3)  # T1/T2 as multiples of entry-to-SL risk

DAILY_SWING_RS_LOOKBACK_DAYS = 63      # ~3 months: relative-strength ranking window
DAILY_SWING_RS_TOP_PERCENTILE = 0.30   # only the top 30% of the scanned universe by trailing return count as "leaders"
DAILY_SWING_MIN_MARKET_BREADTH = 0.50  # fraction of the universe that must sit above its own 200 SMA, else the whole run stands down

# --- Weekly breakout ------------------------------------------------------
BREAKOUT_RANGE_WEEKS = 6         # look at the 6 candles preceding the breakout candle
BREAKOUT_RANGE_TIGHTNESS = 0.15  # (range_high - range_low) / range_low must be <= 15%

# --- Stop-loss / target construction -------------------------------------
SL_BUFFER = 0.02          # extra cushion placed below the structural stop level
# Breakout SL is anchored to the breakout level itself (old resistance ->
# new support), not the bottom of the consolidation range - the 12-month
# backtest showed the wider range_low anchor produces a high win rate but
# a handful of large tail losses that drag average return negative.
BREAKOUT_RANGE_MULTIPLES = (1, 2)      # measured-move multiples of the range height
ATH_BREAKOUT_TARGET_PCTS = (0.15, 0.25)  # open-ended ATH breakouts: %-based T1, T2

# --- NSE Nifty 500 constituent list --------------------------------------
NSE_INDEX_LIST_URLS = (
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
)

# --- Upstox market data API -------------------------------------------------
# https://upstox.com/developer/api-documentation/ - verify against current
# docs if requests start failing, brokers do change these occasionally.
# NSE's own historical-data API was tried first (no auth needed at all),
# but it appears to block GitHub Actions' cloud IP ranges outright - a live
# run hung for 90+ minutes retrying every single request. Upstox is the
# reliable option; see tools/refresh_upstox_token.py for the one-tap daily
# token refresh that keeps this fully working without a manual token paste.
UPSTOX_BASE_URL = "https://api.upstox.com/v2"
UPSTOX_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"
UPSTOX_EQUITY_TYPE = "EQUITY"  # instrument_type value for cash-market equities (confirmed live, not "EQ")
UPSTOX_REQUEST_DELAY_SECONDS = 0.25  # spacing between historical-candle calls
UPSTOX_MAX_RETRIES = 3

# If fewer than this fraction of requested symbols map to an Upstox
# instrument_key, something is systemically wrong (e.g. the instrument
# master's column values changed shape) rather than a handful of unlisted
# symbols - fail loudly instead of quietly scanning nothing.
MIN_INSTRUMENT_MATCH_RATIO = 0.5

# Single daily-history depth, reused (via resampling) for weekly, monthly,
# and the monthly all-time-high check - one fetch per symbol serves every
# strategy that fires that day. 6 years comfortably covers a weekly
# SMA200 lookback (~4y) with margin; it also caps how far back the
# monthly ATH check can "see" (see README caveats).
DAILY_HISTORY_YEARS = 6

# --- Circuit breaker --------------------------------------------------------
# If the data source is blocking/rejecting requests wholesale (as NSE direct
# scraping turned out to do from GitHub Actions), fail fast after a small
# sample instead of grinding through hundreds of symbols for hours.
CIRCUIT_BREAKER_SAMPLE_SIZE = 20     # check failure rate after this many symbols
CIRCUIT_BREAKER_FAILURE_RATIO = 0.8  # abort if this fraction of the sample failed
