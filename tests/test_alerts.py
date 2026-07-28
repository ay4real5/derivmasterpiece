import json

from tools.alerts import Alert, emit, read_alerts, should_emit


def test_should_emit_allows_the_first_occurrence():
    state: dict[str, float] = {}
    assert should_emit("stall", now=1000.0, state=state, cooldown=900)


def test_should_emit_suppresses_a_repeat_inside_the_cooldown():
    # the point: a box that stays down must not toast once per check
    state: dict[str, float] = {}
    assert should_emit("stall", 1000.0, state, cooldown=900)
    assert not should_emit("stall", 1300.0, state, cooldown=900)
    assert not should_emit("stall", 1899.0, state, cooldown=900)


def test_should_emit_allows_again_after_the_cooldown():
    state: dict[str, float] = {}
    assert should_emit("stall", 1000.0, state, cooldown=900)
    assert should_emit("stall", 1901.0, state, cooldown=900)


def test_cooldown_is_per_event_type():
    # a stall must not silence an unrelated risk_stop
    state: dict[str, float] = {}
    assert should_emit("stall", 1000.0, state, cooldown=900)
    assert should_emit("risk_stop", 1001.0, state, cooldown=900)


def test_emit_writes_one_json_line(tmp_path):
    p = tmp_path / "alerts.jsonl"
    assert emit(str(p), Alert("stall", "problem", "no trade for 300s"))
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["event"] == "stall"
    assert row["level"] == "problem"
    assert row["message"] == "no trade for 300s"
    assert row["ts"]  # auto-stamped


def test_emit_respects_the_cooldown(tmp_path):
    p = tmp_path / "alerts.jsonl"
    state: dict[str, float] = {}
    assert emit(str(p), Alert("stall", "problem", "first"), state, 900, now=1000.0)
    assert not emit(str(p), Alert("stall", "problem", "second"), state, 900, now=1100.0)
    assert len(read_alerts(str(p))) == 1


def test_emit_without_state_always_writes(tmp_path):
    p = tmp_path / "alerts.jsonl"
    emit(str(p), Alert("stall", "problem", "a"))
    emit(str(p), Alert("stall", "problem", "b"))
    assert len(read_alerts(str(p))) == 2


def test_emit_never_raises_on_an_unwritable_path(tmp_path):
    # alerting must never be able to kill the supervisor it is watching
    bad = tmp_path / "not-a-dir.txt"
    bad.write_text("x", encoding="utf-8")
    assert emit(str(bad / "alerts.jsonl"), Alert("stall", "problem", "x")) is False


def test_read_alerts_skips_a_half_written_line(tmp_path):
    p = tmp_path / "alerts.jsonl"
    p.write_text('{"event":"stall","level":"problem","message":"a","ts":"t"}\n'
                 '{"event":"partial"\n', encoding="utf-8")
    rows = read_alerts(str(p))
    assert len(rows) == 1
    assert rows[0]["event"] == "stall"


def test_read_alerts_on_a_missing_file():
    assert read_alerts("nope-does-not-exist.jsonl") == []
