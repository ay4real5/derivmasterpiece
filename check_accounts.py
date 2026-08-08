"""Verify whether the two API tokens point to the same or different demo accounts."""
import asyncio
import yaml
from deriv_bot.api import DerivAPI

cfg = yaml.safe_load(open('config.yaml', encoding='utf-8'))


async def check(token, app_id, label):
    api = DerivAPI(app_id)
    accts = await api.list_accounts(token)
    print(f'{label}: token ...{token[-6:]}')
    for a in accts:
        loginid = a.get('account_id')
        atype = a.get('account_type')
        currency = a.get('currency')
        print(f'   loginid={loginid} type={atype} currency={currency}')
        if atype == 'demo':
            demo_url = await api.request_trading_ws_url(token, loginid)
            await api.connect(demo_url)
            bal = await api.balance()
            print(f'   -> live balance: {bal["balance"]["balance"]}')
            await api.close()


asyncio.run(check(
    'pat_6d57ffc4003170efe8279c8e0c6ab73cc362c7f4d5b7bd6adace490d4804d2b3',
    cfg['app_id'], 'Account 1 (.env)'))
asyncio.run(check(
    'pat_0d0c11105972c87e0304d0291851363fd243094a4a257db8ea0aa31acb41f1a2',
    '343GsiWjpyIskHP1nbTzi', 'Account 2 (.env.ac2)'))
