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

# --- Volume candle --------------------------------------------------------
VOLUME_LOOKBACK = 20
DAILY_VOLUME_MULTIPLIER = 1.5
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

# --- NSE historical-data scraping ------------------------------------------
# No auth, no daily token - but NSE's public API is undocumented, rate
# limited, and blocks obviously bot-like traffic. Requests are chunked and
# spaced out accordingly; see signals/nse_client.py.
NSE_HISTORICAL_URL = "https://www.nseindia.com/api/historical/cm/equity"
NSE_CHUNK_DAYS = 360          # stay safely under NSE's per-request date-range limit
NSE_REQUEST_DELAY_SECONDS = 0.4
NSE_MAX_RETRIES = 3

# Single daily-history depth, reused (via resampling) for weekly, monthly,
# and the monthly all-time-high check - one NSE scrape per run serves every
# strategy that fires that day. 8 years comfortably covers a weekly
# SMA200 lookback (~4y) with margin; it also caps how far back the
# monthly ATH check can "see" (see README caveats).
DAILY_HISTORY_YEARS = 8
