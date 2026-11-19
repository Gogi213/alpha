#!/usr/bin/env python3
"""TK-580: record raw gzip frames of BingX swap-market for a decoder bench.
usage: capture_gz.py <out-file> [seconds=150] [topics-per-conn=200]
File: repeated records <u32 len><u8 kind 0=book 1=trade 2=mark 3=other><f64 mono_s><len bytes of the frame as received>."""
import asyncio, gzip, json, ssl, struct, sys, time, urllib.request
import websockets
OUT = sys.argv[1]; SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 150; PER = int(sys.argv[3]) if len(sys.argv) > 3 else 200
UA = {"User-Agent": "alpha-collector/bingx"}
def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=15))["data"]
base = "https://open-api.bingx.com/openApi/swap/v2/quote"
con = {c["symbol"] for c in get(base + "/contracts") if c.get("currency") == "USDT" and c.get("status") == 1}
tk = sorted(((float(t.get("quoteVolume") or 0), t["symbol"]) for t in get(base + "/ticker") if t["symbol"] in con), reverse=True)
syms = [s for _, s in tk]
step = max(1, len(syms) // PER)
pick = syms[::step][:PER]   # spread over the whole turnover ranking (like the production pool), not only the top
print("symbols", len(syms), "picked", len(pick), flush=True)
KIND = {"bookTicker": 0, "trade": 1, "markPrice": 2}
stats = {0: 0, 1: 0, 2: 0, 3: 0}
async def conn(kind, f):
    async with websockets.connect("wss://open-api-swap.bingx.com/swap-market", ssl=ssl.create_default_context(), max_size=None, compression=None, ping_interval=None) as ws:
        for s in pick:
            await ws.send(json.dumps({"id": f"{s}@{kind}", "reqType": "sub", "dataType": f"{s}@{kind}"}))
            await asyncio.sleep(0.02)
        end = time.monotonic() + SECS
        k = KIND[kind]
        while time.monotonic() < end:
            try:
                m = await asyncio.wait_for(ws.recv(), 3)
            except asyncio.TimeoutError:
                continue
            b = m if isinstance(m, (bytes, bytearray)) else m.encode()
            if len(b) < 64:   # heartbeat: gzip-wrapped text Ping -> text Pong (as the collector does)
                try:
                    if gzip.decompress(b).lower() == b"ping":
                        await ws.send("Pong")
                        continue
                except Exception:
                    pass
            b = bytes(b)
            f.write(struct.pack("<IBd", len(b), k, time.monotonic()) + b)
            stats[k] += 1
async def main():
    with open(OUT, "wb") as f:
        await asyncio.gather(*(conn(k, f) for k in KIND))
    print("frames", stats, flush=True)
asyncio.run(main())
