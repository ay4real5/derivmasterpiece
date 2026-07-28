import csv

from deriv_bot.journal import TradeJournal

OLD_FIELDS = [
    "timestamp", "symbol", "contract_type", "barrier",
    "stake", "payout", "profit", "balance_after", "reason",
]


def _write_old_journal(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OLD_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_new_journal_gets_the_full_header(tmp_path):
    p = tmp_path / "j.csv"
    TradeJournal(str(p)).close()
    with open(p, newline="", encoding="utf-8") as fh:
        assert next(csv.reader(fh)) == TradeJournal.FIELDS


def test_record_writes_the_selector(tmp_path):
    p = tmp_path / "j.csv"
    j = TradeJournal(str(p))
    j.record(symbol="R_10", contract_type="DIGITEVEN", barrier="",
             stake=5.0, payout=9.77, profit=4.77, balance_after=100.0,
             reason="x", selector="study")
    j.close()
    rows = list(csv.DictReader(open(p, newline="", encoding="utf-8")))
    assert rows[0]["selector"] == "study"


def test_existing_journal_is_migrated_not_discarded(tmp_path):
    # the real risk: the supervisor rebuilds the day's PnL from this file,
    # so a migration that dropped rows would hand the bot a fresh loss budget
    p = tmp_path / "j.csv"
    _write_old_journal(p, [
        {"timestamp": "2026-07-27T10:00:00+00:00", "symbol": "R_10",
         "contract_type": "DIGITEVEN", "barrier": "", "stake": "2.0",
         "payout": "3.91", "profit": "-2.0", "balance_after": "998.0",
         "reason": "rotation"},
        {"timestamp": "2026-07-27T10:01:00+00:00", "symbol": "R_25",
         "contract_type": "DIGITOVER", "barrier": "4", "stake": "4.0",
         "payout": "7.81", "profit": "3.81", "balance_after": "1001.81",
         "reason": "rotation"},
    ])
    TradeJournal(str(p)).close()

    rows = list(csv.DictReader(open(p, newline="", encoding="utf-8")))
    assert len(rows) == 2
    assert rows[0]["profit"] == "-2.0"          # old data intact
    assert rows[1]["barrier"] == "4"
    assert rows[0]["selector"] == ""            # blank for pre-existing rows


def test_migrated_journal_accepts_new_rows_aligned(tmp_path):
    p = tmp_path / "j.csv"
    _write_old_journal(p, [
        {"timestamp": "2026-07-27T10:00:00+00:00", "symbol": "R_10",
         "contract_type": "DIGITEVEN", "barrier": "", "stake": "2.0",
         "payout": "3.91", "profit": "-2.0", "balance_after": "998.0",
         "reason": "rotation"},
    ])
    j = TradeJournal(str(p))
    j.record(symbol="R_50", contract_type="DIGITODD", barrier="",
             stake=5.0, payout=9.77, profit=4.77, balance_after=1002.77,
             reason="study pick", selector="study")
    j.close()

    rows = list(csv.DictReader(open(p, newline="", encoding="utf-8")))
    assert len(rows) == 2
    assert rows[0]["selector"] == ""
    assert rows[1]["selector"] == "study"
    assert rows[1]["profit"] == "4.77"          # not shifted by the extra column


def test_migration_is_a_noop_when_the_header_already_matches(tmp_path):
    p = tmp_path / "j.csv"
    TradeJournal(str(p)).close()
    before = p.read_bytes()
    TradeJournal(str(p)).close()
    assert p.read_bytes() == before


def test_empty_file_is_handled(tmp_path):
    p = tmp_path / "j.csv"
    p.write_text("", encoding="utf-8")
    TradeJournal(str(p)).close()  # must not raise
