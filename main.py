"""CLI entrypoint: `python main.py backtest [--compare]`, `python main.py
scan-edge`, or `python main.py live [--dry-run]`."""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import time
from typing import Any

import yaml
from dotenv import load_dotenv

from deriv_bot.analysis import analyze_journal
from deriv_bot.api import DerivAPI
from deriv_bot.backtester import (
    approx_net_win, backtest_over_prices, fetch_ticks, last_digit, run_backtest,
)
from deriv_bot.edge import scan_edge
from deriv_bot.journal import TradeJournal
from deriv_bot.ladder_risk import max_base_for_risk, summarise as ladder_summary
from deriv_bot.multi_scan import (
    CATEGORY_LEGS, DEFAULT_CANDIDATES, DEFAULT_SYMBOLS, RoundRobin, parse_candidate_specs, scan_best,
)
from deriv_bot.preflight import account_is_demo, check_staking, run_checks
from deriv_bot.profiles import activate, list_profiles, load_profile, targets_real_money
from deriv_bot.reporting import (
    by_selector, load_settled, pooled_matched_gap, stake_matched,
)
from deriv_bot.risk import RiskLimits, RiskManager
from deriv_bot.selection import pick as select_pick
from deriv_bot.selection import summarise as select_summary
from deriv_bot.staking import build_staker
from deriv_bot.study import choose as study_choose
from deriv_bot.study import digits_from_ticks, score_legs, summarise
from deriv_bot.strategy import STRATEGIES, Strategy, build_strategy

MIN_STAKE = 0.35  # Deriv's minimum contract stake


def confirm_real_money(config: dict[str, Any], demo_mode: bool, dry_run: bool) -> None:
    """Gate real-money trading behind two independent switches.

    The original version called `input()` unconditionally. The supervisor runs
    the bot under `pythonw.exe` in session 0, which has NO stdin, so on a real
    account that raised EOFError before a single trade - and the supervisor
    would have restarted it into the same crash, forever.

    So: `config.i_understand_real_money` must be true (checked always, works
    headless), and the typed phrase is asked for only when there is a real
    terminal to ask on. Two switches in two files means no single mistake can
    start live trading, and no missing terminal can crash it.
    """
    if demo_mode or dry_run:
        return

    if config.get("i_understand_real_money") is not True:
        sys.exit(
            "DEMO_MODE=false but 'i_understand_real_money: true' is not set in "
            "config.yaml.\nReal-money trading needs both switches, deliberately. "
            "Refusing to start."
        )

    if not sys.stdin or not sys.stdin.isatty():
        # Headless (scheduled task / pythonw): the config flag above is the
        # acknowledgement. Say so loudly in the log rather than prompting a
        # terminal that does not exist.
        # ASCII: this line lands in scan_trade_live.log, which gets read back
        # by cp1252 consoles and by PowerShell 5.1.
        print("DEMO_MODE=false, i_understand_real_money=true, no TTY - "
              "starting REAL-MONEY trading unattended.")
        return

    confirm = input(
        "DEMO_MODE=false in your .env — this will place REAL-MONEY trades.\n"
        "Type exactly: yes I understand   to continue: "
    )
    if confirm.strip().lower() != "yes i understand":
        sys.exit("Aborted — DEMO_MODE left unconfirmed.")


async def _study_pick(
    api: Any, cfg: dict[str, Any], results: list[dict[str, Any]],
    symbol: str, legs: list[tuple[str, str | None]], after_loss: bool,
    symbols: list[str], candidates: list[tuple[str, str | None]],
) -> tuple[dict[str, Any] | None, str]:
    """Study recent digit history before staking; return (pick, selector).

    A normal cycle studies only the symbol the rotation landed on — one extra
    `ticks_history` call. After a loss, `deep_after_loss` widens the window
    and studies EVERY symbol, which is the "look harder after a loss"
    behaviour that was asked for; it costs ~10 calls, so it is deliberately
    not the default path.

    Returns `(None, "study-abstain")` when nothing clears the significance
    threshold, and the caller keeps its cheapest-margin pick. On a genuinely
    independent digit stream that abstention is the expected outcome, not a
    failure.
    """
    deep = bool(after_loss and cfg.get("deep_after_loss", True))
    window = int(cfg.get("deep_window", 1000) if deep else cfg.get("window", 200))
    min_z = float(cfg.get("min_z", 2.0))
    mode = str(cfg.get("mode", "momentum"))

    target_symbols = list(symbols) if deep else [symbol]
    target_legs = list(candidates) if deep else list(legs)
    if deep:
        print(f"study: DEEP review after a loss — {len(target_symbols)} symbols "
              f"x {len(target_legs)} legs over {window} digits")

    best_row: dict[str, Any] | None = None
    best_quote: dict[str, Any] | None = None
    best_strength = float("-inf")

    async def _history(sym: str) -> tuple[str, Any, Any]:
        """Fetch one symbol's history, returning the exception rather than
        raising it, so one bad symbol cannot cancel the whole gather."""
        try:
            resp = await api.ticks_history(sym, count=window)
            return sym, (resp["history"]["prices"], int(resp["pip_size"])), None
        except Exception as exc:  # noqa: BLE001 — a failed study must not stop trading
            return sym, None, exc

    # Concurrently, not in sequence: DerivAPI.send tags each request with a
    # req_id and resolves its own future off a shared read loop, so these are
    # safe in parallel. A deep review is 10 of these, and awaiting them one
    # at a time multiplied the cycle by the round-trip latency — measured as
    # 90-220s gaps between trades on an already-flaky link, against a 45s
    # interval.
    fetched = await asyncio.gather(*(_history(s) for s in target_symbols))

    for sym, payload, error in fetched:
        if error is not None or payload is None:
            print(f"study[{sym}]: history unavailable "
                  f"({type(error).__name__}: {error}) — skipping")
            continue
        prices, pip_size = payload

        digits = digits_from_ticks(prices, pip_size)
        scored = score_legs(digits, target_legs)
        quotes = {(r["contract_type"], r["barrier"]): r
                  for r in results if r["symbol"] == sym}
        print(f"study[{sym}] {summarise(scored)}")
        row, why = study_choose(scored, quotes, min_z=min_z, mode=mode)
        print(f"study[{sym}] {why}")
        if row is None:
            continue
        strength = row["z"] if mode == "momentum" else -row["z"]
        if strength > best_strength:
            best_strength, best_row = strength, row
            best_quote = quotes[(row["contract_type"], row["barrier"])]

    if best_row is None or best_quote is None:
        return None, "study-abstain"
    return best_quote, "study"

# Every combination in a scan failing means the connection is gone, not that
# the market is quiet — a live scan normally returns ~60 quotes. Retrying
# forever on the same dead websocket just spins: observed as an 18-minute
# stall logging "scan returned no quotes" while placing no trades. Give it a
# few cycles to cover a transient blip, then exit non-zero so the supervisor
# restarts with a fresh connection.
MAX_EMPTY_SCANS = 3


def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_strategy_from_config(config: dict[str, Any]) -> Strategy:
    strategy_cfg = dict(config.get("strategy", {}))
    name = strategy_cfg.pop("name", "digit_frequency")
    return build_strategy(name, **strategy_cfg)


def cmd_backtest(config: dict[str, Any], compare: bool = False) -> None:
    if compare:
        prices, pip_size = asyncio.run(fetch_ticks(
            config["app_id"], config["symbol"], config.get("backtest_ticks", 5000),
        ))
        rows = []
        for name, cls in STRATEGIES.items():
            report, _trades = backtest_over_prices(prices, config["stake"], cls(), pip_size)
            rows.append((name, report))
        rows.sort(key=lambda r: r[1]["total_pnl"], reverse=True)

        print(f"{'strategy':<20}{'trades':>8}{'win rate':>10}{'approx pnl':>14}")
        for name, report in rows:
            print(f"{name:<20}{report['num_trades']:>8}{report['win_rate']:>9.1%}{report['total_pnl']:>14.2f}")
        print(
            "\n(approximate per-contract payout table, default params for "
            "every strategy — see deriv_bot/backtester.py, not the real "
            "per-trade payout. This ranks ideas against the same data; it does "
            "not prove any of them hold up out-of-sample.)"
        )
        return

    strategy = _build_strategy_from_config(config)
    report, _trades = asyncio.run(run_backtest(
        app_id=config["app_id"],
        symbol=config["symbol"],
        count=config.get("backtest_ticks", 5000),
        stake=config["stake"],
        strategy=strategy,
    ))
    print(
        f"Trades: {report['num_trades']}  Win rate: {report['win_rate']:.1%}  "
        f"Approx PnL: {report['total_pnl']:.2f} "
        f"(approximate per-contract payout table — see deriv_bot/backtester.py, "
        f"not the real per-trade payout)"
    )


def cmd_scan_edge(config: dict[str, Any]) -> None:
    load_dotenv()
    token = os.environ.get("DERIV_API_TOKEN")
    if not token:
        sys.exit(
            "Set DERIV_API_TOKEN in a .env file first (copy .env.example) — "
            "Deriv's proposal/payout data requires an authorized session, "
            "even just to look up prices."
        )

    results = asyncio.run(scan_edge(
        config["app_id"], config["symbol"], config["stake"], config.get("currency", "USD"), token,
    ))
    if not results:
        sys.exit(
            "No contracts returned a quote — check that the symbol in config.yaml "
            "is currently tradeable and that your token/account supports it."
        )
    print(f"{'contract':<12}{'barrier':>8}{'dur':>5}{'win prob':>10}{'payout':>10}{'ask':>8}{'EV':>9}{'edge %':>9}")
    for r in results:
        barrier = r["barrier"] if r["barrier"] is not None else "-"
        dur = f"{r.get('duration', 1)}t"
        print(
            f"{r['contract_type']:<12}{barrier:>8}{dur:>5}{r['win_prob']:>10.1%}"
            f"{r['payout']:>10.2f}{r['ask_price']:>8.2f}{r['ev']:>9.3f}{r['edge_pct']:>8.2f}%"
        )
    print(
        "\nLowest edge % = smallest house margin on this contract right now. "
        "win prob is the theoretical value (digits are ~uniform), not a prediction — "
        "this tells you which bet is cheapest, not which one will win."
    )


def cmd_ladder_risk(config: dict[str, Any], capital: float | None,
                    win_prob: float, seconds_per_trade: float) -> None:
    """Size the ladder against the capital behind it."""
    staking = config.get("staking") or {}
    base = float(config.get("stake") or 0)
    rungs = int(staking.get("reset_after_losses") or 0)
    if staking.get("name") not in ("doubling", "recovery_ladder") or rungs < 1:
        sys.exit("config staking is not a capped ladder (need staking.name: "
                 "doubling or recovery_ladder, plus reset_after_losses).")
    # A recovery ladder climbs at ~2.083x, not 2x, so its worst case must be
    # summed from the real sequence rather than assumed from base*(2**n - 1).
    sequence = staking.get("sequence")
    if sequence:
        sequence = [float(x) for x in sequence]
        base = sequence[0]

    if capital is None:
        capital = float(input("capital / balance to size against: ").strip())

    s = ladder_summary(capital, base, rungs, win_prob, seconds_per_trade,
                       sequence=sequence)
    kind = "recovery" if sequence else "doubling"
    print(f"ladder: {kind}, {rungs} rungs from a {base:.2f} base, "
          f"win prob {win_prob:.0%}")
    if sequence:
        print("  sequence: " + ", ".join(f"{x:g}" for x in sequence))
    print(f"capital: {capital:,.2f}")
    print()
    print(f"one full ladder costs      {s['ladder_cost']:,.2f} "
          f"({s['pct_of_capital']:.1f}% of capital)")
    print(f"capital covers             {s['ladders_capital_covers']:.1f} full ladders")
    print(f"a wipeout arrives every    {s['wipeout_every_n_trades']:.0f} trades "
          f"= {s['hours_between_wipeouts']:.1f} hours at {seconds_per_trade:.0f}s/trade")
    print(f"over 24h of running        {s['wipeouts_per_day']:.1f} wipeouts "
          f"= {s['expected_daily_wipeout_cost']:,.0f}")
    print()
    print("base stake for a chosen risk per ladder:")
    for pct in (0.05, 0.10, 0.15, 0.20):
        print(f"  {pct:.0%} of capital -> base <= "
              f"{max_base_for_risk(capital, rungs, pct, sequence):.2f}")
    print()
    print("Rarity per cycle is not rarity per day: 1-in-128 per cycle becomes")
    print("several per day once the bot runs unattended.")


def cmd_use_profile(name: str | None) -> None:
    """Switch the whole configuration in one reversible step."""
    repo = os.path.dirname(os.path.abspath(__file__))
    names = list_profiles(repo)
    if not name:
        print("profiles:")
        for n in names:
            try:
                p = load_profile(repo, n)
                staking = (p.get("staking") or {}).get("name", "?")
                risk = (p.get("risk") or {}).get("max_daily_loss", "?")
                tag = "REAL MONEY" if targets_real_money(p, n) else "demo"
                print(f"  {n:<14} {tag:<11} staking={staking:<9} "
                      f"stake={p.get('stake')} max_daily_loss={risk}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {n:<14} (unreadable: {exc})")
        print("\nactivate with: python main.py use-profile <name>")
        return

    if name not in names:
        sys.exit(f"no profile '{name}'. Available: {', '.join(names) or '(none)'}")

    profile = load_profile(repo, name)
    if targets_real_money(profile, name):
        # Switching to real money is the one irreversible-feeling step here,
        # so it gets its own confirmation rather than sharing the generic one.
        print(f"'{name}' targets REAL MONEY.")
        print("It still needs DEMO_MODE=false in .env, and `python main.py "
              "preflight` should pass before you start the bot.")
        if (profile.get("staking") or {}).get("name") != "flat":
            print("It also enables the doubling ladder on real money. "
                  "Run tools/martingale_sim.py with YOUR deposit first.")

    _, changes = activate(repo, name)
    print(f"\nactivated '{name}' (previous config saved to config.yaml.bak)")
    if changes:
        print("changed:")
        for c in changes:
            print(f"  {c}")
    else:
        print("no differences from the previous config.")
    print("\nRestart the bot for this to take effect:")
    print("  Stop-ScheduledTask -TaskName DerivScanTradeSupervisor; "
          "Start-ScheduledTask -TaskName DerivScanTradeSupervisor")


def cmd_preflight(config: dict[str, Any]) -> None:
    """Refuse-or-approve, before anything is funded.

    Deliberately a separate command rather than only a startup check: the
    startup check runs inside a background process whose output nobody
    watches, which is no place to discover that a limit is meaningless.
    """
    load_dotenv()
    token = os.environ.get("DERIV_API_TOKEN")
    demo_mode = os.environ.get("DEMO_MODE", "true").strip().lower() != "false"
    if not token:
        sys.exit("Set DERIV_API_TOKEN in a .env file first (copy .env.example).")

    staking_name = dict(config.get("staking", {})).get("name", "flat")
    wanted = "demo" if demo_mode else "real"
    print(f"DEMO_MODE={'true' if demo_mode else 'false'} -> expecting a {wanted.upper()} account")

    async def _look() -> tuple[dict[str, Any], float]:
        api = DerivAPI(config["app_id"])
        accounts = await api.list_accounts(token)
        account = next((a for a in accounts if a.get("account_type", "").lower() == wanted),
                       accounts[0] if accounts else {})
        ws_url = await api.request_trading_ws_url(token, account["account_id"])
        await api.connect(ws_url)
        try:
            bal = await api.balance()
            return account, float(bal["balance"]["balance"])
        finally:
            await api.close()

    try:
        account, balance = asyncio.run(_look())
    except Exception as exc:  # noqa: BLE001 — report, don't traceback at a user
        sys.exit(f"Could not reach Deriv to check the account: {type(exc).__name__}: {exc}")

    is_demo = account_is_demo(account)
    kind = "DEMO" if is_demo else ("REAL" if is_demo is False else "UNKNOWN TYPE")
    print(f"account {account.get('account_id', '?')} ({kind}) "
          f"balance {balance:.2f} {account.get('currency', '')}")
    print(f"staking: {staking_name}   stake: {config.get('stake')}   "
          f"max_daily_loss: {config.get('risk', {}).get('max_daily_loss')}")

    problems = run_checks(config, demo_mode, account, balance, staking_name)
    if problems:
        print(f"\nPREFLIGHT FAILED ({len(problems)}):")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("\nPREFLIGHT PASSED.")
    if not demo_mode:
        print("This account is REAL. Nothing here says the strategy is profitable —\n"
              "the measured figure to check is loss as a % of staked in `analyze`.")


def cmd_study_report(config: dict[str, Any]) -> None:
    """Compare study-selected trades against rotation-selected ones.

    This is the question the study step exists to answer, and the only
    honest way to answer it: same account, same period, same staking — the
    only thing that differs is which mechanism chose the contract. A
    difference inside the noise band means the study is decorative and
    `scan_trade.study.enabled` should go back to false.
    """
    path = config.get("journal_path", "trade_journal.csv")
    try:
        rows = load_settled(path)
    except FileNotFoundError:
        sys.exit(f"No journal found at {path}.")
    if not rows:
        sys.exit(f"{path} has no settled trades yet.")

    buckets = by_selector(rows)
    print(f"{'selector':<15}{'trades':>8}{'win rate':>10}{'total pnl':>12}"
          f"{'avg/trade':>12}{'mean stake':>12}{'% of staked':>13}")
    for name, b in sorted(buckets.items()):
        print(
            f"{name:<15}{b['trades']:>8}{b['win_rate']:>9.1%}{b['pnl']:>12.2f}"
            f"{b['avg']:>12.3f}{b['mean_stake']:>12.2f}{b['pct_of_staked']:>12.2f}%"
        )

    # study vs study-abstain, NOT vs rotation: once the study is enabled,
    # every trade is one or the other and `rotation` never appears.
    a, b = buckets.get("study"), buckets.get("study-abstain")
    if not a or not b or a["trades"] < 30 or b["trades"] < 30:
        print("\nNot enough trades in both study and study-abstain yet "
              "for a meaningful comparison (want 30+ each).")
        return

    print(f"\nMean stake: study {a['mean_stake']:.2f} vs abstain {b['mean_stake']:.2f}. "
          "The deep review runs after a loss, when the ladder is already\n"
          "elevated, so avg/trade above compares stake size as much as skill. "
          "The stake-matched table below is the one that answers the question.")

    cells = stake_matched(rows, "study", "study-abstain")
    if not cells:
        print("\nNo stake rung yet has 10+ trades on both sides — nothing "
              "comparable like-for-like.")
        return

    print(f"\n{'stake':>8}{'study n':>9}{'study win':>11}{'abstain n':>11}"
          f"{'abstain win':>13}{'gap':>9}{'sigma':>8}")
    for c in cells:
        print(f"{c['stake']:>8.0f}{c['a']['trades']:>9}{c['a']['win_rate']:>10.1%}"
              f"{c['b']['trades']:>11}{c['b']['win_rate']:>12.1%}"
              f"{c['gap']:>+8.1%}{c['sigma']:>+8.2f}")

    gap, se, sigma = pooled_matched_gap(cells)
    print(f"\nstake-matched win-rate gap: {gap:+.2%} (SE {se:.2%}, {sigma:+.2f} sigma)")
    if abs(sigma) < 1.96:
        print("Inside +/-1.96 sigma: consistent with chance. On an independent "
              "digit stream this is\nthe expected result, and it means the study "
              "is not adding information.")
    elif sigma > 0:
        print("Beyond +1.96 sigma: the study is winning more often than "
              "abstaining. Worth a second\nlook before believing it - re-check "
              "after another few hundred trades.")
    else:
        print("Beyond -1.96 sigma: the study is winning LESS often than "
              "abstaining, which is what\nfitting noise looks like. Consider "
              "scan_trade.study.enabled: false.")


def cmd_independence_test(config: dict[str, Any]) -> None:
    """Re-measure whether this digit stream carries any signal to study.

    The claim that it does not is currently a comment in strategy.py quoting
    a one-off measurement. This re-runs it against live data so the premise
    is checkable rather than trusted.
    """
    symbol = config.get("symbol", "R_100")
    count = int(config.get("backtest_ticks", 5000))
    prices, pip_size = asyncio.run(fetch_ticks(config["app_id"], symbol, count))
    digits = digits_from_ticks(prices, pip_size)
    if len(digits) < 100:
        sys.exit(f"only got {len(digits)} digits — need more data")

    # Uniformity: are the ten digits equally likely?
    expected = len(digits) / 10
    counts = [digits.count(d) for d in range(10)]
    chi_uniform = sum((c - expected) ** 2 / expected for c in counts)

    # Lag-1 independence: does the previous digit predict the next?
    trans = [[0] * 10 for _ in range(10)]
    for a, b in zip(digits, digits[1:]):
        trans[a][b] += 1
    chi_lag1 = 0.0
    total = len(digits) - 1
    row_tot = [sum(r) for r in trans]
    col_tot = [sum(trans[a][b] for a in range(10)) for b in range(10)]
    for a in range(10):
        for b in range(10):
            exp = row_tot[a] * col_tot[b] / total if total else 0
            if exp > 0:
                chi_lag1 += (trans[a][b] - exp) ** 2 / exp

    print(f"symbol {symbol}, {len(digits)} digits (pip_size={pip_size})")
    print(f"digit counts: {counts}")
    print(f"\nuniformity chi-square : {chi_uniform:8.2f}   (9 df, 5% threshold 16.92)")
    print(f"lag-1 transition chi-2: {chi_lag1:8.2f}   (81 df, 5% threshold 103.01)")
    verdict_u = "NO deviation detected" if chi_uniform < 16.92 else "deviation from uniform"
    verdict_l = "NO dependence detected" if chi_lag1 < 103.01 else "dependence detected"
    print(f"\nuniformity : {verdict_u}")
    print(f"lag-1      : {verdict_l}")
    if chi_uniform < 16.92 and chi_lag1 < 103.01:
        print("\nBoth tests pass as random. On an independent stream no study of past\n"
              "digits can improve which side wins — expect `study` to abstain, and\n"
              "expect study-report to show no real gap over rotation.")


def cmd_analyze(config: dict[str, Any]) -> None:
    path = config.get("journal_path", "trade_journal.csv")
    try:
        result = analyze_journal(path)
    except FileNotFoundError:
        sys.exit(f"No journal found at {path} — run `live` (demo) first to generate trades.")

    overall = result["overall"]
    if overall["trades"] == 0:
        sys.exit(f"{path} has no settled trades yet (dry-run rows don't count).")

    print(f"{'contract':<12}{'barrier':>8}{'trades':>8}{'win rate':>10}{'total pnl':>11}{'avg/trade':>11}{'loss % of staked':>18}")
    for (contract, barrier), s in result["by_contract"].items():
        print(
            f"{contract:<12}{barrier:>8}{s['trades']:>8}{s['win_rate']:>9.1%}"
            f"{s['total_pnl']:>11.2f}{s['avg_pnl']:>11.3f}{s['loss_pct_of_staked']:>17.2f}%"
        )
    print(
        f"\nOverall: {overall['trades']} trades, {overall['win_rate']:.1%} win rate, "
        f"{overall['total_pnl']:+.2f} PnL ({overall['loss_pct_of_staked']:.2f}% of "
        f"{overall['total_staked']:.2f} staked lost)"
    )
    print(
        "Honest benchmark: expect win rate ≈ each contract's theoretical probability "
        "and loss ≈ its house margin (see scan-edge). Deviations on small samples "
        "are noise, not signal."
    )


async def _run_live(config: dict[str, Any], dry_run: bool,
                    daily_pnl_offset: float = 0.0) -> None:
    load_dotenv()
    token = os.environ.get("DERIV_API_TOKEN")
    demo_mode = os.environ.get("DEMO_MODE", "true").strip().lower() != "false"

    if not token:
        sys.exit("Set DERIV_API_TOKEN in a .env file first (copy .env.example).")

    confirm_real_money(config, demo_mode, dry_run)

    strategy = _build_strategy_from_config(config)
    # Carried in by the supervisor so max_daily_loss caps the DAY, not this
    # process. Zero when run by hand.
    if daily_pnl_offset:
        print(f"opening daily PnL carried over: {daily_pnl_offset:+.2f} "
              f"(budget left before the {config['risk']['max_daily_loss']:.0f} "
              f"stop: {abs(config['risk']['max_daily_loss']) + daily_pnl_offset:.2f})")
    risk = RiskManager(RiskLimits(**config["risk"]), opening_daily_pnl=daily_pnl_offset)
    if not risk.can_trade():
        sys.exit(f"Day already past its limit ({risk.stop_reason}) - not trading.")
    journal = TradeJournal(config.get("journal_path", "trade_journal.csv"))

    staking_cfg = dict(config.get("staking", {}))
    staking_name = staking_cfg.pop("name", "flat")
    staking_refusal = check_staking(staking_name, demo_mode, config)
    if staking_refusal:
        sys.exit(staking_refusal)
    if staking_name != "flat" and not demo_mode:
        # Opted in deliberately. Put the number in the log every single run,
        # so it lives in the record rather than only in whoever remembered it.
        print(f"REAL MONEY + progressive staking '{staking_name}' - enabled via "
              f"i_accept_progressive_staking_on_real. See tools/martingale_sim.py "
              f"for the bust rate at your bankroll.")
    staker = build_staker(staking_name, **staking_cfg)

    api = DerivAPI(config["app_id"])
    accounts = await api.list_accounts(token)
    wanted_type = "real" if not demo_mode else "demo"
    account = next((a for a in accounts if a["account_type"] == wanted_type), None)
    if account is None:
        sys.exit(
            f"No {wanted_type} account found for this token/app — check "
            f"DEMO_MODE and that the token has access to a {wanted_type} account."
        )

    ws_url = await api.request_trading_ws_url(token, account["account_id"])
    await api.connect(ws_url)
    try:
        print(f"Authorized as {account['account_id']} ({wanted_type.upper()} account)")

        symbol = config["symbol"]
        base_stake = config["stake"]
        currency = account.get("currency", config.get("currency", "USD"))

        async for tick_msg in api.subscribe({"ticks": symbol}):
            if not risk.can_trade():
                print(f"Risk manager stopped the bot: {risk.stop_reason}")
                break

            tick = tick_msg["tick"]
            digit = last_digit(tick["quote"], int(tick["pip_size"]))
            signal = strategy.on_tick(digit)
            if signal is None:
                continue

            # A staker may swap the contract (e.g. smart_recovery routes
            # recovery bets to whichever contract is cheapest to recover on).
            override = staker.override_signal(signal)
            if override is not None:
                print(override.reason)
                signal = override

            # Size this bet. `net_mult` is what a win pays per 1.0 staked —
            # progressive staking needs it to know how much recovers a run.
            net_mult = approx_net_win(signal, 1.0)
            budget_left = abs(risk.limits.max_daily_loss) + risk.daily_pnl
            stake = round(staker.stake_for(base_stake, net_mult, budget_left), 2)
            if stake < MIN_STAKE:
                print(f"Remaining budget ${budget_left:.2f} below minimum stake — stopping.")
                break

            proposal_params: dict[str, Any] = dict(
                contract_type=signal.contract_type,
                underlying_symbol=symbol,
                amount=stake,
                basis="stake",
                duration=1,
                duration_unit="t",
                currency=currency,
            )
            if signal.barrier is not None:
                proposal_params["barrier"] = signal.barrier
            proposal = await api.proposal(**proposal_params)
            details = proposal["proposal"]
            payout = float(details["payout"])
            ask_price = float(details["ask_price"])

            if dry_run:
                print(
                    f"[dry-run] would buy {signal.contract_type} barrier={signal.barrier} "
                    f"stake={ask_price:.2f} payout={payout:.2f} — {signal.reason}"
                )
                continue

            bought = await api.buy(details["id"], ask_price)
            contract_id = bought["buy"]["contract_id"]
            print(
                f"Bought {signal.contract_type} barrier={signal.barrier} "
                f"stake={ask_price:.2f} payout={payout:.2f} contract_id={contract_id}"
            )

            # Digit contracts settle in one tick, so waiting here before
            # picking up the next signal keeps risk accounting strictly
            # ordered rather than juggling concurrent settlements.
            contract = await api.wait_for_settlement(contract_id)
            profit = float(contract["profit"])
            bal = await api.balance()
            balance_after = bal["balance"]["balance"]
            print(f"Settled contract_id={contract_id} profit={profit:.2f} balance={balance_after:.2f}")

            risk.record_trade(profit)
            staker.record(profit)
            journal.record(
                symbol=symbol, contract_type=signal.contract_type,
                barrier=signal.barrier, stake=ask_price, payout=payout,
                profit=profit, balance_after=balance_after, reason=signal.reason,
            )
    finally:
        journal.close()
        await api.close()


def cmd_live(config: dict[str, Any], dry_run: bool,
             daily_pnl_offset: float = 0.0) -> None:
    asyncio.run(_run_live(config, dry_run, daily_pnl_offset))


async def _run_scan_trade(config: dict[str, Any], dry_run: bool,
                          daily_pnl_offset: float = 0.0) -> None:
    """Every cycle: quote every configured symbol x contract type, trade
    whichever quote has the smallest house margin right now, then wait out
    the rest of `interval_seconds` before scanning again. No tick stream is
    watched — digit contracts settle on their own, and there is nothing in
    a digit's history worth reacting to (see README). The only genuine
    lever is picking the cheapest bet on offer, and that varies by symbol.
    """
    load_dotenv()
    token = os.environ.get("DERIV_API_TOKEN")
    demo_mode = os.environ.get("DEMO_MODE", "true").strip().lower() != "false"

    if not token:
        sys.exit("Set DERIV_API_TOKEN in a .env file first (copy .env.example).")

    confirm_real_money(config, demo_mode, dry_run)

    st_cfg = config.get("scan_trade", {})
    symbols = st_cfg.get("symbols", DEFAULT_SYMBOLS)
    interval = float(st_cfg.get("interval_seconds", 45))
    candidates = (
        parse_candidate_specs(st_cfg["contracts"]) if st_cfg.get("contracts") else DEFAULT_CANDIDATES
    )
    # Force interchange: a pure "pick the single cheapest quote" greedily
    # starves whichever categories/symbols are never quite the cheapest —
    # observed live picking the same symbol+contract every cycle. These two
    # independent round-robins guarantee every category and every symbol
    # gets its turn; scoring only picks the best LEG within the forced cell.
    # Which contract families the rotation cycles. Measured within the seven
    # cheap symbols: over_under 2.29%, even_odd 2.30%, rise_fall 3.79% - so
    # dropping rise_fall removes ~1.5 percentage points from a third of all
    # trades while two families still interchange.
    categories = list(st_cfg.get("categories") or CATEGORY_LEGS)
    unknown = [c for c in categories if c not in CATEGORY_LEGS]
    if unknown:
        sys.exit(f"scan_trade.categories has unknown entries {unknown}. "
                 f"Valid: {sorted(CATEGORY_LEGS)}")
    if not categories:
        sys.exit("scan_trade.categories cannot be empty.")
    category_rr = RoundRobin(categories)
    symbol_rr = RoundRobin(symbols)
    study_cfg = dict(st_cfg.get("study", {}))
    selection_mode = str((st_cfg.get("selection") or {}).get("mode", "global_best")).lower()
    if selection_mode not in ("global_best", "rotation"):
        sys.exit(f"scan_trade.selection.mode must be 'global_best' or 'rotation', "
                 f"got {selection_mode!r}")

    # Carried in by the supervisor so max_daily_loss caps the DAY, not this
    # process. Zero when run by hand.
    if daily_pnl_offset:
        print(f"opening daily PnL carried over: {daily_pnl_offset:+.2f} "
              f"(budget left before the {config['risk']['max_daily_loss']:.0f} "
              f"stop: {abs(config['risk']['max_daily_loss']) + daily_pnl_offset:.2f})")
    risk = RiskManager(RiskLimits(**config["risk"]), opening_daily_pnl=daily_pnl_offset)
    if not risk.can_trade():
        sys.exit(f"Day already past its limit ({risk.stop_reason}) - not trading.")
    journal = TradeJournal(config.get("journal_path", "trade_journal.csv"))

    staking_cfg = dict(config.get("staking", {}))
    staking_name = staking_cfg.pop("name", "flat")
    staking_refusal = check_staking(staking_name, demo_mode, config)
    if staking_refusal:
        sys.exit(staking_refusal)
    if staking_name != "flat" and not demo_mode:
        # Opted in deliberately. Put the number in the log every single run,
        # so it lives in the record rather than only in whoever remembered it.
        print(f"REAL MONEY + progressive staking '{staking_name}' - enabled via "
              f"i_accept_progressive_staking_on_real. See tools/martingale_sim.py "
              f"for the bust rate at your bankroll.")
    staker = build_staker(staking_name, **staking_cfg)
    base_stake = config["stake"]

    api = DerivAPI(config["app_id"])
    accounts = await api.list_accounts(token)
    wanted_type = "real" if not demo_mode else "demo"
    account = next((a for a in accounts if a["account_type"] == wanted_type), None)
    if account is None:
        sys.exit(
            f"No {wanted_type} account found for this token/app — check "
            f"DEMO_MODE and that the token has access to a {wanted_type} account."
        )

    ws_url = await api.request_trading_ws_url(token, account["account_id"])
    await api.connect(ws_url)
    try:
        print(
            f"Authorized as {account['account_id']} ({wanted_type.upper()} account) — "
            f"scanning {len(symbols)} symbols x {len(candidates)} contracts every {interval:.0f}s"
        )
        currency = account.get("currency", config.get("currency", "USD"))

        empty_scans = 0
        last_trade_lost = False  # drives the deeper post-loss study
        while risk.can_trade():
            cycle_start = time.monotonic()
            scan_errors: list[str] = []
            results = await scan_best(api, symbols, candidates, base_stake, currency,
                                      errors=scan_errors)
            if not results:
                empty_scans += 1
                detail = f" — first error: {scan_errors[0]}" if scan_errors else ""
                print(f"scan returned no quotes ({empty_scans}/{MAX_EMPTY_SCANS}){detail}")
                if empty_scans >= MAX_EMPTY_SCANS:
                    print(
                        f"{empty_scans} consecutive empty scans — the connection looks dead. "
                        "Exiting so the supervisor reconnects."
                    )
                    raise SystemExit(3)
            else:
                empty_scans = 0
                overall_best = results[0]
                print(
                    f"scanned {len(results)} quotes across {len(symbols)} symbols — cheapest overall: "
                    f"{overall_best['symbol']} {overall_best['contract_type']} "
                    f"(edge {overall_best['edge_pct']:.2f}%) — not necessarily this cycle's pick"
                )

                if selection_mode == "global_best":
                    # Stage 1 best-per-symbol, stage 2 best-of-those. Kept
                    # available, but NOT the default: it concentrates every
                    # trade on whichever symbol/leg is cheapest, which in
                    # practice was R_10 DIGITOVER in 200 of 200 cycles.
                    best, stage1 = select_pick(results)
                    if best is None:
                        print("nothing quoted this cycle — skipping")
                        elapsed = time.monotonic() - cycle_start
                        if elapsed < interval:
                            await asyncio.sleep(interval - elapsed)
                        continue
                    selector = "global-best"
                    symbol = best["symbol"]
                    category = next((c for c, legs in CATEGORY_LEGS.items()
                                     if (best["contract_type"], best["barrier"]) in legs),
                                    "unknown")
                    barrier_desc = "" if best["barrier"] is None else f":{best['barrier']}"
                    print(select_summary(stage1, best))
                    print(
                        f"picked cheapest of {len(stage1)} symbols: {symbol} "
                        f"{best['contract_type']}{barrier_desc} "
                        f"(edge {best['edge_pct']:.2f}%, win prob {best['win_prob']:.0%})"
                    )
                else:
                    category = category_rr.next()
                    symbol = symbol_rr.next()
                    legs = CATEGORY_LEGS[category]
                    cell = [r for r in results if r["symbol"] == symbol
                           and (r["contract_type"], r["barrier"]) in legs]
                    if not cell:
                        print(f"{category} not offered on {symbol} this cycle — skipping")
                        elapsed = time.monotonic() - cycle_start
                        if elapsed < interval:
                            await asyncio.sleep(interval - elapsed)
                        continue
                    best = min(cell, key=lambda r: r["edge_pct"])
                    selector = "rotation"
                    barrier_desc = "" if best["barrier"] is None else f":{best['barrier']}"
                    print(
                        f"this cycle's turn: {category} on {symbol} -> picked "
                        f"{best['contract_type']}{barrier_desc} (edge {best['edge_pct']:.2f}%, "
                        f"win prob {best['win_prob']:.0%})"
                    )

                if study_cfg.get("enabled"):
                    # The study still RUNS in both modes - the tick review
                    # before every stake, deeper after a loss, is wanted - but
                    # it may only replace the pick under `rotation`.
                    #
                    # Under `global_best` it must not: the pick is already the
                    # cheapest quoted margin, which is a fact, and overriding
                    # it with the best of 60 noisy win-rate estimates is what
                    # measured 10.89% WORSE than abstaining, stake-matched.
                    # Letting the study override here would hand back the
                    # entire reason for choosing globally.
                    study_legs = legs if selection_mode == "rotation" else list(candidates)
                    studied, study_selector = await _study_pick(
                        api, study_cfg, results, symbol, study_legs,
                        after_loss=last_trade_lost,
                        symbols=symbols, candidates=candidates,
                    )
                    if selection_mode == "rotation":
                        selector = study_selector
                        if studied is not None:
                            best = studied
                            barrier_desc = ("" if best["barrier"] is None
                                            else f":{best['barrier']}")
                            print(
                                f"study overrode the rotation -> {best['symbol']} "
                                f"{best['contract_type']}{barrier_desc} "
                                f"(edge {best['edge_pct']:.2f}%)"
                            )
                    elif studied is not None:
                        studied_desc = ("" if studied["barrier"] is None
                                        else f":{studied['barrier']}")
                        print(
                            f"study would have picked {studied['symbol']} "
                            f"{studied['contract_type']}{studied_desc} "
                            f"(edge {studied['edge_pct']:.2f}%) - NOT taken; "
                            f"selection.mode=global_best ranks on quoted edge. "
                            f"Compare the two with `study-report`."
                        )

                # Real quoted net multiplier, not the backtester's approximation —
                # we just fetched the actual payout, so use it.
                net_mult = best["payout"] / best["ask_price"] - 1 if best["ask_price"] else 0.0
                budget_left = abs(risk.limits.max_daily_loss) + risk.daily_pnl
                stake = round(staker.stake_for(base_stake, net_mult, budget_left), 2)
                if stake < MIN_STAKE:
                    print(f"Remaining budget ${budget_left:.2f} below minimum stake — stopping.")
                    break

                params: dict[str, Any] = dict(
                    contract_type=best["contract_type"], underlying_symbol=best["symbol"],
                    amount=stake, basis="stake", duration=1, duration_unit="t", currency=currency,
                )
                if best["barrier"] is not None:
                    params["barrier"] = best["barrier"]
                proposal = await api.proposal(**params)
                details = proposal["proposal"]
                payout = float(details["payout"])
                ask_price = float(details["ask_price"])
                reason = (f"{selector}: {category} on {best['symbol']} "
                          f"({best['edge_pct']:.2f}% edge)")

                if dry_run:
                    print(
                        f"[dry-run] would buy {best['symbol']} {best['contract_type']}{barrier_desc} "
                        f"stake={ask_price:.2f} payout={payout:.2f} — {reason}"
                    )
                else:
                    bought = await api.buy(details["id"], ask_price)
                    contract_id = bought["buy"]["contract_id"]
                    print(
                        f"Bought {best['symbol']} {best['contract_type']}{barrier_desc} "
                        f"stake={ask_price:.2f} payout={payout:.2f} contract_id={contract_id}"
                    )
                    contract = await api.wait_for_settlement(contract_id)
                    profit = float(contract["profit"])
                    bal = await api.balance()
                    balance_after = bal["balance"]["balance"]
                    print(f"Settled contract_id={contract_id} profit={profit:.2f} balance={balance_after:.2f}")

                    risk.record_trade(profit)
                    staker.record(profit)
                    last_trade_lost = profit < 0
                    journal.record(
                        symbol=best["symbol"], contract_type=best["contract_type"],
                        barrier=best["barrier"], stake=ask_price, payout=payout,
                        profit=profit, balance_after=balance_after, reason=reason,
                        selector=selector,
                    )
                    if not risk.can_trade():
                        print(f"Risk manager stopped the bot: {risk.stop_reason}")
                        break

            elapsed = time.monotonic() - cycle_start
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)
    finally:
        journal.close()
        await api.close()


def cmd_scan_trade(config: dict[str, Any], dry_run: bool = False,
                   daily_pnl_offset: float = 0.0) -> None:
    asyncio.run(_run_scan_trade(config, dry_run, daily_pnl_offset))


def main() -> None:
    parser = argparse.ArgumentParser(description="Deriv Digits trading bot")
    sub = parser.add_subparsers(dest="mode", required=True)

    bt = sub.add_parser("backtest", help="Backtest the strategy against historical ticks")
    bt.add_argument("--config", default="config.yaml")
    bt.add_argument(
        "--compare", action="store_true",
        help="Backtest every registered strategy (default params) and rank them",
    )

    scan = sub.add_parser(
        "scan-edge",
        help="Query live payouts across Digits contracts/barriers and rank by smallest house edge",
    )
    scan.add_argument("--config", default="config.yaml")

    live = sub.add_parser("live", help="Run against live ticks (demo account by default)")
    live.add_argument("--config", default="config.yaml")
    live.add_argument("--dry-run", action="store_true", help="Compute signals but never place trades")

    st = sub.add_parser(
        "scan-trade",
        help="Every cycle, quote every symbol x contract and trade whichever is cheapest right now",
    )
    st.add_argument("--config", default="config.yaml")
    st.add_argument("--dry-run", action="store_true", help="Compute the pick but never place trades")
    st.add_argument(
        "--daily-pnl-offset", type=float, default=0.0,
        help="PnL the day has already realised before this process started. "
             "The supervisor passes this so max_daily_loss caps the day "
             "rather than resetting on every restart.",
    )

    an = sub.add_parser("analyze", help="Report per-contract performance from the trade journal")
    an.add_argument("--config", default="config.yaml")

    sr = sub.add_parser(
        "study-report",
        help="Compare study-selected trades against rotation-selected ones",
    )
    sr.add_argument("--config", default="config.yaml")

    lr = sub.add_parser(
        "ladder-risk",
        help="Size the doubling ladder against your capital",
    )
    lr.add_argument("--config", default="config.yaml")
    lr.add_argument("--capital", type=float, default=None)
    lr.add_argument("--win-prob", type=float, default=0.5)
    lr.add_argument("--seconds-per-trade", type=float, default=45.0)

    up = sub.add_parser(
        "use-profile",
        help="List or activate a named configuration (demo-ladder, real-flat, ...)",
    )
    up.add_argument("name", nargs="?", default=None)
    up.add_argument("--config", default="config.yaml")

    pf = sub.add_parser(
        "preflight",
        help="Check the config and account are safe before trading (esp. real money)",
    )
    pf.add_argument("--config", default="config.yaml")

    it = sub.add_parser(
        "independence-test",
        help="Re-measure whether the digit stream carries any signal worth studying",
    )
    it.add_argument("--config", default="config.yaml")

    args = parser.parse_args()
    config = load_config(args.config)

    if args.mode == "backtest":
        cmd_backtest(config, compare=args.compare)
    elif args.mode == "scan-edge":
        cmd_scan_edge(config)
    elif args.mode == "analyze":
        cmd_analyze(config)
    elif args.mode == "study-report":
        cmd_study_report(config)
    elif args.mode == "ladder-risk":
        cmd_ladder_risk(config, args.capital, args.win_prob, args.seconds_per_trade)
    elif args.mode == "use-profile":
        cmd_use_profile(args.name)
    elif args.mode == "preflight":
        cmd_preflight(config)
    elif args.mode == "independence-test":
        cmd_independence_test(config)
    elif args.mode == "scan-trade":
        cmd_scan_trade(config, dry_run=args.dry_run,
                       daily_pnl_offset=args.daily_pnl_offset)
    else:
        cmd_live(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
