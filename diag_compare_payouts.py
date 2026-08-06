"""Compare payouts across Deriv synthetic indices via the public websocket."""
import asyncio
import json
import websockets

INSTRUMENTS = ['R_10', 'R_25', 'R_50', 'R_75', 'R_100',
               '1HZ10V', '1HZ25V', '1HZ50V', '1HZ75V', '1HZ100V']


async def try_app_id(app_id):
    uri = f'wss://ws.derivws.com/websockets/v3?app_id={app_id}'
    ws = await websockets.connect(uri, open_timeout=10)
    # Test with R_50 first
    await ws.send(json.dumps({
        'proposal': 1, 'amount': 40, 'basis': 'stake',
        'contract_type': 'CALL', 'currency': 'USD',
        'duration': 55, 'duration_unit': 's', 'symbol': 'R_50',
    }))
    r = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    if 'error' in r:
        await ws.close()
        return None
    return ws


async def main():
    ws = None
    for app_id in ['1089', '11700', '3397', '1119']:
        try:
            ws = await try_app_id(app_id)
            if ws:
                print(f"Connected with app_id={app_id}")
                break
        except Exception:
            continue
    if not ws:
        print("Could not connect to any public WS endpoint")
        return

    print(f"{'Symbol':>10}  {'Payout%':>8}  {'BreakEven%':>11}  {'Type':>12}")
    print('-' * 55)
    for sym in INSTRUMENTS:
        try:
            await ws.send(json.dumps({
                'proposal': 1, 'amount': 40, 'basis': 'stake',
                'contract_type': 'CALL', 'currency': 'USD',
                'duration': 55, 'duration_unit': 's', 'symbol': sym,
            }))
            r = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if 'proposal' in r:
                payout = float(r['proposal']['payout'])
                payout_pct = (payout - 40) / 40 * 100
                be = 40 / payout * 100
                label = 'Vol Index' if sym.startswith('R_') else 'Jump Index'
                print(f"{sym:>10}  {payout_pct:>7.1f}%  {be:>10.1f}%  {label:>12}")
            elif 'error' in r:
                msg = r['error']['message'][:35]
                print(f"{sym:>10}  ERROR: {msg}")
        except Exception as e:
            print(f"{sym:>10}  ERROR: {str(e)[:35]}")
    await ws.close()

asyncio.run(main())
