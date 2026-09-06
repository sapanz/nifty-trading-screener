# nifty-trading-screener

Automated Nifty 500 technical screener that posts Entry / Stop-Loss / Target
levels to Telegram, on a schedule, for four strategies:

| Strategy | When | Trigger |
|---|---|---|
| **Weekly SMA-30 Support** | Fridays, 5pm IST | Above 200 SMA, weekly low tests the 30 SMA and closes back above it, proper close, volume candle |
| **Weekly Range Breakout** | Fridays, 5pm IST | Above 200 SMA, last 6 weekly candles form a tight range, close breaks above it, proper close, volume candle |
| **Daily Swing** | Every trading day, 5pm IST | Above 200 SMA, price tests the 44 SMA *and* the lower Bollinger Band together (confluence), proper close, volume candle |
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
  always runs the daily swing screener, additionally runs both weekly
  strategies on Fridays, and additionally runs the monthly ATH breakout
  on the last trading day of the month — one Upstox pass serves every
  strategy that fires that day, whatever the day.

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
| `UPSTOX_MOBILE_NUMBER` | your Upstox login mobile number |
| `UPSTOX_TOTP_SECRET` | from step 3 |

(Login here is mobile number + a verification code, with no separate
password step — the TOTP code from step 3 fills that verification-code
field instead of a texted SMS OTP.)

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

This setup stores your Upstox TOTP secret as a GitHub Actions secret,
which is more sensitive than anything else in this repo handles. Since
login here is just mobile number + verification code with no separate
password, **the TOTP secret is the entire gate on logging into your
account** — anyone who has it (plus your mobile number, which isn't
really a secret) can log in as you. Concretely:

- These secrets are only ever readable by workflows running in this
  repo — never logged in plaintext (the derived access token is
  explicitly masked in `scripts/login_upstox.py` before it's used) and
  not visible to anyone browsing the repo, including you, once saved.
- Anyone with admin/write access to this repository's secrets could use
  them to log into your Upstox account. Keep this repo private (it
  already is) and don't add collaborators you wouldn't trust with your
  broker login.
- If you ever suspect this secret has leaked, re-link your authenticator
  app in Upstox's security settings immediately (this issues a new TOTP
  secret and invalidates the old one), then update the GitHub secret.
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
- **"Volume candle"**: volume >= 1.3-1.5x the trailing 20-period average
  (the multiplier differs slightly by timeframe).
- **"Taking support at an SMA"**: the candle's low comes within 2% above
  the SMA (doesn't need to touch it exactly) and the close is back above it.
- **Weekly breakout range**: the 6 weeks preceding the breakout candle
  must have a high-low range within 15% of the range low, i.e. a genuine
  consolidation, not just drift.
- **Daily swing confluence**: the 44 SMA and the lower Bollinger Band
  (20, 2σ) must sit within 2% of each other, and the candle's low must
  reach both — two independent support levels lining up, not one.
- **Monthly ATH**: all-time high is the max monthly close within the
  trailing history the screener fetches (see caveat below), not
  necessarily since IPO for very old listings.

Entry is always the candle's close. Stop-loss sits just under the
structural support level that was tested (with a 2% buffer). Targets are
either risk-multiple based (2R/3R) for support/confluence setups, a
measured-move projection of the range height for breakouts, or open
percentage targets for fresh all-time-high breakouts (which by definition
have no prior resistance to aim at).

Weekly and monthly OHLCV are both *derived* from the same daily fetch by
resampling (`signals/data.py`) — Upstox only gets called once per symbol,
for the "day" interval, no matter how many strategies fire that day.

## Known limitations

- **The login automation is inherently fragile.** It drives Upstox's own
  login page rather than a documented API, so a layout change on their
  end will break it. When it breaks, the run fails loudly with a
  Telegram error report (not silently), and the uploaded screenshot
  artifact should make the fix quick — see setup step 5.
- **All-time-high depth is bounded**, not literal all-time. The monthly
  ATH check only sees `DAILY_HISTORY_YEARS` (6, by default) of history,
  because it's derived from the same daily fetch every other strategy
  uses. A stock whose real all-time high was set further back than that
  won't be recognized as still being below it. Increase
  `DAILY_HISTORY_YEARS` in `signals/config.py` for a deeper look-back.
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
export UPSTOX_MOBILE_NUMBER=... UPSTOX_TOTP_SECRET=...
HEADLESS=false python scripts/login_upstox.py
```

## Project layout

```
signals/
  config.py          tunable thresholds
  universe.py        Nifty 500 constituent list (from NSE)
  upstox_client.py   Upstox API wrapper (instrument master + daily candles)
  upstox_login.py    Playwright-driven TOTP login -> OAuth authorization code
  upstox_oauth.py    OAuth code -> access token exchange (shared by CI login + manual tool)
  data.py            daily fetch orchestration + weekly/monthly resampling + circuit breaker
  indicators.py      SMA, Bollinger Bands, volume avg, candle-quality checks
  models.py          Signal dataclass (entry/SL/targets/note)
  strategies/        one module per strategy, each exposing scan(data) -> list[Signal]
  formatting.py       Signal list -> Telegram HTML message
  telegram.py         Telegram Bot API sender (with message chunking)
  runtime.py          env var handling, logging, error reporting to Telegram
  calendar_utils.py   "is this the last trading day of the month" check
scripts/
  login_upstox.py    CI step: TOTP login, writes UPSTOX_ACCESS_TOKEN to $GITHUB_ENV
  run_signals.py     the single daily entry point for the strategies
tools/refresh_upstox_token.py   manual fallback: local one-tap daily token refresh
.github/workflows/            the cron schedule + a test workflow
tests/                         unit tests against synthetic OHLCV data
```

## Disclaimer

This is a technical screener, not investment advice. Signals are
generated mechanically from price/volume rules and have not been
backtested here — validate against your own risk management before
trading real capital.
