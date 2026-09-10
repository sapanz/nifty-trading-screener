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
# getting rejected and closing well below it. Global rule - every
# strategy's is_proper_close() check shares this one threshold.
MAX_UPPER_WICK_RATIO = 0.20  # (high - close) / (high - low) must be <= this

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

# --- Daily swing: SMA / lower Bollinger Band confluence -----------------
# 44 was the original value. 50 tested marginally better on a 5-year
# backtest (PF 1.37 -> 1.41, avg return +1.3% -> +1.4%, win rate flat at
# ~40%) and was kept - a small, non-conclusive edge, not a dramatic one.
SMA_SWING = 50
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2
CONFLUENCE_TOLERANCE = 0.02  # SMA44 and lower BB must sit within 2% of each other
RISK_REWARD_TARGETS = (2, 3)  # T1/T2 as multiples of entry-to-SL risk

# Found by inspecting a 5-year backtest's trade CSV directly (diagnostic
# columns logged but not gated on until now): trades with below-average
# volume were net losers outright (PF 0.91), and trades still close to the
# 200 SMA underperformed ones with more room already built up above it.
# Neither filter touches entry/stop/target sizing, so it doesn't widen risk
# (that lever - a risk_pct floor - was tried separately and reverted per
# explicit direction: quick, low-SL trades are the point here) - it only
# trims which setups get taken. Requiring both together moved a 5-year
# backtest from PF 1.19 (2236 signals) to PF 1.39 (356 signals), with the
# improvement holding across most individual years (2022, a broad market
# downturn, is the one year it doesn't).
DAILY_SWING_MIN_VOL_RATIO = 1.0
DAILY_SWING_MIN_DIST_FROM_SMA200_PCT = 15

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
BREAKOUT_RANGE_TIGHTNESS = 0.20  # (range_high - range_low) / range_low must be <= 20%
# 200 SMA is a pure "above it" long-term filter (no slope requirement - a
# 200-week SMA moves too slowly for a rising check to mean much and just
# shrinks the candidate pool for no real signal). The actual trend check is
# this faster 30-week SMA, which must itself be rising. (50-week was tried
# and came back worse than no trend filter at all - PF 0.87 vs 0.91 - see
# git history; 30-week at least matched baseline while cutting more noise.)
#
# Also used by Daily Swing as a multi-timeframe confirmation (the weekly
# chart must be trending too, not just the daily pullback) - shared rather
# than duplicated, since both strategies mean the same thing by "the weekly
# trend SMA".
BREAKOUT_TREND_SMA = 30

# How far above the consolidation range's high the breakout candle's close
# may sit - (entry - range_high) / range_high. Found by inspecting a 5-year
# backtest's trade CSV directly: barely-there breakouts (0-4% above the
# range) mostly fail (weak, low-conviction, easily reversed), and anything
# beyond ~12% is often already extended - including a genuine bug case
# where the breakout candle's own close had already run past the fixed
# measured-move target (target1), guaranteeing a loss before the trade
# even started. Restricting entries to this 4-12% band alone flipped a
# 5-year backtest from net-losing to net-profitable (PF 0.81 -> 1.09).
BREAKOUT_MIN_EXTENSION = 0.04
BREAKOUT_MAX_EXTENSION = 0.12

# --- Stop-loss / target construction -------------------------------------
SL_BUFFER = 0.02          # extra cushion placed below the structural stop level
# Weekly's SL sits at the midpoint of the consolidation range, not the
# breakout level itself - see weekly_breakout.py. Price often comes back to
# retest the range as support after breaking out, and a stop right at the
# breakout level gets hit by that normal retest rather than a genuine
# failed breakout.
#
# Widening T2 from (1, 2) to (1, 3) was tried and reverted: it changed
# nothing (avg win +5.1% -> +5.0%, PF 1.00 -> 0.98). Root cause turned out
# to be architectural, not a threshold: simulate_forward exits a trade the
# first day ANY target is touched (using the highest one reached that same
# day), so it never keeps walking forward to see if a farther target would
# eventually be hit too - in practice nearly every winning trade exits at
# T1 (313 of 315 weekly wins), and a farther T2 only matters on the rare
# day price gaps past both at once. Widening T2 alone can't "let winners
# run" past T1.
#
# Widening T1 ITSELF is a different lever - mining the 5-year backtest's
# diagnostic columns (vol_ratio, extension_pct, tightness_pct,
# dist_from_sma200_pct, rsi14) for a selectivity filter found nothing
# robust (every threshold tried was either non-monotonic across buckets or
# fell apart when checked year-by-year - one looked good only because of a
# single outlier year, PF 12.39 on 31 trades - not implemented). The real
# problem was structural instead: a 75% win rate should support a far
# higher PF than 1.14 at only a 4.6%/12.3% avg-win/avg-loss ratio - there
# was slack to trade some win rate for a bigger win before PF suffers.
# 1.5x/3x (vs 1x/2x) confirmed it: PF 1.14 -> 1.34, avg return +0.3% ->
# +1.4%, avg win +4.6% -> +9.8%, win rate 75% -> 64% (a real trade-off, but
# not a collapse like the earlier trailing-SMA-exit attempt's 67% -> 21%).
BREAKOUT_RANGE_MULTIPLES = (1.5, 3)      # measured-move multiples of the range height
ATH_BREAKOUT_TARGET_PCTS = (0.15, 0.25)  # open-ended ATH breakouts: %-based T1, T2

# Minimum months a stock must have spent below its old all-time high before
# a fresh breakout counts as a signal. Found by inspecting a 5-year
# backtest's trade CSV directly: months_gap correlates positively and
# almost monotonically with performance - a breakout only 1-2 months after
# the last ATH is still noisy/choppy, not a genuine fresh breakout out of a
# real base. Requiring months_gap > 3 moved a 5-year backtest from
# PF 1.42 (1510 trades) to PF 1.66 (530 trades).
MONTHLY_MIN_GAP_MONTHS = 3

# --- Futures OI buildup ---------------------------------------------------
# Classic price+OI matrix: price up + OI up = Long Buildup (fresh longs
# entering, bullish); price up + OI down = Short Covering (shorts being
# forced out, also bullish - the covering itself is buying pressure). Both
# quadrants are traded the same way here; the other two (Short Buildup,
# Long Unwinding - price down either way) are bearish and not traded, since
# this system only goes long. Day-over-day, the textbook definition, not an
# N-day-smoothed variant - there's no historical F&O trade CSV to mine a
# smoothing window from the way the other three strategies' thresholds
# were tuned (see tools/debug_futures.py: Upstox only serves a contract's
# own ~2-3 month life, no expired-contract history), so this stays close
# to the standard definition rather than adding unvalidated parameters.
# Reuses RISK_REWARD_TARGETS/WEEKLY_VOLUME_MULTIPLIER's *values* below
# rather than inventing different-sounding "tuned for leverage" numbers
# that would misrepresent this as tuned when it hasn't been backtested at
# all - validate this one forward (paper-track live signals) rather than
# against history, since history isn't available.
FUTURES_VOLUME_MULTIPLIER = 1.3
FUTURES_RISK_REWARD_TARGETS = (2, 3)

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
# v2's historical-candle endpoint only accepts "day" as an interval -
# "week"/"month" 400 there (confirmed live, not just undocumented). Weekly
# and monthly native fetches use v3 instead, which takes interval as a
# separate {unit}/{multiple} pair (e.g. weeks/1, months/1) rather than a
# single day-only segment - see UpstoxClient._get_history_v3.
UPSTOX_BASE_URL_V3 = "https://api.upstox.com/v3"
UPSTOX_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"
UPSTOX_EQUITY_TYPE = "EQUITY"  # instrument_type value for cash-market equities (confirmed live, not "EQ")
UPSTOX_REQUEST_DELAY_SECONDS = 0.25  # spacing between historical-candle calls
UPSTOX_MAX_RETRIES = 3

# If fewer than this fraction of requested symbols map to an Upstox
# instrument_key, something is systemically wrong (e.g. the instrument
# master's column values changed shape) rather than a handful of unlisted
# symbols - fail loudly instead of quietly scanning nothing.
MIN_INSTRUMENT_MATCH_RATIO = 0.5

# --- F&O (stock futures) ---------------------------------------------------
# Only ~150-220 of Nifty 500 actually have futures listed - unlike equities,
# a low match ratio against the full 500-symbol list is expected here, so
# this is a floor on the absolute count, not a fraction (see
# data.build_futures_instrument_map). Confirmed live (2026-09-10): 629
# FUTSTK rows / ~210 unique underlyings in the same instrument master file
# used for equities.
MIN_FO_MATCH_COUNT = 100

# Daily-history depth for Daily Swing (the only strategy left resampling
# off of it). 6 years comfortably covers its SMA200 lookback with margin.
DAILY_HISTORY_YEARS = 6

# Weekly Range Breakout fetches its own native weekly candles directly
# (UpstoxClient.get_weekly_history) rather than resampling the daily
# fetch above, so each weekly candle matches what Upstox itself
# considers "the week's" OHLCV rather than a pandas resample of daily
# bars. This used to be 6 (same as DAILY_HISTORY_YEARS), on the
# assumption that a ~4-year 200-week SMA lookback left plenty of room -
# it doesn't: with only 6 years fetched, ~4 of them are consumed just
# building the SMA200 lead-in before any signal can fire at all, leaving
# a real usable window of only ~2 years regardless of how far back a
# backtest asks to look (confirmed directly: a 5-year backtest's weekly
# signals all clustered in the most recent ~2 years). 12 years leaves a
# genuine ~8-year usable window after the same ~4-year lead-in.
WEEKLY_HISTORY_YEARS = 12

# Monthly ATH Breakout fetches its own native monthly candles directly
# (UpstoxClient.get_monthly_history) rather than resampling the capped
# daily fetch above, specifically so "all-time high" means what it says.
# Monthly candles are cheap enough that a deep lookback costs almost
# nothing: 25 years is ~300 candles/symbol vs ~6,300 for the same span at
# daily granularity. It won't reach a handful of decades-old listings
# (Reliance, ITC, etc. IPO'd well before this) - Upstox just returns
# however much history actually exists rather than erroring, so those
# still get a much deeper (if not literally all-time) ATH check than the
# old 6-year cap gave every symbol. Raise this further if that gap matters.
MONTHLY_ATH_HISTORY_YEARS = 25

# --- Circuit breaker --------------------------------------------------------
# If the data source is blocking/rejecting requests wholesale (as NSE direct
# scraping turned out to do from GitHub Actions), fail fast after a small
# sample instead of grinding through hundreds of symbols for hours.
CIRCUIT_BREAKER_SAMPLE_SIZE = 20     # check failure rate after this many symbols
CIRCUIT_BREAKER_FAILURE_RATIO = 0.8  # abort if this fraction of the sample failed
