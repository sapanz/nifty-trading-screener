# nifty-trading-screener

Automated Nifty 500 technical screener that posts Entry / Stop-Loss / Target
levels to Telegram, on a schedule, for six strategies:

| Strategy | When | Trigger |
|---|---|---|
| **Daily Swing** | Every trading day, 5pm IST | Above 200 SMA, rising 50 SMA; a bullish candle with a proper close takes support at the 50 SMA and also reaches down to the lower Bollinger Band, which itself sits right on top of the 50 SMA — all three (SMA, band, candle) converging at once — plus volume at/above average and price already well clear of the 200 SMA |
| **Price Action Breakout (Daily)** | Every trading day, 5pm IST | **Long:** above 200 SMA; the *longest* tight prior base found (8-40 daily candles, whatever the data actually supports, accumulation-biased) breaks out on clearly elevated volume (2x average), price later pulls back to retest that broken level without closing convincingly below it, and today closes green, properly closed, and back above the level - that candle is the actual trigger. **Short (F&O-eligible symbols only):** the exact mirror - below 200 SMA, a distribution-biased base breaks *down* on elevated volume, a retest rallies back toward the broken support without reclaiming it, today closes red, properly closed, back below the level. The base's shape (Range, Ascending/Descending/Symmetrical Triangle, Rising/Falling Wedge) is labeled from the slope of its highs and lows - a heuristic, not real geometric pattern recognition. Entry is a resting stop at today's high (long) or low (short); targets are a measured move off the base's own height; signals sort by the base's own range %, largest first. Backtested over 5 years on the long side - see [Known limitations](#known-limitations); the short leg is new and not yet backtested at scale |
| **Price Action Breakout (Weekly)** | Fridays, 5pm IST | Same base → high-volume breakout/breakdown → retest → confirmation logic as the daily leg above (both long and short), run on weekly candles instead (5-20 week base) - a separate, independently tracked signal, not a duplicate of the daily one |
| **Weekly Range Breakout** | Fridays, 5pm IST | Above 200 SMA, rising 30 SMA, last 6 weekly candles form a tight range with more volume on up candles than down (accumulation), close breaks above the range by 4-12% (not a weak break, not already extended), breakout candle is bullish (green) with a proper close, volume candle; entry is a resting buy-stop at the breakout candle's high, filled only once a later candle trades through it |
| **Monthly ATH Breakout** | Last trading day of the month, 5pm IST | Monthly close breaks above its prior all-time high on volume, the breakout candle is bullish (green) with a proper close, at least `MONTHLY_MIN_GAP_MONTHS` (3) months after that prior high; reports how many months it took, sorted longest-dormant first |

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
- **Price Action Breakout**: `signals/strategies/price_action_breakout.py`,
  run twice (daily, weekly - see the `PRICE_ACTION_*_DAILY` / `_WEEKLY`
  families in config.py for the two sets of window sizes `scan()` is
  called with; no monthly leg - a 5-year backtest showed it never fired
  under these thresholds, and Monthly ATH Breakout already covers that
  timeframe). Above the 200 SMA, then a
  four-stage sequence: (1) a **base** - `_detect_base` walks backward from
  a candidate breakout candle looking for the *longest* window (between
  each timeframe's `PRICE_ACTION_PATTERN_MIN_LOOKBACK_*` and
  `_MAX_LOOKBACK_*`) whose high-low band still stays within
  `PRICE_ACTION_RANGE_TIGHTNESS` (20%, the same convention Weekly Range
  Breakout's own range uses) with more volume on its up candles than down
  (`is_accumulation_range`, again shared with Weekly Range Breakout) - the
  base's length is genuinely detected per signal, not a fixed number, and
  `_classify_shape` labels its shape (Range, Ascending/Descending/
  Symmetrical Triangle, Rising/Falling Wedge) from the slopes of straight
  lines fit through its highs and lows; (2) a **breakout candle**, within
  the last `PRICE_ACTION_BREAKOUT_WINDOW_*` candles, closing above the
  base's high, bullish with a proper close, on volume at least
  `PRICE_ACTION_BREAKOUT_VOLUME_MULTIPLIER` (2x - deliberately higher than
  every other strategy's volume bar, since "high volumes" was the explicit
  ask for the breakout itself); (3) a **retest** - at least one later
  candle coming back down within `PRICE_ACTION_RETEST_TOLERANCE` (2%) of
  that broken level, with no close in between falling more than
  `PRICE_ACTION_INVALIDATION_PCT` (3%) below it (a wick undercutting the
  level is a normal retest; a close well below it means the level failed);
  and (4) **today** - a green, properly-closed candle (the same "20%-wick"
  rule from the table above, left exactly as it was) closing back above
  the breakout level. That last candle is the actual trigger the strategy
  sends - everything before it is context the candle confirms, not a
  separate thing it's watching for on its own.

  `_classify_shape` fits a straight line (least-squares) through the
  base's highs and another through its lows, and labels the combination of
  slopes (flat/rising/falling per `PRICE_ACTION_FLAT_SLOPE_PCT`) - flat
  top + flat bottom is a **Range**, flat top + rising bottom an
  **Ascending Triangle**, falling top + flat bottom a **Descending
  Triangle**, falling top + rising bottom a **Symmetrical Triangle**, both
  rising (top slower) a **Rising Wedge**, both falling (top faster) a
  **Falling Wedge**. This is a real classification, but a heuristic one -
  slope sign and magnitude, not genuine trendline-touch-point geometry -
  and is labeled as such rather than dressed up as more rigorous than it
  is. Every signal's note and `extra` dict report the base's detected
  length (`base_candles`), its calendar start/end (`base_start`/
  `base_end`), and its shape (`breakout_type`); signals sort by the base's
  own range % (`sort_key`), **largest range first**, per explicit request
  - not by volume or recency.

  **Short leg (breakdown), F&O-eligible symbols only**: a cash-segment
  equity short can't be carried overnight in India for retail - it's
  intraday-only unless the position is actually a sale of the stock's
  futures contract, so shorting a symbol with no futures market isn't
  practically actionable the way a long always is. `short_eligible`
  (`data.fetch_fo_eligible_symbols` → `UpstoxClient.fetch_fo_symbol_set`,
  the `name` column of the same instrument master's `FUTSTK` rows) gates
  which symbols the short side is even considered for; the long side is
  unaffected and still runs on the full universe either way. Detection is
  the *exact mirror* of the long side, same thresholds throughout, just
  flipped: below the 200 SMA, `_detect_base` requires a **distribution**
  base (`is_distribution_range` - down-volume beats up-volume, not the
  long side's `is_accumulation_range`), the breakout candle closes
  *below* the base's low on the same elevated volume, the retest rallies
  back *up* toward that broken support without reclosing above it, and
  today is a red, properly-closed candle (`is_bearish` +
  `is_proper_close_bearish`) that closes back below the level. No
  short-specific tuning anywhere in `config.py`.

  Long side backtested over 5 years - every threshold above is a judgment
  call, not something mined from a backtest CSV the way the older
  strategies' numbers were; see [Known limitations](#known-limitations).
  The short leg is new and unbacktested at scale (the F&O-eligible slice
  of the universe is much smaller, and Upstox's instrument master only
  gives *today's* F&O universe, not a historical snapshot per date - see
  Known limitations for what that means for backtesting it).

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
  of the range height (`BREAKOUT_RANGE_MULTIPLES`, 1.5x and 3x). Widening
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
  definition has no prior resistance to aim at.
- **Price Action Breakout (long)**: entry is a resting buy-stop at
  **today's** (the confirmation candle's) own high - same buy-stop
  construction every other strategy here uses, filled only once a later
  candle trades up through it. Stop-loss is the **lower of the retest's
  own low and today's low** - the support just demonstrated holding, not
  a fixed buffer below the breakout level. Targets are a measured move
  (`PRICE_ACTION_TARGET_MULTIPLES`, 1x/2x) - the base's own height
  projected up from the breakout level, the same idea as Weekly Range
  Breakout's `BREAKOUT_RANGE_MULTIPLES` off its own range height.
- **Price Action Breakout (short, F&O-eligible only)**: the exact mirror -
  entry is a resting sell-stop at today's own **low**; stop-loss is the
  **higher of the retest's own high and today's high** - the resistance
  just demonstrated holding; targets project the same base height
  *downward* from the breakdown level.

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

## Known limitations

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
- **Price Action Breakout has now been backtested over 5 years (daily +
  weekly), and the edge is thin, not strong.** Daily: 2,654 signals, 48%
  win rate, profit factor 1.13. Weekly: 190 signals, 63% win rate but
  losses running much bigger than wins (avg loss -8.1% vs avg win +5.3%),
  profit factor 1.10. Both are barely above breakeven after transaction
  costs - real, mechanically-working strategies, but not a strong edge on
  either timeframe. The monthly leg was dropped entirely after that same
  backtest showed it produced **zero signals across the full 5 years**
  under these thresholds (too little monthly history per stock to form a
  base this strict) - Monthly ATH Breakout already covers the monthly
  timeframe, so this wasn't worth loosening the thresholds to force. Every
  remaining threshold in `signals/config.py`'s Price Action Breakout
  section (the base's min/max length, its tightness, the breakout's volume
  multiplier, the retest tolerance and invalidation floor, the target
  multiples, the shape classifier's flat-slope cutoff) is still a judgment
  call, not a number mined from the backtest CSV the way the older
  strategies' thresholds were - that CSV exists now and could be mined the
  same way if this strategy is worth tightening further. The shape label
  (Range/Triangle/Wedge) is a genuine classification - it's computed from
  real slopes fit through the base's highs and lows - but it's a
  heuristic, not real trendline-touch-point geometry the way a human
  chartist or a dedicated pattern-recognition library would do it; treat
  it as a useful label, not a rigorous one.
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
  universe.py        Nifty 500 constituent list (from NSE)
  upstox_client.py   Upstox API wrapper (instrument master + daily/weekly/monthly candles)
  upstox_login.py    Playwright-driven TOTP login -> OAuth authorization code
  upstox_oauth.py    OAuth code -> access token exchange (shared by CI login + manual tool)
  data.py            daily/weekly/monthly-ATH fetch orchestration, resampling fallbacks, circuit breaker
  indicators.py      SMA, volume avg, candle-quality checks
  models.py          Signal dataclass (entry/SL/targets/note)
  strategies/        one module per strategy, each exposing scan(data) -> list[Signal]
    daily_swing.py            Daily Swing (SMA50/lower-BB confluence)
    price_action_breakout.py  Price Action Breakout (detected-length base -> high-volume breakout -> retest -> green confirmation, with a shape classifier; run on daily and weekly candles)
    weekly_breakout.py        Weekly Range Breakout
    monthly_breakout.py       Monthly ATH Breakout
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
