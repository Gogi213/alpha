#!/usr/bin/env python3
"""TK-532: замер лимитов подписки Bybit linear — длина args в одном запросе и число топиков на соединение; pong на ws-ping. Запуск на srv: probe_bybit_limits.py"""
import asyncio
import json
import time
import urllib.request

import websockets

URL = "wss://stream.bybit.com/v5/public/linear"


def symbols():
    with urllib.request.urlopen("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000", timeout=20) as r:
        return [s["symbol"] for s in json.load(r)["result"]["list"] if s["status"] == "Trading" and s["quoteCoin"] == "USDT"]


async def sub(ws, args, wait=3.0):
    await ws.send(json.dumps({"op": "subscribe", "args": args, "req_id": "p"}))
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            d = json.loads(await asyncio.wait_for(ws.recv(), wait))
        except asyncio.TimeoutError:
            break
        if d.get("op") == "subscribe":
            return d.get("success"), d.get("ret_msg")
    return None, "no-reply"


async def one(args):
    async with websockets.connect(URL, compression=None, ping_interval=None, max_size=None) as ws:
        return await sub(ws, args)


async def main():
    syms = symbols()
    print("symbols", len(syms))
    topics = [f"orderbook.1.{s}" for s in syms]
    # граница длины args: сумма знаков топиков (как считает документация) 20 000 / 21 000 / 22 000 / 30 000 / 60 000, три вида вперемешку
    allt = [f"{k}.{s}" for s in syms for k in ("orderbook.1", "publicTrade", "tickers")]
    for target in (20000, 21000, 22000, 30000, 60000):
        acc, n = [], 0
        for t in allt:
            if n + len(t) > target:
                break
            acc.append(t)
            n += len(t)
        print("topic_chars", n, "args_json_chars", len(json.dumps(acc)), "topics", len(acc), "->", await one(acc))
    # число топиков на соединение: подписываемся пачками по 100 на одном соединении до отказа
    async with websockets.connect(URL, compression=None, ping_interval=None, max_size=None) as ws:
        tot, i = 0, 0
        pool = topics + [f"publicTrade.{s}" for s in syms] + [f"tickers.{s}" for s in syms]
        while i < len(pool):
            b = pool[i : i + 100]
            ok, msg = await sub(ws, b)
            if not ok:
                print("refused at", tot, "->", ok, msg)
                break
            tot += len(b)
            i += 100
            await asyncio.sleep(0.1)
        else:
            print("all", tot, "topics on one connection accepted")
        # pong на ws-ping
        async def drain():
            async for _ in ws:
                pass

        task = asyncio.create_task(drain())
        rtts = []
        for _ in range(5):
            t0 = time.time()
            await asyncio.wait_for(await ws.ping(), 5)
            rtts.append(round((time.time() - t0) * 1000, 1))
            await asyncio.sleep(1)
        print("ws.ping rtt_ms", rtts)
        task.cancel()


asyncio.run(main())
