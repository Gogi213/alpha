#!/usr/bin/env python3
"""Gate futures WS limits probe: one connection per test; prints the non-update frames (answers/errors) and the update counts. Raw frames -> frames/<test>.jsonl.
usage: gate_limits.py <test> [secs]   tests: bad, dup, bigpayload, burst, allthree, all1051"""
import asyncio, json, sys, time, collections, urllib.request, websockets

URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
def pool(minq=300000.0):
    c = json.load(urllib.request.urlopen("https://api.gateio.ws/api/v4/futures/usdt/tickers"))
    ct = json.load(urllib.request.urlopen("https://api.gateio.ws/api/v4/futures/usdt/contracts"))
    ok = {x["name"] for x in ct if x["status"] == "trading"}
    all_ = [x["contract"] for x in c if x["contract"] in ok]
    p = [x["contract"] for x in sorted(c, key=lambda x: -float(x["volume_24h_quote"])) if x["contract"] in ok and float(x["volume_24h_quote"]) >= minq]
    return p, all_

def sub(ch, payload, i=[0]):
    i[0] += 1
    return {"time": int(time.time()), "id": i[0], "channel": ch, "event": "subscribe", "payload": payload}

async def run(name, msgs, secs, gap=0.0):
    out = open(f"frames/lim-{name}.jsonl", "w")
    t0 = time.time()
    async with websockets.connect(URL, additional_headers={"X-Gate-Size-Decimal": "1"}, max_size=None, ping_interval=None, compression=None) as ws:
        print(f"[{name}] connected in {time.time()-t0:.2f}s, sending {len(msgs)} msgs")
        for m in msgs:
            await ws.send(json.dumps(m))
            if gap: await asyncio.sleep(gap)
        t_sent = time.time()
        cnt = collections.Counter(); other = []; end = time.time() + secs; syms = collections.defaultdict(set)
        try:
            while time.time() < end:
                m = await asyncio.wait_for(ws.recv(), timeout=max(0.05, end - time.time()))
                out.write(json.dumps({"ts": time.time_ns(), "f": m}) + "\n")
                d = json.loads(m)
                if d.get("event") == "update":
                    cnt[d["channel"]] += 1
                    r = d["result"]
                    if isinstance(r, dict): syms[d["channel"]].add(r.get("s"))
                    else:
                        for x in r: syms[d["channel"]].add(x.get("contract"))
                else:
                    other.append(d)
        except asyncio.TimeoutError:
            pass
        except websockets.ConnectionClosed as e:
            print(f"[{name}] CLOSED code={e.rcvd.code if e.rcvd else None} reason={e.rcvd.reason if e.rcvd else None} after {time.time()-t_sent:.1f}s of sending")
    print(f"[{name}] updates {dict(cnt)} distinct symbols { {k: len(v) for k, v in syms.items()} }")
    errs = [d for d in other if d.get("error") or (d.get("result") or {}).get("status") != "success"]
    ok = [d for d in other if (d.get("result") or {}).get("status") == "success"]
    print(f"[{name}] answers ok={len(ok)} errors={len(errs)}")
    for d in errs[:5]: print("   ERR", json.dumps(d)[:400])
    if ok: print("   OK sample", json.dumps(ok[0])[:300])

async def main():
    test = sys.argv[1]; secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20
    p, all_ = pool()
    if test == "bad": await run(test, [sub("futures.book_ticker", ["BTC_USDT", "NOPE_USDT"]), sub("futures.book_ticker", ["ZZZ_USDT"]), sub("futures.nope", ["BTC_USDT"]), sub("futures.book_ticker", [])], secs)
    if test == "dup": await run(test, [sub("futures.book_ticker", ["BTC_USDT"]), sub("futures.book_ticker", ["BTC_USDT"]), sub("futures.book_ticker", ["BTC_USDT", "ETH_USDT"])], secs)
    if test == "bigpayload": await run(test, [sub("futures.book_ticker", all_)], secs)
    if test == "burst": await run(test, [sub("futures.book_ticker", [s]) for s in all_], secs)
    if test == "allthree": await run(test, [sub(ch, p) for ch in ("futures.book_ticker", "futures.trades", "futures.tickers")], secs)
    if test == "all1051": await run(test, [sub(ch, all_) for ch in ("futures.book_ticker", "futures.trades", "futures.tickers")], secs)
    if test == "burst3": await run(test, [sub(ch, [s]) for s in p for ch in ("futures.book_ticker", "futures.trades", "futures.tickers")], secs)
asyncio.run(main())
