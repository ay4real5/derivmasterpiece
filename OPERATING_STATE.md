# What is running, and why

Snapshot of the live configuration and the measurements behind it.
`config.yaml` is gitignored, so without this file every reason for the current
settings lives only on one machine's disk.

Re-measure costs with `python main.py scan-edge`. Check what is actually
deployed with `python -m tools.check_deploy` — the config is intent, the log is
fact, and they have disagreed before.

---

## The one bot

Everything else is stopped. `DerivScanTradeSupervisor` is the only registered
trading task; the Rise/Fall task was unregistered and the digit bot is the
single instance, enforced by a pid lock in `tools/lockfile.py`.

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

| contract | margin |
|---|---|
| `DIGITEVEN` / `DIGITODD` | **2.33–2.39%** |
| `CALL` / `PUT` (Rise/Fall) | 3.83–3.99% |
| Touch/No Touch, **5m–2h** | **2.31–2.52%** |
| Touch/No Touch, 5–10 ticks | 6.52% |

**Do not price `over_under` this way.** `DIGITOVER:4` wins on 5–9 and
`DIGITUNDER:4` on 0–3, so digit **4 loses both** — they don't partition, and the
pair trick returns a nonsense *negative* margin. A negative house margin is the
tell that the pair is wrong, not that free money exists.

The cheap/expensive symbol split does **not** follow the 1HZ vs R_ line — the
three expensive ones are a mix of both families.

---

## The results so far

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

Three of these were **pinned in place by tests asserting the broken
behaviour** — which is why fixing them looked like breaking the code. A test
that guards a decision rather than an invariant is worse than no test.

---

## Open

- **Touch/No Touch, evaluated properly.** At 5m–2h it is as cheap as digits and
  the barrier *sets the win probability* — measured 3% to 90% on R_25; a 2h
  NOTOUCH at +20 wins 94% of the time. EV is identical at every barrier, so it
  buys a payout shape rather than an edge, but it is the only untested lever
  left on this platform.
- **Periodic auth check.** The dead token hid behind a stale session for a week
  and nothing alerted. `preflight` only catches it at startup, which is exactly
  when it is too late to matter.
- **Multi-user setup.** Three hardcoded `C:\Users\ayori\derivmasterpiece` paths
  (`tools/_elevated_install.ps1:4`, `_elevated_consolidate.ps1:18`,
  `install_risefall_task.ps1:10`) and a `config.example.yaml` with no
  `scan_trade` or `staking` section, so a new user gets defaults rather than
  this setup. **Nobody should ever share a Deriv PAT** — one token per person,
  in their own gitignored `.env`.
