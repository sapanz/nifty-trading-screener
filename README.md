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

There's no server to keep online, and no broker account or API token
needed. A single GitHub Actions workflow does the work on a cron schedule
and posts straight to Telegram:

- `.github/workflows/signals.yml` — Mon-Fri, 11:30 UTC (5:00pm IST).
  `scripts/run_signals.py` fetches NSE daily data **once**, then always
  runs the daily swing screener, additionally runs both weekly
  strategies on Fridays, and additionally runs the monthly ATH breakout
  on the last trading day of the month — one NSE scrape serves every
  strategy that fires that day, whatever the day.

It can also be triggered manually from the **Actions** tab ("Run
workflow"), with checkboxes to force the weekly/monthly strategies to run
on any day for testing.

Market data comes directly from **NSE's own public historical-data API**
(the same one nseindia.com's charts use) — no Yahoo Finance/yfinance
(unreliable for bulk NSE data) and no broker account (which would mean a
daily access-token refresh). The Nifty 500 constituent list is fetched
fresh from NSE's own archives on every run too. The trade-off: this is an
undocumented API that NSE rate-limits and blocks bot-like traffic on —
see [Known limitations](#known-limitations).

## One-time setup

### 1. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
   follow the prompts. You'll get a **bot token** like `123456:ABC-DEF...`.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find
   `"chat":{"id": ...}` — that number is your **chat ID**.
   - For a group chat, add the bot to the group first, send a message
     there, then look for the group's (negative) chat ID the same way.

### 2. Add GitHub repository secrets

In this repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | your chat ID |

That's it — no broker credentials, no daily token refresh. Once these
are set, the schedule in `.github/workflows/signals.yml` fires
automatically with no further action needed.

### 3. Test it

Go to the **Actions** tab → **Nifty500 Signals** → **Run workflow**. Tick
**force_weekly** and/or **force_monthly** to exercise those strategies on
a day when they wouldn't normally fire.

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
resampling (`signals/data.py`) — NSE's public API only speaks daily bars,
so there's no separate network round-trip per timeframe.

## Known limitations

- **NSE is an undocumented, rate-limited API.** `signals/nse_client.py`
  scrapes the same JSON endpoint nseindia.com's own charts use, not an
  official/stable API. NSE aggressively blocks bot-like traffic; requests
  are spaced out and a failing symbol/chunk is skipped (logged) rather
  than failing the whole run, but if NSE tightens blocking further,
  expect more skipped symbols or a need to re-tune
  `NSE_REQUEST_DELAY_SECONDS` / `NSE_CHUNK_DAYS` in `signals/config.py`.
  If NSE changes the response field names, only `nse_client.py` needs
  fixing — it fails loudly (and reports to Telegram) rather than silently
  returning wrong data.
- **All-time-high depth is bounded**, not literal all-time. The monthly
  ATH check only sees `DAILY_HISTORY_YEARS` (8, by default) of history,
  because it's derived from the same daily fetch every other strategy
  uses. A stock whose real all-time high was set further back than that
  won't be recognized as still being below it. Increase
  `DAILY_HISTORY_YEARS` in `signals/config.py` for a deeper look-back at
  the cost of a slower daily fetch (more chunked requests per symbol).
- **NSE holiday calendar**: the "last trading day of the month" check is
  pure calendar math (last weekday of the month). If the real last
  trading day happens to be an NSE holiday, the run fires one weekday
  early instead.
- If a stock's data fetch fails for the day, it's just skipped (logged in
  the Actions run) rather than failing the whole screener.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q                      # runs against synthetic OHLCV data, no network needed

export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python scripts/run_signals.py                              # daily swing only, on a non-Friday/month-end day
FORCE_WEEKLY=true FORCE_MONTHLY=true python scripts/run_signals.py   # exercise every strategy
```

## Project layout

```
signals/
  config.py          tunable thresholds
  universe.py        Nifty 500 constituent list (from NSE)
  nse_client.py      NSE historical-data scraper (no auth, chunked + rate-limited)
  data.py            daily fetch orchestration + weekly/monthly resampling
  indicators.py      SMA, Bollinger Bands, volume avg, candle-quality checks
  models.py          Signal dataclass (entry/SL/targets/note)
  strategies/        one module per strategy, each exposing scan(data) -> list[Signal]
  formatting.py       Signal list -> Telegram HTML message
  telegram.py         Telegram Bot API sender (with message chunking)
  runtime.py          env var handling, logging, error reporting to Telegram
  calendar_utils.py   "is this the last trading day of the month" check
scripts/run_signals.py   the single daily entry point
.github/workflows/       the cron schedule + a test workflow
tests/                   unit tests against synthetic OHLCV data
```

## Disclaimer

This is a technical screener, not investment advice. Signals are
generated mechanically from price/volume rules and have not been
backtested here — validate against your own risk management before
trading real capital.
