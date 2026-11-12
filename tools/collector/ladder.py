#!/usr/bin/env python3
"""TK-531 (G-022): ступень лестницы нагрузки Binance — одно соединение, n потоков одного вида, окно dur с -> json (сообщ/с, КБ/с, лаг, обрывы, тишина, CPU).
Запуск: ladder.py <book|agg|mark> <n> <top|mid> <dur_с> <out.json> [endpoint]   (endpoint: ws | public/ws | market/ws; по умолчанию book — ws, как на ступенях 10…320, agg/mark — market/ws)
Не коллектор: ничего не пишет на диск кроме итогового json, без записи данных. Путь выбирается тем же `wscore.dial` (RTT рукопожатия ≤ 300 мс до TLS), группа top — самые активные по quoteVolume24h, mid — со 150-й позиции.
Первые 5 с после подписки (снапшот) не считаются; ответы биржи на SUBSCRIBE (не данные) сохраняются в `ctl` — чтобы «0 сообщений» не оставалось без причины.
"""
import asyncio
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import websockets  # noqa: E402

from wscore.dial import dial  # noqa: E402

HOST = "fstream.binance.com"
SUB_BATCH, SUB_PAUSE_S = 50, 0.25  # пачка потоков в одном SUBSCRIBE; 4 сообщ/с из 10 допустимых
KINDS = {"book": ("{s}@bookTicker", "ws"), "agg": ("{s}@aggTrade", "market/ws"), "mark": ("{s}@markPrice@1s", "market/ws")}
SNAPSHOT_S = 5


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "alpha-ladder"}), timeout=20))


def symbols():
    t = get("https://fapi.binance.com/fapi/v1/ticker/24hr")
    info = get("https://fapi.binance.com/fapi/v1/exchangeInfo")
    ok = {s["symbol"] for s in info["symbols"] if s["status"] == "TRADING" and s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT"}
    return [x["symbol"] for x in sorted((x for x in t if x["symbol"] in ok), key=lambda x: -float(x["quoteVolume"]))]


async def run(kind, n, group, dur, out, endpoint=None):
    topic, default_ep = KINDS[kind]
    url = f"wss://{HOST}/{endpoint or default_ep}"
    syms = symbols()
    start = 0 if group == "top" else min(150, max(0, len(syms) - n))
    streams = [topic.format(s=s.lower()) for s in syms[start:start + n]]
    res = {"exch": "binance", "kind": kind, "n": n, "group": group, "dur": dur, "url": url, "t_start": time.time(), "streams": len(streams), "pool": len(syms), "disconnects": 0, "events": [], "ctl": []}
    lags, cnt, nbytes, per_sec = [], 0, 0, {}
    cpu0, w0 = time.process_time(), time.monotonic()
    sock, rtt = None, None
    for _ in range(12):
        sock, rtt = await dial(url)
        if sock:
            break
    res["rtt_ms"] = round(rtt)
    if sock is None:
        res["error"] = "no_fast_path"
        json.dump(res, open(out, "w"))
        return
    try:
        async with websockets.connect(url, sock=sock, server_hostname=HOST, max_size=None, ping_interval=None, compression=None, close_timeout=1) as ws:
            for i in range(0, len(streams), SUB_BATCH):
                await ws.send(json.dumps({"method": "SUBSCRIBE", "params": streams[i:i + SUB_BATCH], "id": i + 1}))
                await asyncio.sleep(SUB_PAUSE_S)
            t0 = time.monotonic()
            res["t_sub_done_s"] = round(t0 - w0, 2)
            t_end = t0 + dur + SNAPSHOT_S
            while time.monotonic() < t_end:
                try:
                    m = await asyncio.wait_for(ws.recv(), timeout=max(0.1, t_end - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                now, mono = time.time(), time.monotonic()
                try:
                    d = json.loads(m)
                except ValueError:
                    continue
                if "result" in d or "error" in d or "code" in d:  # ответ на SUBSCRIBE, не данные
                    if len(res["ctl"]) < 20:
                        res["ctl"].append(m[:200])
                    continue
                if mono - t0 < SNAPSHOT_S:
                    continue
                nbytes += len(m)
                cnt += 1
                a = per_sec.setdefault(int(mono - t0), [0, 0])
                a[0] += 1
                a[1] += len(m)
                if d.get("E"):
                    lags.append(now * 1000 - d["E"])
    except Exception as e:  # noqa: BLE001 — любой обрыв — результат ступени
        res["disconnects"] += 1
        res["events"].append(repr(e)[:200])
    res.update(
        msgs=cnt, kb=round(nbytes / 1024), msg_s=round(cnt / dur, 1), kb_s=round(nbytes / 1024 / dur, 1),
        lag_p50=pct(lags, .5), lag_p90=pct(lags, .9), lag_p99=pct(lags, .99), lag_max=max(lags) if lags else None,
        lag_first_p50=pct(lags[:len(lags) // 3], .5), lag_last_p50=pct(lags[-(len(lags) // 3):], .5),
        silent_s=sum(1 for s in range(SNAPSHOT_S, dur + SNAPSHOT_S) if s not in per_sec), peak_msg_s=max((v[0] for v in per_sec.values()), default=0),
        cpu_s=round(time.process_time() - cpu0, 2), t_end=time.time(),
    )
    json.dump(res, open(out, "w"))


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), sys.argv[5], sys.argv[6] if len(sys.argv) > 6 else None))
