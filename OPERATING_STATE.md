# What is running, and why

> **UPDATED 2026-08-20 (this machine, `deriv-digit-bot`).** Everything below the
> "SUPERSEDED" marker describes the NOTOUCH/digit era and is kept for its
> measurements and bug history. **It is no longer what runs.**

## Currently running: the S/R Rise/Fall bot, two demo accounts

Two Windows Scheduled Tasks, installed by `tools/install_sr_task.ps1`:

| task | account | direction | log | journal |
|---|---|---|---|---|
| `DerivSRBotAccount1` | `.env` (DOT93163621) | `call` only | `sr_bot.log` | `sr_trades.csv` |
| `DerivSRBotAccount2` | `.env.ac2` (DOT94081509) | `both` | `sr_bot_ac2.log` | `ac2_sr_trades.csv` |

Both run `run_sr_bot.py` on **R_50 only**, 55-second Rise/Fall, with:

    --group-system --recovery-mode breakeven --on-exhaust reset
    --group-targets gentle --max-daily-loss 5000 --target-profit 3000
    --poll 10 --cooldown 60 --rescan-minutes 5 --retire-after-losses 2
    --no-confirm --require-wick --adaptive-tolerance --max-per-line 50

AC1 vs AC2 is a deliberate **A/B test on direction** - do not make them
identical without discarding it. Over 207 settled trades: CALL 51.5% (n=130)
vs PUT 44.2% (n=77), a 7.4pp gap against a 7.2pp standard error (**z=1.03, not
significant**), both 95% intervals containing the 51.99% break-even. PUT's
-2,300 net was a stake-size artefact: -2,253 of it from six deep recovery
rungs. Resolving this needs ~700 trades per arm.

### The 6-group recovery ladder (`deriv_bot/group_ladder.py`)

Six groups traded one at a time, 1->2->...->6->1. Four fixed base stakes, then
computed recovery rungs, capped at 10 rungs per run.

**`--recovery-mode breakeven` is the important setting.** The original rule
sized recovery to clear the deficit AND deliver the group's whole profit target
in one win. Because targets dwarfed the small base stakes, rung 5 jumped 6-20x
off rung 4 and the Group 4-6 ladders cost **13k/24k/46k against a 10k bankroll
- they could never complete**. Break-even recovery (restore to zero only)
brings them to 2.9k/3.9k/4.9k. Live proof: Account 2 sat at Group 5 rung 8
needing a **2,907 stake**; under the new rule the same position stakes **4**.

`--group-targets gentle` (20/30/40/50/60/70) replaced 20/32/64/128/256/512.
P(a run wipes out) is roughly target/(target+bankroll), so escalating targets
escalated ruin ~25x from Group 1 to Group 6. Simulated over 1,500 sixty-day
careers (`python -m tools.ladder_lab`): gentle completes **100% of groups at
8.4% ruin** against the escalation's **71% at 10.7%** - strictly better.

`--on-exhaust reset` writes off a run that loses every rung and restarts the
group instead of stopping the bot forever. Median career: 13 days -> 60.

### Trade rate: maximise it while on DEMO

Bleed scales linearly with volume (~0.73 per trade), and that fact was briefly
used to justify throttling. **That was the wrong objective** - on demo the money
is not real and DATA is the only output. Measured rates before the throttle:
AC1 127/day, AC2 223/day; halving them pushed the CALL-vs-PUT answer from ~5
days to ~10. Reverted. Throttle only when real money is in play.

### Live results under this design

768 trades all-time across both accounts, **50.26% win rate, 95% CI
[46.72%, 53.80%]**. Break-even is 51.99%, a coin flip is 50% - **the interval
contains both**, so after 768 trades the S/R levels remain indistinguishable
from random. Return on turnover: -9.47% overall.

### Bugs found live during the 2026-08-19/20 session

Every one silent, none visible in the P&L:

| bug | symptom |
|---|---|
| `tools/reset_day.py` never wired into `run_sr_bot.py` | it wrote the `.day_reset` marker; this script never read it, so every daily-loss reset silently did nothing |
| `line_stats` keyed by NAME, reused across rescans | a rescan reuses S1..S6/R1..R6 for different prices, so fresh levels inherited dead levels' losing records and `--retire-after-losses` killed them on sight - observed retiring the best level (0.38% from spot) the same second it was created |
| rescan clock reset by UNTRADEABLE zones | price parked between S1 and R1, dipping into a resistance zone the bot could never trade (CALL-only / trend filter), resetting the timer forever. Both accounts idled over an hour. Fixed: only zones this bot can act on count |
| both accounts shared one `lines.json` | with `--rescan-minutes` on both, either could overwrite the set the other was mid-trade on. Now `--lines` per account |
| lowering targets silently voided the group structure | every group's carried `cumulative_profit` already exceeded its new gentle target, so each win merely rotated groups. Counters reset so live state matches what `ladder_lab` models |
| `--help` crashed | a bare `%` in `--direction` help text; argparse formats that string. Escaped to `%%` |
| TLS handshake failures killed startup | corporate TLS-inspecting proxy. `deriv_bot/api.py` now uses an OS-trust SSL context on BOTH REST and websocket paths - fixing only REST let it pass `list_accounts` then die on `connect` |

### Research tooling added

- **`tools/ladder_lab.py`** - Monte Carlo that drives `GroupLadder` (and any
  `Staker`) with real payouts, bankroll, daily cap and target stops. The old
  `tools/martingale_sim.py` hardcodes 2x doubling and cannot simulate the group
  ladder. Note: the comparison numbers in `deriv_bot/staking.py`'s docstrings
  were produced by code **not in this repo** and are not reproducible.
- **`tools/xcorr.py`** - tests whether Deriv's synthetic feeds are independent
  of *each other*. Every prior test here examined one symbol alone. If two feeds
  share entropy, one symbol's tick predicts another's - real prediction, which
  unlike staking survives the margin. **SETTLED 2026-08-21: the feeds are
  independent.** The first run (n~999) found nothing but could only see
  |r|>0.089 while |r|>0.063 is already profitable - a real blind spot. After
  accumulating to n=14,943-29,890 the blind spot closed (detectable |r|=0.0281
  vs profitable 0.0626) and the answer held: largest |z| 2.21 against a 3.434
  Bonferroni threshold across 28 pairs x 3 lags. A properly powered negative. `tests/test_xcorr.py` plants a known r~0.15 and requires the estimator to
  find it, so a negative result means something.

### Known-unfixed

- **Daily cap overshoots.** A 1,300 cap stopped at **-2,238** (72% past) because
  a stake is never compared to remaining headroom before being placed. More
  dangerous now at `--max-daily-loss 5000`.
- **`GroupLadder` has no `budget_left` interaction** - it can name a stake
  larger than the day's remaining allowance and nothing refuses it.
  `deriv_bot/staking.py::fit_or_refuse` is the primitive to route through.
- **Cap resets at UTC midnight mid-run**, so a run spanning midnight gets a
  fresh allowance.
- **Two hardcoded API tokens** in `check_accounts.py` and `check_profit_table.py`
  are committed to a PUBLIC GitHub repo and are live. Revoke and move to env.
- **Scheduled tasks register Interactive, not S4U** (needs elevated PowerShell),
  so they do not survive logoff.

---

# SUPERSEDED - the NOTOUCH/digit era below


Snapshot of the live configuration and the measurements behind it.
`config.yaml` is gitignored (the digit bot's per-machine config);
`config.notouch.yaml` is committed since it holds no secrets, but the
reasoning behind its numbers still lives here, not in code comments alone.

Re-measure digit costs with `python main.py scan-edge`, Touch/No Touch costs
with `python main.py scan-touch`. Check what is actually deployed with
`python -m tools.check_deploy --config config.notouch.yaml --log
notouch_live.log --cap 1000 --target 1200` — the config is intent, the log is
fact, and they have disagreed before (three times now: once within a single
machine's history, once between this file and the actual scheduled tasks,
and once when a bug fix silently changed what the live bot was actually
trading - see "Bugs found by watching" below).

---

## The one bot

`DerivNoTouchSupervisor` is the only registered trading task. The digit bot's
`DerivScanTradeSupervisor` task was fully removed (not just stopped) on
2026-07-31 in favour of this one, enforced by a pid lock in
`tools/lockfile.py` named per-config so a future re-enabled Rise/Fall bot
cannot silently share this bot's lock (it used to be hardcoded to
`risefall_supervisor` regardless of which config was passed).

| setting | value | why |
|---|---|---|
| instrument | Touch/No Touch, `NOTOUCH` | cheapest measured shape available, see below |
| symbols | `R_50, R_75, R_100, 1HZ50V, 1HZ75V` | the 5 of 10 synthetics that reach a ~93-96% win rate at THIS bot's 5-minute duration - see below |
| barrier | per-symbol: `R_50`/`1HZ50V` 0.30%, `R_75`/`1HZ75V` 0.40%, `R_100` 0.50% | each chosen for a comparable win-rate shape, not because it is cheaper - margin is ~flat across barriers and symbols |
| duration | 5 minutes, every symbol | inside the 5m-2h band measured cheapest for Touch/No Touch |
| strategy | `fixed_notouch` (with `barrier_by_symbol`) | no prediction at all - buys the same barrier shape every cycle, per symbol, `deriv_bot.strategy.LowEdgeStrategy`'s Touch equivalent |
| staking | flat 3.00, per symbol | not a ladder - a NOTOUCH loss is already the full stake, nothing to recover in rungs |
| daily cap | −1000 loss / +1200 target | raised from 850/1000 for 5x the trading volume - a deliberately modest increase, not proportional, and still not properly re-derived for this bot's risk shape - see Open |

Verify what's actually deployed against this table:
`python -m tools.check_deploy --config config.notouch.yaml --log notouch_live.log --cap 1000 --target 1200`.

### Previously: the digit bot (stopped 2026-07-31)

Ran `even, rise` categories on `R_10, R_25, R_50, R_75` at a 9-rung recovery
ladder. Kept here because "The results so far" and "Bugs found by watching"
below are its history, and because the settings are the reference point NOTOUCH
was chosen to be cheaper than.

| setting | value | why |
|---|---|---|
| symbols | `R_10, R_25, R_50, R_75` | cheapest quoted tier, 2.39% against 3.99% for R_100/1HZ10V/1HZ100V |
| categories | `even, rise` | one-sided: never buys DIGITODD or PUT |
| selection | `signal` | scores both sides every cycle instead of alternating |
| abstain_action | `wait` | sits out when nothing clears `min_z` |
| min_z | 2.0 | ~4.5% of cycles fire over two legs |
| stake | 3.00 flat base | |
| ladder | 9 rungs to 265.05, 775.00 total | 8th rung repeated rather than a true 552.31 ninth |
| daily cap | −850 loss / +1000 target | 850 clears the 775 ladder AND the 10%-of-balance guard |

---

## Measured costs

Model-free, from complementary quote pairs — exactly one of the pair pays, so
`stake/payout_a + stake/payout_b − 1` **is** the margin with no model at all.
`python main.py scan-touch` (`deriv_bot/touch_edge.py`) runs the same trick
for Touch/No Touch across barriers/durations, since a barrier's true win
probability isn't a known constant the way a digit's is.

| contract | margin |
|---|---|
| `DIGITEVEN` / `DIGITODD` | **2.33–2.39%** |
| `CALL` / `PUT` (Rise/Fall) | 3.83–3.99% |
| Touch/No Touch, **5m–2h** | **2.31–2.55%**, essentially flat across every barrier tried |
| Touch/No Touch, 5–10 ticks | 6.52% |

**Do not price `over_under` this way.** `DIGITOVER:4` wins on 5–9 and
`DIGITUNDER:4` on 0–3, so digit **4 loses both** — they don't partition, and the
pair trick returns a nonsense *negative* margin. A negative house margin is the
tell that the pair is wrong, not that free money exists.

The cheap/expensive symbol split does **not** follow the 1HZ vs R_ line — the
three expensive ones are a mix of both families.

### Touch/No Touch, evaluated (2026-07-31, corrected same day)

**The first version of this scan was wrong.** It reported R_75 as
"barrier-insensitive" (~3.4% win rate at every barrier from 0.1% to 30%) and
R_10/R_25 as unable to reach a useful win rate at all. Both were artifacts of
two bugs, not real properties of those symbols:

1. **Barriers were never scaled by spot.** Deriv's relative barrier string is
   an ABSOLUTE price offset, not a fraction, whatever "relative" suggests -
   the same nominal "0.30" barrier was a real 0.27% width on R_50 (spot
   ~112) and an unmeasurable 0.00004% width on 1HZ25V (spot ~780,000). Fixed
   in `pricebot/instruments.py::build_proposal`, which now requires the
   current spot and multiplies by it.
2. **Barrier decimal precision was hardcoded to 4 places.** Deriv actually
   caps this PER SYMBOL - confirmed live, R_10 accepts at most 3 places,
   1HZ10V at most 2. A universal `.4f` format silently rejected every quote
   for any symbol narrower than 4, which is why the corrected (spot-scaled)
   first re-run returned ZERO quotes for R_10, R_25, R_100 and the entire
   1HZ family. Fixed by reading each symbol's own `pip_size` (already in the
   `ticks_history` response) instead of assuming 4.

With both fixed, a full 10-symbol sweep (all of R_10/25/50/75/100 and
1HZ10V/25V/50V/75V/100V) shows every symbol behaves sensibly - margin holds
~2.3-2.55% everywhere, and win rate climbs smoothly with barrier width on
every one of them, R_75 included:

| symbol | 5-minute shape reaching ~93-96% win rate | margin | usable at 5m? |
|---|---|---|---|
| **R_50** | 0.30% → 96.5% | 2.32% | yes — deployed |
| **R_75** | 0.40% → 93.5% | 2.26% | yes — deployed |
| **R_100** | 0.50% → 91.7% (only 2 points tested, not finely tuned) | 2.52% | yes — deployed |
| **1HZ50V** | 0.30% → 96.5% | 2.47% | yes — deployed |
| **1HZ75V** | 0.40% → 93.5% | 2.50% | yes — deployed |
| R_10 | needs 15m+ for a comparable shape, not usable at 5m | ~2.3–2.5% | no, different duration needed |
| R_25 | needs 15m+ for a comparable shape, not usable at 5m | ~2.3–2.5% | no, different duration needed |
| 1HZ10V | needs 15m+ for a comparable shape, not usable at 5m | ~2.3–2.5% | no, different duration needed |
| 1HZ25V | needs 15m+ for a comparable shape, not usable at 5m | ~2.3–2.5% | no, different duration needed |
| 1HZ100V | needs 60m+ for a comparable shape, not usable at 5m | ~2.3–2.5% | no, different duration needed |

Confirms the barrier-shape claim from a live table: margin holds ~2.3-2.55%
regardless of barrier, so a wider barrier buys a higher win rate at the same
expected cost — a payout shape, not an edge. R_10/R_25/1HZ10V/1HZ25V/1HZ100V
simply carry less volatility per unit time than R_50/R_75 at a 5-minute
window (they ARE the lower-numbered Volatility indices), so reaching a
comparable win-rate on them needs a longer duration, not a different
barrier - left out of this deployment rather than mixing durations across
symbols in one config, which the current schema doesn't support cleanly.

---

## The results so far

**Digit bot** (stopped 2026-07-31, `trade_journal.csv`):

| date | net |
|---|---|
| 2026-07-24 | −82.64 |
| 2026-07-25 | −4,395.86 |
| 2026-07-26 | −794.26 |
| 2026-07-27 | −360.17 |
| **2026-07-28** | **+1,154.28** |
| 2026-07-29 | −1,000.00 |
| 2026-07-30 | −633.68 |
| 2026-07-31 | −777.19 |
| **total** | **−6,889.52** over 5,504 rows |

One winning day in eight. The 25th alone (−4,395.86) is nearly 4× the size of
the best day. Per trade the run has been roughly −1.22 with an SD of 39.20,
which is a statistically detectable loss rate rather than variance.

**NOTOUCH bot** (running, `notouch_journal.csv`, reconciled against Deriv's
`profit_table` — see the journal-gap bug below): on R_50 alone, several
trades settled clean at the expected ~93-96% win-rate shape (small wins,
no losses yet) before this was expanded to 5 symbols on 2026-07-31. Far too
few trades to say anything about variance - the only claim being made is
that the shape matches what was measured, not that it wins over time.

**This is demo money throughout.** No real-money session has ever run.

---

## Why patience is the only lever that works

`abstain_action: wait` is the largest single improvement available, and it is
worth being exact about what it does:

| | trades/hour | cost/hour |
|---|---|---|
| trade every cycle | 80 | $5.59 |
| **wait for `min_z`** | **3.6** | **$0.25** |

**22× fewer trades, 22× less bleed.** And it makes those trades no likelier to
win — a z≥2 reading on a structureless feed is a false positive by
construction, about 1 draw in 22. Cost scales with trade count; the odds do
not move. **Patience cuts the cost, not the odds.**

That is the honest summary of every strategy result in this repo:

- [TICK_ANALYSIS.md](TICK_ANALYSIS.md) — 260 tests on 864,000 synthetic ticks,
  zero survive correction, resolved 7× finer than any tradeable edge
- [REAL_MARKETS.md](REAL_MARKETS.md) — real volatility clustering exists, and
  Deriv prices the only instrument that expresses it at approximately its own
  value
- [BYBIT.md](BYBIT.md) — better venue, but no directional edge survives fees,
  and my first positive reading there was a small-sample error I had to retract

---

## Bugs found by watching, not by testing

Every one of these was live and silent. Two were spotted from the Deriv
Positions screen before any log check caught them.

| bug | symptom |
|---|---|
| ladder rung truncated to the day's remaining budget | staked 14.45 after 61.13 — not a rung at all |
| daily cap never fired | PnL summed to −899.9999999999989 against 900; float error |
| ladder reset on every restart | staked 3.00 straight after a 3.00 loss |
| deep study ignored `categories` | bought DIGITODD when config said `even, rise` |
| token dead for a week | bot kept trading on a websocket authenticated before it expired |
| TOUCH barrier had 5 decimal places | every order rejected: "Barrier can only be up to 4 decimal places" — found live, first time this path ever ran |
| TOUCH routed through Multiplier leverage lookup | could silently skip every Touch trade if the (irrelevant) multiplier range came back empty |
| **journal gap: 4 of 5 real NOTOUCH trades never journaled** | two machines shared one demo account; each local journal only recorded what IT watched settle. Fixed by reconciling against Deriv's own `profit_table` at startup (`pricebot/reconcile.py`) and by `Session.run` waiting for in-flight positions before exiting |
| reconciliation itself pulled in the digit bot's trades | `profit_table` is account-wide, not per-bot — fixed by scoping to the bot's own `contract_type`s (`CONTRACT_TYPES_FOR_INSTRUMENT`) before it was ever deployed |
| this file claimed the Rise/Fall bot was running | the digit bot actually was — `README.md` and this file disagreed with each other and with `Get-ScheduledTask`; only the live task list was ground truth |
| **TOUCH barrier was never scaled by spot** | a "0.30 barrier" was a real 0.27% width on R_50 and 0.00004% on 1HZ25V — the same nominal offset meant wildly different things per symbol. Made R_75 LOOK barrier-insensitive and R_10/R_25/1HZ*/R_100 look unusable, when the real issue was every barrier tested on them was effectively zero width. Fixed live, but not before the fix ITSELF broke the running bot for ~4 minutes (see next row) |
| the spot-scaling fix broke the live bot on its own next restart | `config.notouch.yaml` still said `barrier_pct: 0.30`, which after the fix meant a genuine 30% barrier — Deriv wouldn't even quote it ("This contract offers no return"), so the bot sat failing every cycle until the config was corrected to the real equivalent (0.27%) |
| TOUCH barrier decimal precision was hardcoded to 4, not per-symbol | Deriv rejects R_10 past 3 places and 1HZ10V past 2 ("...more than N decimal places") — a universal `.4f` silently zeroed out every symbol narrower than 4 from the corrected scan, which is why the FIRST corrected sweep returned zero quotes for 6 of 10 symbols |

Three of the digit-bot bugs were **pinned in place by tests asserting the
broken behaviour** — which is why fixing them looked like breaking the code.
A test that guards a decision rather than an invariant is worse than no test.

---

## Open

- **~~Touch/No Touch, evaluated properly~~ — done, deployed 2026-07-31,
  corrected same day.** Measured across barriers/durations
  (`python main.py scan-touch`), confirmed EV is the same at every barrier,
  and deployed on 5 symbols. See "Touch/No Touch, evaluated" above -
  including the two real bugs (barrier not scaled by spot, decimal
  precision hardcoded instead of per-symbol) that made the first version of
  this measurement wrong.
- **~~R_75's Touch quotes are barrier-insensitive~~ — resolved, was the
  barrier-scaling bug, not R_75.** Once barriers were properly scaled by
  spot, R_75 behaves exactly like every other symbol - smooth, sensible
  win-rate progression with barrier width. Now deployed at 0.40%/5m.
- **R_10, R_25, 1HZ10V, 1HZ25V, 1HZ100V need a longer duration (15m-60m+)
  to reach a comparable win-rate shape to the 5 deployed symbols.** Not
  added to this bot because the current strategy/config schema has one
  `duration` for every symbol - adding them means either accepting a lower
  win-rate at 5m, or extending `FixedNoTouch`/config to support a
  per-symbol duration too (the same kind of change `barrier_by_symbol` just
  added, one more axis).
- **The daily cap (−1000/+1200) was bumped up from the digit bot's
  inherited 850/1000 to account for 5x the trading volume, but this is
  still a rough scaling, not a proper derivation.** A NOTOUCH loss is
  always exactly the stake (3.00) per symbol, so the cap in TRADES-until-
  stop (now across 5 concurrent symbols) is what actually matters, not a
  ladder-completion argument that never applied to this bot in the first
  place.
- **Running this bot from more than one machine on the same account is a
  real, now-confirmed failure mode**, not a hypothetical one — it is how 4 of
  5 real trades were nearly lost from the risk cap's view (see the journal-gap
  bug above). The account, not the machine, is the unit that needs one
  supervisor; nothing currently enforces that across machines, only within
  one (`tools/lockfile.py`'s pid file is local to the machine it runs on). If
  this account is ever run from two machines again, treat it as two
  supervisors on one account, which the existing lock design was explicitly
  built to prevent and cannot, across a network.
- **Periodic auth check.** The dead token hid behind a stale session for a week
  and nothing alerted. `preflight` only catches it at startup, which is exactly
  when it is too late to matter.
- **Multi-user setup.** Three hardcoded `C:\Users\ayori\derivmasterpiece` paths
  (`tools/_elevated_install.ps1:4`, `_elevated_consolidate.ps1:18`,
  `install_risefall_task.ps1:10`) and a `config.example.yaml` with no
  `scan_trade` or `staking` section, so a new user gets defaults rather than
  this setup. **Nobody should ever share a Deriv PAT** — one token per person,
  in their own gitignored `.env`.
