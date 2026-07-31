import csv

import pytest

from deriv_bot.journal import TradeJournal
from pricebot.reconcile import (
    as_journal_row,
    existing_contract_ids,
    missing_transactions,
    reconcile_journal,
)


def txn(contract_id, buy=3.0, sell=3.2, symbol="R_50", contract_type="NOTOUCH",
        payout=3.2, sell_time=1785492294):
    return {
        "contract_id": contract_id, "buy_price": buy, "sell_price": sell,
        "underlying_symbol": symbol, "contract_type": contract_type,
        "payout": payout, "sell_time": sell_time,
    }


# --- existing_contract_ids ------------------------------------------------

def test_no_file_means_nothing_known(tmp_path):
    assert existing_contract_ids(str(tmp_path / "missing.csv")) == set()


def test_reads_back_recorded_ids(tmp_path):
    p = tmp_path / "j.csv"
    j = TradeJournal(str(p))
    j.record(symbol="R_50", contract_type="NOTOUCH", barrier="", stake=3.0,
             payout=3.2, profit=0.2, balance_after="", reason="x",
             selector="pricebot", contract_id=111)
    j.close()
    assert existing_contract_ids(str(p)) == {"111"}


def test_blank_contract_ids_are_not_counted(tmp_path):
    """An older journal predates this column - every row reads back blank,
    which must mean 'unknown', not a literal empty-string id."""
    p = tmp_path / "j.csv"
    j = TradeJournal(str(p))
    j.record(symbol="R_50", contract_type="NOTOUCH", barrier="", stake=3.0,
             payout=3.2, profit=0.2, balance_after="", reason="x", selector="pricebot")
    j.close()
    assert existing_contract_ids(str(p)) == set()


# --- missing_transactions --------------------------------------------------

def test_filters_out_already_known_ids():
    known = {"1", "2"}
    txns = [txn(1), txn(2), txn(3)]
    missing = missing_transactions(known, txns)
    assert [t["contract_id"] for t in missing] == [3]


def test_nothing_missing_when_everything_is_known():
    txns = [txn(1), txn(2)]
    assert missing_transactions({"1", "2"}, txns) == []


def test_everything_missing_from_an_empty_journal():
    txns = [txn(1), txn(2)]
    assert missing_transactions(set(), txns) == txns


def test_contract_types_scopes_to_one_bots_own_trades():
    """The bug this guards: an account running more than one bot returns
    every bot's trades from profit_table mixed together. Reconciling a
    Touch journal without this filter pulled a digit bot's DIGITEVEN/CALL
    rows into it on a real account running both."""
    txns = [txn(1, contract_type="NOTOUCH"), txn(2, contract_type="DIGITEVEN"),
            txn(3, contract_type="CALL")]
    missing = missing_transactions(set(), txns, contract_types={"ONETOUCH", "NOTOUCH"})
    assert [t["contract_id"] for t in missing] == [1]


def test_no_contract_types_filter_means_take_everything():
    txns = [txn(1, contract_type="NOTOUCH"), txn(2, contract_type="DIGITEVEN")]
    missing = missing_transactions(set(), txns, contract_types=None)
    assert [t["contract_id"] for t in missing] == [1, 2]


# --- as_journal_row ---------------------------------------------------------

def test_profit_is_sell_minus_buy():
    row = as_journal_row(txn(1, buy=3.0, sell=3.2))
    assert row["profit"] == pytest.approx(0.2)


def test_a_loss_is_negative():
    row = as_journal_row(txn(1, buy=3.0, sell=0.0))
    assert row["profit"] == -3.0


def test_timestamp_comes_from_sell_time_not_now():
    """Reconciling a trade from yesterday must book it against yesterday,
    or day_pnl (which buckets by this timestamp) hands today's cap a loss
    that already happened."""
    row = as_journal_row(txn(1, sell_time=1785492294))
    assert row["timestamp"].startswith("2026-07-31")


def test_selector_marks_the_row_as_reconciled_not_live_watched():
    """So a reconciled row is distinguishable from one Session._watch
    recorded live, for anyone auditing the journal later."""
    assert as_journal_row(txn(1))["selector"] == "reconcile"


def test_contract_id_round_trips():
    assert as_journal_row(txn(42))["contract_id"] == 42


# --- reconcile_journal (integration of the pure pieces) --------------------

class FakeAPI:
    def __init__(self, transactions):
        self._transactions = transactions

    async def profit_table(self, limit=50, offset=0):
        return self._transactions


@pytest.mark.asyncio
async def test_reconcile_appends_only_the_missing_rows(tmp_path):
    p = tmp_path / "j.csv"
    j = TradeJournal(str(p))
    j.record(symbol="R_50", contract_type="NOTOUCH", barrier="", stake=3.0,
             payout=3.2, profit=0.2, balance_after="", reason="x",
             selector="pricebot", contract_id=1)

    api = FakeAPI([txn(1), txn(2), txn(3)])
    appended = await reconcile_journal(api, j, str(p))
    j.close()

    assert [t["contract_id"] for t in appended] == [2, 3]
    rows = list(csv.DictReader(open(p, newline="", encoding="utf-8")))
    assert len(rows) == 3
    assert {r["contract_id"] for r in rows} == {"1", "2", "3"}


@pytest.mark.asyncio
async def test_reconcile_journal_scopes_by_contract_type(tmp_path):
    p = tmp_path / "j.csv"
    j = TradeJournal(str(p))

    api = FakeAPI([txn(1, contract_type="NOTOUCH"), txn(2, contract_type="DIGITEVEN")])
    appended = await reconcile_journal(api, j, str(p), contract_types={"ONETOUCH", "NOTOUCH"})
    j.close()

    assert [t["contract_id"] for t in appended] == [1]
    rows = list(csv.DictReader(open(p, newline="", encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["contract_type"] == "NOTOUCH"


@pytest.mark.asyncio
async def test_reconcile_is_a_noop_when_nothing_is_missing(tmp_path):
    p = tmp_path / "j.csv"
    j = TradeJournal(str(p))
    j.record(symbol="R_50", contract_type="NOTOUCH", barrier="", stake=3.0,
             payout=3.2, profit=0.2, balance_after="", reason="x",
             selector="pricebot", contract_id=1)

    api = FakeAPI([txn(1)])
    appended = await reconcile_journal(api, j, str(p))
    j.close()

    assert appended == []
    rows = list(csv.DictReader(open(p, newline="", encoding="utf-8")))
    assert len(rows) == 1
