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
# An old resistance *zone* - validated by multiple distinct swing-high
# peaks near each other, not just a single exact price - approached
# repeatedly without breaking and then finally broken on strong volume,
# later gets retested from above; if a bullish candle holds that
# old-zone-turned-support level, that's the "change in polarity". (An
# earlier version required the zone to be the stock's own all-time high
# specifically, on the theory that it's the strongest possible support -
# reverted: it made signals extremely rare (18 in 12 months, too few to
# even evaluate), so the zone is back to being any well-tested recent
# resistance, not necessarily the ATH. A daily-timeframe version was also
# tried and dropped: even after tightening its resistance-zone criteria
# hard, backtesting over 12 months showed a genuinely negative edge, not
# just noise.)
CIP_ZONE_TOLERANCE = 0.02        # how wide the resistance zone is below its own top
CIP_VOLUME_MULTIPLIER = 1.5      # breakout candle's volume vs its trailing average
CIP_RISK_REWARD_TARGETS = (2, 3)  # T1/T2 as multiples of entry-to-SL risk

CIP_WEEKLY_TOUCH_LOOKBACK = 12      # weeks scanned for zone touches before a candidate breakout
CIP_WEEKLY_BREAKOUT_SEARCH = 26     # how many weeks back a qualifying breakout can still count
CIP_WEEKLY_MIN_ZONE_POINTS = 2      # the zone must show at least this many distinct swing-high peaks before breaking

# A daily-timeframe "Daily Swing" strategy used to run here (Darvas Box,
# then Darvas + CANSLIM, then SMA-30 support, then Accumulation Spring's
# Wyckoff base/breakout/retest, then a "pocket pivot" volume-anomaly
# entry). None of the five variants showed positive expectancy over a
# 12-month backtest - the slot was retired rather than tuned further; see
# git history if reviving daily-timeframe signals is worth trying again.

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
