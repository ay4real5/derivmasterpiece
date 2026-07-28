import io

import pytest

from main import confirm_real_money


class _NoTTY(io.StringIO):
    def isatty(self):
        return False


class _TTY(io.StringIO):
    def isatty(self):
        return True


def test_demo_mode_never_prompts_or_exits():
    confirm_real_money({}, demo_mode=True, dry_run=False)  # must not raise


def test_dry_run_never_prompts_or_exits():
    confirm_real_money({}, demo_mode=False, dry_run=True)


def test_real_money_without_the_config_switch_is_refused():
    with pytest.raises(SystemExit) as exc:
        confirm_real_money({}, demo_mode=False, dry_run=False)
    assert "i_understand_real_money" in str(exc.value)


def test_headless_real_money_does_not_raise_eoferror(monkeypatch, capsys):
    """The blocker this replaced: input() under pythonw in session 0 has no
    stdin, so it raised EOFError before a single trade and the supervisor
    restarted it into the same crash forever."""
    monkeypatch.setattr("sys.stdin", _NoTTY(""))
    confirm_real_money({"i_understand_real_money": True},
                       demo_mode=False, dry_run=False)
    assert "no TTY" in capsys.readouterr().out


def test_headless_still_requires_the_config_switch(monkeypatch):
    # no TTY must not become a way to skip the acknowledgement
    monkeypatch.setattr("sys.stdin", _NoTTY(""))
    with pytest.raises(SystemExit):
        confirm_real_money({}, demo_mode=False, dry_run=False)


def test_stdin_set_to_none_is_handled(monkeypatch, capsys):
    # pythonw sets sys.stdin to None outright
    monkeypatch.setattr("sys.stdin", None)
    confirm_real_money({"i_understand_real_money": True},
                       demo_mode=False, dry_run=False)
    assert "no TTY" in capsys.readouterr().out


def test_interactive_requires_the_typed_phrase(monkeypatch):
    monkeypatch.setattr("sys.stdin", _TTY("yes I understand\n"))
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes I understand")
    confirm_real_money({"i_understand_real_money": True},
                       demo_mode=False, dry_run=False)


def test_interactive_wrong_phrase_aborts(monkeypatch):
    monkeypatch.setattr("sys.stdin", _TTY("no\n"))
    monkeypatch.setattr("builtins.input", lambda _prompt="": "ok")
    with pytest.raises(SystemExit):
        confirm_real_money({"i_understand_real_money": True},
                           demo_mode=False, dry_run=False)
