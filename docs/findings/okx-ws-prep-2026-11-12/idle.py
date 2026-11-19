#!/usr/bin/env python3
"""Проба тишины OKX public WS (30 с — документация): как соединение ведёт себя без данных и какой пинг его держит.
  idle.py MODE SECS: MODE = none (без подписки, без пинга) | protonosub / textnosub (без подписки + протокольный ping websockets раз в 5 с / текстовый 'ping' раз в 20 с) |
  proto (подписка price-limit символа + протокольный ping раз в 5 с) | text (то же + текстовый 'ping' раз в 20 с)
Печатает JSON: когда и чем закрыто, число кадров по типам, RTT протокольного ping, ответы на текстовый ping."""
import asyncio, json, sys, time
import websockets

mode, secs = sys.argv[1], float(sys.argv[2])


async def main():
    t0 = time.monotonic()
    out = {"mode": mode, "frames": 0, "pong_text": 0, "events": [], "proto_rtt_ms": [], "closed_at_s": None, "close": None}
    try:
        async with websockets.connect("wss://ws.okx.com/ws/v5/public", max_size=None, ping_interval=None, compression=None) as ws:
            out["connect_s"] = round(time.monotonic() - t0, 3)
            t1 = time.monotonic()
            if mode not in ("none", "protonosub", "textnosub"):
                await ws.send(json.dumps({"op": "subscribe", "args": [{"channel": "price-limit", "instId": "STABLE-USDT-SWAP"}]}))

            async def proto():
                while True:
                    await asyncio.sleep(5)
                    s = time.monotonic()
                    w = await ws.ping()
                    await asyncio.wait_for(w, 10)
                    out["proto_rtt_ms"].append(round((time.monotonic() - s) * 1000, 1))

            async def text():
                while True:
                    await asyncio.sleep(20)
                    await ws.send("ping")

            tasks = []
            if mode in ("proto", "protonosub"):
                tasks.append(asyncio.create_task(proto()))
            if mode in ("text", "textnosub"):
                tasks.append(asyncio.create_task(text()))
            try:
                while time.monotonic() - t1 < secs:
                    fr = await asyncio.wait_for(ws.recv(), timeout=secs - (time.monotonic() - t1))
                    out["frames"] += 1
                    if fr == "pong":
                        out["pong_text"] += 1
                    elif len(out["events"]) < 6:
                        out["events"].append([round(time.monotonic() - t1, 1), fr[:160]])
            except asyncio.TimeoutError:
                pass
            finally:
                for t in tasks:
                    t.cancel()
    except Exception as e:  # noqa: BLE001
        out["closed_at_s"] = round(time.monotonic() - t0, 1)
        out["close"] = repr(e)[:200]
    print(json.dumps(out))


asyncio.run(main())
