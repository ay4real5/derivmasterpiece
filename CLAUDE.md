# CLAUDE.md — deriv-digit-bot

> Auto-loaded every session. Read this first, then `OPERATING_STATE.md`.
> **`README.md` is stale** — it still claims the NOTOUCH bot is running. It is
> not. `OPERATING_STATE.md`'s top section is the current truth.

## What actually runs right now

**The S/R Rise/Fall bot, `run_sr_bot.py`, on two demo accounts**, as two Windows
Scheduled Tasks installed by `tools/install_sr_task.ps1`:

| task | env file | direction | log | journal | state |
|---|---|---|---|---|---|
| `DerivSRBotAccount1` | `.env` | `call` only | `sr_bot.log` | `sr_trades.csv` | `sr_bot_state.json` |
| `DerivSRBotAccount2` | `.env.ac2` | `both` | `sr_bot_ac2.log` | `ac2_sr_trades.csv` | `ac2_sr_bot_state.json` |

Both trade **R_50 only**, 55-second Rise/Fall, using the 6-group recovery ladder
in `deriv_bot/group_ladder.py`. The NOTOUCH and digit bots are **stopped** —
their tasks are not registered.

## Setting this repo up on a new machine

Nothing below comes from git; all of it is gitignored and must be created locally.

1. **`python -m venv .venv && .venv/Scripts/pip install -r requirements.txt`**
   Always invoke as `.venv/Scripts/python.exe` — the system Python does not have
   `websockets` or `pytest` installed and will fail confusingly.
2. **`.env`** — copy `.env.example`, add account 1's Deriv PAT, keep
   `DEMO_MODE=true`.
3. **`.env.ac2`** — same shape, account 2's token. Only needed for
   `DerivSRBotAccount2`.
4. **`config.yaml`** — copy `config.example.yaml`. **`run_sr_bot.py` reads
   `app_id` from it and crashes without it.** The registered app ids in use are
   `33ULSRYkmDK8Y515CmE1l` (account 1) and `343GsiWjpyIskHP1nbTzi` (account 2,
   passed via `--app-id`). An app id is a public identifier, not a secret.
5. **Install the tasks:** `.\tools\install_sr_task.ps1 -Start`. Run it from an
   **elevated** PowerShell if you want S4U registration (survives logoff);
   otherwise it falls back to an Interactive principal and dies at logoff.

## ⛔ Do not do these without asking

- **Do not make Account 1 and Account 2 identical.** AC1 `call` vs AC2 `both` is
  a deliberate A/B test and the only controlled comparison available. Measured
  over 207 settled trades: CALL 51.5% (n=130) vs PUT 44.2% (n=77) — a 7.4pp gap
  against a 7.2pp standard error, **z=1.03, not significant**, both 95%
  intervals containing the 51.99% break-even. It needs ~700 trades per arm to
  resolve. PUT's −2,300 net is a stake-size artefact: −2,253 of it came from six
  deep recovery rungs.
- **Do not run these bots against the same Deriv accounts from two machines.**
  `tools/lockfile.py` is per-machine and cannot see across a network.
  `OPERATING_STATE.md` records this exact failure: two machines on one account
  meant each local journal only saw its own trades, so the daily-loss cap lost
  track of most of them.
- **Do not switch `--recovery-mode` back to `target`** without understanding
  what it does. That rule sized recovery to clear the deficit *and* deliver the
  group's whole profit target in one win, which made the Group 4–6 ladders cost
  13k/24k/46k against a 10k bankroll — they could never complete. Account 2 sat
  at Group 5 rung 8 needing a **£2,907 stake**; under `breakeven` the same
  position stakes **£4**.
- **Do not throttle trade rate while on demo.** Bleed scales with volume, but on
  demo the money is not real and *data* is the only output. Throttling once
  halved the collection rate and pushed the CALL-vs-PUT answer from ~5 days to
  ~10. Throttle only when real money is involved.

## The honest state of the strategy

**768 trades, 50.26% win rate, 95% CI [46.72%, 53.80%].** Break-even is 51.99%
and a coin flip is 50% — **the interval contains both**. After 768 trades the
S/R levels are still indistinguishable from random. Return on turnover −9.47%.

This matches `TICK_ANALYSIS.md` (260 tests on 864,000 ticks, zero survivors).
The recovery ladder changes *how* losses arrive, never *whether* — that is
arithmetic, not pessimism. Recent positive sessions have run ahead of what the
margin predicts, which is favourable variance, not the strategy working.

If asked to improve profitability, the only two real levers are **margin**
(Rise/Fall 3.83–3.99% vs Touch/NoTouch 2.26–2.55% at 5m–2h) and **trade count**.
Neither creates an edge. Deriv synthetics are the house's own RNG priced by a
single dealer — `python -m tools.xcorr` and the complementary-pair trick in
`deriv_bot/touch_edge.py` both confirm no arbitrage exists (every pair sums to
102.4–104.0%, never under 100%). Real edges, if wanted, need a market with more
than one price-setter; the same API already reaches 25 forex, 11 index, 4
commodity and 2 crypto symbols.

## Key commands

    .venv/Scripts/python.exe -m pytest tests/ -q     # 803 pass, 11 pre-existing failures
    .venv/Scripts/python.exe -m tools.ladder_lab      # simulate ladder designs
    .venv/Scripts/python.exe -m tools.xcorr collect   # accumulate ticks
    .venv/Scripts/python.exe -m tools.xcorr test      # feed-independence test
    .venv/Scripts/python.exe -m tools.reset_day --journal ac2_sr_trades.csv
    .\tools\install_sr_task.ps1 -Start                # (re)install both tasks
    .\tools\install_sr_task.ps1 -Uninstall

**The 11 test failures pre-date this work** — stale assertions on `config.yaml`
keys (`staking`, `scan_trade`) the current config no longer has. Not a
regression; do not "fix" them by editing `config.yaml`.

## Known-unfixed (do not rediscover these the hard way)

- **The daily cap overshoots.** A 1,300 cap stopped at **−2,238** (72% past)
  because a stake is never compared to the remaining headroom before it is
  placed. Now more dangerous at `--max-daily-loss 5000`.
- **`GroupLadder` has no `budget_left` interaction** — it can name a stake
  bigger than the day's remaining allowance and nothing refuses it.
  `deriv_bot/staking.py::fit_or_refuse` is the primitive to route through.
- **The cap resets at UTC midnight mid-run**, handing an in-progress ladder a
  fresh allowance.
- **Two hardcoded live API tokens** sit in `check_accounts.py` and
  `check_profit_table.py`, committed to a **public** GitHub repo. Revoke them and
  move to env.
- **A corporate TLS-inspecting proxy** on the original machine intermittently
  broke startup. `deriv_bot/api.py` uses an OS-trust SSL context on *both* the
  REST and websocket paths — fixing only REST let startup pass `list_accounts`
  and then die on `connect`.

## Conventions

- Live repo: `github.com/ay4real5/derivmasterpiece` (branch `master`).
- Runtime artefacts — journals, state, logs, locks, `lines.json` rewrites,
  `xcorr_ticks.json` — are gitignored. `lines.json` is tracked but rewritten
  every rescan, so it will always look modified.
- Everything here is **demo money**. No real-money session has ever run.
