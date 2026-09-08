# nifty-trading-screener

Automated Nifty 500 technical screener that posts Entry / Stop-Loss / Target
levels to Telegram, on a schedule, for three strategies:

| Strategy | When | Trigger |
|---|---|---|
| **Daily Swing** | Every trading day, 5pm IST | Above 200 SMA, rising 44 SMA, price tests support at the 44 SMA, and the 44 SMA sits right on top of the lower Bollinger Band |
| **Weekly Range Breakout** | Fridays, 5pm IST | Above 200 SMA, rising 30 SMA, last 6 weekly candles form a tight range with more volume on up candles than down (accumulation), close breaks above the range by 4-12% (not a weak break, not already extended), breakout candle is bullish (green) with a proper close, volume candle |
| **Monthly ATH Breakout** | Last trading day of the month, 5pm IST | Monthly close breaks above its prior all-time high on volume; reports how many months it took, sorted longest-dormant first |

No manual judgement calls at run time — every "properly closed candle" /
"volume candle" / "support test" rule is a precise, testable condition (see
[How the rules are encoded](#how-the-rules-are-encoded)).

## How it runs

There's no server to keep online. A single GitHub Actions workflow does
the work on a cron schedule and posts straight to Telegram:

- `.github/workflows/signals.yml` — Mon-Fri, 11:30 UTC (5:00pm IST). It
  first logs into Upstox automatically (`scripts/login_upstox.py`, via
  TOTP), then `scripts/run_signals.py` fetches daily OHLCV **once** and
  always runs the daily swing screener, additionally runs the weekly
  range breakout on Fridays, and additionally runs the monthly ATH
  breakout on the last trading day of the month — one Upstox pass serves
  every strategy that fires that day, whatever the day.

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

- **"Properly closed candle"**: `(high - close) / (high - low) <= 0.25`
  — the close sits in the top 75% of the candle's range (small upper wick).
- **"Volume candle"**: volume >= 1.3x the trailing 20-period average.
  Weekly breakout and monthly ATH breakout require this; Daily Swing gates
  on the SMA44/lower-BB confluence instead.
- **Daily Swing**: `signals/strategies/daily_swing.py`. Above the 200 SMA
  (long-term uptrend), with the `SMA_SWING` (44) SMA itself rising - not
  flat or falling - and price testing support at it (low within
  `DAILY_SUPPORT_TOLERANCE` above the SMA, closing back above). On top of
  that, the 44 SMA and the lower Bollinger Band (`BOLLINGER_PERIOD`,
  `BOLLINGER_STD`) must sit within `CONFLUENCE_TOLERANCE` of each other -
  two independently-computed support levels lining up is a stronger signal
  than either alone - and the candle's low must reach down to the lower
  band too, not just the SMA. (Several other Daily Swing designs - Darvas
  Box, CANSLIM overlays, ATH-proximity SMA-30 support, Wyckoff-style
  base/breakout/retest, a volume-anomaly "pocket pivot" - were tried later
  and dropped without beating this original version; see git history.)
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
  needs much more lookback than a trend/range check does.

**Entry/stop-loss differ by strategy:**
- **Daily Swing**: entry is the signal candle's **high** (a buy-stop
  triggered the next day price trades up to it); stop-loss is the
  **lower of the signal candle's own low and the previous candle's low**.
  Targets are risk-multiples of that entry-to-SL distance
  (`RISK_REWARD_TARGETS`, 2R/3R by default).
- **Weekly Range Breakout**: entry is the breakout candle's close.
  Stop-loss is whichever of two anchors is tighter: just under the
  breakout level itself (the top of the consolidation range — "old
  resistance becomes new support"), or a fixed `WEEKLY_MAX_RISK_PCT`
  (4%) below entry — the latter caps risk from ballooning on a more
  extended entry (entries can sit up to 12% above the breakout level).
  There's no fixed target: the trade is held as long as the weekly close
  stays above its own `BREAKOUT_TREND_SMA`-week SMA, exiting the week it
  closes back below (`backtest.simulate_weekly_trailing_sma`) — fixed
  measured-move targets were dropped because `simulate_forward` exits at
  the first target touched, so a farther target almost never mattered in
  practice and winners couldn't run past the nearest one; see the note in
  `signals/config.py`.
- **Monthly ATH Breakout**: entry is the candle's close, stop-loss sits
  just under the prior all-time high with a 2% buffer (unchanged). Like
  Weekly Range Breakout, there's no fixed target: the trade is held as
  long as the *weekly* close stays above its own `MONTHLY_TRAIL_SMA`-week
  SMA, exiting the week it closes back below.

Daily Swing is the only strategy on the shared daily fetch (`data.fetch_daily`,
the "day" interval, once per run). Weekly Range Breakout and Monthly ATH
Breakout each fetch their own native interval directly instead of
resampling that daily data: `data.fetch_weekly_history` ("week") and
`data.fetch_monthly_ath_history` ("month"), each only on the day its
strategy actually runs (Fridays / month-end). Weekly's native fetch was
originally motivated by candle accuracy rather than depth (each weekly
candle should match what Upstox itself considers "the week's" OHLCV,
rather than a pandas resample of daily bars that can draw week
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

## Backtesting

Go to the **Actions** tab → **Backtest** → **Run workflow**, set **months**
(default 3), and run it. `scripts/run_backtest.py` fetches the same ~500
symbol universe as a live run, then for every historical date in that
window reconstructs what each strategy would have signalled using only
data available up to that date — the same `scan()` functions the live
screener uses, unmodified, so there's no separate backtest logic that
could silently drift out of sync with what actually runs Monday-Friday.

Every signal found is then walked forward on real subsequent price action
to see how it would have played out. Daily Swing checks a fixed target vs.
a fixed stop-loss (if a single day's range could have hit both, the
stop-loss is assumed to trigger first — conservative, since there's no
intraday data to say which happened first within the day). Weekly Range
Breakout and Monthly ATH Breakout have no fixed target — each is walked
forward on the weekly close instead, held as long as it stays above its
own trailing SMA and exited the week it closes back below (or immediately
on a stop-loss breach).

**Every reported return is net of `ROUND_TRIP_COST_PCT`** (0.22%) — real
transaction costs for an Indian cash-equity delivery trade (brokerage is
0 at every major discount broker for delivery; the unavoidable cost is
STT + stamp duty + exchange charges). A gross edge that can't survive
this is not a real edge; see `signals/config.py` for the full breakdown
and what's deliberately excluded (DP charges, slippage — both push real
costs higher still).

You'll get one Telegram message per strategy — signal count, win rate,
average return/win/loss, profit factor, average holding period, and the
best/worst individual trades — plus a `backtest-trades` artifact on the
workflow run containing every individual trade (symbol, dates,
entry/SL/targets, outcome, return) as a CSV, if you want to dig into the
detail yourself.

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
    daily_swing.py      Daily Swing (SMA44/lower-BB confluence)
    weekly_breakout.py  Weekly Range Breakout
    monthly_breakout.py Monthly ATH Breakout
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
