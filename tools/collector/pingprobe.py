#!/usr/bin/env python3
"""TK-531 (G-022): откуда у WS-пинга коллектора ≈ +70 мс к RTT рукопожатия, по которому отбирается путь.
Запуск: pingprobe.py <dur_с> <out.json> <порт_источника_первого> — 4 соединения к /public/stream (как коллектор): 2 пустых (без подписки), 2 с подпиской 64 bookTicker; порты источника — подряд от первого (для tcpdump).
Меряет на каждом соединении раз в 2 с: WS ping→pong (как `Session.pinger`), `tcpi_rtt` ядра (сглаженный) и задержку цикла событий; RTT рукопожатия — как в `wscore.dial`.
Не коллектор: ничего не пишет кроме json. Разбор по tcpdump (ACK пинга и pong по времени) — вне скрипта.
"""
import asyncio
import json
import os
import socket
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import websockets  # noqa: E402

from wscore.dial import kernel_rtt_ms  # noqa: E402

HOST, URL = "fstream.binance.com", "wss://fstream.binance.com/public/stream"
PING_EVERY_S = 2.0


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def book_streams(n):
    t = json.load(urllib.request.urlopen(urllib.request.Request("https://fapi.binance.com/fapi/v1/ticker/24hr", headers={"User-Agent": "alpha-ping"}), timeout=20))
    top = sorted((x for x in t if x["symbol"].endswith("USDT")), key=lambda x: -float(x["quoteVolume"]))
    return [f"{x['symbol'].lower()}@bookTicker" for x in top[10:10 + n]]


async def connect_sock(port):
    loop = asyncio.get_running_loop()
    fam, typ, proto, _, addr = (await loop.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM))[0]
    s = socket.socket(fam, typ, proto)
    s.setblocking(False)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", port))
    await loop.sock_connect(s, addr)
    return s, addr[0]


async def one(i, port, subscribed, streams, dur):
    sock, ip = await connect_sock(port)
    rec = {"conn": i, "port": port, "ip": ip, "subscribed": subscribed, "rtt_connect_ms": kernel_rtt_ms(sock), "ping_ms": [], "kernel_rtt_ms": [], "t": []}
    async with websockets.connect(URL, sock=sock, server_hostname=HOST, max_size=None, ping_interval=None, compression=None, close_timeout=1) as ws:
        if subscribed:
            await ws.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": 1}))

        async def drain():
            async for _ in ws:
                pass

        task = asyncio.create_task(drain())
        await asyncio.sleep(3)
        t_end = time.monotonic() + dur
        while time.monotonic() < t_end:
            t0 = time.monotonic()
            waiter = await ws.ping()
            await asyncio.wait_for(waiter, 10)
            rec["ping_ms"].append(round((time.monotonic() - t0) * 1000, 1))
            rec["kernel_rtt_ms"].append(kernel_rtt_ms(sock))
            rec["t"].append(time.time())
            await asyncio.sleep(PING_EVERY_S)
        task.cancel()
    return rec


async def loop_lag(dur, lags):
    t_end = time.monotonic() + dur
    while time.monotonic() < t_end:
        t0 = time.monotonic()
        await asyncio.sleep(0.01)
        lags.append((time.monotonic() - t0 - 0.01) * 1000)


async def main(dur, out, port0):
    streams = book_streams(64)
    lag = []
    res = await asyncio.gather(*(one(i, port0 + i, i >= 2, streams, dur) for i in range(4)), loop_lag(dur + 3, lag))
    conns = res[:4]
    for c in conns:
        c["ping_p50"], c["ping_min"], c["ping_max"] = pct(c["ping_ms"], .5), min(c["ping_ms"]), max(c["ping_ms"])
        ks = [k for k in c["kernel_rtt_ms"] if k]
        c["kernel_rtt_p50"] = pct(ks, .5)
    json.dump({"t_start": time.time() - dur, "dur": dur, "conns": conns, "loop_lag_p50": pct(lag, .5), "loop_lag_p99": pct(lag, .99), "loop_lag_max": max(lag)}, open(out, "w"))


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2], int(sys.argv[3])))
