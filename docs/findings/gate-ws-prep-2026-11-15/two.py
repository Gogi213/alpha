#!/usr/bin/env python3
"""Gate: two simultaneous connections, same topics; record frames per connection. usage: gate_two.py <secs> <topN> <out-prefix> [ipA ipB]"""
import asyncio, json, sys, time, urllib.request, websockets
URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
async def conn(tag, syms, secs, out, ip=None):
    f = open(out, "w")
    kw = dict(additional_headers={"X-Gate-Size-Decimal": "1"}, max_size=None, ping_interval=None, compression=None)
    if ip: kw["host"] = ip; kw["server_hostname"] = "fx-ws.gateio.ws"
    async with websockets.connect(URL, **kw) as ws:
        f.write(json.dumps({"ev": "connected", "ts": time.time_ns(), "peer": ws.transport.get_extra_info("peername")[0]}) + "\n")
        for ch in ("futures.book_ticker", "futures.trades", "futures.tickers"):
            await ws.send(json.dumps({"time": int(time.time()), "channel": ch, "event": "subscribe", "payload": syms}))
        end = time.time() + secs; last = time.time()
        while time.time() < end:
            try: m = await asyncio.wait_for(ws.recv(), timeout=1)
            except asyncio.TimeoutError: continue
            f.write(json.dumps({"ts": time.time_ns(), "f": m}) + "\n")
            if time.time() - last > 5: await ws.ping(); last = time.time()
async def main():
    secs = float(sys.argv[1]); n = int(sys.argv[2]); pre = sys.argv[3]
    c = json.load(urllib.request.urlopen("https://api.gateio.ws/api/v4/futures/usdt/tickers"))
    syms = [x["contract"] for x in sorted(c, key=lambda x: -float(x["volume_24h_quote"]))[:n]]
    ips = sys.argv[4:6] if len(sys.argv) > 5 else [None, None]
    await asyncio.gather(conn("A", syms, secs, pre + "-A.jsonl", ips[0]), conn("B", syms, secs, pre + "-B.jsonl", ips[1]))
asyncio.run(main())
