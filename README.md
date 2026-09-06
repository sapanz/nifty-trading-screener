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

There's no server to keep online. Three GitHub Actions workflows do the
work on a cron schedule, and post straight to Telegram:

- `.github/workflows/daily-swing.yml` — Mon-Fri, 11:30 UTC (5:00pm IST)
- `.github/workflows/weekly-signals.yml` — Fridays, 11:30 UTC (runs both
  weekly strategies as two separate Telegram messages)
- `.github/workflows/monthly-breakout.yml` — every day from the 28th
  onward at 11:30 UTC; the script itself checks whether today is actually
  the last trading day of the month and silently no-ops otherwise

Each workflow can also be triggered manually from the **Actions** tab
("Run workflow") for testing.

Market data comes from the **Upstox API** (not Yahoo Finance/yfinance,
which is unreliable for bulk NSE data). The Nifty 500 constituent list is
fetched fresh from NSE's own archives on every run.

## One-time setup

### 1. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
   follow the prompts. You'll get a **bot token** like `123456:ABC-DEF...`.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find
   `"chat":{"id": ...}` — that number is your **chat ID**.
   - For a group chat, add the bot to the group first, send a message
     there, then look for the group's (negative) chat ID the same way.

### 2. Create an Upstox app and get an access token

1. Register an app at [developer.upstox.com](https://developer.upstox.com/)
   to get an **API key** and **API secret**.
2. Generate a **daily access token** by completing Upstox's OAuth login
   flow (redirect → authorization code → token exchange). See Upstox's
   [Authentication docs](https://upstox.com/developer/api-documentation/authentication)
   for the exact steps.
3. **Upstox access tokens expire daily** (around 3:30am IST). Since this
   project uses manual token refresh (not auto-login), you need to repeat
   step 2 and update the GitHub secret below **once every day before
   5pm IST** for that day's runs to work. If this becomes tedious, the
   screener can be adapted to log in automatically via TOTP — ask if you
   want that added.

### 3. Add GitHub repository secrets

In this repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | your chat ID |
| `UPSTOX_ACCESS_TOKEN` | today's Upstox access token (refresh daily) |

Once these are set, the schedules in `.github/workflows/` will start
firing automatically — no further action needed beyond the daily token
refresh.

### 4. Test it

Go to the **Actions** tab → pick a workflow → **Run workflow**. For the
monthly workflow, tick **force** to bypass the "is it month-end" check
so you can test any day.

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
- **Monthly ATH**: all-time high is the max monthly close within
  whatever price history Upstox returns for that stock (not necessarily
  since IPO for very old listings) — see caveat below.

Entry is always the candle's close. Stop-loss sits just under the
structural support level that was tested (with a 2% buffer). Targets are
either risk-multiple based (2R/3R) for support/confluence setups, a
measured-move projection of the range height for breakouts, or open
percentage targets for fresh all-time-high breakouts (which by definition
have no prior resistance to aim at).

## Known limitations

- **NSE holiday calendar**: the "last trading day of the month" check is
  pure calendar math (last weekday of the month). If the real last
  trading day happens to be an NSE holiday, the run fires one weekday
  early instead.
- **All-time-high depth**: limited to whatever historical range Upstox's
  API returns for a given instrument, which may not reach back to a
  stock's actual listing date for very old companies.
- **Manual token refresh**: see setup step 2 — this is the trade-off
  chosen for this project instead of automated TOTP login.
- If a stock's data fetch fails for the day, it's just skipped (logged in
  the Actions run) rather than failing the whole screener.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q                      # runs against synthetic OHLCV data, no network needed

export UPSTOX_ACCESS_TOKEN=...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python scripts/run_daily_swing.py
python scripts/run_weekly.py
python scripts/run_monthly_breakout.py
```

## Project layout

```
signals/
  config.py          tunable thresholds
  universe.py        Nifty 500 constituent list (from NSE)
  upstox_client.py   Upstox API wrapper (instrument master + historical candles)
  data.py            per-symbol OHLCV fetch orchestration
  indicators.py      SMA, Bollinger Bands, volume avg, candle-quality checks
  models.py          Signal dataclass (entry/SL/targets/note)
  strategies/        one module per strategy, each exposing scan(data) -> list[Signal]
  formatting.py       Signal list -> Telegram HTML message
  telegram.py         Telegram Bot API sender (with message chunking)
  runtime.py          env var handling, logging, error reporting to Telegram
  calendar_utils.py   "is this the last trading day of the month" check
scripts/              one runnable entry point per schedule
.github/workflows/    the three cron schedules + a test workflow
tests/                unit tests against synthetic OHLCV data
```

## Disclaimer

This is a technical screener, not investment advice. Signals are
generated mechanically from price/volume rules and have not been
backtested here — validate against your own risk management before
trading real capital.
