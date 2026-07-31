"""CSV trade journal — one row per trade, flushed immediately."""
from __future__ import annotations

import csv
import os
import tempfile
from datetime import datetime, timezone
from typing import Any


class TradeJournal:
    FIELDS = [
        "timestamp", "symbol", "contract_type", "barrier",
        "stake", "payout", "profit", "balance_after", "reason", "selector",
        "contract_id",
    ]

    def __init__(self, path: str = "trade_journal.csv"):
        self.path = path
        is_new = not os.path.exists(path)
        if not is_new:
            self._migrate_header_if_needed(path)
        self._file = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.FIELDS)
        if is_new:
            self._writer.writeheader()
            self._file.flush()

    @classmethod
    def _migrate_header_if_needed(cls, path: str) -> None:
        """Rewrite an older journal in place when new columns are added.

        Appending rows with more fields than the file's header leaves every
        new row misaligned against it, so a journal written before a column
        existed has to be upgraded rather than appended to. Old rows keep
        their values and get blanks for the new columns — the history is the
        whole point of this file (the supervisor rebuilds the day's PnL from
        it), so it is migrated, never discarded.

        The rewrite goes to a temp file swapped in with os.replace, which is
        atomic: an interrupted migration leaves the original journal intact
        rather than half-written. Nothing runs unless the header differs.
        """
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                return  # empty file; the caller writes a fresh header
            if header == cls.FIELDS:
                return
            rows = list(csv.DictReader(fh, fieldnames=header))

        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".csv")
        os.close(fd)
        try:
            with open(tmp, "w", newline="", encoding="utf-8") as out:
                writer = csv.DictWriter(out, fieldnames=cls.FIELDS)
                writer.writeheader()
                for row in rows:
                    writer.writerow({k: (row.get(k) or "") for k in cls.FIELDS})
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def record(self, **fields: Any) -> None:
        row = {"timestamp": datetime.now(timezone.utc).isoformat(), **fields}
        self._writer.writerow(row)
        self._file.flush()

    def close(self) -> None:
        self._file.close()
