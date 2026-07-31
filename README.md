# deriv-digit-bot

A small, testable trading bot for Deriv's **Digits** contracts (Over/Under,
Even/Odd) built against Deriv's official WebSocket API. Built for
backtesting and demo-account trading first — going live with real money is
an explicit, separate step you take on purpose.

## What is in this repo now

Four bots and two research reports. All on demo.

**The NOTOUCH bot is the only one running**, via `tools/risefall_supervisor.py`
(generic despite the name - it takes `--config`/`--journal`/`--log`, so it
supervises whichever of these it's pointed at). It **replaced the digit bot**,
which is now fully stopped (scheduled task removed). The Rise/Fall bot's task
was never actually installed despite an earlier version of this file claiming
it was running - see "Docs vs. reality" below for how that was found.

| | status | config | supervisor | journal | log |
|---|---|---|---|---|---|
| **NOTOUCH bot** | **RUNNING** | `config.notouch.yaml` | `tools/risefall_supervisor.py` | `notouch_journal.csv` | `notouch_live.log` |
| Digit bot (Over/Under, Even/Odd) | **stopped** (task removed) | `config.yaml` | `tools/supervisor.py` | `trade_journal.csv` | `scan_trade_live.log` |
| Rise/Fall bot (PDF strategy) | not deployed | `config.risefall.yaml` | `tools/risefall_supervisor.py` | `risefall_journal.csv` | `risefall_live.log` |
| Multiplier pricebot | manual only | `config.pricebot.yaml` | — | `pricebot_journal.csv` | — |

NOTOUCH trades **R_50 only**, a fixed 30%-of-spot barrier over a 5-minute
window, flat 3.00 stake, no ladder. Chosen with `python main.py scan-touch`
(see `deriv_bot/touch_edge.py`): ~93.5% win rate at ~2.28% margin, cheaper
than the digit bot's blended ~3.0% (even/rise categories) and, like every
contract measured in this repo, no better than that margin in expectation -
a wider barrier buys a higher win rate at the same expected cost, not an
edge. `-850`/`+1000` daily loss/target caps, matching the digit bot's scale.

**Only one supervisor per bot-name lock can run at a time** (`tools/lockfile.py`,
named per `--config` since `tools/risefall_supervisor.py` started supervising
more than one bot - previously hardcoded to `risefall_supervisor` regardless
of config, which would have let this bot and a future re-enabled Rise/Fall
bot silently share one lock). Two supervisors on the same lock would double
the trade rate *and* give each its own daily-loss cap, silently doubling the
limit - which happened once before the lock existed.

**Docs vs. reality: read `OPERATING_STATE.md`, not this table, when in
doubt.** `config.yaml`/`config.notouch.yaml`'s actual deployed state can only
be confirmed from the running scheduled tasks and live logs, not assumed from
a markdown file - this file previously claimed the Rise/Fall bot was running
when the digit bot actually was, undetected until a live scheduled-task check
during this NOTOUCH deployment. `OPERATING_STATE.md` is kept current with
what `Get-ScheduledTask` and the live logs actually show.

Install the NOTOUCH bot so it survives logoff and reboot — **elevated
PowerShell required** (`-ExecutionPolicy Bypass` if scripts are blocked in
that session):

```powershell
.\tools\install_risefall_task.ps1 -TaskName DerivNoTouchSupervisor -ConfigPath config.notouch.yaml -JournalPath notouch_journal.csv -LogPath notouch_live.log -MaxDailyLoss 850 -TargetProfit 1000
.\tools\install_risefall_task.ps1 -TaskName DerivNoTouchSupervisor -Uninstall
Get-Content notouch_live.log -Tail 20 -Wait   # watch it
```

The same script installs the Rise/Fall bot with its historical defaults
(`-TaskName DerivRiseFallSupervisor -ConfigPath config.risefall.yaml`, no
other flags needed) if it's ever re-enabled.

### Changing settings: always redeploy, never assume

```powershell
python -m tools.check_deploy --config config.notouch.yaml --log notouch_live.log --cap 850 --target 1000
.\tools\redeploy.ps1              # restart, and prove the new settings took
```

`check_deploy` reads intent from `--config` and **fact** from `--log`
(defaults to the Rise/Fall paths - pass the NOTOUCH ones explicitly, as
above), exiting non-zero on any disagreement. Run it after every config
change.

This is not ceremony. Three config changes — the 700/700 caps, the 3-tick
expiry, the ladder — were once committed, tested and reported as live while the
trading process kept the old 5-minute flat-stake settings for over an hour, and
every individual step reported success:

- Killing by `CommandLine` matches **nothing**: that field (and
  `ExecutablePath`) is empty for task-owned processes from a non-elevated
  session, so the matcher silently matched zero processes.
- `Start-ScheduledTask` then returned `0x80070420` *"already running"* because
  `MultipleInstances: IgnoreNew`, and nobody checked the code.
- Deleting the lock file **disarms** the single-instance guard rather than
  stopping anything, since the lock is only read at startup — which let a manual
  session trade in parallel with the task-owned bot.

`redeploy.ps1` stops by task handle, polls until the process tree is confirmed
gone **by pid**, clears the lock only then, checks the start result, and reads
the log back. Its own first run showed why the polling matters:
`Stop-ScheduledTask` reported `Ready` while two processes lingered ~14 seconds
longer.

Worth knowing: the supervisor relaunches its child every 30 minutes, so **child**
settings (expiry, staking) land on their own eventually, but **supervisor**
settings (the caps) need a real restart.

**[OPERATING_STATE.md](OPERATING_STATE.md) — what is running right now and why.**
Start there. `config.yaml` is gitignored, so that file is the only durable
record of the live settings, the measured contract costs behind them, the
running results, and the open items.

### The reports, and what they concluded

- **[TICK_ANALYSIS.md](TICK_ANALYSIS.md)** — 260 statistical tests on 864,000
  ticks across ten synthetic indices. Zero survive correction. The synthetic
  price feed is a pure random walk, resolved at a sensitivity **7x finer**
  than the smallest edge that could pay for itself. No rule computed from past
  prices can work on these symbols; that is arithmetic, not pessimism.
- **[REAL_MARKETS.md](REAL_MARKETS.md)** — real markets *do* have a genuine
  edge: day-ahead volatility persistence, silver r=+0.61, confirmed by two
  independent estimators. But the cheapest instrument that can express it
  charges **16.4%** and the edge is worth about **16.2%**. Deriv has priced it
  at approximately its own value.

**So the NOTOUCH bot is running as a cost-minimisation measure, not as an
expected money-maker.** Nothing in this repo has ever found a predictive
edge on these feeds - TICK_ANALYSIS.md ruled it out at a sensitivity 7x
finer than the smallest edge that could pay for itself. NOTOUCH's ~93.5%
win rate is a payout SHAPE bought at Deriv's own quoted margin (~2.28%,
`python main.py scan-touch`), not a forecast: EV is the same at every
barrier width, wide or narrow. It stakes flat, caps the day's loss, and
journals every trade (reconciled against Deriv's own `profit_table` at
startup - see `pricebot/reconcile.py` - so a trade missed by this
process's own watcher, or placed by a different machine on the same
account, still reaches the cap), so what it actually does stays checkable
rather than assumed.

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

## Symbol and contract costs (measured, not assumed)

`config.yaml` is gitignored, so the reasoning behind the symbol set lives here.
Re-measure with `python main.py scan-edge` whenever Deriv reprices.

Quoted margin per contract, measured live on 2026-07-30 from **complementary
pairs** — `3/payout_a + 3/payout_b − 1` **is** the margin, with no model of
anything, because exactly one of the pair pays:

| symbols | even_odd | rise_fall |
|---|---|---|
| R_10, R_25, R_50, R_75, 1HZ25V, 1HZ50V, 1HZ75V | **2.39%** | 3.99% |
| R_100, 1HZ10V, 1HZ100V | 3.99% | 3.99% |

Two things fall out of this that are easy to get wrong:

- **The cheap/expensive split does not follow the 1HZ vs R_ line.** The three
  expensive symbols are a mix of both families. "1HZ is noisier" is not a usable
  axis — [TICK_ANALYSIS.md](TICK_ANALYSIS.md) found both families to be pure
  random walks and statistically indistinguishable.
- **`rise_fall` costs 67% more than `even_odd`** on the same symbol. Contract
  family is a bigger cost lever than symbol choice.

**Do not price `over_under` this way.** `DIGITOVER:4` wins on 5–9 and
`DIGITUNDER:4` on 0–3, so digit **4 loses both** — they don't partition the
outcomes and the pair trick returns a nonsense *negative* margin. A negative
house margin is the tell that the pair is wrong, not that free money exists.
Only `DIGITEVEN`/`DIGITODD` partition exactly.

**Per-symbol PnL is not usable for choosing symbols.** The journal shows the
1HZ family at −0.283/trade against R_ at −1.728, which looks decisive and
isn't: with an 8-rung ladder those numbers record which symbol happened to be
in rotation when a rung-7/8 run landed, not anything about the symbol.

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
- `rotation` — cycles a fixed list of contracts (`contracts: ["DIGITOVER:0",
  "DIGITEVEN", "CALL"]`) on a tick cadence. A variety/comfort knob, not an
  edge — mixing blends the margins you pay, it can't change the sign.
- `adaptive_bias` — scores each candidate contract by raw recent win rate
  and bets whichever scored highest (`mode: momentum`) or lowest
  (`reversion`). **Known limitation, kept for the test it demonstrates:**
  raw win rate always favours the highest-probability contract, so on a
  mixed list it collapses into always betting the 90%-tier one — verified
  live (212 trades: 100% DIGITOVER/DIGITUNDER, 0% Even/Odd, 0% Rise/Fall).
  Use `quota_rotation` if you actually want a mix.
- `quota_rotation` — the fix for the above. Guarantees each contract
  *family* trades its configured share via weighted round-robin (e.g. 50%
  Over/Under, 25% Even/Odd, 25% Rise/Fall — deterministic, not
  probabilistic), then scores only *within* a family where that's
  meaningful (Rise/Fall's CALL/PUT just alternate, since they resolve on
  price rather than digits). Configure via `families: [[name, [contract
  specs], share], ...]`.

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

## Staking (`deriv_bot/staking.py`)

Set `staking.name` in `config.yaml`. **`flat` is the default and the only
one usable on a real account** — `main.py` refuses non-flat staking whenever
`DEMO_MODE=false`.

- `flat` — always the configured `stake`. No progression, ever.
- `martingale` — recovers a losing cycle's losses (`recovery_fraction`,
  default 1.0 = all of it) on the next win, capped at `max_stake_multiple`x
  the base stake so one run can't demand the whole session budget.
  `tools/martingale_sim.py` simulates unbounded double-after-loss staking:
  on 50%-tier contracts with a $10k bankroll and $10 base stake, **64% of
  careers bust**, median peak $13.8k, median final $4.9k. The peak is why
  people believe it works; the final is why it doesn't.
- `smart_recovery` — `martingale`, but while a losing cycle is open it
  swaps the strategy's contract choice for one of `recovery_contracts`
  (default the ~50%-tier ones) instead of whatever the strategy's rotation
  served up. Found by inspecting a real session: every stake over $73 had
  landed on DIGITOVER/DIGITUNDER purely by rotation luck, and that contract
  only pays 8.7% — recovering there costs ~11x the loss, vs ~1x on a
  50%-tier contract. Measured (20k sessions, +600/-1000, $35 base, 20x cap):
  blind rotation hits the target 46.3% of the time (mean −$253); routing
  recovery to the cheap contracts hits 53.6% (mean −$135). Same worst-case
  tail either way — this improves the odds and the average, not the sign of
  the expectation.

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
