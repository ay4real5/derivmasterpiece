# What is running, and why

Snapshot of the live configuration and the measurements behind it.
`config.yaml` is gitignored (the digit bot's per-machine config);
`config.notouch.yaml` is committed since it holds no secrets, but the
reasoning behind its numbers still lives here, not in code comments alone.

Re-measure digit costs with `python main.py scan-edge`, Touch/No Touch costs
with `python main.py scan-touch`. Check what is actually deployed with
`python -m tools.check_deploy --config config.notouch.yaml --log
notouch_live.log --cap 850 --target 1000` — the config is intent, the log is
fact, and they have disagreed before (twice now: once within a single
machine's history, once between this file and the actual scheduled tasks).

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
| symbol | `R_50` only | the only one of R_10/25/50/75 that reached a useful win-rate at a 5m/30% shape - see Open |
| barrier | 30% of spot | chosen for win-rate shape, not cost - margin is ~flat across barriers |
| duration | 5 minutes | inside the 5m-2h band measured cheapest for Touch/No Touch |
| strategy | `fixed_notouch` | no prediction at all - buys the same barrier every cycle, `deriv_bot.strategy.LowEdgeStrategy`'s Touch equivalent |
| staking | flat 3.00 | not a ladder - a NOTOUCH loss is already the full stake, nothing to recover in rungs |
| daily cap | −850 loss / +1000 target | same scale as the digit bot's cap, not re-derived for this bot's different risk shape yet - see Open |

Verify what's actually deployed against this table:
`python -m tools.check_deploy --config config.notouch.yaml --log notouch_live.log --cap 850 --target 1000`.

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

### Touch/No Touch, evaluated (2026-07-31)

Live `scan-touch` results on R_10/25/50/75, 5m–2h durations, 0.1%–30% barriers:

| symbol | 5m/30% NOTOUCH win rate | margin | usable? |
|---|---|---|---|
| **R_50** | **93.5%** | 2.28% | yes — deployed |
| R_10 | 20.5% (needs a much wider barrier/longer duration for a comparable win rate) | ~2.3–2.5% | not at this shape |
| R_25 | 16.3% (same issue — less volatility per unit time by construction) | ~2.3–2.5% | not at this shape |
| R_75 | **~3.4% at every barrier from 0.1% to 30%, every duration tried** | ~2.4% | **no — anomaly, see Open** |

Confirms the barrier-shape claim from a live table: margin holds ~2.3-2.55%
regardless of barrier, so a wider barrier buys a higher win rate at the same
expected cost — a payout shape, not an edge. R_10/R_25 carry less volatility
per unit time than R_50 by construction (they ARE Volatility 10/25 vs 50), so
reaching a comparable win-rate on them needs a wider barrier or a longer
duration than was tried here, not a different conclusion.

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
`profit_table` — see the journal-gap bug below): 5 trades, 5 wins, +1.00 total
as of 2026-07-31. Too few to say anything about variance yet; the shape
(frequent +0.20 wins, one rare −3.00 loss) is exactly what a 93.5%-win-rate
NOTOUCH predicts, which is the only claim being made — not that it wins.

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

Three of the digit-bot bugs were **pinned in place by tests asserting the
broken behaviour** — which is why fixing them looked like breaking the code.
A test that guards a decision rather than an invariant is worse than no test.

---

## Open

- **~~Touch/No Touch, evaluated properly~~ — done, deployed 2026-07-31.**
  Measured across barriers/durations (`python main.py scan-touch`), confirmed
  EV is the same at every barrier, and deployed as the running bot. See
  "Touch/No Touch, evaluated" above.
- **R_75's Touch quotes are barrier-insensitive — real anomaly, not
  understood.** Every barrier from 0.1% to 30%, every duration from 5m to
  30m, came back at essentially the same ~3.4% NOTOUCH win rate on R_75. That
  should be impossible if the pricer is barrier-sensitive the way it
  obviously is on R_10/25/50 - worth a proper investigation (compare
  `contracts_for` output, check for a stale/cached quote, try other
  durations) before trusting R_75 for anything Touch-related.
- **The daily cap (−850/+1000) was carried over from the digit bot's ladder
  math, not re-derived for NOTOUCH's flat-stake, single-large-loss shape.**
  Worth sizing properly: a NOTOUCH loss is always exactly the stake (3.00),
  so the cap in TRADES-until-stop is what matters, not a ladder-completion
  argument that no longer applies.
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
