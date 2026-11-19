"""famprobe SEC N OUT — A/B семейств адресов до Bybit (TK-532, причина таймаутов: путь IPv6 или IPv4): по N соединений каждого семейства одновременно, SEC с на соединение.
На соединение: orderbook.1.BTCUSDT (поток кадров), протокольный пинг 5/с, TCP_INFO раз в секунду. Итог в OUT (JSON): по соединению и сводка по семейству.
Показатели: total_retrans за окно (наши сегменты без ACK — потери пути), пинги дольше 0,5 с, макс. пауза между кадрами, число пауз > 1 с. Боевой коллектор не трогает (свои соединения)."""
import asyncio
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wscore.dial import tcp_state  # noqa: E402

HOST = "stream.bybit.com"
URL = f"wss://{HOST}/v5/public/linear"
PING_EVERY_S = 0.2
STALL_MS = 1500  # пауза между кадрами orderbook.1.BTCUSDT (≈ 30 кадров/с) дольше этого — стоп потока
TOPIC = "orderbook.1.BTCUSDT"


def q(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 1) if xs else None


async def one(fam, k, sec):
    import websockets

    loop = asyncio.get_running_loop()
    await asyncio.sleep(k * 0.3)  # темп подключений как у коллектора (sub_period), без пачки
    f, typ, proto, _, addr = (await loop.getaddrinfo(HOST, 443, family=fam, type=socket.SOCK_STREAM))[0]
    sock = socket.socket(f, typ, proto)
    sock.setblocking(False)
    await loop.sock_connect(sock, addr)
    res = {"fam": "v6" if fam == socket.AF_INET6 else "v4", "k": k, "ip": addr[0], "lport": sock.getsockname()[1]}
    async with websockets.connect(URL, sock=sock, server_hostname=HOST, max_size=None, ping_interval=None, compression=None, close_timeout=1) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": [TOPIC]}))
        t0 = time.monotonic()
        st0 = tcp_state(sock)
        pings, gaps, retr, stalls, inside = [], [], [], [], []
        last = [t0]
        stop = asyncio.Event()

        async def pinger():
            n_to = 0
            while not stop.is_set():
                t = time.monotonic()
                try:
                    w = await ws.ping()
                    await asyncio.wait_for(w, 10)
                    pings.append((time.monotonic() - t) * 1000)
                except asyncio.TimeoutError:
                    n_to += 1
                await asyncio.sleep(max(0, PING_EVERY_S - (time.monotonic() - t)))
            res["ping_timeouts"] = n_to

        async def sampler():
            prev, mid, tick = st0["total_retrans"], False, 0
            while not stop.is_set():
                await asyncio.sleep(0.25)
                tick += 1
                st = tcp_state(sock)
                if time.monotonic() - last[0] > 1.0:  # внутри паузы кадров (до её конца): состояние TCP — потери пути (retransmits/unacked) или чистый TCP при тишине источника
                    if not mid:
                        mid = True
                        inside.append({"utc": time.strftime("%H:%M:%S", time.gmtime()), "tcp": st})
                else:
                    mid = False
                if tick % 4 == 0 and st["total_retrans"] != prev:
                    retr.append((round(time.monotonic() - t0, 1), st["total_retrans"] - prev, st["retransmits"], st["last_data_recv_ms"]))
                    prev = st["total_retrans"]

        tasks = [asyncio.create_task(pinger()), asyncio.create_task(sampler())]
        n = 0
        try:
            while time.monotonic() - t0 < sec:
                try:
                    await asyncio.wait_for(ws.recv(), 5)
                except asyncio.TimeoutError:
                    gaps.append(5000.0)
                    continue
                now = time.monotonic()
                gaps.append((now - last[0]) * 1000)
                if n and gaps[-1] > STALL_MS:  # пауза в потоке кадров: когда кончилась и что было в TCP (retransmits/backoff — потери пути; нули — молчание источника)
                    stalls.append({"end_utc": time.strftime("%H:%M:%S", time.gmtime()), "gap_ms": round(gaps[-1]), "tcp": tcp_state(sock)})
                last[0] = now
                n += 1
        finally:
            stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        st1 = tcp_state(sock)
    res.update(
        frames=n, pings=len(pings), ping_p50=q(pings, 0.5), ping_p99=q(pings, 0.99), ping_max=q(pings, 1.0), ping_gt500=sum(1 for p in pings if p > 500),
        gap_max_ms=q(gaps, 1.0), gaps_gt1s=sum(1 for g in gaps if g > 1000), retrans=st1["total_retrans"] - st0["total_retrans"], retrans_events=retr, stalls=stalls, inside=inside, rtt_end_ms=st1["rtt_ms"],
    )
    return res


def summary(rows, fam):
    r = [x for x in rows if isinstance(x, dict) and x["fam"] == fam]
    if not r:
        return None
    pings = sum(x["pings"] for x in r)
    return {
        "conns": len(r), "pings": pings, "retrans_total": sum(x["retrans"] for x in r), "conns_with_retrans": sum(1 for x in r if x["retrans"]),
        "retrans_per_1000_pings": round(1000 * sum(x["retrans"] for x in r) / max(1, pings), 2),
        "ping_gt500_total": sum(x["ping_gt500"] for x in r), "ping_timeouts": sum(x.get("ping_timeouts", 0) for x in r), "ping_max_ms": max(x["ping_max"] for x in r),
        "gap_max_ms": max(x["gap_max_ms"] for x in r), "gaps_gt1s_total": sum(x["gaps_gt1s"] for x in r), "stalled_conns": sum(1 for x in r if x["stalls"]), "stall_times": sorted({s["end_utc"] for x in r for s in x["stalls"]}), "ping_p50_med": q([x["ping_p50"] for x in r], 0.5),
    }


async def main():
    sec, n, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    jobs = [one(fam, k, sec) for k in range(n) for fam in (socket.AF_INET6, socket.AF_INET)]
    rows = await asyncio.gather(*jobs, return_exceptions=True)
    rows = [r if isinstance(r, dict) else repr(r) for r in rows]
    json.dump({"t_end_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sec": sec, "n_per_family": n, "v6": summary(rows, "v6"), "v4": summary(rows, "v4"), "rows": rows}, open(out, "w"), indent=1)


asyncio.run(main())
