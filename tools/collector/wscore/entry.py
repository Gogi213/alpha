"""Проба входов (TK-532): одни и те же подписки через независимые входы биржи, мера — запись с лагом приёма rx − E больше LATE_MS.
Делает: run_entrance — один вход (имя, url, необязательно ip): соединения по шардам адаптера, каждая разобранная запись -> строка лога, поздние записи -> late-<вход>.jsonl, окна статистики -> stats-<вход>.jsonl.
Не пишет в боевой формат и не знает биржу (всё через адаптер: plan, url, subscribe_messages, parse); разбор лога и сравнение входов — `entrycmp.py`.
Лог записей `rec-<вход>.tsv.zst`: rx_ms \\t вид \\t символ \\t ключ(dedup_key) \\t E \\t T \\t цена \\t соединение — все записи как пришли, без снятия дублей.
"""
import asyncio
import json
import os
import time

from wscore.codec import dedup_key
from wscore.dial import dial, hostname, peer_ip
from wscore.fresh import GRACE_S, LATE_MS
from wscore.stats import percentile

STATS_PERIOD_S = 10
LOOP_TICK_S = 0.25  # тик сторожа цикла событий: опоздание тика отличает задержку у нас от задержки пути
PING_PERIOD_S = 15.0
PING_TIMEOUT_S = 10.0
STAGGER_S = 0.4  # пауза между открытиями соединений входа (лимит Bybit — 500 подключений / 5 мин на домен)


class Sink:
    """Приёмник записей входа: лог, поздние записи, окно статистики."""

    def __init__(self, root, name):
        import zstandard

        self.dir = root
        os.makedirs(root, exist_ok=True)
        self.name = name
        self.f = open(os.path.join(root, f"rec-{name}.tsv.zst"), "ab")
        self.c = zstandard.ZstdCompressor(level=1).compressobj()
        self.zstd = zstandard
        self.late_f = open(os.path.join(root, f"late-{name}.jsonl"), "a")
        self.stats_f = open(os.path.join(root, f"stats-{name}.jsonl"), "a")
        self.events_f = open(os.path.join(root, f"events-{name}.jsonl"), "a")
        self.last_flush = time.monotonic()
        self.win = self._new_window()
        self.loop_late = 0.0
        self.pings = []
        self.conns = 0

    @staticmethod
    def _new_window():
        return {"frames": 0, "recs": 0, "late": 0, "lag_max": None, "lags": []}

    def event(self, **kw):
        kw.update(ts_ms=int(time.time() * 1000), entrance=self.name)
        self.events_f.write(json.dumps(kw) + "\n")
        self.events_f.flush()

    def frame(self, conn, rx_ns, recs, settled=True):
        w = self.win
        w["frames"] += 1
        rms = rx_ns // 1_000_000
        out = []
        for rec in recs:
            kind, sym, E = rec[0], rec[1], rec[2]
            lag = rms - E
            w["recs"] += 1
            if w["recs"] % 16 == 0:
                w["lags"].append(lag)
            if w["lag_max"] is None or lag > w["lag_max"]:
                w["lag_max"] = lag
            key = dedup_key(rec)
            if settled and lag > LATE_MS:
                w["late"] += 1
                self.late_f.write(json.dumps({"rx_ms": rms, "kind": kind, "sym": sym, "key": key, "E": E, "lag_ms": lag, "conn": conn}) + "\n")
            T = rec[3] if kind in ("b", "t") else ""
            px = rec[5] if kind in ("b", "t") else rec[3]
            out.append(f"{rms}\t{kind}\t{sym}\t{key}\t{E}\t{T}\t{px}\t{conn}\n")
        if out:
            data = self.c.compress("".join(out).encode())
            if data:
                self.f.write(data)
        if time.monotonic() - self.last_flush >= 1.0:
            self.flush()

    def flush(self):
        self.f.write(self.c.flush(self.zstd.COMPRESSOBJ_FLUSH_BLOCK))
        self.f.flush()
        self.late_f.flush()
        self.last_flush = time.monotonic()

    def take_stats(self):
        w, self.win = self.win, self._new_window()
        line = {
            "ts_ms": int(time.time() * 1000), "entrance": self.name, "conns": self.conns, "frames": w["frames"], "recs": w["recs"], "late": w["late"],
            "lag_max_ms": w["lag_max"], "lag_p50_ms": percentile(w["lags"], 0.5), "loop_late_ms": round(self.loop_late * 1000), "ping_max_ms": round(max(self.pings), 1) if self.pings else None,
        }
        self.loop_late = 0.0
        self.pings = []
        self.stats_f.write(json.dumps(line) + "\n")
        self.stats_f.flush()

    def close(self):
        self.f.write(self.c.flush())
        for f in (self.f, self.late_f, self.stats_f, self.events_f):
            f.close()


async def _pinger(ws, sink, conn):
    while True:
        await asyncio.sleep(PING_PERIOD_S)
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(await ws.ping(), PING_TIMEOUT_S)
        except asyncio.TimeoutError:
            sink.event(ev="ping_timeout", conn=conn)
            continue
        sink.pings.append((time.monotonic() - t0) * 1000)


async def _conn(adapter, url, ip, conn, streams, sink, t_end):
    import websockets

    backoff = 1.0
    while time.time() < t_end:
        t0 = time.monotonic()
        pinger = None
        try:
            sock, rtt = await dial(url, None, ip)
            async with websockets.connect(url, sock=sock, server_hostname=hostname(url), max_size=None, ping_interval=None, compression=None, close_timeout=1) as ws:
                resp = getattr(ws, "response", None)
                hdr = resp.headers if resp is not None else getattr(ws, "response_headers", {})
                sink.conns += 1
                sink.event(ev="connected", conn=conn, ip=peer_ip(sock), rtt_ms=round(rtt), pop=hdr.get("x-amz-cf-pop"), server=hdr.get("server"), streams=len(streams))
                pinger = asyncio.create_task(_pinger(ws, sink, conn))
                try:
                    for msg in adapter.subscribe_messages(streams):
                        await ws.send(msg)
                        await asyncio.sleep(adapter.sub_period_s)
                    settle = time.monotonic() + GRACE_S
                    async for frame in ws:
                        rx = time.time_ns()
                        recs = adapter.parse(frame)
                        if recs:
                            sink.frame(conn, rx, recs, time.monotonic() >= settle)
                        elif recs is None and '"success"' in (frame if isinstance(frame, str) else frame.decode(errors="replace"))[:80]:
                            sink.event(ev="sub_response", conn=conn, frame=frame[:200])
                finally:
                    sink.conns -= 1
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — любой сбой: событие и переподключение
            sink.event(ev="error", conn=conn, err=repr(e)[:200])
        finally:
            if pinger is not None:
                pinger.cancel()
        backoff = 1.0 if time.monotonic() - t0 > 60 else min(backoff * 2, 30.0)
        await asyncio.sleep(backoff)


async def _watch(sink, t_end):
    prev, last_stats = time.monotonic(), time.monotonic()
    while time.time() < t_end:
        await asyncio.sleep(LOOP_TICK_S)
        now = time.monotonic()
        sink.loop_late = max(sink.loop_late, now - prev - LOOP_TICK_S)
        prev = now
        if now - last_stats >= STATS_PERIOD_S:
            sink.take_stats()
            last_stats = now


async def run_entrance(adapter, name, url, ip, shards, root, max_s):
    """Один вход: соединение на шард (shards = adapter.plan(...)), до max_s секунд; остановка — отмена задачи."""
    sink = Sink(root, name)
    t_end = time.time() + max_s
    sink.event(ev="start", url=url, ip=ip, conns=len(shards), max_s=max_s)
    tasks = []
    try:
        for i, (shard, _endpoint, streams) in enumerate(shards):
            tasks.append(asyncio.create_task(_conn(adapter, url, ip, shard, streams, sink, t_end)))
            await asyncio.sleep(STAGGER_S)
        await asyncio.gather(_watch(sink, t_end), *tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            t.cancel()
        sink.event(ev="stop")
        sink.close()
