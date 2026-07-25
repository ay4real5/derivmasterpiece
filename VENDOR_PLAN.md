# The vendor seat: earning from Deriv instead of betting into it

## Why this document exists

Every measurement in this repo points the same way: digit and Rise/Fall
contracts carry a house margin of **2.17% to 16.67%** (run `scan-edge` for
today's numbers), the underlying digits are provably uniform and independent
(`tests/` + the studies in the session log), and 309 demo trades landed
almost exactly on the predicted bleed. There is no strategy seat at that
table with positive expectancy.

There *is* a seat with positive expectancy, and Deriv documents it publicly:
**app developers earn markup on every trade routed through their app_id, and
affiliates earn commission on referred traders.** The payer is Deriv, not the
trader's luck. This is the only position on the platform where money flows
toward you regardless of whether any given trade wins or loses.

## What already exists in your favour

- A registered app: `masterpiece_bot` (`app_id: 33ULSRYkmDK8Y515CmE1l`),
  currently at **0% markup** — the tap is installed but closed.
- A working, tested bot: current-API auth, settlement tracking, a risk kill
  switch, journalling, and an edge scanner. 37 passing tests and CI.
- Something almost nobody else selling Deriv bots has: **honest, published
  measurements**, including the ones that say the game can't be beaten.

## The build

### 1. Ship the open-source bot (credibility)
- `git push origin main` — the repo is committed and CI-ready.
- Rewrite `README.md` as a landing page: what it does, the honest findings,
  screenshots of `scan-edge` and `analyze` output.
- The honesty *is* the marketing. The Deriv bot market is saturated with
  "95% win rate" scams; a tool that publishes its own losing sessions and
  explains the house edge stands out precisely because it is credible.

### 2. Turn on markup (the revenue mechanism)
- Set markup in the Deriv dashboard for `masterpiece_bot` (max 3%).
- **Important honesty constraint:** markup is charged to the user's payout.
  Disclose it plainly in the README and in-app — "this app adds X% markup,
  which is how it is funded". A tool whose selling point is honesty cannot
  hide its own fee.
- Recommend a *low* markup (0.5–1%). It compounds across users' trade counts
  and keeps the tool genuinely cheaper than competitors.
- Anyone running the bot with `app_id` set to yours generates markup on
  every contract they buy.

### 3. Distribution (where the users are)
- **Deriv App Builder** (`developers.deriv.com/dashboard/builder`): deploy a
  branded no-code Digits/Rise-Fall app bound to your `app_id`. This is the
  low-effort storefront — the same markup applies.
- Deriv community Slack, forums, r/algotrading, YouTube/GitHub: publish the
  research (uniformity study, edge table, martingale ruin simulation). Each
  is a genuinely useful artifact that ends with "the tool that produced this
  is here".

### 4. Adjacent, higher-value products
Once there is an audience, the software that is *actually* worth paying for
is the analysis layer, not the betting layer:
- the live edge scanner (which contract is cheapest right now),
- the journal + theory-vs-actual analyzer,
- the martingale ruin simulator (`tools/martingale_sim.py`).

## Honest expectations

- Markup is a **volume** business: 1% of a $10 stake is $0.10. Meaningful
  income needs many users trading often, which takes months of audience
  building. This is a real business, not a fast one.
- There is an ethical edge to walk: the revenue comes from people trading a
  negative-EV product. The defensible version is a tool that is *honest
  about the odds, refuses martingale, and enforces risk limits* — helping
  users lose less slowly than the alternatives they would otherwise use. The
  indefensible version is another "guaranteed profit" bot. This project has
  already chosen its side; keep it that way.
- Deriv's affiliate terms, markup caps, and app review requirements change.
  Verify current terms at `deriv.com/partners` before counting on numbers.

## First three actions

1. `git push origin main` (publish the repo).
2. Rewrite `README.md` as a landing page with the real `scan-edge` output.
3. Set markup on `masterpiece_bot` to 1% and document it in the README.
