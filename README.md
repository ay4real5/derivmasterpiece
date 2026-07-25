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

> Deriv retired their Legacy API (numeric app_ids + WS `authorize` tokens
> from app.deriv.com). This bot targets the **current** Deriv API:
> alphanumeric app IDs, `pat_...` Personal Access Tokens, and a REST
> handshake that mints an authenticated trading WebSocket URL.

1. `pip install -r requirements.txt`
2. Get a Deriv account (demo is free, no card needed) at
   [deriv.com](https://deriv.com).
3. Register an app at
   [developers.deriv.com/dashboard](https://developers.deriv.com/dashboard)
   → *Registered apps* → *Create new app* → **Native apps (PAT)** type.
   Name it anything (no "Deriv"/"Binary" in the name), markup 0%. Copy the
   alphanumeric **App ID** it gives you.
4. Create a **Personal Access Token** on the same dashboard under
   *API tokens* — `Trade` scope is required. It looks like `pat_<hex>`.
5. `cp config.example.yaml config.yaml` and fill in your `app_id` and the
   symbol you want to trade (default `R_100`, Volatility 100 Index).
6. `cp .env.example .env` and paste your PAT into `DERIV_API_TOKEN`. Leave
   `DEMO_MODE=true` — one PAT can reach both your demo and real accounts,
   and `DEMO_MODE` controls which one the bot will connect to.

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
`deriv_bot/backtester.py`. Uses whichever strategy is set in `config.yaml`'s
`strategy.name` (see "Strategies" below).

Compare every strategy (default params) against the same historical data:
```
python main.py backtest --compare
```
This ranks ideas against each other, not against reality — see the honesty
note in `deriv_bot/strategy.py` before reading anything into a strategy that
comes out ahead.

Scan Deriv's live payouts across every Digits contract/barrier and rank by
smallest house edge (needs `DERIV_API_TOKEN` in `.env` — payout data requires
an authorized session even just to look up prices):
```
python main.py scan-edge
```
This is the one legitimate lever available here: which bet currently costs
you the least, not which one will win. Win probability is the theoretical
value (digits are ~uniform), not a prediction.

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

Analyze the journal after a session — per-contract win rates, PnL, and loss
as a % of money staked, next to the honest theoretical expectation:
```
python main.py analyze
```

### Going to a real-money account

Only after you're satisfied with demo results: set `DEMO_MODE=false` in
`.env`. The bot then selects your real account instead of demo, and asks
you to type a confirmation phrase before starting a session that can place
real trades. While `DEMO_MODE=true`, the bot never connects to the real
account at all.

## Strategies

Set `strategy.name` in `config.yaml` to pick one (see `deriv_bot/strategy.py`
for the registry and each strategy's honesty caveat):

- `digit_frequency` (default) — Over/Under reversion on digit frequency drift.
- `even_odd_frequency` — same idea, on the Even/Odd contract pair.
- `streak_reversal` — bets against a run of consecutive same-side digits.
  This one is the gambler's fallacy made explicit; it's included as a
  baseline to backtest against, not a recommendation.
- `low_edge` — predicts nothing; just takes the cheapest bet Deriv offers
  (default DIGITOVER 0, ~90% win probability, smallest house margin) on a
  fixed cadence (`every`, default 15 ticks). `contract_type`/`barrier` are
  configurable — run `scan-edge` and point it at whatever is currently
  cheapest. The honest benchmark: the slowest possible expected bleed,
  which every "predictive" strategy above has to beat to justify itself.
  Supports the digit contracts plus `CALL`/`PUT` (Rise/Fall).

### Measured house margins (R_100, July 2026)

| contract | win prob | house margin |
|---|---|---|
| DIGITOVER 0 / DIGITUNDER 9 / DIGITDIFF | 90% | **2.17%** |
| DIGITOVER 1 / DIGITUNDER 8 | 80% | 2.40% |
| Even/Odd, Over/Under 4 | 50% | 3.85% |
| **Rise/Fall (CALL/PUT, 1–10 ticks)** | ~50% | **3.80–3.90%** |
| DIGITMATCH, Over 8 / Under 1 | 10% | 16.67% |

Rise/Fall is priced like the other 50/50 contracts — it is *not* a cheaper
seat than the 90% digit contracts, despite looking like "real" trading. Note
its win probability is a shade under 50% (an exactly flat tick loses for both
sides), so its true margin is slightly worse than shown.

## Martingale

`tools/martingale_sim.py` simulates double-after-loss staking. On the 50%
contracts with a $10k bankroll and $10 base stake: **64% of careers bust**,
median peak balance $13.8k, median final balance $4.9k. The peak is why
people believe it works; the final is why it doesn't. The live bot has no
stake-progression path by design.

### Why digits-only, on purpose

The bot deliberately trades only the digit contract family (Over/Under,
Even/Odd, Matches/Differs) and not Deriv's other products (Rise/Fall,
Multipliers, Accumulators, Vanillas, Turbos). Digit outcomes are uniform
and exactly computable — every bet's fair odds are known to the decimal,
which is what makes `scan-edge`'s house-margin math, honest backtesting,
and `analyze`'s theory-vs-actual comparison possible at all. The other
products need volatility modeling (or are path-dependent or leveraged) —
with those, you can't tell a mispriced bet from bad luck, and this project's
whole point is measuring instead of guessing.

Add your own by subclassing `Strategy` and registering it in `STRATEGIES`.

## Project layout

- `deriv_bot/api.py` — asyncio client for the current Deriv API: public WS
  for market data, REST (PAT bearer + `Deriv-App-ID`) for account discovery
  and minting the authenticated trading WS URL, then the classic JSON
  message protocol (ticks_history, proposal, buy, `wait_for_settlement`,
  live tick subscription) over that socket.
- `deriv_bot/strategy.py` — `Strategy` interface, the strategy registry, and
  the shipped strategies (see "Strategies" above).
- `deriv_bot/risk.py` — session risk manager (max daily loss, max
  consecutive losses, max trade count) — the kill switch, with a UTC
  day-boundary reset.
- `deriv_bot/journal.py` — CSV trade log.
- `deriv_bot/backtester.py` — historical replay + approximate PnL report.
  Signals resolve against the *next* tick, matching how a real duration=1
  contract settles.
- `deriv_bot/edge.py` — live payout scanner (see "Usage" above).
- `main.py` — CLI: `backtest [--compare]`, `scan-edge`, `live [--dry-run]`.
- `tests/` — strategy, risk-manager, and backtester unit tests, no network
  access.

## Settlement tracking

`main.py live` waits for each contract to settle (via `proposal_open_contract`)
before moving on to the next signal, then logs the real `profit` and
resulting account `balance_after` to the journal and feeds `profit` into the
`RiskManager` — this is what makes `max_daily_loss` / `max_consecutive_losses`
/ `max_trades` actually take effect. Digit contracts settle in one tick, so
this adds negligible latency; strategies with longer-duration contracts would
need this reworked to settle concurrently instead of blocking the tick loop.
