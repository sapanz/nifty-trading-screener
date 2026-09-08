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
# Weekly breakout and monthly ATH breakout gate on volume; daily swing
# doesn't (it gates on the SMA44/lower-BB confluence instead).
VOLUME_LOOKBACK = 20
WEEKLY_VOLUME_MULTIPLIER = 1.3
MONTHLY_VOLUME_LOOKBACK = 12
MONTHLY_VOLUME_MULTIPLIER = 1.3

# --- Support tests (price testing a moving average from above) ----------
# The candle's low is allowed to dip this much *above* the MA and still
# count as "taking support" (it does not need to touch the MA exactly).
DAILY_SUPPORT_TOLERANCE = 0.02    # 2% for daily 44-SMA support

# The 44 SMA being tested for support must itself be rising (trending up),
# not flat or falling - compares its current value against this many
# periods back. The 200 SMA has no such requirement: price just needs to
# be above it, trend can be sideways or rising.
SMA_SLOPE_LOOKBACK = 3

# --- Daily swing: SMA44 / lower Bollinger Band confluence ---------------
SMA_SWING = 44
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2
CONFLUENCE_TOLERANCE = 0.02  # SMA44 and lower BB must sit within 2% of each other
RISK_REWARD_TARGETS = (2, 3)  # T1/T2 as multiples of entry-to-SL risk

# --- Weekly Darvas Box (replaces CIP Weekly) ------------------------------
# Nicolas Darvas only ever bought stocks consolidating into a tight box
# that was itself sitting at a fresh new high, then breaking out of that
# box on volume - applied here on the weekly timeframe (this slot
# previously ran CIP, a resistance-zone/retest strategy - dropped in
# favor of this; see git history). The box is variable-length - a
# genuine consolidation can run short or long - so the strategy searches
# backward for the shortest qualifying box between
# DARVAS_WEEKLY_BOX_MIN_WEEKS and DARVAS_WEEKLY_BOX_MAX_WEEKS immediately
# before today, rather than assuming a single fixed window.
DARVAS_WEEKLY_NEW_HIGH_LOOKBACK = 52        # ~52 weeks: the box's top must be a new high over this many weekly candles
DARVAS_WEEKLY_NEW_HIGH_TOLERANCE = 0.02     # how much the box top may sit below the actual 52-week high and still count as "new"
DARVAS_WEEKLY_BOX_MIN_WEEKS = 3             # minimum consecutive weeks price must hold inside the box to confirm it
DARVAS_WEEKLY_BOX_MAX_WEEKS = 15            # give up looking for a box for a given peak after this many weeks
DARVAS_WEEKLY_BOX_TIGHTNESS = 0.12          # (box_top - box_bottom) / box_bottom must be <= 12%
DARVAS_WEEKLY_VOLUME_MULTIPLIER = 1.5       # breakout candle's volume vs its trailing average
DARVAS_WEEKLY_RISK_REWARD_TARGETS = (2, 3)  # T1/T2 as multiples of entry-to-SL risk

# Five other "Daily Swing" variants were tried and dropped in this slot
# before landing back on the original SMA44/lower-BB confluence version
# above (Darvas Box, Darvas + CANSLIM, SMA-30 support in ATH stocks,
# Accumulation Spring's Wyckoff base/breakout/retest under both a fixed
# target and a trailing stop, and a "pocket pivot" volume-anomaly entry)
# - none showed positive expectancy over a 12-month backtest; see git
# history for that tuning trail.

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
