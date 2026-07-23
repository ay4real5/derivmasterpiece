# deriv-digit-bot

A small, testable trading bot for Deriv's **Digits** contracts (Over/Under,
Even/Odd) built against Deriv's official WebSocket API. Built for
backtesting and demo-account trading first — going live with real money is
an explicit, separate step you take on purpose.

## Read this first

- **Digits contracts have a built-in payout margin (house edge).** No
  strategy shipped here is a proven money-maker — the included
  `DigitFrequencyStrategy` (see `deriv_bot/strategy.py`) is a simple example
  to backtest and iterate on, not a guaranteed edge. Deriv's synthetic-index
  digits are generated to be close to uniform/independent draws, so treat
  "frequency reversion" ideas skeptically.
- **This is not investment advice.** Only ever risk money you can afford to
  lose completely.
- **Start on a demo account and stay there** until you've watched the bot
  behave correctly for a while. `DEMO_MODE=true` is the default and the bot
  actively refuses to trade a real-money token while it's set.
- The bot never applies martingale/progressive staking by default — only
  flat stakes. Adding progressive staking is possible but is the single
  most common way retail bots blow up an account after a losing streak.

## Setup

1. `pip install -r requirements.txt`
2. Get a Deriv account (demo is free, no card needed) at
   [deriv.com](https://deriv.com).
3. Register an app to get an `app_id`: log in at
   [api.deriv.com](https://api.deriv.com) → *Manage Applications* → create
   an app (any name, redirect URL can be `http://localhost`).
4. Get an API token for your **demo** account at
   [app.deriv.com/account/api-token](https://app.deriv.com/account/api-token)
   — scopes: `Read` and `Trade` are enough to start.
5. `cp config.example.yaml config.yaml` and fill in your `app_id` and the
   symbol you want to trade (default `R_100`, Volatility 100 Index).
6. `cp .env.example .env` and paste your demo API token into
   `DERIV_API_TOKEN`. Leave `DEMO_MODE=true`.

## Usage

Run tests (no network calls):
```
pytest
```

Backtest against real historical ticks (no orders placed, no auth needed):
```
python main.py backtest
```
Prints trade count, win rate, and an *approximate* PnL — the payout
multiplier is a flat estimate, not Deriv's real per-contract payout. See
`deriv_bot/backtester.py`.

Paper-trade against **live** ticks and real proposal payouts, but never buy
anything (best next step after backtesting looks reasonable):
```
python main.py live --dry-run
```

Trade for real on your demo account once you're comfortable with the
dry-run output:
```
python main.py live
```
This authorizes with your `DERIV_API_TOKEN`, streams live ticks, and places
real (demo-money) trades when the strategy signals, subject to the limits in
`config.yaml`'s `risk` section. Every trade is appended to
`trade_journal.csv`.

### Going to a real-money account

Only after you're satisfied with demo results: put a real-account API token
in `.env` and set `DEMO_MODE=false`. The bot will still refuse to run
against a token whose account isn't what `DEMO_MODE` says, and will ask you
to type a confirmation phrase before the first real trade of a session.

## Project layout

- `deriv_bot/api.py` — asyncio WebSocket client (connect, authorize,
  ticks_history, proposal, buy, live tick subscription).
- `deriv_bot/strategy.py` — `Strategy` interface + `DigitFrequencyStrategy`
  default. Add your own by subclassing `Strategy`.
- `deriv_bot/risk.py` — session risk manager (max daily loss, max
  consecutive losses, max trade count) — the kill switch.
- `deriv_bot/journal.py` — CSV trade log.
- `deriv_bot/backtester.py` — historical replay + approximate PnL report.
- `main.py` — CLI: `backtest`, `live [--dry-run]`.
- `tests/` — strategy and risk-manager unit tests, no network access.

## Known limitation

`main.py live` logs each trade as it's placed but doesn't yet poll
`proposal_open_contract` to find out whether it won or lost (`profit`/
`balance_after` are left blank in the journal). Validate entries via
`--dry-run` and demo trading first; wiring up settlement tracking is a
natural next step once you trust the entry logic.
