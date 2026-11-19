"""TK-532 п.4: кадры против новых записей по тихому символу (поток orderbook.1/publicTrade/tickers одного символа, как в боевом адаптере)."""
import asyncio, json, sys, time
sys.path.insert(0, "/root/bbws")
import websockets
import bybit_ws
from wscore.codec import dedup_key
syms = sys.argv[1].split(","); sec = int(sys.argv[2]); out = sys.argv[3]
ad = bybit_ws.Adapter()
topics = [t.format(s) for s in syms for t in bybit_ws.TOPICS.values()]
frames, recs, new, last = {}, {}, {}, {}
first_last = {}
async def main():
    t_end = time.time() + sec
    async with websockets.connect(bybit_ws.WS_URL, ping_interval=None, compression=None, max_size=None) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": topics}))
        while time.time() < t_end:
            try:
                frame = await asyncio.wait_for(ws.recv(), 5)
            except asyncio.TimeoutError:
                continue
            rx = time.time()
            sym = ad.symbol(frame)
            if sym is None: continue
            kind = frame[10:frame.find(".", 10)]
            k = (sym, kind)
            frames[k] = frames.get(k, 0) + 1
            first_last.setdefault(k, [rx, rx])[1] = rx
            rs = ad.parse(frame)
            for r in rs or []:
                recs[k] = recs.get(k, 0) + 1
                key = (r[0], r[1]); dk = dedup_key(r)
                if dk > last.get(key, -1):
                    last[key] = dk; new[k] = new.get(k, 0) + 1
asyncio.run(main())
res = {f"{s}/{t}": {"frames": frames[(s, t)], "records": recs.get((s, t), 0), "new_records": new.get((s, t), 0), "last_frame_age_s": round(time.time() - first_last[(s, t)][1], 1)} for (s, t) in sorted(frames)}
json.dump({"sec": sec, "ts": time.time(), "by_topic": res}, open(out, "w"), indent=1)
