#!/usr/bin/env python3
"""TK-527 (G-022, шаг 1): коллектор Binance USD-M по вебсокету.

Пишет сырые кадры bookTicker (эндпоинт /public) и aggTrade (эндпоинт /market) пула как пришли (В-172):
строка файла = `<local_ns> <кадр>\\n`, часовые файлы zstd `<root>/<день UTC>/<шард>-<сессия>-<ЧЧ>.jsonl.zst`.
Пул: TRADING USDT PERPETUAL, quoteVolume24h >= MIN_QUOTE_VOLUME, без TOP_EXCLUDE крупнейших по обороту (G-022).
Только Linux (srv). Запуск: binance_ws.py --root /data/bnws [--streams-per-conn N] [--procs]
"""
import argparse
import asyncio
import json
import multiprocessing as mp
import os
import sys
import time
import urllib.request

REST = "https://fapi.binance.com"
WS = "wss://fstream.binance.com"
MIN_QUOTE_VOLUME = 300_000.0
TOP_EXCLUDE = 10
KINDS = {"book": ("public", "bookTicker"), "trade": ("market", "aggTrade")}
SUB_BATCH = 100  # потоков в одном SUBSCRIBE
SUB_PERIOD_S = 0.2  # лимит 10 входящих сообщений/с на соединение -> 5/с
MAX_AGE_S = 23 * 3600  # соединение живёт 24 ч: новое открывается заранее, старое закрывается после перекрытия
OVERLAP_S = 20
STATS_PERIOD_S = 10
FLUSH_PERIOD_S = 10  # кадр на диске не реже 10 с
LAG_SAMPLE = 64  # разбирать каждый N-й кадр для отставания


def select_pool(symbols, tickers, min_qv=MIN_QUOTE_VOLUME, top_exclude=TOP_EXCLUDE):
    """symbols — exchangeInfo['symbols'], tickers — список ticker/24hr. Возвращает (пул, исключённые топ)."""
    qv = {}
    for t in tickers:
        try:
            v = float(t["quoteVolume"])
        except (KeyError, ValueError, TypeError):
            continue
        if v >= 0:
            qv[t["symbol"]] = v
    ok = [
        s["symbol"]
        for s in symbols
        if s.get("status") == "TRADING"
        and s.get("contractType") == "PERPETUAL"
        and s.get("quoteAsset") == "USDT"
        and s["symbol"] in qv
    ]
    ranked = sorted(ok, key=lambda x: (-qv[x], x))
    top = ranked[:top_exclude]
    pool = sorted(x for x in ranked[top_exclude:] if qv[x] >= min_qv)
    return pool, top


def shard_streams(pool, kind, per_conn):
    """Потоки вида kind, раскладка по соединениям по per_conn штук: [(шард, [stream,...]), ...]."""
    suffix = KINDS[kind][1]
    streams = [f"{s.lower()}@{suffix}" for s in pool]
    return [(f"{kind}{i // per_conn:02d}", streams[i : i + per_conn]) for i in range(0, len(streams), per_conn)]


def hour_path(root, shard, session, t_s):
    tm = time.gmtime(t_s)
    d = time.strftime("%Y-%m-%d", tm)
    return os.path.join(root, d, f"{shard}-{session}-{tm.tm_hour:02d}.jsonl.zst"), tm.tm_hour, d


def percentile(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


class HourWriter:
    def __init__(self, root, shard, session):
        import zstandard

        self.zstd = zstandard
        self.root, self.shard, self.session = root, shard, session
        self.f = None
        self.c = None
        self.hour = None
        self.last_flush = time.monotonic()
        self.bytes_in = 0

    def write(self, recv_ns, frame):
        now_s = recv_ns / 1e9
        path, hour, d = hour_path(self.root, self.shard, self.session, now_s)
        if (hour, d) != self.hour:
            self.close()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.f = open(path, "ab")
            self.c = self.zstd.ZstdCompressor(level=3).compressobj()
            self.hour = (hour, d)
        line = str(recv_ns).encode() + b" " + (frame if isinstance(frame, bytes) else frame.encode()) + b"\n"
        self.bytes_in += len(line)
        out = self.c.compress(line)
        if out:
            self.f.write(out)
        if time.monotonic() - self.last_flush >= FLUSH_PERIOD_S:
            self.flush()

    def flush(self):
        if self.f is not None:
            self.f.write(self.c.flush(self.zstd.COMPRESSOBJ_FLUSH_BLOCK))
            self.f.flush()
        self.last_flush = time.monotonic()

    def close(self):
        if self.f is not None:
            self.f.write(self.c.flush())
            self.f.close()
            self.f = self.c = None


class Shard:
    def __init__(self, root, name, endpoint, streams):
        self.root, self.name, self.endpoint, self.streams = root, name, endpoint, streams
        self.session_seq = 0
        self.stats_path = os.path.join(root, f"stats-{name}.jsonl")
        self.events_path = os.path.join(root, f"events-{name}.jsonl")
        self.msgs = 0
        self.bytes = 0
        self.max_gap = 0.0
        self.last_rx = None
        self.lags = []
        self.cpu0 = time.process_time()

    def event(self, **kw):
        kw.update(ts_ms=int(time.time() * 1000), shard=self.name)
        with open(self.events_path, "a") as f:
            f.write(json.dumps(kw) + "\n")

    async def session(self, seq):
        import websockets

        w = HourWriter(self.root, self.name, seq)
        url = f"{WS}/{self.endpoint}/stream"
        try:
            async with websockets.connect(url, max_size=None, ping_interval=None, compression=None) as ws:
                self.event(ev="connected", session=seq, url=url)
                for i in range(0, len(self.streams), SUB_BATCH):
                    await ws.send(json.dumps({"method": "SUBSCRIBE", "params": self.streams[i : i + SUB_BATCH], "id": i + 1}))
                    await asyncio.sleep(SUB_PERIOD_S)
                self.event(ev="subscribed", session=seq, streams=len(self.streams))
                n = 0
                async for frame in ws:
                    rx = time.time_ns()
                    w.write(rx, frame)
                    self.msgs += 1
                    self.bytes += len(frame)
                    mono = time.monotonic()
                    if self.last_rx is not None:
                        self.max_gap = max(self.max_gap, mono - self.last_rx)
                    self.last_rx = mono
                    n += 1
                    if n % LAG_SAMPLE == 0:
                        self.sample_lag(frame, rx)
        finally:
            w.close()

    def sample_lag(self, frame, rx_ns):
        try:
            d = json.loads(frame).get("data")
            if d and "E" in d:
                self.lags.append(rx_ns / 1e6 - d["E"])
        except (ValueError, AttributeError):
            pass

    async def run_session_forever(self, seq, stop_at):
        """Одна сессия с переподключением при сбое до stop_at (монотонное время), None — без предела."""
        backoff = 1.0
        while stop_at is None or time.monotonic() < stop_at:
            t0 = time.monotonic()
            try:
                await self.session(seq)
                self.event(ev="closed", session=seq)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — любой сбой сети: запись события и повтор
                self.event(ev="error", session=seq, err=repr(e)[:200])
            backoff = 1.0 if time.monotonic() - t0 > 60 else min(backoff * 2, 30.0)
            await asyncio.sleep(backoff)
            self.last_rx = None  # пауза переподключения — в разрыв считается событием, не max_gap

    async def stats_loop(self):
        t_prev = time.monotonic()
        while True:
            await asyncio.sleep(STATS_PERIOD_S)
            now = time.monotonic()
            cpu = time.process_time()
            line = {
                "ts_ms": int(time.time() * 1000),
                "msgs": self.msgs,
                "bytes": self.bytes,
                "max_gap_ms": round(self.max_gap * 1000),
                "lag_p50_ms": percentile(self.lags, 0.5),
                "lag_p99_ms": percentile(self.lags, 0.99),
                "cpu_frac": round((cpu - self.cpu0) / (now - t_prev), 3),
            }
            with open(self.stats_path, "a") as f:
                f.write(json.dumps(line) + "\n")
            self.msgs = self.bytes = 0
            self.max_gap = 0.0
            self.lags = []
            self.cpu0, t_prev = cpu, now

    async def run(self):
        stats = asyncio.create_task(self.stats_loop())
        seq = 1
        cur = asyncio.create_task(self.run_session_forever(seq, None))
        try:
            while True:
                await asyncio.sleep(MAX_AGE_S)
                # перекрытие: новая сессия работает вместе со старой OVERLAP_S, потом старая закрывается
                seq += 1
                nxt = asyncio.create_task(self.run_session_forever(seq, None))
                await asyncio.sleep(OVERLAP_S)
                cur.cancel()
                self.event(ev="rotated", new_session=seq)
                cur = nxt
        finally:
            stats.cancel()


def fetch_json(path):
    with urllib.request.urlopen(REST + path, timeout=30) as r:
        return json.load(r)


def shard_main(root, name, endpoint, streams):
    sh = Shard(root, name, endpoint, streams)
    asyncio.run(sh.run())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--streams-per-conn", type=int, default=256)
    ap.add_argument("--min-quote-volume", type=float, default=MIN_QUOTE_VOLUME)
    ap.add_argument("--top-exclude", type=int, default=TOP_EXCLUDE)
    ap.add_argument("--kinds", default="book,trade")
    ap.add_argument("--pool-only", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.root, exist_ok=True)
    ei = fetch_json("/fapi/v1/exchangeInfo")
    t24 = fetch_json("/fapi/v1/ticker/24hr")
    pool, top = select_pool(ei["symbols"], t24, a.min_quote_volume, a.top_exclude)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with open(os.path.join(a.root, f"pool-{day}.json"), "w") as f:
        json.dump({"ts_ms": int(time.time() * 1000), "min_quote_volume": a.min_quote_volume, "top_excluded": top, "pool": pool}, f)
    print(f"pool {len(pool)} top_excluded {top}", flush=True)
    if a.pool_only:
        return
    shards = []
    for kind in a.kinds.split(","):
        for name, streams in shard_streams(pool, kind, a.streams_per_conn):
            shards.append((name, KINDS[kind][0], streams))
    procs = [mp.Process(target=shard_main, args=(a.root, n, e, s), name=n) for n, e, s in shards]
    for p in procs:
        p.start()
    print(f"shards {len(procs)}: " + ", ".join(f"{n}={len(s)}" for n, _, s in shards), flush=True)
    for p in procs:
        p.join()


if __name__ == "__main__":
    sys.exit(main())
