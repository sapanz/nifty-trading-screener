# nifty-trading-screener

Automated Nifty 500 technical screener that posts Entry / Stop-Loss / Target
levels to Telegram, on a schedule, for six strategies (one of which,
Weekly Value Stocks Breakout, screens the full NSE instead of the Nifty
500 - see the table below):

| Strategy | When | Trigger |
|---|---|---|
| **Daily Swing** | Every trading day, 5pm IST | Above 200 SMA, rising 50 SMA; a bullish candle with a proper close takes support at the 50 SMA and also reaches down to the lower Bollinger Band, which itself sits right on top of the 50 SMA — all three (SMA, band, candle) converging at once — plus volume at/above average and price already well clear of the 200 SMA |
| **Price Action Breakout (Daily)** | Every trading day, 5pm IST | **Long:** above 200 SMA; the *longest* tight prior base found (8-40 daily candles, whatever the data actually supports, accumulation-biased) breaks out *today*, closing above the base's high, bullish, properly closed, on clearly elevated volume (2x average) - entered immediately, no retest wait. **Short (F&O-eligible symbols only):** the exact mirror - below 200 SMA, a distribution-biased base breaks *down* today, closing below the base's low, bearish, properly closed, on elevated volume. The base's shape (Range, Ascending/Descending/Symmetrical Triangle, Rising/Falling Wedge) is labeled from the slope of its highs and lows - a heuristic, not real geometric pattern recognition. Entry is a resting stop at today's (the breakout candle's own) high (long) or low (short), stop-loss is today's own low (long) or high (short), capped at 5% risk from entry (a signal needing a wider stop is skipped, not tightened to fit); targets are a measured move off the base's own height; signals sort by the base's own range %, largest first. Backtested over 5 years (1,194 signals, 30% win rate, PF 1.23) - see [Known limitations](#known-limitations) |
| **Price Action Breakout (Weekly)** | Fridays, 5pm IST | The same base-detection and shape classifier as the daily leg above (5-20 week base), but a different, retest-based trigger, not the daily leg's immediate entry - see [How the rules are encoded](#how-the-rules-are-encoded) for why the two timeframes diverge. **Long:** above 200 SMA; a base breaks out on clearly elevated volume (within the last 6 weekly candles), a later candle pulls back to retest that level without invalidating it, then today closes back above it, bullish and properly closed - entry is a resting buy-stop at today's high, stop-loss the lower of the retest's low and today's low. **No risk-pct cap and no reward:risk gate** on this leg, unlike the daily leg - a signal fires whenever the sequence completes, whatever risk/reward that implies. **Short (F&O-eligible symbols only):** the exact mirror. This exact combination (6-candle window, ungated) hasn't been backtested yet - see [Known limitations](#known-limitations) |
| **Weekly Range Breakout** | Fridays, 5pm IST | Above 200 SMA, rising 30 SMA, last 6 weekly candles form a tight range with more volume on up candles than down (accumulation), close breaks above the range by 4-12% (not a weak break, not already extended), breakout candle is bullish (green) with a proper close, volume candle, and the resulting target clears at least 1:1 reward:risk against the entry-to-stop distance; entry is a resting buy-stop at the breakout candle's high, filled only once a later candle trades through it |
| **Monthly ATH Breakout** | Last trading day of the month, 5pm IST | Monthly close breaks above its prior all-time high on volume, the breakout candle is bullish (green) with a proper close, at least `MONTHLY_MIN_GAP_MONTHS` (3) months after that prior high; reports how many months it took, sorted longest-dormant first. No reward:risk floor here, unlike the other strategies - tried and reverted, see [Known limitations](#known-limitations) |
| **Weekly Value Stocks Breakout** | Fridays, 5pm IST | The only strategy here not limited to the Nifty 500 - it screens the **full NSE** for stocks currently passing a fundamental value screen (profit growth > 25%, debt/equity < 0.5, market cap > ₹5000 Cr - scraped from screener.in, which requires logging in), since that's the whole point of using a fundamental screen instead of just filtering the Nifty 500 by price action. Within that universe, the only price-action condition is weekly close breaking above the highest weekly close in at least 52 candles (a genuinely dormant-value high, not a stock already mid-trend) - no bullish-candle, proper-close, or volume filter on top, per explicit direction, since the fundamentally-screened universe is already small. The 52-week floor was briefly removed, then restored for live use once a test run confirmed why it's needed: without it, an already-trending stock re-signals on nearly every subsequent weekly high instead of once (one test run went from 31 signals to 1114 over the same window, almost entirely repeat signals on a handful of trending names) - see [Known limitations](#known-limitations) for the full story, including the real stock (Engineers India) that motivated trying to remove it in the first place. Entry is the breakout candle's own close; **no fixed stop-loss and no profit target at all** - held as long as the weekly close stays above its own 30-week SMA, exiting the next trading day's open the first week it closes back below. A candidate replacement for Price Action Breakout (Weekly), pending a backtest comparing the two - both run live for now. First real 5-year backtest (full-NSE, with the floor in place): 31 signals, 81% win rate, PF 53 - promising but too small a sample to call validated yet |

No manual judgement calls at run time — every "properly closed candle" /
"volume candle" / "support test" rule is a precise, testable condition (see
[How the rules are encoded](#how-the-rules-are-encoded)).

## How it runs

There's no server to keep online. A single GitHub Actions workflow does
the work on a cron schedule and posts straight to Telegram:

- `.github/workflows/signals.yml` — Mon-Fri, 11:37 UTC (5:07pm IST; a
  deliberately odd minute, not the round 11:30 - see caveat below). It
  first logs into Upstox automatically (`scripts/login_upstox.py`, via
  TOTP), then `scripts/run_signals.py` fetches daily OHLCV **once** and
  always runs the daily swing screener and Price Action Breakout's daily
  leg (both off that same fetch), additionally runs the weekly range
  breakout and Price Action Breakout's weekly leg on Fridays (sharing one
  native-weekly fetch between them), and additionally runs the monthly
  ATH breakout on the last trading day of the month, off its own
  native-monthly fetch (Price Action Breakout has no monthly leg - see
  below) — one Upstox pass per timeframe serves every strategy that fires
  that day, whatever the day. It also makes one cheap instrument-master
  call (no price history) for the F&O-eligible symbol set that gates
  Price Action Breakout's short leg (`data.fetch_fo_eligible_symbols`) -
  see [How the rules are encoded](#how-the-rules-are-encoded) for why
  shorting is scoped to that subset. A failure fetching it just disables
  shorts for that run rather than failing the whole screener.

  **GitHub Actions' `schedule` trigger is best-effort, not exact** - a
  cron time is a request, not a guarantee, and GitHub can delay a
  scheduled run (or even skip it) during high platform load, especially
  at popular on-the-hour/half-hour minutes that every other repo's cron
  jobs also target. Live runs on the old `:30` schedule showed 3-5 hour
  delays (5pm IST scheduled, signals arriving 8-10pm IST); `:37` is an
  attempt to sidestep that queue contention, not a fix for it - actual
  delivery time still isn't guaranteed.

  Because of that, a run can land at any hour, including well past IST
  midnight - and GitHub Actions runners use UTC, not IST. Every "what
  day is it" decision in this codebase (message labeling, month-end
  detection, the Upstox fetch's date bound) goes through
  `calendar_utils.ist_today()` rather than the machine's local
  `date.today()`, so a late-running job doesn't silently mislabel a
  message with the wrong calendar day. Each signal also carries the
  actual date of the candle that qualified it (`candle_date`) - if that
  ever falls behind the run date (a stale/delayed Upstox fetch, or a
  market holiday the code doesn't otherwise know about), the Telegram
  message flags it explicitly instead of silently assuming the two
  match.

  That IST fix turned out not to be the whole story: Upstox's
  historical-candle endpoint is backward-looking only and never includes
  the *current* trading day, confirmed live even ~2 hours after market
  close (a run at 5:27pm IST still had no candle for that day). Without
  a same-evening fix, every Daily Swing run would silently signal off
  yesterday's close, one full day behind, no matter how correctly the
  "what day is it" logic above ran. `data.fetch_daily` now tops each
  symbol up with its own candle from Upstox's separate v3 *intraday*
  endpoint (`UpstoxClient.get_intraday_daily_candle`) whenever the
  historical fetch doesn't already reach today - if that also comes back
  empty (before the market opens, or a holiday) it just falls back to
  the historical data as before, rather than failing the whole fetch.
  Weekly Range Breakout and Monthly ATH Breakout have the same
  underlying gap for their own still-forming current period (this week /
  this month) - not yet fixed the same way, since synthesizing a partial
  week/month candle needs combining several days' worth of data rather
  than fetching one extra candle; less urgent since they only run once a
  week or month.

It can also be triggered manually from the **Actions** tab ("Run
workflow"), with checkboxes to force the weekly/monthly strategies to run
on any day for testing.

Market data comes from the **Upstox API**. Two alternatives were tried
and rejected first: **yfinance** is unreliable for bulk NSE data, and
**scraping NSE's own historical-data API directly** (no auth needed at
all) turned out to be blocked outright from GitHub Actions' cloud IP
ranges — a live run hung for 90+ minutes with every request failing. The
Nifty 500 constituent list is still fetched fresh from NSE's own archives
(a lightweight, one-off CSV download, unrelated to the blocked per-symbol
historical endpoint).

The catch with a broker API: Upstox access tokens **expire daily**
(~3:30am IST) and there's no refresh-token mechanism, only a login. This
repo solves that with fully automated TOTP login
(`signals/upstox_login.py`), which runs as the first step of every
workflow run: it drives Upstox's real login page headlessly (mobile
number → a TOTP code generated from your authenticator secret) and mints
a fresh access token for that run only — nothing to do daily. See [Security trade-offs](#security-trade-offs-of-totp-auto-login)
before setting this up; `tools/refresh_upstox_token.py` remains available
as a manual fallback if you'd rather not store login credentials at all.

## One-time setup

### 1. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
   follow the prompts. You'll get a **bot token** like `123456:ABC-DEF...`.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find
   `"chat":{"id": ...}` — that number is your **chat ID**.
   - For a group chat, add the bot to the group first, send a message
     there, then look for the group's (negative) chat ID the same way.

### 2. Create an Upstox app

Register an app at [developer.upstox.com](https://developer.upstox.com/)
to get an **API key** (client ID) and **API secret**. While registering,
set the **redirect URI** to anything syntactically valid, e.g.
`https://localhost/callback` — it never needs to actually resolve to
anything; the login automation intercepts the redirect before the
browser tries to load it.

### 3. Enable TOTP (authenticator app) 2FA on your Upstox account

In the Upstox app/website's account security settings, switch your
second factor from SMS OTP to an authenticator app. During setup, look
for a link like **"can't scan the QR code? enter this key manually"** —
that string (a short base32 code) is your `UPSTOX_TOTP_SECRET`. Save it
now; most apps only show it once.

### 4. Add GitHub repository secrets

In this repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | your chat ID |
| `UPSTOX_CLIENT_ID` | from your Upstox app |
| `UPSTOX_CLIENT_SECRET` | from your Upstox app |
| `UPSTOX_REDIRECT_URI` | must exactly match what you registered in step 2 |
| `UPSTOX_MOBILE_NUMBER` | your Upstox login mobile number — exactly 10 digits, no `+91`/`91` prefix |
| `UPSTOX_TOTP_SECRET` | from step 3 |
| `UPSTOX_PIN` | your 6-digit Upstox login PIN |
| `SCREENER_EMAIL` | your screener.in account email — needed only for Weekly Value Stocks Breakout's fundamental screen; screener.in requires a logged-in (free) account to run a custom-screen query at all, anonymous requests get redirected to its registration page |
| `SCREENER_PASSWORD` | your screener.in account password |

(The real flow, confirmed against a live account: mobile number → a
verification code screen, where the TOTP code from step 3 fills the
"OTP or TOTP" field instead of a texted SMS OTP → a "Hi \<name\>, welcome
back" screen asking for your 6-digit login PIN before Upstox completes
the authorization. No separate account *password* is ever needed.)

There is no `UPSTOX_ACCESS_TOKEN` secret to manage — it's minted fresh at
the start of every run and only exists in that run's memory.

### 5. Test it

Go to the **Actions** tab → **Nifty500 Signals** → **Run workflow**. Tick
**force_weekly** and/or **force_monthly** to exercise those strategies on
a day when they wouldn't normally fire.

**Expect the first run to need one fix.** The login automation
(`signals/upstox_login.py`) drives Upstox's real login page, but its
field selectors were written without being able to load that page live —
there's no officially documented way to log in headlessly, so this is
the least-bad option, not a verified one. If the "Log in to Upstox
(TOTP)" step fails, download the `upstox-login-failure` artifact from
that run (a screenshot of the page at the point it got stuck) and share
it — the fix is almost always a one-line selector update in
`signals/upstox_login.py`.

## Security trade-offs of TOTP auto-login

This setup stores your Upstox TOTP secret **and** your login PIN as
GitHub Actions secrets, which is more sensitive than anything else in
this repo handles — together they're the entire gate on logging into
your account (plus your mobile number, which isn't really a secret).
Concretely:

- These secrets are only ever readable by workflows running in this
  repo — never logged in plaintext (the derived access token is
  explicitly masked in `scripts/login_upstox.py` before it's used) and
  not visible to anyone browsing the repo, including you, once saved.
- Anyone with admin/write access to this repository's secrets could use
  them to log into your Upstox account. Keep this repo private (it
  already is) and don't add collaborators you wouldn't trust with your
  broker login.
- If you ever suspect these have leaked: re-link your authenticator app
  in Upstox's security settings immediately (issues a new TOTP secret,
  invalidating the old one), change your login PIN, then update both
  GitHub secrets.
- If this trade-off stops feeling worth it, switch back to
  `tools/refresh_upstox_token.py` (manual daily refresh, no credentials
  stored at all) by removing the "Log in to Upstox (TOTP)" step from
  `.github/workflows/signals.yml` and restoring an `UPSTOX_ACCESS_TOKEN`
  secret.

## How the rules are encoded

All thresholds live in [`signals/config.py`](signals/config.py) — tune
them there rather than in the strategy code.

- **"Properly closed candle"**: `(high - close) / (high - low) <= MAX_UPPER_WICK_RATIO`
  (0.20) — the close sits in the top 20% of the candle's range (small
  upper wick). A global rule - every strategy's proper-close check shares
  this one threshold.
- **"Volume candle"**: volume >= 1.3x the trailing 20-period average.
  Weekly breakout and monthly ATH breakout require this; Daily Swing gates
  on the SMA/lower-BB confluence primarily, plus its own separate volume
  and trend-extension filters (below).
- **Daily Swing**: `signals/strategies/daily_swing.py`. Above the 200 SMA
  (long-term uptrend), with the `SMA_SWING` (50) SMA itself rising - not
  flat or falling - and price testing support at it (low within
  `DAILY_SUPPORT_TOLERANCE` above the SMA, closing back above). On top of
  that, the SMA and the lower Bollinger Band (`BOLLINGER_PERIOD`,
  `BOLLINGER_STD`) must sit within `CONFLUENCE_TOLERANCE` of each other -
  two independently-computed support levels lining up is a stronger signal
  than either alone - and the candle's low must reach down to the lower
  band too, not just the SMA. All three - the SMA, the lower band, and
  the candle itself - have to converge at once: the same candle must also
  be bullish (close > open) with a proper close (small upper wick), the
  same "properly closed candle" test used elsewhere in this table. It's
  easy to read this as just "SMA sits on the band" from a quick summary,
  but the candle's own shape and its low both have to line up there too,
  not only the two moving levels. On top of all of that, volume must be at
  or above its own trailing average and price must already sit a healthy
  distance above the 200 SMA (`DAILY_SWING_MIN_VOL_RATIO`,
  `DAILY_SWING_MIN_DIST_FROM_SMA200_PCT`) - found by mining a 5-year
  backtest's diagnostic columns: below-average-volume pullbacks were net
  losers, and pullbacks still close to the 200 SMA underperformed ones with
  more established trend beneath them; requiring both moved that backtest
  from PF 1.19 to PF 1.39. Finally, a multi-timeframe check: the weekly
  `BREAKOUT_TREND_SMA` (30-week, the same trend SMA Weekly Range Breakout
  uses) must itself be rising too, not just the daily one - a stock can
  look fine on a daily pullback while its weekly chart is flat or rolling
  over, and this rejects that case. (44 was the original `SMA_SWING`; 50
  tested marginally better - PF 1.37 -> 1.41 - and was kept. Several other
  Daily Swing designs - Darvas Box, CANSLIM overlays, ATH-proximity SMA-30
  support, Wyckoff-style base/breakout/retest, a volume-anomaly "pocket
  pivot" - were tried later and dropped without beating this original
  version; see git history.)
- **Weekly breakout range**: the 6 weeks preceding the breakout candle
  must have a high-low range within 20% of the range low, i.e. a genuine
  consolidation, not just drift. (This is currently the only weekly
  strategy: CIP, a resistance-zone/retest strategy, and Weekly Darvas Box,
  a variable-length box-then-breakout strategy, both ran in this slot
  before - Darvas Box tightened enough to beat Weekly Range Breakout's own
  numbers collapsed to 3 signals in 12 months, too few to trust, and its
  looser version was just a slower, noisier version of the same
  tight-range-then-breakout idea already covered here; see git history.)
- **Monthly ATH**: all-time high is the max monthly close within a
  dedicated deep monthly fetch (`MONTHLY_ATH_HISTORY_YEARS`, 25 years by
  default - see caveat below), fetched separately from the 6-year daily
  history the other two strategies use, since a genuine all-time high
  needs much more lookback than a trend/range check does. A breakout
  within `MONTHLY_MIN_GAP_MONTHS` (3) months of that prior high is
  excluded - found by inspecting a 5-year backtest's trade CSV directly,
  `months_gap` correlates positively and almost monotonically with
  performance, and requiring it to be > 3 moved that backtest from
  PF 1.42 (1510 trades) to PF 1.66 (530 trades).
- **Weekly Value Stocks Breakout**: `signals/strategies/value_breakout.py`.
  A fundamental screen gates the universe first - `signals/value_universe.py`
  logs into screener.in (`SCREENER_EMAIL`/`SCREENER_PASSWORD` - a custom-screen
  query is gated behind a logged-in account, anonymous requests get redirected
  to screener.in's registration page) and scrapes its custom-screen query
  feature for `VALUE_SCREEN_QUERY` (config.py: profit growth > 25%, debt/equity
  < 0.5, market cap > ₹5000 Cr) - only symbols currently passing that screen
  are even considered,
  regardless of price action. Unlike every other strategy here, this isn't
  scoped to the fixed Nifty 500 universe - the whole point of using a
  fundamental screen is to reach the full NSE, so `scripts/run_signals.py`/
  `scripts/run_backtest.py` build a separate instrument map and weekly
  (plus, for the backtest, daily) Upstox fetch scoped to exactly the
  symbols screener.in's query returns, rather than reusing the shared
  Nifty 500 fetch every other weekly strategy shares. Within that universe,
  the only price-action condition is this week's close above the highest
  weekly close in at least `VALUE_BREAKOUT_MIN_GAP_WEEKS` (52) candles - a
  genuinely dormant-value high, not a stock already mid-trend. This floor
  was briefly removed (it was excluding fundamentally-qualifying stocks
  already mid-uptrend, like Engineers India, which can never clear an
  old-high requirement) and then restored for live use once removing it
  was actually tested: signal count went from 31 to 1114 over the same
  5-year window, almost entirely repeat signals on the same handful of
  already-trending stocks (see Known Limitations and
  `signals/strategies/value_breakout.py`'s docstring for the full story).
  Unlike Monthly ATH Breakout and Price Action Breakout, there's no
  bullish-candle check, no "proper close" check, and no volume
  confirmation - per explicit direction, since this fundamentally-screened
  universe is already small, stacking technical filters on top of the
  fundamental one would starve it of trades further. Unlike every other
  strategy here, there's no fixed stop-loss or target at all - see
  `backtest.simulate_weekly_trailing_sma` for the trailing-exit mechanics.
  Built as a candidate replacement for Price Action Breakout (Weekly), per
  explicit direction, pending a backtest comparing the two - both run live
  until that decision is made.
- **Price Action Breakout**: `signals/strategies/price_action_breakout.py`.
  Daily and weekly now run genuinely different entry logic - `scan()`
  (daily) and `scan_retest()` (weekly) - not the same function called
  twice with different window sizes; see the `PRICE_ACTION_*_DAILY` /
  `_WEEKLY` families in config.py for the two sets of window sizes each is
  called with. No monthly leg on either - a 5-year backtest of the
  retest-based version showed it never fired under these thresholds, and
  Monthly ATH Breakout already covers that timeframe.

  Both share the same base-detection and shape classification. Above the
  200 SMA, `_detect_base` walks backward looking for the *longest* window
  (between each timeframe's `PRICE_ACTION_PATTERN_MIN_LOOKBACK_*` and
  `_MAX_LOOKBACK_*`) whose high-low band still stays within
  `PRICE_ACTION_RANGE_TIGHTNESS` (20%, the same convention Weekly Range
  Breakout's own range uses) with more volume on its up candles than down
  (`is_accumulation_range`, again shared with Weekly Range Breakout) - the
  base's length is genuinely detected per signal, not a fixed number, and
  `_classify_shape` labels its shape (Range, Ascending/Descending/
  Symmetrical Triangle, Rising/Falling Wedge) from the slopes of straight
  lines fit through its highs and lows. `_classify_shape` fits a straight
  line (least-squares) through the base's highs and another through its
  lows, and labels the combination of slopes (flat/rising/falling per
  `PRICE_ACTION_FLAT_SLOPE_PCT`) - flat top + flat bottom is a **Range**,
  flat top + rising bottom an **Ascending Triangle**, falling top + flat
  bottom a **Descending Triangle**, falling top + rising bottom a
  **Symmetrical Triangle**, both rising (top slower) a **Rising Wedge**,
  both falling (top faster) a **Falling Wedge**. This is a real
  classification, but a heuristic one - slope sign and magnitude, not
  genuine trendline-touch-point geometry - and is labeled as such rather
  than dressed up as more rigorous than it is. (A separate attempt to make
  the breakout TRIGGER itself trendline-aware, not just the shape label,
  was tried and reverted - it worked as designed but backtested worse on
  both timeframes; see [Known limitations](#known-limitations).) Every
  signal's note and `extra` dict report the base's detected length
  (`base_candles`), its calendar start/end (`base_start`/`base_end`), and
  its shape (`breakout_type`); signals sort by the base's own range %
  (`sort_key`), **largest range first**, per explicit request - not by
  volume or recency.

  **Daily (`scan()`)**: beyond the base, just *today's own candle* -
  closing above the base's high, bullish with a proper close, on volume
  at least `PRICE_ACTION_BREAKOUT_VOLUME_MULTIPLIER` (2x - deliberately
  higher than every other strategy's volume bar, since "high volumes" was
  the explicit ask for the breakout itself). That's the whole trigger -
  entered immediately on the breakout candle, no retest/reclaim wait.

  **Weekly (`scan_retest()`)**: a two-candle-minimum sequence instead -
  (1) a breakout candle, within the last `PRICE_ACTION_BREAKOUT_WINDOW_
  WEEKLY` candles, closing above the base's high on the same elevated
  volume; (2) at least one later candle pulling back to retest that broken
  level (within `PRICE_ACTION_RETEST_TOLERANCE`) without any close in
  between falling `PRICE_ACTION_INVALIDATION_PCT` below it - the level
  held as support rather than failing; (3) *today* closing back above the
  level, bullish with a proper close - the actual signal trigger;
  everything before it is context this candle confirms. Both directions
  originally ran this retest-based logic; daily was rewritten to enter
  immediately per explicit direction ("due to retest, I am getting bit
  late in trade") and the rewrite held up in its own 5-year backtest, but
  weekly's version of the same rewrite backtested far worse (see
  [Known limitations](#known-limitations)), so weekly reverted to this
  retest-based trigger per explicit direction while daily kept the
  immediate-entry rewrite - the two timeframes are not meant to be
  read as one algorithm at two zoom levels any more.

  **Short leg (breakdown), F&O-eligible symbols only, both timeframes**: a
  cash-segment equity short can't be carried overnight in India for
  retail - it's intraday-only unless the position is actually a sale of
  the stock's futures contract, so shorting a symbol with no futures
  market isn't practically actionable the way a long always is.
  `short_eligible` (`data.fetch_fo_eligible_symbols` →
  `UpstoxClient.fetch_fo_symbol_set`, the `name` column of the same
  instrument master's `FUTSTK` rows) gates which symbols the short side is
  even considered for; the long side is unaffected and still runs on the
  full universe either way. Detection is the *exact mirror* of the long
  side on each function, same thresholds throughout, just flipped: below
  the 200 SMA, `_detect_base` requires a **distribution** base
  (`is_distribution_range` - down-volume beats up-volume, not the long
  side's `is_accumulation_range`), and the trigger closes *below* the
  base's low instead of above its high, bearish with a proper close
  (`is_bearish` + `is_proper_close_bearish`), on the same elevated volume.
  No short-specific tuning anywhere in `config.py`.

  The short leg's F&O-universe caveat applies to both timeframes: the
  F&O-eligible slice of the universe is much smaller, and Upstox's
  instrument master only gives *today's* F&O universe, not a historical
  snapshot per date - see [Known limitations](#known-limitations) for what
  that means for backtesting it.

**Entry/stop-loss differ by strategy:**
- **Daily Swing**: entry is the signal candle's **high** (a buy-stop
  triggered the next day price trades up to it); stop-loss is the
  **lower of the signal candle's own low and the previous candle's low**.
  Targets are risk-multiples of that entry-to-SL distance
  (`RISK_REWARD_TARGETS`, 2R/3R by default).
- **Weekly Range Breakout**: entry is a resting buy-stop at the breakout
  candle's own **high** (like Daily Swing, not an immediate fill at its
  close) - the trade only enters once a later candle actually trades up
  through that high, confirming the breakout continues rather than
  assuming it does from the close alone; an entry never reached is
  reported "unfilled". Stop-loss sits at the **midpoint of the
  consolidation range**, not the breakout level itself - price often
  comes back to retest the range as support after breaking out, and a
  stop right at the breakout level gets hit by that normal retest, not
  just a genuine failed breakout. Targets are measured-move projections
  of the range height (`BREAKOUT_RANGE_MULTIPLES`, 1.5x and 3x), required
  to clear at least `MIN_REWARD_RISK_RATIO` (1:1) against the entry-to-
  stop distance - range height and stop distance are sized independently,
  so a tight range plus a near-max-extension entry can otherwise clear
  every other filter while not earning back its own risk. Widening
  T2 only (1x/2x -> 1x/3x) was tried and reverted - it changed nothing,
  since `simulate_forward` exits at the first target touched and nearly
  every winning trade exits at T1 long before T2 is ever reached. Widening
  T1 itself (1x -> 1.5x, keeping T2 proportional at 3x) is a different
  lever and did move the numbers: PF 1.14 -> 1.34, avg return +0.3% ->
  +1.4%, win rate 75% -> 64% (still solid, not a collapse) - a deliberate
  trade of some win rate for meaningfully bigger wins, motivated by the
  original 75%-win-rate/1.14-PF combination being far below what that win
  rate should support at a healthier win/loss ratio. See the note in
  `signals/config.py`.
- **Monthly ATH Breakout**: entry is the candle's close, stop-loss sits
  just under the prior all-time high with a 2% buffer, and targets are
  open percentage-based (15%/25%) since a fresh all-time high by
  definition has no prior resistance to aim at. No `MIN_REWARD_RISK_RATIO`
  gate, unlike Weekly Range Breakout - tried and reverted; see Known
  limitations.
- **Weekly Value Stocks Breakout**: entry is the breakout candle's own
  close - same reasoning as Monthly ATH Breakout, an open-ended breakout
  has no prior resistance to set a resting buy-stop against. **No fixed
  stop-loss and no target at all** - per explicit direction, this is the
  one strategy in the whole project that isn't walked forward with a
  fixed stop/target (`backtest.simulate_forward`); instead it's held as
  long as the weekly close stays above its own trailing 30-week SMA
  (`BREAKOUT_TREND_SMA`) and exits at the **next trading day's open** the
  first week it closes back below (`backtest.
  simulate_weekly_trailing_sma`) - a Friday close that breaches the SMA
  can't be acted on until markets reopen the following week. `Signal.
  stop_loss` still carries the 30-week SMA's value at signal time, but
  purely as a display figure ("here's where the trailing level starts") -
  it's never checked as a hard exit price; the real level is recomputed
  fresh every future week.
- **Price Action Breakout (daily, `scan()`, long)**: entry is a resting
  buy-stop at **today's** (the breakout candle's) own high - same buy-stop
  construction every other strategy here uses, filled only once a later
  candle trades up through it. Stop-loss is **today's own low** - there's
  no retest to anchor it to, since entry happens immediately on the
  breakout candle - **capped at `PRICE_ACTION_MAX_RISK_PCT_DAILY` (5%) of
  entry**: a signal whose natural stop would sit further away than that is
  skipped outright, not tightened to fit (a breakout candle with that wide
  a range is a messy one). Targets are a measured move
  (`PRICE_ACTION_TARGET_MULTIPLES`, 1x/2x) - the base's own height
  projected up from the breakout level, the same idea as Weekly Range
  Breakout's `BREAKOUT_RANGE_MULTIPLES` off its own range height -
  required to clear the stricter `PRICE_ACTION_MIN_REWARD_RISK_RATIO`
  (2:1) against the (already-capped) risk, checked independently of the
  risk cap since base height and the breakout candle's own range aren't
  tied to each other.
- **Price Action Breakout (daily, `scan()`, short, F&O-eligible only)**:
  the exact mirror - entry is a resting sell-stop at today's own **low**;
  stop-loss is **today's own high**, same risk cap and reward:risk
  requirements; targets project the same base height *downward* from the
  breakdown level.
- **Price Action Breakout (weekly, `scan_retest()`, long)**: entry is a
  resting buy-stop at the **confirmation candle's** (today's) own high -
  not the breakout candle's, since entry only happens once the retest has
  held and today reclaims the level. Stop-loss is the **lower of the
  retest's own low and today's own low** (the support just demonstrated
  holding) - **no risk-pct cap and no reward:risk gate**, unlike the daily
  leg: a signal fires whenever the base/breakout/retest/confirmation
  sequence completes, whatever risk or reward that implies (reverted from
  a gated version per explicit direction, to reproduce the strategy's
  original ungated numbers - see [Known limitations](#known-limitations)).
  Targets are still a measured move off the base height, just measured off
  the retest instead of today's own candle range.
- **Price Action Breakout (weekly, `scan_retest()`, short, F&O-eligible
  only)**: the exact mirror - entry is a resting sell-stop at today's own
  **low**; stop-loss is the **higher of the retest's own high and today's
  own high**, same (absence of) risk cap and reward:risk requirements.

Daily Swing and Price Action Breakout's daily leg share the daily fetch
(`data.fetch_daily`, the "day" interval, once per run) - the second one is
free, not an extra Upstox call. Weekly Range Breakout and Price Action
Breakout's weekly leg share one native-weekly fetch (Fridays); Monthly ATH
Breakout gets its own native-monthly fetch (month-end, no Price Action
Breakout leg to share it with) - `data.fetch_weekly_history` ("week") and
`data.fetch_monthly_ath_history` ("month"), each only on the day its
strategies actually run. Weekly's native
fetch was originally motivated by candle accuracy rather than depth (each
weekly candle should match what Upstox itself considers "the week's"
OHLCV, rather than a pandas resample of daily bars that can draw week
boundaries slightly differently around a holiday-shortened week), but it
turned out depth mattered here too: `WEEKLY_HISTORY_YEARS` is now 12, not
6, because the strategy's 200-week SMA lead-in alone eats ~4 years, and
6 years left only ~2 years of real usable signal window regardless of
how far back a backtest asked to look. Monthly's native fetch has always
been about depth: `MONTHLY_ATH_HISTORY_YEARS` (25 years) so its
all-time-high check isn't silently capped at 6 years.

## Market dashboard

A web page (`docs/index.html`) showing **sector day change** (equal-weighted
average move of each sector's Nifty 500 members, with advancers/decliners and
turnover) and the **highest-volume stocks**, filterable by sector, F&O-only,
gainers/losers and symbol search, sortable by volume, turnover, change or
volume vs. its 20-day average - for picking intraday candidates.

How it works, all free: `.github/workflows/dashboard.yml` starts at 09:10 and
14:30 IST on weekdays, logs in to Upstox, and `scripts/run_dashboard.py`
fetches live quotes for the whole Nifty 500 (two API calls) every 2 minutes
until 15:35, publishing the snapshot as `data.json` on the `dashboard-data`
branch (one force-pushed commit, so no history builds up). The page, served by
GitHub Pages from `/docs`, reads that file every minute. To switch it on once:
repo **Settings -> Pages -> Source: Deploy from a branch -> `main` / `/docs`**;
the page is then at `https://<user>.github.io/<repo>/`. Anyone with the link can
open it (it holds market data only; the Upstox token never leaves the job).
The page reads the live files through GitHub's API, which revalidates on every
poll (so a new snapshot appears within about a minute; raw-file URLs are cached
for ~5 minutes whatever the query string, so they're only the fallback if the
API's 60 requests/hour per-IP limit is hit). Expect data a couple of minutes old,
not tick-by-tick. Running the workflow manually with
"once" ticked publishes a single snapshot for a smoke test (a manual run
cancels a session in progress - they share a concurrency group).

### Previous-day high / low filter

A **Prev-day level** filter and a **vs prev-day H/L** column (Live and Replay)
show where each stock trades against yesterday's high (PDH) and low (PDL):
*above PDH* / *below PDL* (trading beyond it now), *broke PDH / PDL, back inside*
(touched beyond a level at some point today but is back in the range - a
failed breakout to know about before chasing), *broke both*, or *inside range*.
The filter options also include "broke ... (any time today)" and the table can
be sorted by distance above PDH or below PDL, and combined with the sector,
F&O-only, gainers/losers and R-factor controls. Live, yesterday's levels come
from the daily history the dashboard job already fetches, kept only where that
history's last close matches the previous close implied by the live quote
(history sometimes lags a session; a level from the wrong day is worse than
none) and "today" uses the quote's day high/low. Replay uses the levels stored
in each history day file plus the first 5-minute slot in which each was broken,
so a replay at 10:30 only knows about breaks up to 10:30; day files built before
this feature don't have them and say so until the nightly rebuild.

### R-factor and OI change columns

For F&O stocks the table also shows **OI chg %** (open interest added since
09:15) and an **R-factor** (0-100), and you can sort by either (the "Sort by"
box or the column headers). R ranks each F&O stock, among all F&O stocks at that
moment, on (1) volume vs. its 20-day average, (2) OI added since 09:15 and (3)
the size of the price move since 09:15, then takes the geometric mean of the
three ranks - so it is high only when volume, OI and price move are all high
together, and 0 where OI has fallen. The ▲/▼ tag says whether the buildup is
bullish (OI up, price up) or bearish (OI up, price down). Ranks are relative, so
R is not comparable between moments; it is a way to surface candidates, not a
signal, and the weights (equal) are a judgement call, untested against outcomes.
Live it needs `oi.json` (refreshed every 5 minutes); in Replay it uses the day
files' OI arrays.

**Does R-factor work? Not so far.** `scripts/backtest_rfactor.py` (no Upstox
needed - it reads the Replay day files) scores R at seven moments a day (10:00
to 13:00) over the ~20 replay sessions and measures what price did next in the
buildup's direction, net of 0.10% costs. Result (first run, 24 Sep - 23 Oct
2026): the top-5 by R lost about 0.08% per trade over the next 30 min and
about 0.07% over 60 min, no better than every stock with OI building, and
higher R was not better than lower R (R>=70, 40-69 and <40 all within 0.01% of
each other) - i.e. no gradient. The one number that looked interesting (top-5
by volume alone, direction = price since open, held to the close: +0.13%, 58%
winners) is not significant once each session is counted once (t = 1.2). The
window is short (the current futures contract's life), so this says "no
evidence", not "proven useless"; re-run it as the replay history grows.

### Swing view (R-factor on daily charts)

The **Swing** tab applies the R-factor idea to daily charts, for holding
periods of days, and is built once each evening after the close
(`.github/workflows/dashboard_swing.yml`, started by the 15:45 IST history build, `scripts/build_dashboard_swing.py`
-> `swing.json` on the `dashboard-swing` branch). F&O stocks only. Over the last
5 sessions each stock is ranked on (1) the latest session's volume vs. the
average of the 20 sessions before it, (2) open interest added, % and (3) price
move, % - R is the geometric mean of the three ranks, 0 where OI did not grow,
tagged ▲ (OI up, price up) or ▼ (OI up, price down) - the same since-the-open
definition as the intraday R-factor, over sessions instead of candles. The table
also shows 1D/5D price change and 1D/5D OI change; the sector panel shows the
5-day sector move; clicking a stock opens the same buy/sell OI chart in daily
form (1 day / 2 days / 1 week groups).

Open interest across contract rolls is the awkward part: near-month OI alone
collapses in expiry week because traders roll to the next month, so the OI used
is the **sum of the near and next-month contracts**, and since the pair of
contracts changes at each expiry (and an expired contract's history can't be
fetched) each night's total is **saved** (`swing_oi.json`, on the same branch)
and the series is built from the saved totals. The first run seeds about a month
from the two live contracts. A 5-session window that includes an expiry date is
flagged **⚠ roll**: the OI change then includes settled and rolled positions.
There is no backtest of the swing R-factor yet - only about a month of
contract history exists, so it needs the saved series to grow first.

**Strongest order blocks and the chart.** Live (and Replay, for days rebuilt after this was added) has a
**Strongest order blocks** panel above the table: the top 20 bullish and top 20 bearish F&O zones, strongest first, each
with a strength bar, the zone, waiting / retesting / in zone, the stop distance and the stock's R-factor (✓ when its OI
buildup agrees with the zone's direction). A minimum-R filter and the sector filter apply to it. Clicking a card (or a
stock with a zone) opens a **Chart & order block** tab: today's 5-minute candles with the zone shaded, the stop, nearest
swing support / resistance, liquidity pools (two or more swing highs / lows within 0.15% - where stops rest), yesterday's
high / low and the last price, plus why the zone scored what it did. Strength (0-100) =
thrust size 40 + thrust volume 25 + a liquidity sweep before the thrust (the leg first took out an earlier swing low / high) 20 +
going with the stock's direction on the day 15 (`signals/intraday_blocks.py: block_score`); the backtest
(`intraday_ob_backtest.yml`) reports how the score ranks outcomes. The scan also publishes `candles.json`
(candles and levels for stocks that currently have a zone).

### Order blocks (intraday, 5-minute)

An **order block** is the last opposite-coloured candle before a thrust that breaks the recent swing high (bullish: the
last red candle) or swing low (bearish). Its range is the zone; price often comes back to it before the move resumes, and
the zone's low (plus 0.1%) is a tight stop. In **Live** mode, F&O stocks get an **Order block** column and filter:

- **waiting** - the thrust happened today and price has not come back yet;
- **retesting** - price touched the zone in the last 15 minutes without trading through the far edge; **in zone** if it is inside it now;
- **stop %** - distance from the live price to the stop; sort by "Order block: smallest stop %".

A third loop in the dashboard job (`scripts/run_dashboard.py`) rescans every F&O stock's *completed* 5-minute candles every
5 minutes (yesterday's session is only warm-up) and publishes `blocks.json`; detection is `signals/orderblocks.py`,
the live states are `signals/intraday_blocks.py`. **Replay** shows the same column and filter for any stored day and
5-minute mark: the nightly day files carry each F&O stock's blocks of that day with the slots they formed, were retested
and were closed through (`ob`), and the page works out the state at the chosen moment (a test checks it agrees with the
live scan at every slot). Days built before this was added show no Order block column until the history is rebuilt.

Backtests (`orderblock_backtest.yml` for daily charts, `intraday_ob_backtest.yml` for 5-minute) measure retest trades net of
costs against a random-entry baseline with the same stop/target distances. The daily test (Nifty 500, 2020-2026) found no
reliable standalone edge: out-of-sample profit factor 0.90 overall, bullish blocks slightly positive over the whole period
(+0.11% per trade, PF 1.05) but negative in 2022, 2024 and 2026, bearish blocks clearly negative. Treat the column as a way
to find candidates and place a stop, not as a signal.

**Trendlines and confirmations (Swing).** `signals/swing_trendlines.py` finds, for each stock, the widest *intact* daily
trendline: a rising **support** line through at least 3 swing lows (or a falling **resistance** line through 3 swing highs),
20+ sessions wide, no close through it, drawn on the chart. A zone gets **TL ✓** when the line that confirms its direction (support
for bullish, resistance for bearish) runs through the zone now or within 3 candles. Each card shows three separate confirmations:
**zone** (strength 60+: price structure), **trendline** (confluence), **flow** (R-factor 50+ with open-interest buildup in the zone's
direction: money flow). "Rank by: Most confirmations first" (default) sorts by how many agree, then by zone strength; "Zone + trendline
only" and the table options "Bullish / Bearish zone + trendline" show only the confluence setups. R-factor and zone strength measure
different things (positioning vs. price structure), so neither replaces the other - they are two independent checks.

**Finding setups before the entry.** The panel (and the Order block filter) only lists zones price is *close to*: the **Distance
to zone** control defaults to within 1.5% intraday / 3% swing (or "in the zone"), because a zone price has already run far
from is a missed move - e.g. a bearish zone formed ten days ago with price 10% below it. Each card says how far away price is
and whether it is waiting or retesting; "Rank by: Closest to the zone first" and the table sort "closest to the zone" put the
imminent setups on top. Raise the distance (or choose Any) to see every zone; the Max stop filter limits the risk.

**Swing (daily) order blocks and manual backtesting.** (Supply / bearish zones are shown too, with their losing record next to them - see "Removed tools".) The same panel, column, filter and chart exist on the **Swing** tab
for daily candles (`signals/swing_blocks.py`, built each evening by `scripts/build_dashboard_swing.py`: zones formed in the last
40 sessions that are still fresh or being retested, scored the same way, "with the trend" meaning the structure-breaking close is
above / below the close 20 sessions earlier; `swing_candles.json` carries the daily candles and levels for the chart). The Swing tab has an **As of** picker (and ◀ day / day ▶ buttons) for manual backtesting on daily charts: choose any of the
last 60 closes and the page shows the snapshot as it stood that evening - zones and their fresh / retesting state, the
Strongest lists, sector and stock table, and a chart cut at that date (nothing after it) - built each night into
`swing_days/<date>.json` on the `dashboard-swing` branch from the daily history cut at that date (R-factor and OI appear only
for dates where open interest had been saved; earlier dates show it blank). To check
what the Bullish / Bearish lists showed at any moment, use **Replay**: pick a date and a 5-minute time (or step with ±5 min) and
the **Strongest order blocks** panel, the Order block column / filter and the chart tab (candles up to that moment, zone, stop,
levels from that day's candles) all show what the page would have shown then. The day files carry `ob` plus `ox`/`hx`/`lx`
(open / high / low per slot) for F&O stocks that had a zone; days built before this show no panel until the history is rebuilt
(every weekday at 15:45 IST, or run "Dashboard history" by hand).

### More zone filters (Swing tab)

The "Strongest order blocks" panel has a second row of filters, all about the zone (they combine, and the settings are remembered):

- **Trendline near the zone** (within 0.5 / 1 / 2 / 3 / 5%) - how far the confirming trendline (support for a bullish zone, resistance for a bearish one) is from the zone, now or up to 3 candles ahead; 0 means it runs through the zone. "Within 1%" is the same test as the **Zone + trendline only** box (`zone_gap_pct` in `signals/swing_trendlines.py` is the reference; the page has a JS twin).
- **Price near the trendline** (within 1 / 2 / 3 / 5 / 8%) - how far price is from that line.
- **R-factor at least** 30-90 and **OI flow agrees** (R at least 50 with the open-interest buildup in the zone's direction). R-factor needs saved open interest, so it exists for about the last three weeks only - on older as-of dates these two show nothing.
- **Zone strength at least**, **Bullish zone test** and **Bearish zone test** - one for each column, so you can ask for bullish zones that are being retested and, independently, bearish zones that are being retested (each: any / fresh / retesting / held after a wick through; a stock is listed if any of its zones on that side matches, not only its best one), **With the trend**, **Liquidity sweep**, **Volume thrust at least 2x**, **Zone formed within** 1 week / 2 weeks / 1 month.
- **Rank by** also offers highest R-factor, trendline closest to the zone, price closest to the trendline, and newest zone. Each card shows the line's distance from the zone, the price's distance from the line, and the zone's age. **Reset filters** clears everything.

Narrowing the list is not the same as improving it: in the backtests, zone strength, trendline confluence and the other zone filters did not add a measurable edge over trading in the market's direction (see the swing backtests above), so use these to find setups that fit your plan, not as proof a setup is better.

### Which Swing zone filters pay? (6-year backtest, with ROI)

`scripts/backtest_swing_filters.py` (data: `equity_daily.csv.gz` from the `research-data` branch, 218 F&O stocks, Nov 2020 - Nov 2026) trades every daily order-block retest the way the Swing tab shows it (limit entry at the zone, stop 0.1% beyond it, 2R target, 20-session limit, **0.22% round-trip cost**) and records each zone's features as of the evening before the entry: strength and its parts, trendline confluence, distance, age, stop %, the market's side. 7,452 trades. ROI = average net return per trade; R = average result in units of the risk; an average trade lasts 8 days. The flow filter (R-factor / OI) **cannot be backtested**: only about 90 days of futures open interest exist.

| Long (demand) zones | Trades | ROI / trade | R | Won | PF | 2021-23 | 2024-26 |
|---|---|---|---|---|---|---|---|
| all long zones | 3,786 | **0.00%** | +0.07 | 38% | 1.00 | +0.26% | -0.20% |
| thrust of 4+ ATR | 527 | +0.66% | +0.25 | 44% | 1.34 | +1.19% | +0.27% |
| zone strength 70+ | 299 | +0.65% | +0.19 | 43% | 1.31 | +1.06% | +0.36% |
| thrust volume 2x+ | 554 | +0.46% | +0.16 | 41% | 1.22 | +1.11% | -0.05% |
| strength 60+ **and** trendline confirms | 203 | +0.62% | +0.15 | 41% | 1.33 | +0.15% | +1.05% |
| thrust 4+ ATR **and** volume 2x+ | 170 | +0.89% | +0.35 | 46% | 1.45 | +1.18% | +0.65% |
| strength 70+ **and** trendline | 55 | +1.07% | +0.29 | 47% | 1.60 | +1.08% | +1.06% |
| thrust 4+ ATR, strength 60+ and trendline (all three) | 46 | +2.37% | +0.70 | 59% | 2.70 | +2.68% | +2.14% |
| trendline alone | 778 | -0.09% | +0.06 | 38% | 0.96 | -0.06% | -0.12% |

What it says: **the zone's own strength is what pays on the long side** - the size of the thrust, the volume behind it and the combined strength score - and the trendline helps only on top of a strong zone; on its own it does nothing. Zone strength 70+ was positive in five of six years and thrust 4+ ATR in five of six (2026 flat); strength 60+ with a trendline was mixed by year (+0.6, -0.7, +0.5, +2.6, +0.8, -0.2). The three-way combination looks best but is only 46 trades (about 8 a year) and was picked after looking, so treat it as a lead, not a proven rule. Filters on distance (within 1.5% / 3% of the zone), the zone's age (formed within a week / two weeks / a month), stop size, the market's side, "with the trend", a liquidity sweep and the recent volume surge did **not** improve the long side (ROI -0.2% to +0.05%). A month-block bootstrap puts the gain of thrust 4+ ATR over the other trades at +0.4 to +1.2 points and of strength 70+ at +0.2 to +1.3; the two-filter combinations are too small to bound. A walk-forward test (combinations of up to three filters chosen on 2021-2023, judged on 2024-2026) kept only part of the gain: the best in-sample combination fell from +2.5% to +0.5%, so expect the live result of any filter to be well below its backtest.

**Short (supply) zones lose whatever the filter:** all 3,666 trades averaged **-0.64%** (PF 0.73, negative in every year). The best filters only cut the loss - price within 1.5% of the zone -0.32%, stop 3% or less -0.41%, both -0.24% - and the trendline made it worse (-0.95%). Combinations chosen on 2021-2023 lost -0.6% to -1.5% on 2024-2026. For scale: the filters that work give about +0.6% to +0.9% a trade and only 4-7 qualifying trades a month across the whole universe (strength 70+: 299 trades in six years), each held about 8 days - a few percent a year of capital, far below the Momentum top-10 long (+3.3% a pick). Costs are 0.22% (delivery); today's F&O stocks only and a mostly rising market, as elsewhere.

### Momentum stocks near a zone or trendline (Swing tab)

A table under the momentum lists for picking quickly: choose the **List** (the market's side by default: strongest for longs when the index is above its 50-day average, weakest for shorts when below), the **Momentum pool** (top 20 / 30 / 50 by momentum rank) and **Sort by** nearest zone, nearest trendline (price to the confirming line) or momentum rank; **Zone within** and **Trendline within** (1-8%) drop stocks that are further away. For each stock it shows the nearest zone in the trade's direction (the same numbers as the Strongest order blocks panel: zone, test, distance to its near edge, stop distance, strength), the price's distance to the confirming trendline, how far that line is from the zone, R-factor and the daily range. Click a row for the chart; **As of** works as everywhere in the Swing tab.

Expect short lists once you ask for "near": of the strongest 50 stocks on 4 Nov only 4 were within 5% of a bullish zone and 3 within 8% of their support line - strong stocks run away from their zones and lines. The 6-year test (`signals/momentum_zone_backtest.py`, 218 F&O stocks, Nov 2020 - Nov 2026, 38,871 filled trades) found no edge from combining the two: the top-30 momentum pool averaged -0.06R a trade long and -0.13R short against +0.02R / -0.07R for the rest of the universe, and the earlier +0.69R figure (20 trades in one rising stretch) did not hold up. It is a quick way to find setups with a defined entry and stop, not proof that they are better.

**Volume thrust (optional confirmation).** Both momentum views have a **Volume thrust** filter (Off / ≥ 1.2× / ≥ 1.5× / ≥ 2×) and the table a **Volume** column: the average volume of the last 3 sessions divided by the average of the 20 sessions before those (1.0× = normal, 1.5× = half again as much). It is applied *after* taking the top N, so "top 30, thrust ≥ 1.5×" is a subset of the top 30. `signals/momentum.py::volume_thrust` is the reference (the page has a JS twin, checked against it on every pool size and threshold); the swing build now publishes each stock's daily volume in `swing_candles.json` (`v`), so the filter shows nothing until the first build after this change. In the 6-year test it did not act as a confirmation: on the long side results got worse as the threshold rose (top 30 with thrust ≥ 1.5×: -0.16R a trade; ≥ 2×: -0.35R, PF 0.48), so the filter defaults to Off and is best read as "where has volume spiked".

### Removed tools (and why)

To keep the dashboard clean, the tools that lost money or showed no edge in the 6-year backtests were **deleted** (Nov 2026). The evidence, so nobody has to rebuild them to find out:

| Removed | Evidence |
|---|---|
| Price action **shorts** (every bearish setup) | -0.7% a trade overall; the F&O-only restriction did not help |
| W / M patterns | W pattern -0.15R (200 trades), M pattern -0.05R (258); -0.56% a trade in %-terms |
| Head and shoulders / inverse | -0.67R (short, 35), -0.21R (inverse, 36) |
| Triple tops / bottoms | -0.10R (short, 82), -0.31R (long, 69) |
| Bull / bear flags | bull +0.07R (416) but -0.03% a trade in %-terms, bear -0.46R (183) |
| Candle at a weekly zone (hammer, engulfing, piercing, star and mirrors) | +0.02R long (623), -0.07R short (738): no edge on its own |
| Bearish continuation (halt zone) | -0.49R (64 setups), profit factor 0.42 |
| **Momentum near a zone or trendline** table (since restored, see below) | 38,871 filled trades: top-30 pool -0.06R long, -0.13R short against +0.02R / -0.07R for the rest |

*Restored on request (Nov 2026):* the Swing **supply (short) zones** (F&O stocks) the **Weakest** momentum list and the **Momentum stocks near a zone or trendline** table (strongest or weakest pool, nearest zone and trendline) are back, with their record next to them - supply zones -0.64% a trade (profit factor 0.73, negative every year, best filters -0.24%), the weakest list -3.3% a pick over 20 sessions as shorts. They are shown for study, not because they pay.

*Price action shorts restored on request (Nov 2026):* three bearish setups for F&O stocks - **Bear flag** (a fall of 20% or more, a 2-6 week bounce that keeps more than half of it, then a daily close below the fall's low), **Weekly resistance zone** (price rallies into a weekly supply zone and a daily close comes back out below it; the stop is above the zone; tagged **+ TRENDLINE** and filterable with **Zone + trendline only** when a daily falling-resistance line runs through the zone) and **Trendline breakdown** (a rising daily line through at least three swing lows is broken by a red close; stop above the highs since the last touch), next to the range breakdown retest. A **Direction** filter (long and short / long only / short only) is back. The page and the backtest view show each one's record. 6 years, 218 F&O stocks, entry at the next open, stop and target as planned (reward : risk 1.5 or more), 0.10% costs:

| Setup | Setups | ROI a trade | R a trade | Won | Profit factor |
|---|---|---|---|---|---|
| Bear flag (short) | 183 | -1.92% | -0.46R | 14% | 0.43 |
| Head and shoulders (short) | 35 | -2.68% | -0.67R | 11% | 0.24 |
| Range breakdown retest (short) | 419 | -0.71% | -0.18R | 25% | 0.76 |
| Triple top (short) | 82 | -0.74% | -0.10R | 24% | 0.73 |
| M pattern (short) | 258 | -0.60% | -0.05R | 26% | 0.79 |
| Weekly resistance zone (short) | 82 | -0.60% | -0.10R | 39% | 0.84 |
| Trendline breakdown (short) | 313 | -0.42% | -0.13R | 31% | 0.86 |
| - of which a trendline runs through the zone | 4 | +6.19% | +1.03R | 100% | - |
| All shorts | 1,372 | -0.83% | -0.19R | 25% | 0.73 |
| Triple bottom (long) | 69 | -1.62% | -0.31R | 23% | 0.45 |
| Inverse head and shoulders (long) | 36 | -0.97% | -0.21R | 25% | 0.69 |
| W pattern (long) | 200 | -0.50% | -0.15R | 29% | 0.82 |
| Bull flag (long) | 416 | +0.80% | +0.07R | 23% | 1.24 |
| Triangle breakout (long) | 275 | +0.36% | +0.10R | 35% | 1.14 |
| Multi-year breakout (long) | 103 | +3.97% | +1.30R | 20% | 2.06 |
| All longs (for comparison) | 2,473 | +0.45% | +0.07R | 28% | 1.15 |

*The W / M, head and shoulders, triple and bull / bear flag patterns were restored on request too* (the reversal shapes are found on weekly candles and trigger on a daily close beyond the neckline; the bearish twin is the same detector on inverted prices).

Shorts lost again: every bearish kind is negative overall, the head and shoulders and bear flag by a wide margin (the bear flag negative in all six years); the zone and the M pattern are closest to break-even and the 4 zone + trendline cases are far too few to trust. Among the longs only the bull flag, the triangle and the multi-year breakout are positive. Against the equal-weight index of these stocks, which drifted up +0.87% over the average holding window, the hedged results are zone +1.32%, trendline +0.64%, bear flag -0.90%; that is a statement about stock-versus-index, not a tradable edge, because the hedge is not free. Shown for study and paper-trading.

**Failed breakdown (bear trap) long - tested, no edge** (`scripts/backtest_failed_breakdown.py`, Nov 2026). Since every short lost money even when hedged with the index, the mirror idea was tested: a base's floor breaks on a daily close, then price closes back above the floor within a few sessions - buy that reclaim (stop under the trap's low, target the next resistance or the base's height, next-open entry, 0.10% costs). 954 trades (reclaim within 10 sessions): **+0.04% a trade, profit factor 0.93, 31% reached the target**; the index gained +0.43% over the same days, so hedged it is -0.4%. Other windows (3 / 5 / 20 sessions) and a stricter reclaim (above the band's top) gave +0.1% to -0.03% and the same hedged result. By year +1.3, +0.3, +1.6, +0.3, -0.3, -1.0%. So broken floors were followed by roughly market-like moves, not by a bounce worth buying - it was not added to the dashboard.

Kept: the Momentum top-10 long strategy, the Multi-year breakout, the weekly support zone, range / triangle / cup (about break-even - read their record), the long Swing zones with their strength filters, the Movers list and the research code and scripts (the backtests above can be re-run: `scripts/backtest_swing_filters.py`, `signals/momentum_zone_backtest.py`, `signals/weekly_zone_backtest.py`). Older published data may still carry the removed kinds until the next nightly build; the page ignores them.

### Price action (the fourth mode)

**Price action** lists weekly-structure setups with a daily trigger - long setups and the bearish ones for F&O stocks (bear flag, weekly resistance zone, trendline breakdown, range breakdown retest, M pattern, head and shoulders, triple top - each with its record; see "Removed tools"), W pattern, inverse head and shoulders, triple bottom and bull flag included, but not the reversal-candle or halt-zone setups - for the Nifty 500 plus every F&O stock, built each evening by `scripts/build_dashboard_setups.py` (workflow **Dashboard price action**, `setups.json` + `setups_candles.json` on the `dashboard-setups` branch):

| Setup | Structure (completed weekly candles) | Trigger (daily) |
|---|---|---|
| Weekly support zone | a weekly demand zone (`signals/weekly_zone_backtest.find_zones`) that price has closed inside | a close back above the zone |
| Weekly range breakout | 8-40 weeks in a box 5-25% wide with two spaced tests of each side | a close above the top |
| Triangle breakout | falling or flat swing highs and rising swing lows that converge (symmetric / ascending) | a close above the upper line |
| Cup and handle | two rims within 8% around a rounded 12-45% dip, then a 2-15% handle in the upper half | a close above the rim |
| Multi-year breakout | the highest weekly high of the last 2-5 years, **set at least 52 weeks ago (more than a year old; it was 26 weeks until Nov 2026)**, price within 15% of it | a close above that high - **held, not sold at a target**: out on the first weekly close below the 30-week average (next open), or at the stop |
| **Range breakdown retest (short, F&O only)** | a base of 8-40 weeks, at most 30% wide, whose floor was tested at least twice (weeks 2+ apart); the shortest such base is used | the daily close under the floor is the **breakdown**; within 15 sessions a candle's high gets back to the floor (within 1%), it closes back under the floor band and has a long **upper wick** (at least half its range, close in its lower half) - the failure; the short triggers on a daily close under that candle's low within 3 sessions, the **stop is its high**, the target the next weekly support or the base's height projected down |

Each row gives the **reason** in words, the entry (the latest close once triggered, or the level to close above while waiting), the **stop** (just under the zone, or the lower of the breakout level and the trigger day's low), the **target** (the next weekly resistance - a confirmed weekly swing high or the last three weeks' high - or the pattern's height projected up when nothing is overhead, marked `*`), risk %, reward % and **reward : risk**, which is the default sort. Each row also shows **this type's record** - what that kind of setup did in the past (average R per trade and the number of trades). Filters: **F&O stocks only**, kind of setup, **direction** (long / short), triggered / waiting, minimum reward : risk, maximum risk, and "probability beats break-even". Click a row for the chart, with a **Daily / Weekly** toggle (weekly is where the structure is read, daily where the trigger and stop are set): the structure in blue (zone, box, triangle lines, cup, or the swing points and neckline), the reward as a green box from entry to target and the risk as a red box from entry to stop, with the tags for and against it underneath. `signals/price_action.py` holds the detectors (nothing looks ahead: a test cuts the history at arbitrary days and requires the same earlier triggers), `signals/price_action_model.py` the outcomes and the probability, `signals/price_action_board.py` the list.

**Probability** = how often similar past setups reached the target before the stop (entry at the next open, 60-session time exit, 0.10% costs), from a logistic regression on the planned reward : risk and risk %, the trigger day's volume against its 20-day average, the market's trend (equal-weight index against its 50- and 200-session averages), the stock's own trend (above its 200-session average, momentum rank, distance from the 52-week high) and the kind of setup. Setups from a year ago count half as much as today's, and the figure shown is scaled down by how far the model's year-by-year predictions (each year scored by a model fitted only on setups that had already finished) were above what happened. Next to it is the hit rate that reward : risk **needs** to break even. **Open interest is a tag, not an input**: long buildup / short covering / short buildup / long unwinding over the last five sessions, from the swing build's saved OI - there are only a few weeks of it, too little to measure what it adds.

**Backtest: past setups.** The Price action tab has a second view, **Backtest: past setups**, for going through history by hand. It lists every setup that would have been on the list in the past (planned reward : risk of 1.5 or more, 2021 on), each traded by the same rule as the statistics above - entry at the next session's open, the planned stop and target (the stop counts if one candle touches both), an exit at the close after 60 sessions if neither is reached, 0.10% round-trip costs - with how it ended and the result in R (1R = the amount risked). Pick a **year** (or all), a **kind of setup**, **direction**, **F&O only**, **reward : risk**, **risk**, and a **result** (target hit, stopped, time exit, profit, loss, still open); the line above the table gives the result of exactly the trades you selected (hit rate, share in profit, average R, total R, profit factor, sessions held), so you can compare kinds, years and directions yourself. Open any row for the daily chart cut a few sessions after the exit - the structure, the entry, the stop and target and a ring where the trade ended - with a Daily / Weekly toggle, the reason it was listed and what happened, and **Previous / Next** (or the arrow keys) to step through the list. The data is `setups_hist/<year>.json` plus one small candle file per stock (`setups_hist/c/<symbol>.json`, loaded when a row is opened) on the `dashboard-setups` branch, built nightly by `price_action_board.history`. Only stocks listed today are in it (survivorship bias flatters longs and works against shorts).

**Yes / No in the summary tags**: next to volume, trends and momentum the stock's tag row also says `Global macro supportive: Yes / No`, `Value migration into (out of, for a short) its sector: Yes / No` and `Catalyst: Yes / No`, each with the short reason (hover for the full sentence) - macro Yes = verdict Supportive; value migration Yes = money flowing into the sector for a long, out of it for a short; catalyst Yes = a board meeting in the next three weeks (it can move the stock either way, so that tag is neutral) or an event-sized move, gap or fresh 52-week extreme in the last three weeks in the trade's direction (green). "No data" is shown when a source was unavailable. The three blocks below hold the detail.

**Context when you open a stock** - three blocks under the chart, each line with its reason, none of them inside the probability:

- **Global macro, for this stock** (`signals/stock_macro.py`): the section is about the stock in front of you, in four parts, each with its numbers and reasons, then one verdict (Supportive / Mixed / Against, flipped for a short) from how many parts are with the trade:
  1. **Its sector worldwide** (`signals/peers.py`): a curated peer group per stock (IT services, private and PSU banks, pharma, steel, autos, and about 30 more) maps to a US sector fund and a few listed global peers (Yahoo daily closes). Each is rising / falling / sideways (above its 50-session average and higher than 20 sessions ago, or both the other way) with its 3-month change; the sector fund is also shown against the S&P 500. Stocks in no group use the broad-sector fund, and the page says so.
  2. **Its Indian peers**: from the dashboard's own candles - the group's median 3-month return against the market, how many trade above their 50-session average, where the stock ranks among them, and the strongest and weakest peers. Groups with fewer than 3 members in the data fall back to the whole sector.
  3. **News and investor interest** (`signals/news.py`): headlines of the last 14 days from Google News (Yahoo Finance as a fallback), kept only if they name the company (first two words of its name, so "Tata Steel" does not pick up Tata Motors). Each is read by plain keyword rules - upgrades and higher targets, profit beats, order wins, buybacks and insider buying, approvals, acquisitions, new highs on the buying side; downgrades, profit falls, probes and penalties, senior exits, new lows on the selling side - and the page shows the headline, date, source, the cue that matched, why it matters and a link. A negative cue beats a positive one in the same headline. It is a crude reader of headlines, not stories; no headlines means "no data", never bad news.
  4. **Market-wide backdrop** (`signals/macro.py`): S&P 500, Nasdaq 100, US 10-year yield, Dollar index, Brent crude and USD/INR (Yahoo, Stooq fallback) plus Nifty 50 and India VIX (Upstox). Five factors apply to every stock and each sector adds its own with the reason - IT and pharma bill in dollars so a weaker rupee helps; crude is a cost for autos, FMCG, chemicals, cement and logistics; metals are priced in dollars; lenders and property are rate-sensitive; for oil and gas crude cuts both ways and is not counted. This counts as one background vote (sector-specific factors count on their own).

  Votes: sector worldwide, Indian peers, news, the sector-specific factors and the market-wide backdrop; Supportive needs a net of 2 or more, Against -2 or less. The nightly build fetches global tickers once and headlines only for stocks that are listed (about 0.4 s apart). Every fetch fails soft and says what is missing. These groups, thresholds and cues are rules of thumb, not backtested, and none of it is in the probability. Older data without these sections still shows the single market-wide block.
- **Value migration** (`signals/stock_context.py`): whether money is moving into or out of the stock's sector, as a proxy from price and turnover - the sector's 6- and 12-month return against the market, its rank among sectors, and its share of all turnover in the last 3 months against the year before. "Into" needs all three to agree, "out of" likewise; the stock is also placed as a leader or laggard within its sector. It is not profit-pool data.
- **Catalysts**: board meetings (results dates) and ex-dates from NSE's event calendar and corporate actions (`signals/nse_events.py`, a week back to three weeks ahead), with why each matters - results ahead can gap a stock through its stop - plus the price footprints of news in the stock's own candles: a day of 4% or more on 2.5x volume, a 3% opening gap, a fresh 52-week high or low. If the calendar cannot be read the block says so rather than showing nothing.

Every source fails soft (a missing factor shows as "no data" and counts neither way); `scripts/probe_context_sources.py` (workflow **Context sources probe**, no login) reports which ones answer.

**Range breakdown retest (short).** Added at your request from a Swiggy chart (base Jul-Sep, breakdown 1 Oct, retest 7 Oct with an upper wick, short below the candle's low, stop at its high, target the base's height projected down). 6-year backtest (F&O, 2021-2026): **419 listed setups, -0.71% a trade (-0.18R), profit factor 0.77, 21% reached the target** - negative in every year except 2024 (+0.16%): -3.0%, -1.6%, -0.8%, +0.2%, -0.8%, -0.3%. It is shown because you asked for it, not because it pays. A weekly support zone that fails and is retested the same way was tried as a second source and lost more (-2.3% a trade, 55 setups), so it was dropped. No filter turned it round: with the market below its 50-day average -1.6% (222 trades), above -0.8%; stops of 3% or less -0.7%; trigger-day volume 1.5x+ -0.0% (48 trades, the best cut and still zero). Only ranges are used as the "base" for now; the pattern necklines (double top, head and shoulders) can be added the same way but their short setups also lost money before they were removed.

**Break and retest, both ways (range, weekly zone, trendline).** Extended at your request (Nov 2026): when price breaks a structure and comes back to retest it, the rejection candle shows which side holds. *Bullish*: a base's ceiling, a weekly resistance zone or a falling trendline is broken up by a daily close, price retests it from above and a candle with a long lower wick holds (buy above its high, stop at its low). *Bearish* (F&O only): a base's floor, a weekly support zone or a rising trendline is broken down, retested from below and rejected by an upper-wick candle (sell below its low, stop at its high). The long side is the same code run on inverted prices, so the two cannot drift apart; the zone source returns for the short side (it was tried and dropped in the first version), the trendline source reuses `trendlines.find_break` (daily settings) and tests the retest of the line at the break bar's level. Names: "Range / Zone / Trendline breakout retest" and "... breakdown retest (short)"; one setup per source and side inside the 20-session cool-down. 6 years, 218 F&O stocks, entry at the next open, 0.10% costs:

| Setup | Setups | ROI a trade | R a trade | Won | Profit factor |
|---|---|---|---|---|---|
| Range breakout retest (long) | 637 | +0.31% | +0.04R | 31% | 1.10 |
| Zone breakout retest (long) | 100 | +0.87% | +0.10R | 37% | 1.33 |
| Trendline breakout retest (long) | 85 | -0.21% | 0.00R | 32% | 0.91 |
| **All bullish retests** | 822 | +0.32% | +0.04R | 32% | 1.11 |
| Range breakdown retest (short) | 419 | -0.71% | -0.18R | 25% | 0.76 |
| Zone breakdown retest (short) | 113 | -1.06% | -0.22R | 27% | 0.67 |
| Trendline breakdown retest (short) | 113 | -0.70% | -0.29R | 26% | 0.72 |
| **All bearish retests** | 645 | -0.77% | -0.21R | 25% | 0.73 |

The bullish group is profitable only because of 2023 (+2.04% a trade, 246 setups): 2021 -3.05%, 2022 -0.11%, 2023 +2.04%, 2024 -0.74%, 2025 -0.19%, 2026 -0.43%. Selected on 2021-2023 (+1.29% a trade) it did -0.47% a trade in 2024-2026 (451 setups, 27% won), and it trailed the equal-weight index of these stocks by 0.72% over the average holding window (+1.04% index move) - so no edge. The bearish group lost, as every short has; against the index (+0.69% drift in the window) it was about even (-0.08%), which is not tradable, because the hedge is not free. Shown for study and paper-trading.

What the history says (the five remaining kinds, F&O stocks, Dec 2021 - Nov 2026, 1,752 setups with reward : risk of 1.5 or more, costs included): **+0.12R a trade, profit factor 1.16** - but 103 of those are multi-year breakouts (+1.30R each, carried by five 2023 runners; see below), and the other 1,649 average **+0.05R**.

| Setup | Trades | Reached target | Average per trade | PF |
|---|---|---|---|---|
| Multi-year breakout, 30-week exit (20% end in profit; no target) | 103 | - | +1.30R | 2.55 |
| Weekly support zone | 92 | 43% | +0.11R | 1.15 |
| Triangle breakout | 275 | 32% | +0.10R | 1.14 |
| Weekly range breakout | 1,093 | 23% | +0.03R | 1.04 |
| Cup and handle | 189 | 17% | +0.00R | 1.00 |

By year: 2021 -0.85R (14 trades), 2022 -0.14R, 2023 **+0.96R**, 2024 -0.17R, 2025 -0.20R, 2026 -0.32R - one strong year carries the total. **Multi-year breakout, honestly:** it used to accept a high only 26 weeks old, which let a 9-month-old high count as "multi-year" (Eicher Motors, Nov 2026: a 5-year high set 37 weeks earlier). It now needs the old high to be at least a year old: 103 trades instead of 163. Held until a weekly close below the 30-week average it makes +1.30R a trade (+3.97% a trade; 81 stopped out, 22 closed by the exit, median hold 9 sessions, mean 50) - but it is entirely carried by five 2023 runners (BAJAJ-AUTO +47R, GMRAIRPORT +41R, COLPAL +35R, ONGC +20R, BSE +12R): the best five are 116% of the total, without them the average is **-0.22R**, 2023 alone is +4.5R a trade and the other four years are -1.0R to 0.0R (-0.56R over the 65 trades outside 2023). Older-high variants (1.5 and 2 years) gave +1.8R (74 trades) and +1.2R (38) - the same story on fewer trades. The probability orders setups on unseen years but is no more accurate overall than quoting the base rate, and a higher probability did not mean a better result in R - so read "this type's record" alongside it. It is a planner with honest odds, not an edge. The page's banner and each row show the same figures for the full universe as of each night's build.

### What the 6-year research found (Nov 2026)

A search for a next-day system (target: 20% a month) on 6 years of daily candles for 218 F&O stocks, 22 months of 30-minute candles and the stored OI history, with rules chosen on one period and checked on later ones, 0.10% round-trip costs:

| Family | Result on the unseen period |
|---|---|
| 16 end-of-day measures, top / bottom 5, long / short, next day open to close | none reliably above zero |
| Opening-range breakout, both directions, stop at the other side (87,000 trades) | -0.045R a trade, 45% accuracy; none of 19 entry conditions fixed it |
| 336 intraday "go with / against the day's leaders" rules | 0 positive in both years |
| 60 intraday event rules (gap-and-go, gap fade, big first bar, follow-through, strong into the close) | none reliable across days |
| OI buildup quadrants | 26 days of history - no usable signal |
| Overnight hold of the most volatile names | about +9% a month at 5x at tradeable prices, not statistically reliable, worst month -41% |
| **Momentum list: top 10 by 20+60-day rank, only above the 50-day market average, held ~20 sessions** | **beat holding every stock by +1.2 / +1.6 / +1.4% a month in three separate periods**; +0.8% a month unleveraged on the last one (Jul 2025 - Nov 2026) |

So the one thing with repeatable evidence is the Momentum watchlist's long side, held for weeks, not days: about 2.6% a month unleveraged over the whole history (bull-market years included) with a 27% worst drawdown. Leverage does not turn that into 20% a month - at 5x the worst month was -42% and the drawdown exceeded the account. Costs explain much of the rest: trading daily at 0.10% a round trip is about 2% a month of the position, or 10% a month of margin at 5x. Only stocks in F&O today are in the data (survivorship flatters "buy the winners"), so compare against the "hold every stock" control rather than reading the absolute numbers. `scripts/fetch_research_data.py` (workflow **Research data**) downloads the same history to a temporary `research-data` branch.

### Momentum watchlist (Swing tab)

The Swing tab lists the ten **strongest** liquid F&O stocks (₹20 cr+ traded) under a banner that says whether the market is above its 50-day average (the weakest-stocks list was removed: it showed no edge as shorts). Rank = the average of each stock's percentile ranks of its 20- and 60-session returns; the **market switch** is the equal-weight index of those stocks against its own 50-session average (above: the strongest list is the one to use, below: the weakest is the market's direction). Each card shows 5/20/60-day returns, the 14-session daily range (size the stop from it), distance from the 20-day high/low and turnover. It is computed in the browser from the daily candles the page already loads (`signals/momentum.py` is the reference; the page has a JS twin that is checked against it), so change **As of** to see the list on any past close.

How good is it? `python scripts/backtest_momentum.py` (`signals/momentum_backtest.py`) enters at the next open, holds 5 or 10 sessions, charges 0.10% round trip, and measures **accuracy = the share of picks that ended in profit**, against a random pick in the same direction on the same days (23 Jun - 30 Oct 2026, 850 picks per hold):

| | picks' accuracy | random pick, same direction | edge |
|---|---|---|---|
| all, 5 sessions | 55% | 52% | +4 pts (90% range -1 to +7) |
| market up, long strongest, 5 / 10 sessions | 54% / 52% | 45% / 43% | **+9 pts** (range stays above 0) |
| market down, short weakest, 5 / 10 sessions | 58% / 65% | 61% / 70% | none (-3 / -5 pts) |

So: accuracy is 52-57%, **not 80%**; profit comes from winners (+3.9% to +5.5%) being larger than losers (-3.1% to -3.9%), profit factor 1.5-1.9. The strongest stocks in a rising market beat a random long, but most of that came from one month (29 Jul - 31 Aug; the first half of the up signals showed no accuracy edge) and a 20-day-only rank over a longer sample shrinks it to +3 to +6 points; the weakest stocks did no better than any other short in a falling market (they bounce as often). The history is one up phase and one down phase of ~150 sessions - treat the list as a watchlist to study, not a signal, and re-run the backtest as history grows.

### Strategy: Momentum top-10 long (Swing tab, above the lists)

The strongest-stocks list is also laid out as a strategy with a trade plan for each pick (`signals/momentum.py: trade_plan, exit_day`; the page has a JS twin):

| | Rule |
|---|---|
| **When** | only while the equal-weight market index is **above its 50-day average**; below it, stay in cash (the card says STAND ASIDE) |
| **Entry** | buy the 10 strongest liquid F&O stocks (average percentile rank of the 20- and 60-session returns) at the **next open**, equal weight; size each so the stop costs about 1% of capital |
| **Stop-loss** | **4 × ATR(14)** below the entry price (a gap below fills at the open) |
| **Target** | **none** - hold to the exit day. An optional 8 × ATR (2 : 1) profit-booking level changed nothing (+3.33% against +3.32% a pick) |
| **Exit** | the **close of the 20th session** after the signal, or the stop. Do not exit because the market dips below its average (it cut +3.3% to +2.3% a pick) |
| **Repeat** | every 20 sessions take a fresh top 10, only if the market is above its average that day |

Backtest (6 years, 218 F&O stocks, 10,010 picks, 0.10% costs; the table in the page's "Track record by year"): **+3.32% a pick, 56% won, 11% stopped out, worst pick -30%**. By year: 2021 +1.57%, 2022 +1.98%, 2023 **+8.72%**, 2024 +3.10%, 2025 +2.79%, 2026 (to Nov) +0.66%. Compounded as a 10-stock basket re-picked every 20 sessions (cash when the market is below its average; average of ten start dates): +21%, +12%, +116%, +41%, +27%, +4% a year, with a worst drawdown of about -20% (-28% in the worst start). How the exit rules compared (average a pick, positive in all six years for the first two):

| Rule | Avg | Won | Worst | Stopped |
|---|---|---|---|---|
| time exit only, no stop | +3.72% | 57% | -43% | 0% |
| **4 × ATR stop + time exit** | **+3.32%** | 56% | -30% | 11% |
| 4 × ATR stop + 8 × ATR target | +3.33% | 56% | -30% | 11% |
| 3 × ATR stop | +2.92% | 54% | -27% | 23% |
| 2 × ATR stop | +2.25% | 47% | -25% | 42% |
| 8% / 10% / 12% stop | +2.11% / +2.47% / +2.74% | 46-53% | -19% to -25% | 45% / 35% / 26% |
| 3 / 4 × ATR trailing stop | +2.48% / +3.08% | 51-55% | -27% / -30% | - |
| 4 × ATR stop + exit when the market falls below its average | +2.33% | 50% | -30% | 7% (+37% market exits) |

**How to actually place the ten trades** (the card on the page works the numbers out for your capital):

1. **Signal day (after the close):** if the card says ACTIVE, note the 10 stocks. If it says STAND ASIDE, do nothing.
2. **Before the next open (pre-open 9:00-9:08, or at 9:15):** a **limit buy** for each stock at **last close + 3%**. It fills at the open when the stock opens up to 3% higher and does not fill on a bigger gap-up. Tested: skipping gaps above +3% gave +3.41% a pick against +3.32% buying everything, and the 3% of picks that gapped that far earned only +0.15% - a small safety rule. Do **not** wait for a dip with a lower limit: a limit at the signal close missed 14% of picks that averaged +5.6%, a limit 1% under the open missed 32% averaging +5.4% - the picks that never dipped were the winners.
3. **Quantity:** the smaller of capital ÷ 10 and (risk % of capital ÷ stop distance). With 1% risk and typical stops of 10-18% that is 6-10% of capital per stock. Equal weights (+3.32% a pick) and risk-sized weights (+3.13%, slightly smoother) tested about the same. If you cannot afford all ten, buy as many as you can - the rank inside the top 10 made no difference (+3.0% to +4.5% a pick at every rank, no pattern); fewer stocks only makes the result lumpier.
4. **As soon as each buy fills:** place the **stop-loss sell** (SL-M, or a GTT for delivery) at fill price × (1 - 4 × the stock's daily range %). A gap through it fills at the open, so the loss can be bigger; if all ten stops were hit the loss is about 9-10% of capital.
5. **Exit:** sell at the close of the 20th session after the signal (a reminder date is shown), unless the stop is hit first. No target. Then repeat from step 1 with a fresh top 10 - the test treated a stock that stays in the top 10 as a new trade.

Every stop costs some return (momentum stocks are volatile and recover), so the 4 × ATR stop is a limit on a bad month (worst pick -43% to -30%), not a source of profit. Read it honestly: the data is **only the stocks in F&O today** (survivorship) over a mostly rising market, 2023 carried the total, 2026 so far is weak, and a market turn can cost 20-30% in a month. Unleveraged this is about 3% a month, nowhere near 20%.

> **The dashboard page itself carries no how-to or strategy text** (Nov 2026): the notes under each table, the rule boxes of the Momentum strategy card and its track record, the banners, the footer explanations and the explanatory tooltips were removed so a user of the page sees data and trade levels, not the method. Everything that was there is documented in this README.

### Universe: market cap above 10,000 crore (Price action and SMA support)

The Price action build (`scripts/build_dashboard_setups.py`) takes its universe from the market-cap screen itself - **every NSE stock above ₹10,000 crore** (`config.MCAP_MIN_CR`), not only the Nifty 500 - for everything built from the wide universe: the Price action list, its probability model and Backtest view, and the SMA support lists, backtest and sector indices. Screener symbols Upstox does not list on NSE (BSE-only codes) are dropped. Sectors come from the Nifty 500 list, with the Nifty Total Market and Microcap 250 lists filling in the stocks outside it (`universe.fetch_extra_constituents`; a stock no list covers trades without a sector). The leader trades (cash segment) use the same universe. The market caps come from a screener.in custom screen ("Market Capitalization > 10000", `value_universe.fetch_screen_symbols`, which walks the result pages) using the same `SCREENER_EMAIL` / `SCREENER_PASSWORD` secrets as Weekly Value Stocks Breakout - the Price action workflow now passes them. **The filter is applied only when the fetch is credible**: if the login or the screen fails, fewer than 150 symbols come back (one page of 25 would mean the pages were not read), or fewer than a quarter of its symbols are in the Nifty 500, the build logs the reason and falls back to the Nifty 500 plus F&O list with no market-cap line - nothing is guessed. A built list carries `mcap_min_cr` and the page shows "market cap above ₹10,000 cr" next to the counts when the filter was applied. The research backtests in this README were run on today's F&O stocks (no market-cap data in the research set), so the page's own Backtest views, rebuilt on the filtered universe, are the numbers to read for this universe.

### SMA support and resistance - bullish and bearish (Swing tab)

A section under the momentum tables for buying a pullback to a rising average, daily and weekly (`signals/sma_support.py`; the list is built by the **Price action** build because it needs the long candle history, published as `sma_support.json` on the `dashboard-setups` branch; the page loads it when you open the Swing tab; `?smas=<url>` overrides it for testing).

| | Daily | Weekly |
|---|---|---|
| Average | 21-day SMA | 30-week SMA |
| Clearly rising | higher than 3 sessions ago and up 1% or more over 10 sessions | higher than 4 weeks ago and up 1% or more over 8 weeks |
| Up-trend | 21-day above the 50-day and the close above the 50-day | the close above it and the average above where it was 20 weeks ago |
| Green candle near it | green, closes at or above the average, low within 1.5% above it (not more than 3% below) | green, closes at or above it, low within 3% above it (not more than 5% below) |
| Sector support | the sector (an equal-weight index of its stocks, 3 or more) above its own 21-day average, and that average rising over 5 days | above its own 30-week average, rising over 4 weeks |
| Volume support (the "20d volume" check) | signal candle's volume / the average of the 20 sessions before it | the week's volume / the average of the 20 weeks before it |
| Buy | buy-stop above the green candle's high, next session only | above the weekly high, any session of the next week |
| Stop | below the candle's low (0.1% buffer) | below the weekly low |
| Target | the next weekly resistance (capped at 4R) when it pays at least 1.5R, else 3R, so reward : risk is never under 1.5 | same |
| Risk limits | 0.5% - 8% | 1% - 8% |

Controls: timeframe, **Sector support** (Required / Off), **Volume vs 20-avg** (tested default - daily 1.5x, weekly 1.2x / Off / 1.0x / 1.2x / 1.5x / 2x), F&O only, sort. A row opens a chart with the average, the green candle, the buy / stop / target box. Daily: the signal candle is one of the last 3 sessions (waiting while it is the last candle, triggered when the next session traded above its high and neither the stop nor the target was reached since); weekly: the last completed weeks; the latest signal per stock and timeframe.

**6-year backtest** (`scripts/backtest_sma_support.py`, 218 F&O stocks, Dec 2021 - Nov 2026, order mechanics as above, stop first, time exit after 20 sessions / 130 sessions, 0.10% costs; the sector index here is made of the F&O stocks, the live build uses the Nifty 500 too):

| | Daily trades | ROI a trade | Won | Profit factor | Weekly trades | ROI a trade | Won | Profit factor |
|---|---|---|---|---|---|---|---|---|
| Every signal | 10,501 | +0.18% | 33% | 1.09 | 1,689 | +1.68% | 37% | 1.50 |
| Sector supports | 7,549 | +0.24% | 34% | 1.12 | 1,327 | +1.64% | 37% | 1.49 |
| Sector does not support | 2,952 | +0.01% | 32% | 1.01 | 362 | +1.84% | 37% | 1.52 |
| Sector + volume at least 1.0x | 2,607 | +0.47% | 37% | 1.21 | 373 | +2.21% | 39% | 1.63 |
| Sector + volume at least 1.2x | 1,769 | +0.61% | 38% | 1.25 | 195 | +3.26% | 44% | 1.95 |
| **Sector + volume at least 1.5x** | **1,083** | **+0.94%** | **41%** | **1.39** | 73 | +4.86% | 52% | 2.58 |
| Sector + volume at least 2x | 565 | +1.37% | 45% | 1.58 | 16 | +5.30% | 56% | 2.88 |
| Sector + volume under 1.0x | 4,942 | +0.12% | 32% | 1.07 | 954 | +1.42% | 36% | 1.43 |

The tested defaults are daily volume at least 1.5x (1,083 trades) and weekly at least 1.2x (195 trades; 1.5x has only 73). **The volume is the condition that matters**: the return rises step by step with it in both timeframes, while the sector filter helps the daily trade a little (+0.24% against +0.01%) and does not help the weekly one. By year, the daily rule: 2021 +1.41%, 2022 -0.31%, 2023 +2.42%, 2024 +0.73%, 2025 +0.67%, 2026 -2.09% (4 of 6 years positive); weekly: -0.30%, -0.83%, +8.02%, +0.45%, +2.58%, -2.90%. Selected on 2021-2023 and left alone for 2024-2026: daily +1.43% then **+0.25%** (447 trades), weekly +5.87% then **+0.51%** (95 trades) - a good part of the headline is 2023. A month-block bootstrap puts the daily average between +0.42% and +1.38% a trade (positive in every resample) and the weekly between +1.32% and +4.69%. The target choice made little difference: when a resistance was closer than 3R and paid at least 1.5R it did better (daily +1.23% against +0.88%), but those are 193 of 1,083 trades.

**Backtest view on the page.** The SMA section has a "Backtest: past trades" switch next to "Today's setups": every past trade of the rules since 2021 (trades whose volume was at least 1.0x its 20-day / 20-week average - the page's volume filters work on top of that), with signal date, entry date and price, exit date and price, how it ended (target / stopped / time exit), the return after 0.10% costs, the R and the volume ratio. The same Direction / Timeframe / Sector / Volume / F&O controls apply, plus Year, Result, Sort and Show; the line above the table gives the result of the filtered set (target %, win %, average return and R, profit factor, sessions held). A row opens a chart of the trade: candles, the average, the signal candle, the entry, the stop and target lines and the exit. Built by `signals/sma_support.history()` in the Price action build (`sma_hist.json` plus `sma_hist/c/<symbol>.json` on `dashboard-setups`; `?smah=` / `?smahc=` override them for testing); it uses the build's own universe (Nifty 500 plus F&O), so its totals differ a little from the F&O-only research run below.

**Clear uptrend filter (daily bullish list).** The **Trend** control ("Clear uptrend only (top fifth)") keeps the daily bullish signals whose **trend rank is 80 or more**, in Today's setups and in the Backtest view; each row shows its trend rank (0-100, bold from 80). The rank is where the signal's trend score sits among every daily bullish signal the rules ever gave (`signals/sma_support.trend_tables`, built from the whole history in the Price action build): the score is the average percentile of three measures - how fast the 21-day average rose over 10 sessions, how far it sits above the 50-day average, and the stock's 60-session return. It is shown for the daily bullish list only (weekly: the trend split did not separate the winners; bearish: it loses anyway). Backtest (F&O, 6 years, daily bullish):

| Trend rank | Every signal | Default rule (sector + volume 1.5x) |
|---|---|---|
| 0-20 (weakest) | n 2,040, +0.29% | n 240, +1.25%, 45% won |
| 20-40 | n 2,079, +0.02% | n 263, +0.94% |
| 40-60 | n 2,132, +0.04% | n 230, +0.23% |
| 60-80 | n 2,089, -0.18% | n 206, +0.08% |
| **80-100 (clear uptrend)** | **n 2,161, +0.71%, 37% won** | **n 144, +2.80%, 48% won, profit factor 2.13** |

With the default rule the clear uptrend made +2.67% (2021), -0.32% (2022), +4.04% (2023), +3.16% (2024), +3.75% (2025), -0.36% (2026, 5 trades); selected on 2021-23 +2.60% (81 trades) and **+3.07% on the untouched 2024-26 (63 trades, 51% won)**, +1.51% a trade over the equal-weight index over the same days. It is not a smooth line - the weakest fifth also beat the middle three - so the filter keeps the clearest uptrends only; shuffling the rank within years, the top-versus-bottom gap (+0.38%) was beyond chance (95th percentile +0.33%). Several cuts were looked at before this one was built, so treat it as promising, not proven.

*A "close near the high / low" filter (a hammer- or engulfing-style close) was built and tested, then removed: once the volume condition is on it adds nothing measurable - daily long within 10% of the range +0.68% a trade (259 trades) against +0.94% without it (1,083); weekly within 10% +4.62% on 51 trades against +3.26% on 195.*

**Bearish resistance (the bear-market mirror, F&O stocks only).** Direction control "Bearish resistance (sell)": the average clearly falling (lower than 3 sessions / 4 weeks ago and down 1% or more over 10 sessions / 8 weeks), a down-trend (21-day under the 50-day and the close under the 50-day; weekly: the close under the 30-week average and the average under where it was 20 weeks ago), a RED candle closing at or under the average with its high within 1.5% (3%) below it, the sector index below its own falling average, volume at least 1.5x (1.2x). Sell below the red candle's low (next session / next week), stop just above its high, target the next weekly support (capped at 4R) when it pays 1.5R else 3R. **It lost money**, as every short in these tests has: daily, every signal -0.38% a trade (9,193); with the sector weak -0.65% (4,382); with volume at least 1.5x -1.12% (371 trades, 29% won, profit factor 0.60; 2021-23 -1.64%, the untouched 2024-26 -0.83%; the month-block bootstrap interval -1.70% to -0.52%). Unlike the long side the filters did not help - the weaker the sector and the heavier the volume, the more it lost - (a close near the low did not help either). Weekly, every signal -0.82% (845), with the sector weak -1.51%, with volume at least 1.2x -2.50% (23 trades, 22% won). Hedged against the equal-weight index the shorts came out about even (daily every signal -0.01%), which is not tradable because the hedge is not free. It is shown at your request, with the numbers on the page: paper-trade it or keep it very small.

Caveats, as with every long test here: the stocks are today's F&O list (survivors) in a market that rose, so the same trades earned about the same as the equal-weight index of these stocks over the same days (daily -0.05%, weekly -0.46% against it) - the rule picks entries, it does not beat holding the index; weekly trades are held a median of about 22 sessions (about a month, up to 130) and risk about 5% each (4-8%). Paper-trade first.

### High-range leader trades (Swing tab, under Movers)

The leader list changes every day, which made the 20-session rule hard to follow. This section shows each leader as a **trade with its own clock**: bought at the next open, sold at the close of the 20th session after its signal (or at its 4 x ATR stop) - whether or not it is still on the leader list - and never bought again while it is still held. Two views:

* **Holding now** - the trades still running: bought on, entry, stop, last close, open P&L, the date to sell at the close, sessions left and whether the stop has been hit; plus "New entries: ON / OFF" (the market above its 50-session average).
* **Backtest: past trades** - every past trade with signal date, entry date and price, stop, exit date and price, how it ended (stopped / time exit / still held), return after 0.10% costs, sessions held and its Movers / momentum rank; Year, Result, Sort and Show controls, a result line for the filtered set and a chart per trade.

Rules (`signals/leader_trades.py`, which reuses the Momentum strategy's constants): leader = one of the 10 widest-range liquid F&O stocks (14-session ATR%, turnover at least 20 cr) that is also in the 30 strongest by momentum (average percentile of the 20- and 60-session return); entries only while the equal-weight index of the F&O stocks is above its 50-session average; entry at the next open, skipped if it is more than 3% above the signal close; stop = entry x (1 - 4 x ATR%); exit at the close of the 20th session after the signal; one position per stock. Built by the Price action build (`leader_trades.json` plus `leader_hist/c/<symbol>.json` on `dashboard-setups`; `?leader=` / `?leaderc=` override them for testing).

**Backtest** (`scripts/backtest_leaders.py`, 218 F&O stocks, Nov 2020 - Nov 2026): 636 closed trades, **+3.92% a trade, 56% won, 7% stopped out, profit factor 2.00**, median +1.89%, worst -26.1%; 2021 +2.44%, 2022 +2.14%, 2023 +8.90% (133 trades), 2024 +4.12%, 2025 +1.50%, 2026 +1.89%; selected on 2021-23 +4.62%, the untouched 2024-26 **+2.90%** (258 trades); a month-block bootstrap puts the average between +2.72% and +5.09% (positive in every resample). Against the equal-weight index over the same days (+1.92% a trade) the edge was +2.00% a trade. Caveats as before: today's F&O stocks, a rising market, wide stops (about 21% on average, so size from the stop), about 1.4 leaders a day (119 distinct stocks), and 2023 carried the average.

**Cash segment: the leader trades run on the whole market-cap universe (Nov 2026).** The published list is no longer limited to F&O stocks: the same rule runs on every NSE stock above ₹10,000 crore (about 540; the Price action build's universe), because the trades are meant for the cash segment (a roughly 20% stop is too wide for leveraged futures). Backtest of the build's own candles (`Leader research` log lines of the Price action build, same rule, next-open entry): F&O stocks 642 trades +3.96% a trade, profit factor 2.01, worst -26%; market-cap universe 882 trades **+3.02%**, 52% won, 5% stopped, profit factor 1.65, worst -32%, 11% of trades below -15%, 2024+ +3.62%; the non-F&O stocks alone 1,073 trades +2.69%. Larger Movers lists / momentum pools (20 / 60, 30 / 90) did not help (+2.84%). Read with the usual caveats: today's universe (survivorship is stronger here, since stocks above the line now include those that grew into it), 2023 carries the average, and the worst trades are stops about 20-25% away, so position size (not the entry rule) is what limits a drawdown.

### Forward test (frozen signal log)

`signals/forward_log.py` keeps a ledger of the signals the strategies give *as they happen* - the high-range leaders (cash universe, next-open entry, 4 x ATR stop, exit at the close of the 20th session) and the F&O-futures SMA preset (daily, long, sector support required, default volume, clear uptrend, F&O stocks). Each Price action build (after the close) appends the day's signals with the plan as it stood that evening; a logged entry is never changed or removed, and a closed trade is never touched again. Later builds only fill in what the real candles did (entry, stop, exit) with the backtests' own rules (`ss.outcome` for the SMA trades; a re-implementation of the leader rules that a test checks against `leader_trades.trades`). A second signal in a stock that is still held, or a leader that gapped up more than 3%, is recorded as skipped; an SMA buy-stop that never traded is "not triggered". The page's Forward test section shows the entries, counts, win rate, average net %, profit factor, an equity curve at 1% of capital risked per trade (R multiples booked on the exit day, so the drawdown counts closed trades only) and the Nifty 50 move over the same days. Results are net of 0.40% costs (the strategies' own 0.10% plus `EXTRA_COST_PCT` = 0.30% for statutory charges, brokerage and slippage); tax is not deducted.

The ledger lives on its own branch, `dashboard-forward` (`forward_log.json`), read back through the GitHub contents API each build. **A build that cannot read it leaves the branch alone** (it never starts a new ledger over an unreadable one), and a build older than the saved ledger does not overwrite it. A signal is only logged on the build that sees its candle as the latest one, so a missed build means missed signals, not back-filled ones. The branch is public like the rest of the repository.

**Position-size calculator (leader table, "Holding now" view).** Enter your capital and the share of it you are willing to lose if a stop is hit (0.25-2%; both are remembered in the browser). Quantity = floor(capital x risk % / (price - stop)), capped so one position is at most a fifth of the capital. *Held positions* get the quantity their entry would have been sized to, with the amount invested; the line above shows how many of 10 positions are used, the amount invested and the loss if every stop were hit from the last close. *New leaders* (only while the market gate is ON; a stock already held shows "Held") are sized from the last close and a stop 4 ATR below it, with the buy limit (close +3%) - the real quantity depends on tomorrow's open, so recheck it. The sizes change every day because the leaders, their prices and ranges change; a position already bought is not resized. Update the capital figure now and then as your account grows or shrinks. The file `leader_trades.json` carries `today` (close and ATR of each of today's leaders) and `gap_cap` for it.

**Stop and exit tests (Nov 2026, 218 F&O stocks, 1% of capital risked per trade, at most 10 positions) - the live rule stays 4 x ATR stop and exit at the close of the 20th session.** *Tighter stops* (3 x / 2.5 x / 2 x / 1.5 x ATR) cut the average per trade (+3.64 / +3.21 / +3.02 / +2.41% against +3.92%), stopped out 16 / 25 / 35 / 49% of trades (7% live) and made the sized-up positions' drawdown worse (-14.5 / -19.3 / -21.2 / -33.4% against -10.8%); the worst trade stays about -25 to -28% because a gap jumps past any stop. *Wider stops* (5 x / 6 x / 8 x) gave a higher average (+4.25 / +4.32 / +4.37%) and a better return per drawdown (10.9 / 11.1 / 11.1 against 8.2) but at 1.8% / 0.5% / 0% stopped the stop stops protecting (worst -29 / -33 / -28%), most of the gain is in 2021-23 (2024+ +3.02 / +3.09 / +3.08% against +2.90%), and the stop distance is then not the real worst case. *Longer holds* (30 / 40 / 60 sessions) and *targets / trailing stops with no time exit* leave the return per day held at 0.14-0.22% and the total return at about +85-89% (+53% with no time limit), while the drawdown grows (-12.5 to -15.4%; -27.9% with no time limit) and the worst trade reaches -30 to -41% (more gap exposure, slots tied up, fewer signals taken). Closed trades only (positions still open at the end are left out). `leader_trades.trades` has the research options `stop_atr`, `max_atr`, `max_ret20`, `trail_atr`, `gate_exit`, `target_atr`, `top_n`, `pool`, `hold` and `entry`; all are off in the live rule.

**Does buying only after the signal candle's high breaks help? (tested, Nov 2026).** `leader_trades.trades(entry="break", window=N)` buys with a buy-stop above the signal day's high (good for N sessions; filled at the high, or at the open on a gap over it) instead of the next open; the stop (4 x ATR from the fill) and the exit (close of the 20th session after the signal) are the same. Same 218 F&O stocks, 6 years, one position per stock:

| Entry | Trades | Average | Won | Profit factor | Untouched 2024-26 |
|---|---|---|---|---|---|
| Next open (the live rule) | 636 | +3.92% | 56% | 2.00 | +2.90% |
| Buy-stop above the signal high, 1 session | 527 | +4.04% | 58% | 2.07 | +3.22% |
| ... 2 sessions | 551 | +3.65% | 56% | 1.94 | +2.95% |
| ... 3 sessions | 565 | +3.39% | 55% | 1.86 | +2.83% |
| ... 5 sessions | 584 | +3.11% | 54% | 1.77 | +2.40% |
| ... 1 session, skip fills more than 3% above the signal close | 502 | +4.33% | 57% | 2.19 | +3.56% |

The observation is right as a *selection*: of the open-entry trades, those whose next session did break the signal high earned **+5.22%** (186 trades, 62% won) against **+3.39%** (450 trades, 53% won) for those that did not - and the trades that never broke the high within 3 sessions earned only +0.64%. But buying *at* the break does not capture that: the fill is about 0.8% above the open, and on the same 186 signals the break entry made +4.16% against +5.22% for the open entry (difference -1.06%, 90% interval -1.20% to -0.90%). Net, the break entry takes about 17% fewer trades for about the same average (+4.04% against +3.92%), and the total return summed over all signals is lower (+2,173 against +2,495 percentage points for the 3%-capped one-session version), because a missed signal earns nothing. The best variant (one session, 3% chase cap, +4.33%) is one of six looked at and not distinguishable from the live rule. The live rule stays the next open.

### Movers watchlist (Swing tab)

Below the momentum lists, the ten stocks with the **widest daily range** (14-session average high-low range as a % of price, liquid F&O stocks only) - the ones *capable* of a 10-12% move in the next ~10 sessions, up or down. Each card shows the range, a squeeze tag (the last 10 sessions' range against the last 50: `squeezed` / `normal` / `expanding`), the 20-day move, where price sits in its 20-day range and turnover. `signals/movers.py` is the reference (the page has a JS twin checked against it); change **As of** to see any past close.

`python scripts/backtest_movers.py` (`signals/movers_backtest.py`) asks whether the ranking finds big movers: the share of the top ten whose high or low reached the threshold within 10 sessions of the next open, in *either* direction, against all liquid stocks (24 Jun - 14 Oct 2026, 78 signal days):

| move reached | top-10 widest range | typical stock | edge (90% range) |
|---|---|---|---|
| 10% or more | 27% | 13% | +14 pts (+8 to +19) |
| 12% or more | 17% | 7% | +10 pts (+5 to +15) |

So the list finds stocks that move about twice as often, but roughly **three in four picks still do not make a 10% move**, and it says nothing about the direction (a separate walk-forward test of direction found no tradable edge: the picks' hit rates beat the base rate but not the 2-to-1 payoff break-even, and long picks lost money in the falling market of the test). The squeeze reading looked interesting but added nothing over the range itself, so it is shown, not ranked. A wide range also means a wide stop - size the position from it. A candidate list to study, not a signal.

**6-year check of the Movers list (Nov 2026; 218 F&O stocks, 13,850 picks, 0.10% costs).** *Capability:* the 10 widest-range stocks reached +/-10% within 10 sessions of the next open **57%** of the time against **26%** for a random liquid stock, and the lift held every year (64/32, 64/29, 51/21, 63/29, 53/21, 43/23 %). *Direction:* bought with the momentum rules (next open, 20 sessions, 4 x ATR stop) the list averaged **+3.40% a pick against +1.26% for random picks** (+3.71% when the market is above its 50-day average, +2.64% when below), positive in every year in a rising market (+3.3, +1.7, +9.3, +3.5, +2.4, +0.7%), and 4 x ATR is a **22% stop** on average (worst pick -35%, 5th percentile -18%) - so size positions small. Shorting the same list lost -2.8% to -3.6% a pick. Movers that are also in the top-30 momentum list did best (+4.3% a pick against +2.9% for the rest), which is a lead rather than a result. As a 10-stock basket re-picked every 20 sessions with the market gate it made +539% over 2022-2026 (worst drawdown -30%) against +415% (-28%) for the Momentum top-10 - but this is high-beta stocks in a mostly rising market and only today's F&O stocks, so the edge over random may be mostly beta and survivorship; it earns its place on the dashboard as a "can move" list with a long-side tilt, not as proof of an edge.

### Update data now (button on the page)

The header's **Update data** button runs the evening builds by hand instead of waiting for the schedule: Replay history, Swing and Price action (and, in market hours, the live refresher). A static page cannot start a GitHub Actions workflow by itself, so there are two ways:

- **No token:** each row opens that workflow's page on GitHub; press *Run workflow* there.
- **With a token saved in this browser:** *Run now* starts the workflow through the GitHub API and the page follows it (queued / running / finished or failed); *Update all after the close* runs history → swing → price action one after another and offers a reload at the end. Create a fine-grained token at GitHub → Settings → Developer settings: only this repository, permission *Actions: read and write*, nothing else, short expiry. It is kept in the browser's local storage (never in the repository) and sent only to `api.github.com`; *Forget the saved token* removes it. Anyone who can read that browser's storage could use it to start this repository's workflows, so use it on devices you trust.

**Schedule (15:45 IST).** The three evening builds run as one chain that starts at 15:45 IST on weekdays, 15 minutes after the close: *Dashboard history* is on the 15:45 IST cron (`15 10 * * 1-5` UTC); *Dashboard swing* starts when a scheduled history run completes and *Dashboard price action* starts when that swing run completes (`workflow_run`), so only one Upstox login runs at a time. A run started by hand (or by the page's buttons) does not trigger the next one - the page chains those itself. GitHub's cron is best-effort and has started this repository's runs hours late, so each workflow begins with a `gate` job that skips a scheduled or chained run that would start inside market hours (08:40-15:45 IST on weekdays). A chain takes about 45 minutes (history ~18, swing ~12, price action ~14), so the price action list and the forward log are updated around 16:30 IST. The "Nifty500 Signals" Telegram job keeps its own 17:07 IST schedule.

The three builds log in to Upstox, and a second login cancels the first one's token, so the page never starts them **during market hours (08:40-15:45 IST on weekdays)** or while another Upstox job is running (it checks GitHub right before each start). The live refresher can only be started during market hours. (`?repo=owner/name` points the panel at another repository, e.g. when the page is served from a custom domain.)

### Login screen and encrypted data (optional)

The dashboard lives on public GitHub Pages and reads JSON from public branches, so a password form on its own would only hide the page - anyone could still open the data files. Setting one repository secret turns on real protection:

1. Repository -> Settings -> Secrets and variables -> Actions -> **New repository secret**, name `DASHBOARD_PASSWORD`, value a long passphrase (4+ random words; there is no way to rate-limit guesses against downloaded files, so length matters).
2. Re-run the dashboard, history and swing workflows (the next scheduled runs also pick it up).

From then on every published JSON file (`data.json`, `oi.json`, `blocks.json`, `candles.json`, replay day files, swing files) is an AES-256-GCM envelope; the key comes from PBKDF2-HMAC-SHA256 (600,000 iterations) of the password. The page asks for the password, derives the key in your browser and decrypts locally - the password is never sent anywhere or stored; only the derived key is kept (for the tab, or on the device if you tick "Keep me signed in"). The **Lock** pill forgets it. Changing the secret locks every device out until it signs in again.

What it does and does not protect: the data (prices, screens, order blocks, signals) is unreadable without the password. The page's code, the workflow files and the repo itself stay public (use a private repo if that matters - Pages from a private repo needs a paid plan). Without the secret nothing changes: files are plain JSON and no login shows. Code: `signals/secure.py`; the backtest scripts decrypt too when `DASHBOARD_PASSWORD` is set.

### OI buildup (click a stock)

Clicking any row opens a panel with that stock's **futures open-interest
activity as a buy-vs-sell chart**: per time block (5 min / 15 min / 30 min / 1
hour, your choice) a green bar for the buy side next to a red bar for the sell
side, both pointing up, taller = more open interest changed. Each bar is split
into dark = new positions opened (buy side: long buildup, sell side: short
buildup) and light = positions closed (short covering / long unwinding), where a
candle counts as buy-side if price rose in it and sell-side if it fell. Above
the chart: current OI, change since 09:15, futures price move and a **whole-day
reading**; "Show the numbers" lists every
block (price move, OI change, the four components, net reading). F&O stocks
only, on the current-month futures contract. Live, `scripts/run_dashboard.py`
refreshes `oi.json` (today's 5-minute futures candles, one request per F&O
stock) every 5 minutes beside the quote loop; in Replay the nightly history
files carry the OI arrays, so the chart follows the chosen date and time. OI
comes from the current-month contract, so replay days before the previous
monthly expiry (~29-30 Sep) show what was then the next-month contract
(smaller OI, rising as traders rolled over); the chart says so.

**Flat candles and the reading.** A candle whose price moved less than 0.1% (daily:
0.25%) is *flat*: its OI change says nothing about who was in control, so it is
drawn as a grey bar and left out of the buy/sell totals instead of being coloured
by a one-tick move (on 26 Oct a flat 09:35 Coforge candle that added 8% to OI
was counted as "short buildup" and made the sell side look like 72%). The
whole-day reading judges the day so far two ways - by OI and by number of
candles won - and says **Buy / Sell in control** only if the side leads on both
and holds at least 60% of the OI; otherwise **Mixed**. A one-candle spike can
no longer decide it. The tiles also show the candle count (buy / sell / flat).
It is a reading of what happened so far, not a prediction.

### Replay

The dashboard's **Replay** tab shows the same two views - sector day change and
highest-volume stocks - as they stood at any 5-minute mark of a recent
session: pick a date and a time (every 5 minutes, 09:20-15:30; the time is the
END of that candle), or step with the 5 min buttons, and filter by sector /
F&O exactly as in Live. `.github/workflows/dashboard_history.yml` builds the
data each weekday at 15:45 IST (`scripts/build_dashboard_history.py`): the last
~20 sessions of 5-minute candles for the Nifty 500, one compact file per day
(~0.5 MB each), force-pushed to the `dashboard-history` branch. The page
rebuilds each view in the browser (price = that slot's close, volume = the sum
of slots so far, change vs. the previous close - the live page's definitions),
so changing the time is instant. Replay covers only days on that branch (about
a month back, trading days only); the first build is done by running that
workflow once manually. Stocks with fewer than 20 traded 5-minute candles that
day are left out; the F&O flag and sector map are today's.

## Known limitations

- **Trendline Break Short (5-minute) is backtest-only so far.** Shorts an
  F&O stock whose 5-minute candle closes below a rising support trendline
  through the session's swing lows (`signals/trendlines.py`,
  `signals/strategies/trendline_break_short.py`); where several stocks break
  at once, the widest trendline ranks first. Run
  `.github/workflows/trendline_backtest.yml` (months, plus a `symbol_limit`
  for a quick smoke test of the 5-minute fetch) - `signals/trendline_backtest.py`
  replays every 5-minute bar, enters at the next bar's open, applies the
  stop/2R target with the stop winning same-bar ties, squares off at 15:15 and
  charges `TRENDLINE_ROUND_TRIP_COST_PCT`; its summary also tests whether the
  width ranking actually helps and splits results in-sample/out-of-sample.
  A **daily-chart version** (`trendline_break_short.scan_daily`, backtest key
  `trendline_break_daily`, F&O stocks only) applies the same geometry to daily
  candles with daily-scale tolerances (`TRENDLINE_DAILY_*` in config.py),
  requires the break day's volume to be at least 1.5x its 20-day average, and
  is walked forward by the standard daily simulator (entry at the break day's
  close, stop above the highest high since the last touch, 2R target). Run it
  with `BACKTEST_STRATEGIES=trendline_break_daily`; its log section adds the
  width-ranking test and in-sample/out-of-sample feature buckets.
  Neither version is wired into a live run yet, and the 5-minute Upstox history
  endpoint's behaviour (chunk limit, bar timestamps) is assumed, not yet
  confirmed live. Today's F&O list is applied across the whole window.
- **Retired experiment: Intraday Watchlist.** A daily next-day bull/bear
  watchlist (daily-chart trend + breakout/coiled setups, F&O stocks, events
  excluded) was built, backtested over 60 months against every comparable
  stock, and removed: net of the market it showed no edge in any feature
  bucket (trend-day rate 17% bull / 27% bear vs 17% / 25% for all stocks),
  so it was dropped in favour of a rules-based short strategy. It's in git
  history (commits d1a6203..2cc0f93) if it's ever worth revisiting.
- **The login automation is inherently fragile.** It drives Upstox's own
  login page rather than a documented API, so a layout change on their
  end will break it. When it breaks, the run fails loudly with a
  Telegram error report (not silently), and the uploaded screenshot
  artifact should make the fix quick — see setup step 5.
- **The instrument master's column shapes are similarly unverified.**
  `signals/upstox_client.py` maps NSE trading symbols to Upstox
  instrument keys by reading specific column names/values from Upstox's
  instrument file; if that file's shape changes, matching can silently
  drop to zero. `build_instrument_map` guards against this by raising
  (reported to Telegram, not swallowed) if fewer than half of the
  requested symbols match — a "no stocks matched today" message for
  every single strategy in one run is more likely this than a real
  quiet market.
- **All-time-high depth is bounded**, not literal all-time. The monthly
  ATH check fetches its own native monthly candles directly
  (`MONTHLY_ATH_HISTORY_YEARS`, 25 years by default), separately from the
  6-year daily fetch the other two strategies use - so it sees a much
  deeper history, but a handful of decades-old listings (Reliance, ITC,
  etc.) still predate even that. Those stocks get a 25-year ATH check,
  not a literal since-IPO one. Increase `MONTHLY_ATH_HISTORY_YEARS` in
  `signals/config.py` for a deeper look-back (monthly candles are cheap
  enough that this costs little even pushed much further).
- **NSE holiday calendar**: the "last trading day of the month" check is
  pure calendar math (last weekday of the month). If the real last
  trading day happens to be an NSE holiday, the run fires one weekday
  early instead.
- If a stock's data fetch fails for the day, it's just skipped (logged in
  the Actions run) rather than failing the whole screener — unless a
  large fraction of an early sample fails, in which case
  `signals/data.py`'s circuit breaker aborts the whole run early with a
  clear error (protects against a repeat of the NSE-blocking incident
  that motivated the switch to Upstox).
- **Price Action Breakout's daily and weekly legs now run genuinely
  different entry logic - `scan()` (immediate entry) and `scan_retest()`
  (waits for a retest/reclaim) - after a round trip that backtested both
  approaches on both timeframes.** Both legs started retest-based:
  **daily 2,654 signals/48% win/PF 1.13, weekly 190 signals/63% win/PF
  1.10** (before the risk cap and reward:risk gate existed). Both were
  rewritten to immediate entry per explicit direction ("due to retest, I
  am getting bit late in trade"); daily's rewrite held up in its own
  5-year backtest - **1,194 signals, 30% win rate, PF 1.23**, avg win
  +11.1% / avg loss -3.9% - beating the old retest-based version's
  final-config numbers (845 signals, 29% win, PF 1.20) at essentially the
  same win rate, i.e. dropping the retest wait achieved earlier entries
  without costing edge. Weekly's rewrite did not: it thinned to **8
  signals, 38% win rate, PF 1.45** (down from the old version's 20
  signals/45% win/PF 2.15) - 8 trades over 5 years is too thin a sample to
  trust on its own, and a follow-up attempt to fix the trigger (projecting
  the breakout level off a fitted trendline instead of a plain rolling
  max/min, to catch falling-topped bases the flat rolling max structurally
  couldn't) made it worse still: it worked exactly as designed on daily
  (Descending Triangle detections went 4 -> 24 out of 1,264 signals) but
  dragged daily's PF down to 1.15 and collapsed weekly to **11 signals, 9%
  win rate, PF 0.26** - a losing strategy on paper. That trendline change
  was reverted entirely (see git history for
  `signals/strategies/price_action_breakout.py` if it's ever worth
  revisiting), and per explicit direction ("for daily keep the breakout
  candle and for weekly retest price action was working") daily kept the
  immediate-entry rewrite while weekly went back to the original
  retest-based `scan_retest()`. A fresh 5-year backtest of the restored
  code confirmed it: **weekly 20 signals, 45% win rate, PF 2.15**, avg win
  +12.2% / avg loss -4.6%, matching the reference figure above exactly
  (the retest-based version's 190-signal number predates the risk cap and
  reward:risk gate; 20 signals is that same code with every
  currently-active threshold applied); **daily 1,193 signals, 30% win
  rate, PF 1.22** - stable, matching its own prior run (1,194/30%/1.23)
  within date-boundary noise. The daily leg's 30% win rate is still
  structurally fragile - profitable only because avg win runs ~2.8x avg
  loss, not because it wins often; weekly's 44-day average holding period
  is notably longer than daily's 25, consistent with a slower,
  confirmation-gated entry. Every
  threshold in `signals/config.py`'s Price Action Breakout section (the
  base's min/max length, its tightness, the breakout's volume multiplier,
  the target multiples, the shape classifier's flat-slope cutoff, the risk
  caps, the reward:risk floor, weekly's retest tolerance/invalidation
  bands) is still a judgment call, not something re-tuned specifically for
  this exact daily/weekly split. The shape label (Range/Triangle/Wedge) is
  a genuine classification - it's computed from real slopes fit through
  the base's highs and lows - but it's a heuristic, not real
  trendline-touch-point geometry the way a human chartist or a dedicated
  pattern-recognition library would do it; treat it as a useful label, not
  a rigorous one.
- **Weekly's risk cap and reward:risk gate were removed again, and its
  breakout window tightened, after the above validation - both
  unbacktested in this exact combination.** Per explicit direction,
  `scan_retest()` (weekly) no longer takes a `max_risk_pct` parameter at
  all and no longer checks `PRICE_ACTION_MIN_REWARD_RISK_RATIO` - reverted
  from the gated version validated above (20 signals, 45% win, PF 2.15)
  back to the strategy's ORIGINAL ungated form, to reproduce its own
  190-signal/63%-win/PF-1.10 reference numbers. Separately,
  `PRICE_ACTION_BREAKOUT_WINDOW_WEEKLY` was tightened from 8 to 6 candles
  (also per explicit direction) - narrows how far back a breakout
  candle can sit and still count toward today's retest/reclaim, which
  should reduce signal count further, not increase it. Neither change has
  been backtested together with the other yet (the 190-signal reference
  above used window=8; the ungate reversal happened after that number was
  already recorded) - the daily leg is completely unaffected by either
  change and keeps its risk cap and reward:risk gate as before.
- **Price Action Breakout's short leg is new and unbacktested at scale.**
  It's gated to F&O-eligible symbols (`data.fetch_fo_eligible_symbols`) -
  a much smaller slice of the universe than the long side's full ~500 -
  and Upstox's instrument master only exposes *today's* F&O-eligible set,
  with no historical snapshot of which symbols had a live futures
  contract on a given past date. A backtest run therefore applies today's
  F&O universe uniformly across the whole historical window - a real but
  unavoidable approximation (the same kind of limitation the old, since-
  removed Futures OI Buildup strategy had with futures price history
  itself), so short-leg backtest results should be read with that caveat
  in mind, more so than the long side's.
- **`MIN_REWARD_RISK_RATIO` (1:1) has been backtested on Weekly Range
  Breakout and Monthly ATH Breakout, with opposite results.** Both
  strategies size their targets and stops independently (range height vs.
  range midpoint; %-based vs. ATH-anchored), so nothing previously stopped
  a signal from clearing every other filter while still not earning back
  its own risk. On Weekly Range Breakout it was a clean win: PF 1.34 ->
  **1.72**, win rate only dipping 64% -> 60%, on a healthy 115-signal
  sample - kept. On Monthly ATH Breakout it went the other way: PF
  **1.66 -> 1.45** (530 -> 190 signals) - a fresh-ATH breakout apparently
  carries enough of its own statistical edge that a snapshot reward:risk
  ratio doesn't capture it well, so the gate removed more good trades than
  bad ones there. Reverted for Monthly ATH Breakout specifically (see git
  history) - it has no reward:risk gate. The lesson: this principle isn't
  automatically a net positive for every strategy just because it sounds
  universal - each application needs its own backtest before being
  trusted, not just the reasoning behind it.
- **Weekly Value Stocks Breakout's data source and login are now confirmed
  working against the live site, but its backtest sample is still very
  small.** Getting here took three real test runs (via GitHub Actions,
  which has actual network access unlike this development environment) to
  correct three wrong assumptions in turn: the scrape endpoint itself was
  guessed wrong at first (404); the corrected endpoint turned out to
  require a logged-in screener.in account (anonymous requests redirect to
  screener.in's registration page); and the login flow (`SCREENER_EMAIL`/
  `SCREENER_PASSWORD`, a standard Django-style CSRF-protected form login)
  then worked on the very first real attempt once a password existed on
  the account (it had been Google-sign-in-only). The first real 5-year
  backtest came back with 13 signals, 83% win rate, PF 83 - but **N=13 is
  too small to trust that PF number**: it's dominated by one outsized
  winner (avg holding is ~10.5 months with no fixed target, so a single
  huge run skews the average hard), and the fundamentally-screened
  universe is inherently small by design, so this won't grow to a large
  sample quickly. Per explicit direction, the strategy also carries no
  bullish-candle, proper-close, or volume-confirmation filter on top of
  the breakout condition (unlike Monthly ATH Breakout and Price Action
  Breakout) specifically to avoid starving an already-small universe of
  trades further - a deliberate trade of precision for sample size that
  hasn't itself been split-tested against the filtered version. Separately,
  even a solid backtest here can only ever apply *today's* screen result
  uniformly across the whole historical window - screener.in has no
  historical snapshot of past fundamentals, the same kind of approximation
  Price Action Breakout's F&O short leg already lives with (see above) for
  a similar reason. And its exit mechanic (a trailing 30-week-SMA stop
  with no fixed target, `backtest.simulate_weekly_trailing_sma`) is a
  design that was tried on Weekly Range Breakout and Monthly ATH Breakout
  earlier in this project and reverted there (see git history), though not
  necessarily because the mechanism itself was flawed for those strategies
  specifically. That first backtest (and the filter-removal one that
  followed it, 22 signals/90% win/PF 108) was also **scoped to the Nifty
  500 only** - a real bug, since the whole point of screening fundamentals
  via screener.in rather than just filtering the Nifty 500 by price action
  is to reach the full NSE. Fixed by giving this strategy its own
  instrument map and weekly (plus, for the backtest, daily) Upstox fetch
  scoped to exactly the symbols screener.in's query returns, instead of
  reusing the shared Nifty 500 fetch - confirmed working on the first live
  run after the fix (31 signals, 81% win, PF 53, no instrument-match or
  Upstox-load issues). Separately: investigating why a well-known,
  comfortably-qualifying stock (Engineers India) never appeared surfaced a
  real gap in `VALUE_BREAKOUT_MIN_GAP_WEEKS` (requiring the high being
  broken to be ~a year+ old) - it was quietly excluding any
  fundamentally-qualifying stock already mid-uptrend, since such a stock's
  own new highs are always recent, never old. Removed, for testing only,
  to see what it was actually filtering out - and that test (not a live
  run) answered the question decisively: signal count went from 31 to
  1114 over the same window, almost entirely repeat signals on the same
  handful of already-trending stocks (one symbol, CUPID, alone accounted
  for 19 of the 20 most recent "open" trades, each about a week apart -
  one move sliced into 19 overlapping pseudo-trades, not 19 independent
  opportunities). That both floods a live Telegram feed with near-
  duplicate alerts and statistically invalidates any win-rate/PF number
  computed across the result (massive autocorrelation, not independent
  samples) - so the floor is restored for live use, per explicit
  direction, and the Engineers India gap is an accepted, known cost of
  avoiding that failure mode rather than an oversight. (That same
  no-floor test run also surfaced and fixed a real bug on its own: a
  single very long summary line - the kind many-open-trades runs like
  that one produce - could exceed even `telegram.py`'s own chunking,
  crashing the whole script with a Telegram "message too long" error;
  `_chunk` now hard-splits any individual oversized line, and `summarize()`
  caps the "Open:" list at 20 with a remainder count, independent of
  whether the min-gap floor itself is ever revisited again.) Built as a
  candidate replacement for Price Action Breakout (Weekly) per explicit
  direction; both run live until a larger sample settles it.

## Backtesting

Go to the **Actions** tab → **Backtest** → **Run workflow**, set **months**
(default 3), optionally set **strategies** (comma-separated strategy keys,
blank = all six; `price_action_breakout` is a shorthand for both of its
timeframe legs) to skip strategies you don't need validated - useful for a
long lookback window, since each excluded strategy's scan is skipped
entirely rather than just filtered from the output, which is what made a
full 5-year backtest practical to run at all - and run it.
`scripts/run_backtest.py` fetches
the same ~500 symbol universe as a live run, then for every historical
date in that window reconstructs what each strategy would have signalled
using only
data available up to that date — the same `scan()` functions the live
screener uses, unmodified, so there's no separate backtest logic that
could silently drift out of sync with what actually runs Monday-Friday.

Every signal found is then walked forward on the real subsequent daily
price action to see how it would have played out. Most strategies check a
fixed target vs. a fixed stop-loss (if a single day's range could have hit
both, the stop-loss is assumed to trigger first — conservative, since
there's no intraday data to say which happened first within the day).

**Every reported return is net of `ROUND_TRIP_COST_PCT`** (0.22%) — real
transaction costs for an Indian cash-equity delivery trade (brokerage is
0 at every major discount broker for delivery; the unavoidable cost is
STT + stamp duty + exchange charges). A gross edge that can't survive
this is not a real edge; see `signals/config.py` for the full breakdown
and what's deliberately excluded (DP charges, slippage — both push real
costs higher still).

You'll get one Telegram message per strategy — signal count, win rate,
average return/win/loss, profit factor, average holding period, and the
best/worst individual trades. Price Action Breakout's summary also breaks
win rate down by base shape (`By shape: Range 412 (46%) | Ascending
Triangle 298 (51%) | ...`, mined from `breakout_type`, the same
diagnostic each live signal's note already carries) and by base length
(`By base length: Under 15 210 (32%) | 15-25 380 (41%) | 25-35 490 (44%) |
35+ 764 (51%)`, mined from `base_candles`, fixed absolute-candle-count
bands) - since a strategy-level rollup wouldn't otherwise surface whether
some shapes or base lengths actually perform better than others. Plus a
`backtest-trades` artifact on the workflow run containing every
individual trade (symbol, dates, entry/SL/targets, outcome, return) as a
CSV, if you want to dig into the detail yourself.

This takes noticeably longer than a live run (order of 10-20 minutes for
3 months, more for a longer window) since it's re-scanning the whole
universe once per historical date rather than just once.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q                      # runs against synthetic OHLCV data, no network needed

export UPSTOX_ACCESS_TOKEN=...    # get one from tools/refresh_upstox_token.py, or export manually
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export SCREENER_EMAIL=... SCREENER_PASSWORD=...   # optional - only needed for Weekly Value Stocks Breakout (Fridays); its absence just disables that one strategy for the run
python scripts/run_signals.py                              # daily swing only, on a non-Friday/month-end day
FORCE_WEEKLY=true FORCE_MONTHLY=true python scripts/run_signals.py   # exercise every strategy

# To test the TOTP login automation itself (install chromium first with
# `playwright install chromium`; HEADLESS=false opens a real, visible
# browser window instead of the workflow's headless one, useful for
# watching exactly where a selector doesn't match):
export UPSTOX_CLIENT_ID=... UPSTOX_CLIENT_SECRET=... UPSTOX_REDIRECT_URI=...
export UPSTOX_MOBILE_NUMBER=... UPSTOX_TOTP_SECRET=... UPSTOX_PIN=...
HEADLESS=false python scripts/login_upstox.py
```

## Project layout

```
signals/
  config.py          tunable thresholds
  universe.py        Nifty 500 constituent list + each stock's industry (from NSE)
  upstox_client.py   Upstox API wrapper (instrument master + daily/weekly/monthly candles)
  upstox_login.py    Playwright-driven TOTP login -> OAuth authorization code
  upstox_oauth.py    OAuth code -> access token exchange (shared by CI login + manual tool)
  data.py            daily/weekly/monthly-ATH fetch orchestration, resampling fallbacks, circuit breaker
  value_universe.py  screener.in fundamental-screen scraper (gates Weekly Value Stocks Breakout)
  indicators.py      SMA, volume avg, candle-quality checks
  models.py          Signal dataclass (entry/SL/targets/note)
  strategies/        one module per strategy, each exposing scan(data) -> list[Signal]
    daily_swing.py            Daily Swing (SMA50/lower-BB confluence)
    price_action_breakout.py  Price Action Breakout (detected-length base -> high-volume breakout with a shape classifier; daily enters immediately, weekly waits for a retest/reclaim; long + F&O-gated short on both)
    weekly_breakout.py        Weekly Range Breakout
    monthly_breakout.py       Monthly ATH Breakout
    value_breakout.py         Weekly Value Stocks Breakout (fundamental screen + multi-year high breakout, trailing-SMA exit)
  formatting.py       Signal list -> Telegram HTML message
  telegram.py         Telegram Bot API sender (with message chunking)
  runtime.py          env var handling, logging, error reporting to Telegram
  calendar_utils.py   "is this the last trading day of the month" check
  backtest.py         historical replay of scan() over a lookback window + forward simulation
scripts/
  login_upstox.py    CI step: TOTP login, writes UPSTOX_ACCESS_TOKEN to $GITHUB_ENV
  run_signals.py     the single daily entry point for the strategies
  run_backtest.py    on-demand historical backtest (see Backtesting below)
tools/refresh_upstox_token.py   manual fallback: local one-tap daily token refresh
.github/workflows/            the cron schedule, backtest workflow, and a test workflow
tests/                         unit tests against synthetic OHLCV data
```

## Disclaimer

This is a technical screener, not investment advice. Signals are
generated mechanically from price/volume rules — the backtest above
tells you how they'd have performed historically, which is informative
but never a guarantee of future results. Validate against your own risk
management before trading real capital.
