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
# Only weekly breakout and monthly ATH breakout gate on volume; weekly SMA
# support and daily swing dropped it as a condition.
VOLUME_LOOKBACK = 20
WEEKLY_VOLUME_MULTIPLIER = 1.3
MONTHLY_VOLUME_LOOKBACK = 12
MONTHLY_VOLUME_MULTIPLIER = 1.3

# --- Support tests (price testing a moving average from above) ----------
# The candle's low is allowed to dip this much *above* the MA and still
# count as "taking support" (it does not need to touch the MA exactly).
SMA_SUPPORT_WEEKLY = 30
WEEKLY_SUPPORT_TOLERANCE = 0.02   # 2% for weekly 30-SMA support
DAILY_SUPPORT_TOLERANCE = 0.02    # 2% for daily 44-SMA support

# --- Daily swing: SMA44 / lower Bollinger Band confluence ---------------
SMA_SWING = 44
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2
CONFLUENCE_TOLERANCE = 0.02  # SMA44 and lower BB must sit within 2% of each other

# --- Weekly breakout ------------------------------------------------------
BREAKOUT_RANGE_WEEKS = 6         # look at the 6 candles preceding the breakout candle
BREAKOUT_RANGE_TIGHTNESS = 0.15  # (range_high - range_low) / range_low must be <= 15%

# --- Stop-loss / target construction -------------------------------------
SL_BUFFER = 0.02          # extra cushion placed below the structural stop level
RISK_REWARD_TARGETS = (2, 3)          # for support/confluence style entries -> T1, T2
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
UPSTOX_EQUITY_SEGMENT = "NSE_EQ"
UPSTOX_REQUEST_DELAY_SECONDS = 0.25  # spacing between historical-candle calls
UPSTOX_MAX_RETRIES = 3

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
