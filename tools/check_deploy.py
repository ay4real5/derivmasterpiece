"""Does the bot that is RUNNING match the config? Exit 0 if yes, 1 if no.

    python -m tools.check_deploy

Read-only and offline. Reads intent from config.risefall.yaml and fact from
risefall_live.log, and reports every way they disagree. This is the check that
was missing when three config changes were committed, tested, and reported as
live while the trading process kept the old settings.
"""
from __future__ import annotations

import argparse
import os
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from pricebot.deploy_verify import (  # noqa: E402
    caps_match_running,
    config_matches_running,
    observed_expiries,
    observed_rungs,
    parse_effective_settings,
    parse_supervisor_settings,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare running bot to config")
    ap.add_argument("--config", default="config.risefall.yaml")
    ap.add_argument("--log", default="risefall_live.log")
    ap.add_argument("--cap", type=float, default=700.0)
    ap.add_argument("--target", type=float, default=700.0)
    args = ap.parse_args()

    cfg_path = os.path.join(REPO, args.config)
    log_path = os.path.join(REPO, args.log)
    if not os.path.exists(cfg_path):
        print(f"no config at {cfg_path}")
        return 1
    cfg = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    text = ""
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()

    running = parse_effective_settings(text)
    sup = parse_supervisor_settings(text)
    pb = cfg.get("pricebot", {})
    staking = (pb.get("staking") or {}).get("name", "flat")

    print("CONFIG WANTS")
    print(f"  instrument {pb.get('instrument')}  "
          f"expiry {pb.get('duration')}{pb.get('duration_unit')}  "
          f"stake {pb.get('stake')}  staking {staking}")
    print(f"  caps: loss {args.cap}  target {args.target}")

    print("\nRUNNING NOW")
    if running is None:
        print("  nothing - no session start in the log")
    else:
        print(f"  instrument {running['instrument']}  "
              f"expiry {running['expiry'] or 'strategy-derived'}  "
              f"stake {running['stake']}  "
              f"staking {running['staking'] or 'flat'}")
    if sup is None:
        print("  caps: unknown - no 'supervisor up' line")
    else:
        print(f"  caps: loss {sup['cap']}  target {sup['target']}")

    exp = observed_expiries(text)
    rungs = observed_rungs(text)
    print(f"\nACTUALLY TRADED")
    print(f"  expiries: {exp or 'none'}")
    print(f"  ladder rungs: {rungs or 'none - no ladder has run'}")

    problems = config_matches_running(cfg, running)
    problems += caps_match_running(args.cap, args.target, sup)
    if problems:
        print(f"\nMISMATCHED ({len(problems)}):")
        for p in problems:
            print(f"  X {p}")
        print("\nThe running bot is NOT what the config describes.")
        return 1
    print("\nOK - the running bot matches the config.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
