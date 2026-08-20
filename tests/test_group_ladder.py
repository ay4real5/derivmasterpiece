from deriv_bot.group_ladder import GroupLadder, MAX_TRADES_PER_RUN

PAYOUT = 1.9233   # ~R_50 5-tick Rise/Fall, matches the number already used
EDGE = PAYOUT - 1.0  # 0.9233


def profit_for(stake: float, won: bool) -> float:
    return round(stake * EDGE, 2) if won else -stake


def test_trades_1_to_4_use_the_fixed_group1_stakes():
    gl = GroupLadder()
    assert gl.next_stake(PAYOUT) == 5.0
    gl.record_result(5.0, profit_for(5.0, won=False))
    assert gl.next_stake(PAYOUT) == 10.0
    gl.record_result(10.0, profit_for(10.0, won=False))
    assert gl.next_stake(PAYOUT) == 20.0
    gl.record_result(20.0, profit_for(20.0, won=False))
    assert gl.next_stake(PAYOUT) == 40.0


def test_trade_5_recovers_the_run_and_reaches_the_target_in_one_win():
    gl = GroupLadder()
    for stake in (5.0, 10.0, 20.0, 40.0):
        gl.record_result(stake, profit_for(stake, won=False))
    # down 75, target is 20 -> trade 5 must earn back 95 net
    stake5 = gl.next_stake(PAYOUT)
    assert stake5 == round(95.0 / EDGE, 2)

    info = gl.record_result(stake5, profit_for(stake5, won=True))
    assert info["won"] is True
    assert info["reached_target"] is True
    assert info["advanced_to_group"] == 2
    # cumulative profit landed at (approximately) the 20 target
    assert abs(gl.states[1].cumulative_profit - 20.0) < 0.05


def test_win_before_target_resets_to_trade_1_same_group():
    gl = GroupLadder()
    gl.record_result(5.0, profit_for(5.0, won=False))       # -5, group 1 still
    info = gl.record_result(10.0, profit_for(10.0, won=True))  # small win, not enough
    assert info["reached_target"] is False
    assert gl.group.number == 1
    assert gl.state.trade_number == 1
    assert gl.state.run_losses == 0.0


def test_loss_before_target_moves_to_next_trade_number():
    gl = GroupLadder()
    gl.record_result(5.0, profit_for(5.0, won=False))
    assert gl.state.trade_number == 2
    assert gl.state.run_losses == 5.0


def test_run_exhausted_after_10th_straight_loss():
    gl = GroupLadder()
    for _ in range(MAX_TRADES_PER_RUN):
        stake = gl.next_stake(PAYOUT)
        gl.record_result(stake, profit_for(stake, won=False))
    assert gl.exhausted is True
    # further calls report exhausted rather than silently continuing
    assert gl.record_result(1.0, -1.0) == {"exhausted": True}


def test_groups_loop_from_6_back_to_1():
    gl = GroupLadder()
    gl.current_group_index = 5   # group 6
    gl.states[6].cumulative_profit = 511.0
    win_stake = gl.next_stake(PAYOUT)
    info = gl.record_result(win_stake, profit_for(win_stake, won=True))
    assert info["advanced_to_group"] == 1
    assert gl.group.number == 1
    assert gl.state.trade_number == 1


def test_persists_and_restores_state():
    gl = GroupLadder()
    gl.record_result(5.0, profit_for(5.0, won=False))
    gl.record_result(10.0, profit_for(10.0, won=False))
    data = gl.to_dict()

    gl2 = GroupLadder.from_dict(data)
    assert gl2.group.number == gl.group.number
    assert gl2.state.trade_number == gl.state.trade_number
    assert gl2.state.run_losses == gl.state.run_losses
    assert gl2.next_stake(PAYOUT) == gl.next_stake(PAYOUT)
