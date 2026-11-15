#!/usr/bin/env python3
"""Gate prep window: one connection per channel for the whole pool (no sharding), raw frames with rx ns to frames/<tag>-<ch>.jsonl.
usage: gate_w.py <tag> <secs> <pool.json> [topN]"""
import asyncio, json, sys, time, websockets
URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
CH = {"book": "futures.book_ticker", "trade": "futures.trades", "mark": "futures.tickers"}
async def one(tag, kind, syms, secs):
    f = open(f"frames/{tag}-{kind}.jsonl", "w")
    async with websockets.connect(URL, additional_headers={"X-Gate-Size-Decimal": "1"}, max_size=None, ping_interval=None, compression=None) as ws:
        f.write(json.dumps({"ev": "connected", "ts": time.time_ns(), "peer": ws.transport.get_extra_info("peername")[0]}) + "\n")
        for s in syms:
            await ws.send(json.dumps({"time": int(time.time()), "channel": CH[kind], "event": "subscribe", "payload": [s]}))
        end = time.time() + secs; last = time.time()
        while time.time() < end:
            try: m = await asyncio.wait_for(ws.recv(), timeout=1)
            except asyncio.TimeoutError: continue
            except websockets.ConnectionClosed as e:
                f.write(json.dumps({"ev": "closed", "ts": time.time_ns(), "code": e.rcvd.code if e.rcvd else None}) + "\n"); break
            f.write(json.dumps({"ts": time.time_ns(), "f": m}) + "\n")
            if time.time() - last > 5: await ws.ping(); last = time.time()
async def main():
    tag, secs, pool = sys.argv[1], float(sys.argv[2]), json.load(open(sys.argv[3]))
    syms = sorted(pool["pool"], key=lambda s: -pool["qv"][s])
    if len(sys.argv) > 4: syms = syms[:int(sys.argv[4])]
    await asyncio.gather(*(one(tag, k, syms, secs) for k in CH))
asyncio.run(main())
