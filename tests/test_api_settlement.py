import asyncio

import pytest

from deriv_bot.api import DerivAPI, DerivAPIError


class _HangingAPI(DerivAPI):
    """Simulates the half-open socket: subscribe() yields nothing and never
    ends, exactly like a dropped connection with no close frame."""

    def __init__(self):
        pass  # deliberately skip DerivAPI.__init__ — no socket needed

    async def subscribe(self, _request):
        await asyncio.Event().wait()  # blocks forever
        yield {}  # pragma: no cover — unreachable, makes this an async generator


class _SettlingAPI(DerivAPI):
    def __init__(self, updates):
        self._updates = updates

    async def subscribe(self, _request):
        for u in self._updates:
            yield u


def test_wait_for_settlement_times_out_on_a_half_open_socket():
    api = _HangingAPI()
    with pytest.raises(DerivAPIError) as exc:
        asyncio.run(api.wait_for_settlement(123, timeout=0.05))
    assert "did not settle" in str(exc.value)
    assert "half-open" in str(exc.value)


def test_wait_for_settlement_returns_the_sold_contract():
    api = _SettlingAPI([
        {"proposal_open_contract": {"is_sold": 0}},
        {"proposal_open_contract": {"is_sold": 1, "profit": 1.91}},
    ])
    contract = asyncio.run(api.wait_for_settlement(123, timeout=5))
    assert contract["profit"] == 1.91


def test_wait_for_settlement_raises_if_the_stream_ends_early():
    api = _SettlingAPI([{"proposal_open_contract": {"is_sold": 0}}])
    with pytest.raises(DerivAPIError) as exc:
        asyncio.run(api.wait_for_settlement(123, timeout=5))
    assert "subscription ended" in str(exc.value)
