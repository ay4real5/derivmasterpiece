"""Backfills the local journal from Deriv's own `profit_table`.

WHY THIS EXISTS. `Session._watch` records a settled trade to the journal
from a live subscription (see `pricebot/runner.py`). That subscription can
miss a trade it would otherwise have caught: the process is killed, the
session window ends before settlement, the socket drops. The contract still
settles fine on Deriv's side - Deriv holds the exit, not this bot - but the
journal, and the daily-loss cap that reads it (`tools/risefall_supervisor.py`
day_pnl), never finds out. Measured live: 4 of 5 NOTOUCH trades placed
across two machines sharing one demo account never reached either machine's
journal, because each machine's local file only ever recorded what IT
watched.

`profit_table` is the fix: it is the account's own record, independent of
which process or machine placed the trade, so reconciling against it makes
the journal (and the cap) self-healing regardless of the reason a trade was
missed - the runner's own straggler-wait (see `Session.run`) reduces how
often reconciliation has to do this work, but this is what makes a miss
non-permanent.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from typing import Any, Sequence

from deriv_bot.api import DerivAPI
from deriv_bot.journal import TradeJournal


def existing_contract_ids(journal_path: str) -> set[str]:
    """contract_id values already on disk, as strings (CSV has no types).

    Missing file or missing column both mean "nothing recorded yet" rather
    than an error - an older journal predates this column entirely.
    """
    if not os.path.exists(journal_path):
        return set()
    with open(journal_path, newline="", encoding="utf-8") as fh:
        return {
            row["contract_id"].strip()
            for row in csv.DictReader(fh)
            if (row.get("contract_id") or "").strip()
        }


def missing_transactions(
    known_ids: set[str], transactions: Sequence[dict[str, Any]],
    contract_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Transactions from `profit_table` not already in the journal.

    `profit_table` is scoped to the ACCOUNT, not to one bot or journal - on
    an account trading more than one bot (this repo has run a digit bot and
    a pricebot side by side), it returns every bot's trades mixed together.
    `contract_types`, when given, keeps this journal to only the contract
    types this bot actually trades - without it, reconciling a Touch bot's
    journal against the account's full history pulls in the digit bot's
    DIGITEVEN/CALL rows too, and `day_pnl` (which sums the whole file) would
    then judge this bot's daily cap against losses that were never its own.
    Discovered by running this against a live account that had both.

    Pure so the filter is testable without a network call or a real file.
    """
    out = [t for t in transactions if str(t.get("contract_id", "")) not in known_ids]
    if contract_types is not None:
        out = [t for t in out if t.get("contract_type") in contract_types]
    return out


def as_journal_row(transaction: dict[str, Any]) -> dict[str, Any]:
    """One `profit_table` transaction, shaped as `TradeJournal.record` kwargs.

    `timestamp` is the contract's real `sell_time`, not "now" - reconciling
    a trade that settled yesterday must book it against yesterday's day, or
    `day_pnl` (which buckets by the journal's own timestamp) would hand
    today's cap a loss that already happened.
    """
    buy_price = float(transaction.get("buy_price") or 0.0)
    sell_price = float(transaction.get("sell_price") or 0.0)
    sell_time = transaction.get("sell_time")
    stamp = (datetime.fromtimestamp(int(sell_time), tz=timezone.utc).isoformat()
             if sell_time else datetime.now(timezone.utc).isoformat())
    return dict(
        timestamp=stamp,
        symbol=transaction.get("underlying_symbol", ""),
        contract_type=transaction.get("contract_type", ""),
        barrier="",
        stake=buy_price,
        payout=transaction.get("payout", ""),
        profit=sell_price - buy_price,
        balance_after="",
        reason="reconciled from profit_table",
        selector="reconcile",
        contract_id=transaction.get("contract_id", ""),
    )


async def reconcile_journal(
    api: DerivAPI, journal: TradeJournal, journal_path: str, limit: int = 50,
    contract_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetches recent settled trades and appends any missing from the
    journal. Returns the rows it appended, for logging.

    `contract_types` should be the set this bot's OWN instrument trades
    (e.g. `{"ONETOUCH", "NOTOUCH"}` for a Touch bot) - see
    `missing_transactions` for why this matters on a shared account.
    """
    known = existing_contract_ids(journal_path)
    transactions = await api.profit_table(limit=limit)
    missing = missing_transactions(known, transactions, contract_types)
    for t in missing:
        journal.record(**as_journal_row(t))
    return missing
