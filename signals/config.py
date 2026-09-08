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

# A "Weekly Darvas Box" strategy used to run here (replacing CIP, a
# resistance-zone/retest strategy). Tightening it enough to be
# higher-conviction than Weekly Range Breakout collapsed it to 3 signals
# in 12 months - not a usable sample - and its looser version was just a
# slower, noisier version of Weekly Range Breakout's own tight-range-then-
# breakout idea. Dropped rather than kept alongside a near-duplicate; see
# git history if reviving box-based weekly signals is worth trying again.

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
# 200 SMA is a pure "above it" long-term filter (no slope requirement - a
# 200-week SMA moves too slowly for a rising check to mean much and just
# shrinks the candidate pool for no real signal). The actual trend check is
# this faster 50-week SMA, which must itself be rising.
BREAKOUT_TREND_SMA = 50

# --- Stop-loss / target construction -------------------------------------
SL_BUFFER = 0.02          # extra cushion placed below the structural stop level
# Breakout SL is anchored to the breakout level itself (old resistance ->
# new support), not the bottom of the consolidation range - the 12-month
# backtest showed the wider range_low anchor produces a high win rate but
# a handful of large tail losses that drag average return negative.
#
# Widening the targets from (1, 2) to (1, 3) was tried and reverted: it
# changed nothing (avg win +5.1% -> +5.0%, PF 1.00 -> 0.98). Root cause
# turned out to be architectural, not a threshold: simulate_forward exits
# a trade the first day ANY target is touched (using the highest one
# reached that same day), so it never keeps walking forward to see if a
# farther target would eventually be hit too - in practice nearly every
# winning trade exits at T1, and a farther T2/T3 only matters on the rare
# day price gaps past both at once. A fixed multi-tier target list can't
# "let winners run" past the nearest one; that needs a genuinely
# different exit (a trailing stop, as tried for Daily Swing at one point)
# not a bigger number here.
BREAKOUT_RANGE_MULTIPLES = (1, 2)      # measured-move multiples of the range height
ATH_BREAKOUT_TARGET_PCTS = (0.15, 0.25)  # open-ended ATH breakouts: %-based T1, T2

# --- Transaction costs (Indian cash-equity delivery trades) --------------
# Every signal here is a delivery trade (held days to months, never
# intraday), where brokerage is genuinely 0 at every major Indian discount
# broker (Zerodha, Upstox, etc.) - the real, unavoidable cost is
# regulatory, not the broker's cut:
#   STT (Securities Transaction Tax): 0.1% of the buy value + 0.1% of the
#     sell value = 0.20% round-trip (delivery equity; intraday/F&O differ)
#   Stamp duty: 0.015% of the buy value only (uniform pan-India since 2020)
#   Exchange transaction charges + SEBI turnover fee: ~0.003% per side,
#     ~0.006% round-trip
#   GST (18% on brokerage + exchange charges): negligible here since
#     brokerage is 0 and exchange charges are already tiny
# Backtest returns are net of this. Deliberately excluded, both pushing
# real costs higher than this estimate rather than lower: DP (depository)
# charges, a flat ~Rs 15-20 per scrip sold rather than a percentage (so
# not modelable without an assumed position size), and slippage (a real
# fill will not land exactly on the recorded entry/exit price).
ROUND_TRIP_COST_PCT = 0.22   # % of trade value, applied once per entered trade

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
