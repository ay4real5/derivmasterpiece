"""Named configurations you can switch between with one command.

Four settings differ between "practising on demo" and "trading for real":
account type, staking, stake size and risk limits. Editing four things by
hand every time is how a demo-only ladder ends up pointed at a funded
account, so they move together as a named profile instead.

Activation copies the profile over `config.yaml` rather than passing
`--config` to the scheduled task. That is deliberate: changing the task's
arguments means re-registering it, and an unelevated re-register silently
drops the S4U principal that an elevated install was needed to obtain. The
task keeps reading `config.yaml`; only the contents change.

The previous config is always kept at `config.yaml.bak`, so any switch is
one copy away from being undone.
"""
from __future__ import annotations

import os
import shutil
from typing import Any

import yaml

PROFILE_DIR = "profiles"
ACTIVE = "config.yaml"
BACKUP = "config.yaml.bak"


def profile_path(repo: str, name: str) -> str:
    return os.path.join(repo, PROFILE_DIR, f"{name}.yaml")


def list_profiles(repo: str) -> list[str]:
    directory = os.path.join(repo, PROFILE_DIR)
    if not os.path.isdir(directory):
        return []
    return sorted(f[:-5] for f in os.listdir(directory) if f.endswith(".yaml"))


def load_profile(repo: str, name: str) -> dict[str, Any]:
    path = profile_path(repo, name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"no profile '{name}' in {PROFILE_DIR}/")
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def targets_real_money(profile: dict[str, Any], name: str) -> bool:
    """Whether activating this profile points the bot at real money.

    The profile name is a hint, not the authority - `DEMO_MODE` lives in
    `.env`, outside any profile. So a profile is treated as real-money if it
    says so explicitly, and the name is used only as a fallback signal for
    the warning text.
    """
    if profile.get("i_understand_real_money") is True:
        return True
    return name.startswith("real")


def describe_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """The handful of fields worth showing when switching."""
    fields = [
        ("stake", lambda c: c.get("stake")),
        ("staking", lambda c: (c.get("staking") or {}).get("name")),
        ("max_daily_loss", lambda c: (c.get("risk") or {}).get("max_daily_loss")),
        ("target_profit", lambda c: (c.get("risk") or {}).get("target_profit")),
        ("study", lambda c: ((c.get("scan_trade") or {}).get("study") or {}).get("enabled")),
        ("real_money_ack", lambda c: c.get("i_understand_real_money")),
        ("ladder_on_real_ack", lambda c: c.get("i_accept_progressive_staking_on_real")),
    ]
    out = []
    for label, get in fields:
        before, after = get(old), get(new)
        if before != after:
            out.append(f"{label}: {before!r} -> {after!r}")
    return out


def activate(repo: str, name: str) -> tuple[dict[str, Any], list[str]]:
    """Back up the current config and write the profile in its place.

    Returns (profile, changes). Raises rather than half-writing.
    """
    profile = load_profile(repo, name)
    active_path = os.path.join(repo, ACTIVE)
    old: dict[str, Any] = {}
    if os.path.exists(active_path):
        with open(active_path, encoding="utf-8") as fh:
            old = yaml.safe_load(fh) or {}
        shutil.copyfile(active_path, os.path.join(repo, BACKUP))

    shutil.copyfile(profile_path(repo, name), active_path)
    return profile, describe_changes(old, profile)
