"""Give the rest of today a fresh daily budget, without rewriting history.

    python -m tools.reset_day            # count PnL from now onwards
    python -m tools.reset_day --show     # what is in effect
    python -m tools.reset_day --clear    # back to counting the whole day

Writes a timestamp to .day_reset; the supervisor counts only trades AFTER it.

WHY A MARKER AND NOT AN EDIT. The obvious way to "pretend today's loss did not
happen" is to delete rows from trade_journal.csv. That destroys real trade
history - every later analysis, win-rate check and cost measurement in this
repo reads that file, and they would all silently become wrong. A marker
leaves the record complete and is reversible with --clear.

WHAT IT ACTUALLY DOES. It hands the rest of the day another full
max_daily_loss to lose. The cap stops being "the most today can cost" and
becomes "the most this SEGMENT of today can cost". That is a real reduction in
protection, chosen deliberately, so it is printed plainly and recorded in the
log rather than applied quietly.

It expires by itself at the next UTC day - `read_day_reset` ignores a marker
whose date is not today, so a forgotten reset cannot leave the cap disabled
indefinitely.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))

from supervisor import (  # noqa: E402
    DAY_RESET_PATH, day_pnl, read_day_reset, utc_today,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset today's loss budget")
    ap.add_argument("--journal", default="trade_journal.csv")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--clear", action="store_true")
    args = ap.parse_args()

    journal = os.path.join(REPO, args.journal)
    current = read_day_reset()

    if args.show:
        print(f"day-reset marker : {current or 'none - counting the whole day'}")
        print(f"PnL counted now  : {day_pnl(journal, utc_today(), since=current):+.2f}")
        print(f"PnL for the day  : {day_pnl(journal, utc_today()):+.2f}")
        return 0

    if args.clear:
        try:
            os.remove(DAY_RESET_PATH)
            print("marker cleared - the full day counts again")
        except OSError:
            print("no marker was set")
        print(f"PnL now counted  : {day_pnl(journal, utc_today()):+.2f}")
        return 0

    before = day_pnl(journal, utc_today())
    stamp = datetime.now(timezone.utc).isoformat()
    with open(DAY_RESET_PATH, "w", encoding="utf-8") as fh:
        fh.write(stamp)

    after = day_pnl(journal, utc_today(), since=read_day_reset())
    print(f"day reset at {stamp}")
    print(f"  real PnL for the day : {before:+.2f}  (unchanged in the journal)")
    print(f"  PnL the cap now sees : {after:+.2f}")
    print("  the cap now limits the REST of today, not all of today - "
          "so today can cost more than max_daily_loss in total")
    print("  expires by itself at the next UTC day")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
