"""CSV trade journal — one row per trade, flushed immediately."""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from typing import Any


class TradeJournal:
    FIELDS = [
        "timestamp", "symbol", "contract_type", "barrier",
        "stake", "payout", "profit", "balance_after", "reason",
    ]

    def __init__(self, path: str = "trade_journal.csv"):
        self.path = path
        is_new = not os.path.exists(path)
        self._file = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.FIELDS)
        if is_new:
            self._writer.writeheader()
            self._file.flush()

    def record(self, **fields: Any) -> None:
        row = {"timestamp": datetime.now(timezone.utc).isoformat(), **fields}
        self._writer.writerow(row)
        self._file.flush()

    def close(self) -> None:
        self._file.close()
