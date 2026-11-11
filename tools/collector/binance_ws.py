#!/usr/bin/env python3
"""TK-527 (G-022, шаг 1): коллектор Binance USD-M по вебсокету.

Пишет bookTicker (эндпоинт /public), aggTrade и markPrice (эндпоинт /market) пула (L1 + сделки + mark/фандинг, без глубины, В-115).
Формат на диске (В-133, выбран замером — docs/findings/tk527-binance-ws-2026-11-11.md): текстовые строки с разделителем \\t,
числа биржи — строками как пришли (В-172), метка приёма local_ns на каждой записи (В-137), время и счётчики — разностями (точно, без потерь),
поток zstd, один файл на шард и сессию за сутки UTC: `<root>/<день UTC>/<шард>-<сессия>.tsv.zst`; закрытый день неизменяем, рядом `.ok`.
Записи (первый столбец — тип; `dns` — приём в нс минус приём предыдущей записи файла, первая запись — абсолютное значение):
  b dns lag ET sym du b B a A      bookTicker: lag = приём_мс − E, ET = E − T, du = u − u предыдущего кадра символа (первый — абсолютное)
  t dns lag ET sym da p q lf f m   aggTrade: da = a − (a предыдущего+1), lf = l − f, f = f − (l предыдущего+1), m = 0/1
  m dns lag sym p ap P i r T       markPriceUpdate (элемент массива !markPrice@arr@1s), lag = приём_мс − E
  g dns rtt_us                     пинг WS: время до pong
  c dns what detail                служебное: connected / subscribed / closed
  r dns <кадр>                     всё, что не разобрано точно (ответ на SUBSCRIBE, новые поля биржи): кадр как пришёл
Только Linux (srv). Запуск: binance_ws.py --root /data/bnws [--health /data/health/binance.json]
"""
import argparse
import asyncio
import json
import multiprocessing as mp
import os
import signal
import sys
import time
import urllib.request
from collections import deque

import orjson

REST = "https://fapi.binance.com"
WS = "wss://fstream.binance.com"
MIN_QUOTE_VOLUME = 300_000.0
TOP_EXCLUDE = 10
KINDS = {"book": ("public", "bookTicker"), "trade": ("market", "aggTrade"), "mark": ("market", "!markPrice@arr@1s")}
STREAMS_PER_CONN = 64  # замер TK-527: 256/128 потоков — хроническое отставание и разрывы, 64 — у 1 из 8
SUB_BATCH = 100  # потоков в одном SUBSCRIBE
SUB_PERIOD_S = 0.2  # лимит 10 входящих сообщений/с на соединение -> 5/с
MAX_AGE_S = 23 * 3600  # соединение живёт 24 ч: новое открывается заранее, старое закрывается после перекрытия
OVERLAP_S = 20
SWITCH_WAIT_S = 120  # смена пути без разрыва: ждём приёма новой сессии (окно проверки), затем перекрытие OVERLAP_S
STATS_PERIOD_S = 10
FLUSH_PERIOD_S = 1.0  # кадр на диске не реже 10 с (A8); 1 с — лаг отдачи ≈ 0,5 с, размер +1 % (замер)
ZSTD_LEVEL = 1  # замер: на этих данных уровень 1 и меньше, и быстрее уровня 3
LAG_SAMPLE = 64  # каждый N-й кадр идёт в выборки лагов
LAG_LIMIT_MS = 2000  # назначаемое (подтвердить): p50 отставания выше — соединение пересоздаётся (замер: норма ≈ RTT/2 ≈ 230 мс)
LAG_BAD_WINDOWS = 2  # подряд окон STATS_PERIOD_S выше LAG_LIMIT_MS
LAG_SKIP_WINDOWS = 2  # окна после подключения не считаются: снапшот подписки несёт старый E
PROBE_S = 15  # выбор пути: после подписки окно проверки лага; плохой путь (потери Токио->srv) отбрасывается до записи в «живые»
PROBE_SKIP_S = 4  # снапшот подписки несёт старый E
PROBE_P50_MS = 500  # назначаемое: замер 11.11 (24 соед.) — чистый путь p50 250–265, p99 636–945; плохой p50 2,6–20 с
PROBE_P99_MS = 1500
PROBE_MIN_SAMPLES = 5
PROBE_MAX_REJECTS = 8  # подряд отбракованных путей; дальше соединение принимается (сторож лага остаётся)
PING_PERIOD_S = 5.0
PING_TIMEOUT_S = 10.0  # документация Binance: ответ на ping ≤ 10 мин; здесь — порог «таймаут» страницы здоровья, назначаемое
GAP_TIMEOUT_S = 3.0  # пауза без кадров на соединении; замер: макс. окно 1,55 с (book) / 1,22 с (trade) за 984 окна — порог ≈ 2× (назначаемое)
WINDOW_S = 60  # окно лагов и «символ с данными за минуту»
EXPECT_BOOK = {"e", "u", "s", "ps", "b", "B", "a", "A", "T", "E", "st"}
EXPECT_TRADE = {"e", "E", "a", "s", "p", "q", "nq", "f", "l", "T", "m", "st"}
EXPECT_MARK = {"e", "E", "s", "p", "ap", "P", "i", "r", "T", "st"}


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
    """Потоки вида kind, раскладка по соединениям по per_conn штук: [(шард, [stream,...]), ...]. mark — один общий поток."""
    suffix = KINDS[kind][1]
    if kind == "mark":
        return [("mark00", [suffix])]
    streams = [f"{s.lower()}@{suffix}" for s in pool]
    return [(f"{kind}{i // per_conn:02d}", streams[i : i + per_conn]) for i in range(0, len(streams), per_conn)]


def day_path(root, shard, session, t_s):
    d = time.strftime("%Y-%m-%d", time.gmtime(t_s))
    return os.path.join(root, d, f"{shard}-{session}.tsv.zst"), d


def frame_lag(frame, rx_ns):
    """Лаг прихода кадра, мс (время приёма − E биржи); None — в кадре нет E."""
    try:
        d = orjson.loads(frame).get("data")
        if type(d) is list:
            d = d[0] if d else None
        if d and "E" in d:
            return rx_ns / 1e6 - d["E"]
    except (ValueError, AttributeError, TypeError):
        pass
    return None


def path_verdict(lags):
    """Чистый ли путь по лагам прихода окна проверки: None — мало выборок (не отбраковываем)."""
    if len(lags) < PROBE_MIN_SAMPLES:
        return True
    return percentile(lags, 0.5) <= PROBE_P50_MS and percentile(lags, 0.99) <= PROBE_P99_MS


def percentile(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


class Encoder:
    """Кадр -> строки формата (см. шапку). Состояние (предыдущие u/a/l, приём) — на файл; новый файл — новый Encoder."""

    def __init__(self):
        self.prev_ns = 0
        self.last_u = {}
        self.last_a = {}
        self.last_l = {}

    def _dns(self, rx_ns):
        d = rx_ns - self.prev_ns
        self.prev_ns = rx_ns
        return d

    def raw(self, rx_ns, frame):
        f = frame if isinstance(frame, str) else frame.decode()
        return [f"r\t{self._dns(rx_ns)}\t{f}\n"]

    def frame(self, rx_ns, frame):
        try:
            j = orjson.loads(frame)
            d = j["data"]
            rms = rx_ns // 1_000_000
            if type(d) is dict:
                k = d.keys()
                if k == EXPECT_BOOK and d["ps"] == d["s"] and d["st"] == 1:
                    s = d["s"]
                    du = d["u"] - self.last_u.get(s, 0)
                    self.last_u[s] = d["u"]
                    return [f"b\t{self._dns(rx_ns)}\t{rms - d['E']}\t{d['E'] - d['T']}\t{s}\t{du}\t{d['b']}\t{d['B']}\t{d['a']}\t{d['A']}\n"]
                if k == EXPECT_TRADE and d["nq"] == d["q"] and d["st"] == 1 and type(d["m"]) is bool:
                    s = d["s"]
                    da = d["a"] - self.last_a.get(s, 0)
                    df = d["f"] - self.last_l.get(s, 0)
                    self.last_a[s] = d["a"] + 1
                    self.last_l[s] = d["l"] + 1
                    return [f"t\t{self._dns(rx_ns)}\t{rms - d['E']}\t{d['E'] - d['T']}\t{s}\t{da}\t{d['p']}\t{d['q']}\t{d['l'] - d['f']}\t{df}\t{int(d['m'])}\n"]
            elif type(d) is list and d and all(type(x) is dict and x.keys() == EXPECT_MARK and x["e"] == "markPriceUpdate" and x["st"] == 1 for x in d):
                out = []
                for x in d:
                    out.append(f"m\t{self._dns(rx_ns)}\t{rms - x['E']}\t{x['s']}\t{x['p']}\t{x['ap']}\t{x['P']}\t{x['i']}\t{x['r']}\t{x['T']}\n")
                return out
        except (ValueError, KeyError, TypeError):
            pass
        return self.raw(rx_ns, frame)

    def ping(self, rx_ns, rtt_us):
        return [f"g\t{self._dns(rx_ns)}\t{rtt_us}\n"]

    def control(self, rx_ns, what, detail=""):
        return [f"c\t{self._dns(rx_ns)}\t{what}\t{detail}\n"]


def decode(text):
    """Строки формата -> записи (dict); обратное к Encoder (проверка без потерь, читатель П-40 делает то же)."""
    prev_ns, last_u, last_a, last_l = 0, {}, {}, {}
    for ln in text.split("\n"):
        if not ln:
            continue
        f = ln.split("\t", 2) if ln[0] == "r" else ln.split("\t")
        prev_ns += int(f[1])
        rx = prev_ns
        rms = rx // 1_000_000
        t = f[0]
        if t == "b":
            E = rms - int(f[2])
            u = last_u.get(f[4], 0) + int(f[5])
            last_u[f[4]] = u
            yield {"t": "b", "rx_ns": rx, "E": E, "T": E - int(f[3]), "s": f[4], "u": u, "b": f[6], "B": f[7], "a": f[8], "A": f[9]}
        elif t == "t":
            E = rms - int(f[2])
            a = last_a.get(f[4], 0) + int(f[5])
            first = last_l.get(f[4], 0) + int(f[9])
            last_a[f[4]] = a + 1
            last_l[f[4]] = first + int(f[8]) + 1
            yield {"t": "t", "rx_ns": rx, "E": E, "T": E - int(f[3]), "s": f[4], "a": a, "p": f[6], "q": f[7], "f": first, "l": first + int(f[8]), "m": f[10] == "1"}
        elif t == "m":
            yield {"t": "m", "rx_ns": rx, "E": rms - int(f[2]), "s": f[3], "p": f[4], "ap": f[5], "P": f[6], "i": f[7], "r": f[8], "T": int(f[9])}
        elif t == "g":
            yield {"t": "g", "rx_ns": rx, "rtt_us": int(f[2])}
        elif t == "c":
            yield {"t": "c", "rx_ns": rx, "what": f[2], "detail": f[3] if len(f) > 3 else ""}
        else:
            yield {"t": "r", "rx_ns": rx, "frame": f[2]}


class DayWriter:
    """Файл шарда за сутки UTC; смена суток — закрыть, `.ok` (число записей), новый файл с нуля (Encoder сбрасывается)."""

    def __init__(self, root, shard, session):
        import zstandard

        self.zstd = zstandard
        self.root, self.shard, self.session = root, shard, session
        self.f = None
        self.path = None
        self.day = None
        self.enc = None
        self.rows = 0
        self.last_flush = time.monotonic()
        self.samples = []  # recv_ns выбранных кадров, ещё не сброшенных на диск
        self.out_lags = deque()  # (monotonic, мс приём -> диск)
        self.last_write_ms = 0

    def _open(self, rx_ns):
        self.close()
        path, day = day_path(self.root, self.shard, self.session, rx_ns / 1e9)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, "ab")
        self.c = self.zstd.ZstdCompressor(level=ZSTD_LEVEL).compressobj()
        self.path, self.day, self.enc, self.rows = path, day, Encoder(), 0

    def write(self, rx_ns, make, sample=False):
        """make(enc, rx_ns) -> список строк. Метка приёма — rx_ns (ставится до разбора)."""
        if day_path(self.root, self.shard, self.session, rx_ns / 1e9)[1] != self.day:
            self._open(rx_ns)
        lines = make(self.enc, rx_ns)
        self.rows += len(lines)
        out = self.c.compress("".join(lines).encode())
        if out:
            self.f.write(out)
        if sample:
            self.samples.append(rx_ns)
        if time.monotonic() - self.last_flush >= FLUSH_PERIOD_S:
            self.flush()

    def flush(self):
        now = time.monotonic()
        if self.f is not None:
            self.f.write(self.c.flush(self.zstd.COMPRESSOBJ_FLUSH_BLOCK))
            self.f.flush()
            t_ns = time.time_ns()
            for rx in self.samples:
                self.out_lags.append((now, (t_ns - rx) / 1e6))
            self.samples.clear()
            self.last_write_ms = t_ns // 1_000_000
        while self.out_lags and now - self.out_lags[0][0] > WINDOW_S:
            self.out_lags.popleft()
        self.last_flush = now

    def close(self):
        if self.f is not None:
            self.f.write(self.c.flush())
            self.f.close()
            with open(self.path + ".ok", "w") as ok:
                ok.write(f"{self.rows}\n")
            self.f = None


class Shard:
    def __init__(self, root, name, endpoint, streams):
        self.root, self.name, self.endpoint, self.streams = root, name, endpoint, streams
        self.stats_path = os.path.join(root, f"stats-{name}.jsonl")
        self.events_path = os.path.join(root, f"events-{name}.jsonl")
        self.frag_path = os.path.join(root, "frag", f"{name}.json")
        self.started_ms = int(time.time() * 1000)
        self.msgs = 0
        self.bytes = 0
        self.max_gap = 0.0
        self.last_rx = None
        self.last_frame_ms = 0
        self.lags = {}  # сессия -> лаги окна STATS_PERIOD_S для сторожа
        self.in_lags = deque()  # (monotonic, лаг прихода мс) за WINDOW_S
        self.pings = deque()  # (monotonic, rtt мс)
        self.sym_seen = {}  # символ -> monotonic последнего кадра
        self.timeouts = deque()  # ts_ms
        self.reconnects = deque()  # ts_ms
        self.in_gap = False
        self.connected = 0
        self.cpu0 = time.process_time()
        self.lag_limit_ms = LAG_LIMIT_MS
        self.age = {}  # сессия -> окон с подключения
        self.lag_bad = {}  # сессия -> подряд окон с лагом выше LAG_LIMIT_MS
        self.degraded = set()  # сессии, чей путь испортился посреди жизни: run() открывает замену до закрытия
        self.path_rejects = 0
        self.writers = {}  # сессия -> DayWriter (для lag_out/last_write)

    def event(self, **kw):
        kw.update(ts_ms=int(time.time() * 1000), shard=self.name)
        with open(self.events_path, "a") as f:
            f.write(json.dumps(kw) + "\n")

    def timeout(self, what, session):
        self.timeouts.append(int(time.time() * 1000))
        self.event(ev="timeout", what=what, session=session)

    async def pinger(self, ws, w, seq):
        while True:
            await asyncio.sleep(PING_PERIOD_S)
            t0 = time.monotonic()
            try:
                waiter = await ws.ping()
                await asyncio.wait_for(waiter, PING_TIMEOUT_S)
            except asyncio.TimeoutError:
                self.timeout("ping", seq)
                # без ответа на ping соединение мертво (замер ss: исходящий сегмент в бэкоффе RTO до 120 с) — обрыв сразу, без рукопожатия закрытия
                self.event(ev="ping_reconnect", session=seq)
                ws.transport.abort()
                return
            rtt = time.monotonic() - t0
            self.pings.append((time.monotonic(), rtt * 1000))
            w.write(time.time_ns(), lambda e, rx: e.ping(rx, round(rtt * 1e6)))

    async def gap_watch(self, seq):
        """Пауза без кадров на соединении дольше GAP_TIMEOUT_S — один таймаут на паузу."""
        while True:
            await asyncio.sleep(0.5)
            if self.last_rx is None:
                continue
            gap = time.monotonic() - self.last_rx
            if gap > GAP_TIMEOUT_S and not self.in_gap:
                self.in_gap = True
                self.timeout("gap", seq)
            elif gap <= GAP_TIMEOUT_S:
                self.in_gap = False

    async def session(self, seq, probe=False, accepted=None):
        """Сессия до закрытия; True — путь отбракован окном проверки (probe), соединение закрыто до приёма в «живые». accepted — Event: путь принят."""
        import websockets

        w = DayWriter(self.root, self.name, seq)
        self.writers[seq] = w
        url = f"{WS}/{self.endpoint}/stream"
        helpers = []
        try:
            async with websockets.connect(url, max_size=None, ping_interval=None, compression=None, close_timeout=1) as ws:
                self.event(ev="connected", session=seq, url=url)
                self.connected += 1
                w.write(time.time_ns(), lambda e, rx: e.control(rx, "connected", url))
                self.lags[seq], self.age[seq], self.lag_bad[seq] = [], 0, 0
                helpers = [asyncio.create_task(self.pinger(ws, w, seq)), asyncio.create_task(self.gap_watch(seq))]
                for i in range(0, len(self.streams), SUB_BATCH):
                    await ws.send(json.dumps({"method": "SUBSCRIBE", "params": self.streams[i : i + SUB_BATCH], "id": i + 1}))
                    await asyncio.sleep(SUB_PERIOD_S)
                self.event(ev="subscribed", session=seq, streams=len(self.streams))
                w.write(time.time_ns(), lambda e, rx: e.control(rx, "subscribed", str(len(self.streams))))
                n = 0
                t_sub = time.monotonic()
                if not probe and accepted is not None:
                    accepted.set()
                probe_lags = []
                async for frame in ws:
                    rx = time.time_ns()
                    sample = n % LAG_SAMPLE == 0
                    w.write(rx, lambda e, r: e.frame(r, frame), sample)
                    self.msgs += 1
                    self.bytes += len(frame)
                    mono = time.monotonic()
                    if self.last_rx is not None:
                        self.max_gap = max(self.max_gap, mono - self.last_rx)
                    self.last_rx = mono
                    self.last_frame_ms = rx // 1_000_000
                    n += 1
                    if frame.startswith('{"stream":"') and frame[11] != "!":
                        self.sym_seen[frame[11 : frame.find("@", 11)]] = mono
                    if sample:
                        self.sample_lag(frame, rx, mono, seq)
                    if probe:
                        if mono - t_sub >= PROBE_SKIP_S:
                            lag = frame_lag(frame, rx)
                            if lag is not None:
                                probe_lags.append(lag)
                        if mono - t_sub >= PROBE_S:
                            probe = False
                            if not path_verdict(probe_lags):
                                self.event(ev="path_rejected", session=seq, n=len(probe_lags), p50_ms=round(percentile(probe_lags, 0.5)), p99_ms=round(percentile(probe_lags, 0.99)))
                                return True
                            self.event(ev="path_ok", session=seq, n=len(probe_lags), p50_ms=round(percentile(probe_lags, 0.5)), p99_ms=round(percentile(probe_lags, 0.99)))
                            if accepted is not None:
                                accepted.set()
        finally:
            for h in helpers:
                h.cancel()
            self.connected = max(0, self.connected - 1)
            if w.f is not None:
                w.write(time.time_ns(), lambda e, rx: e.control(rx, "closed"))
            w.close()
            self.writers.pop(seq, None)
            for d in (self.lags, self.age, self.lag_bad):
                d.pop(seq, None)
            self.degraded.discard(seq)

    def sample_lag(self, frame, rx_ns, mono, seq):
        lag = frame_lag(frame, rx_ns)
        if lag is not None:
            self.lags.setdefault(seq, []).append(lag)
            self.in_lags.append((mono, lag))

    async def run_session_forever(self, seq, stop_at, accepted=None):
        """Одна сессия с переподключением при сбое до stop_at (монотонное время), None — без предела."""
        backoff = 1.0
        rejects = 0
        while stop_at is None or time.monotonic() < stop_at:
            t0 = time.monotonic()
            try:
                if await self.session(seq, probe=rejects < PROBE_MAX_REJECTS, accepted=accepted):
                    rejects += 1
                    self.path_rejects += 1
                    await asyncio.sleep(0.5)
                    self.last_rx = None
                    continue
                rejects = 0
                self.event(ev="closed", session=seq)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — любой сбой сети: запись события и повтор
                self.event(ev="error", session=seq, err=repr(e)[:200])
            self.reconnects.append(int(time.time() * 1000))
            backoff = 1.0 if time.monotonic() - t0 > 60 else min(backoff * 2, 30.0)
            await asyncio.sleep(backoff)
            self.last_rx = None  # пауза переподключения — в разрыв считается событием, не max_gap

    def trim(self, now):
        for dq in (self.in_lags, self.pings):
            while dq and now - dq[0][0] > WINDOW_S:
                dq.popleft()
        cut = int(time.time() * 1000) - 24 * 3600 * 1000
        for dq in (self.timeouts, self.reconnects):
            while dq and dq[0] < cut:
                dq.popleft()

    def fragment(self):
        now = time.monotonic()
        self.trim(now)
        out_lags = [v for w in self.writers.values() for _, v in w.out_lags]
        return {
            "shard": self.name, "ts_ms": int(time.time() * 1000), "started_ms": self.started_ms,
            "with_data": sorted(s for s, t in self.sym_seen.items() if now - t <= WINDOW_S),
            "connected": self.connected, "ping_ms": [round(v, 1) for _, v in self.pings],
            "lag_in": [round(v, 1) for _, v in self.in_lags], "lag_out": [round(v, 1) for v in out_lags],
            "timeouts": list(self.timeouts), "reconnects": list(self.reconnects),
            "last_frame_ms": self.last_frame_ms, "last_write_ms": max((w.last_write_ms for w in self.writers.values()), default=0),
        }

    def write_fragment(self):
        os.makedirs(os.path.dirname(self.frag_path), exist_ok=True)
        tmp = self.frag_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.fragment(), f)
        os.replace(tmp, self.frag_path)

    def judge_lags(self):
        """Конец окна: сессия с p50 лага выше LAG_LIMIT_MS LAG_BAD_WINDOWS окон подряд — в degraded (путь испортился), выборки окна сбрасываются."""
        for seq, xs in list(self.lags.items()):
            p50 = percentile(xs, 0.5)
            self.age[seq] = self.age.get(seq, 0) + 1
            if self.age[seq] > LAG_SKIP_WINDOWS and p50 is not None and p50 > self.lag_limit_ms:
                self.lag_bad[seq] = self.lag_bad.get(seq, 0) + 1
            else:
                self.lag_bad[seq] = 0
            if self.lag_bad[seq] >= LAG_BAD_WINDOWS:
                self.degraded.add(seq)
            self.lags[seq] = []

    async def stats_loop(self):
        t_prev = time.monotonic()
        while True:
            await asyncio.sleep(STATS_PERIOD_S)
            now = time.monotonic()
            cpu = time.process_time()
            all_lags = [v for xs in self.lags.values() for v in xs]
            line = {
                "ts_ms": int(time.time() * 1000),
                "msgs": self.msgs,
                "bytes": self.bytes,
                "max_gap_ms": round(self.max_gap * 1000),
                "lag_p50_ms": percentile(all_lags, 0.5),
                "lag_p99_ms": percentile(all_lags, 0.99),
                "cpu_frac": round((cpu - self.cpu0) / (now - t_prev), 3),
            }
            with open(self.stats_path, "a") as f:
                f.write(json.dumps(line) + "\n")
            self.judge_lags()
            self.msgs = self.bytes = 0
            self.max_gap = 0.0
            self.cpu0, t_prev = cpu, now
            self.write_fragment()

    async def run(self):
        """Смена сессии без разрыва: замена открывается и принимается (окно проверки пути) до закрытия старой; на перекрытии оба файла пишут кадры (читатель убирает дубли по u/a). Причины: возраст MAX_AGE_S (rotated) или путь испортился (path_switch)."""
        stats = asyncio.create_task(self.stats_loop())
        seq = cur_seq = int(time.time())  # номер сессии = время старта процесса (с): файл прежнего запуска не дописывается (E-023: старый .ok и обрыв строки)
        cur = asyncio.create_task(self.run_session_forever(seq, None))
        born = time.monotonic()
        try:
            while True:
                await asyncio.sleep(1.0)
                bad = cur_seq in self.degraded
                if not bad and time.monotonic() - born < MAX_AGE_S:
                    continue
                seq += 1
                accepted = asyncio.Event()
                nxt = asyncio.create_task(self.run_session_forever(seq, None, accepted))
                try:
                    await asyncio.wait_for(accepted.wait(), SWITCH_WAIT_S)
                except asyncio.TimeoutError:
                    self.event(ev="switch_timeout", session=seq)
                await asyncio.sleep(OVERLAP_S)
                cur.cancel()
                self.event(ev="path_switch" if bad else "rotated", old_session=cur_seq, new_session=seq)
                cur, cur_seq, born = nxt, seq, time.monotonic()
        finally:
            stats.cancel()


def fetch_json(path):
    with urllib.request.urlopen(REST + path, timeout=30) as r:
        return json.load(r)


def shard_main(root, name, endpoint, streams):
    sh = Shard(root, name, endpoint, streams)

    async def amain():
        task = asyncio.current_task()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)  # остановка: закрыть файлы суток
        try:
            await sh.run()
        except asyncio.CancelledError:
            sh.event(ev="stopped")

    asyncio.run(amain())


def aggregate(frags, now_ms, pool):
    """Фрагменты шардов -> health.json (схема 1, docs/findings/collector-health-format-2026-11-11.md)."""

    def pct(xs):
        return {"p50": percentile(xs, 0.5), "p99": percentile(xs, 0.99), "window_s": WINDOW_S}

    pings = [v for f in frags for v in f["ping_ms"]]
    with_data = {s for f in frags for s in f["with_data"]} & {x.lower() for x in pool}
    hour, day = now_ms - 3600_000, now_ms - 24 * 3600_000
    tos = [t for f in frags for t in f["timeouts"]]
    rcs = [t for f in frags for t in f["reconnects"]]
    return {
        "schema": 1, "exchange": "binance", "ts_ms": now_ms,
        "started_ms": min((f["started_ms"] for f in frags), default=now_ms),
        "symbols": {"subscribed": len(pool), "with_data_1m": len(with_data)},
        "ws": {
            "connections": sum(f["connected"] for f in frags),
            "ping_ms": {"p50": percentile(pings, 0.5), "max": max(pings) if pings else None},
            "timeouts_1h": sum(t > hour for t in tos), "timeouts_24h": sum(t > day for t in tos),
            "reconnects_1h": sum(t > hour for t in rcs), "reconnects_24h": sum(t > day for t in rcs),
        },
        "lag_in_ms": pct([v for f in frags for v in f["lag_in"]]),
        "lag_out_ms": pct([v for f in frags for v in f["lag_out"]]),
        "last_frame_ms": max((f["last_frame_ms"] for f in frags), default=0),
        "last_write_ms": max((f["last_write_ms"] for f in frags), default=0),
    }


def write_health(root, health_path, pool):
    frags = []
    fd = os.path.join(root, "frag")
    now_ms = int(time.time() * 1000)
    for n in sorted(os.listdir(fd)) if os.path.isdir(fd) else []:
        if n.endswith(".json"):
            try:
                with open(os.path.join(fd, n)) as f:
                    fr = json.load(f)
            except (OSError, ValueError):
                continue
            if now_ms - fr["ts_ms"] < 3 * STATS_PERIOD_S * 1000:  # шард молчит — не считается живым
                frags.append(fr)
    os.makedirs(os.path.dirname(health_path), exist_ok=True)
    tmp = health_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(aggregate(frags, now_ms, pool), f)
    os.replace(tmp, health_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--health", default="/data/health/binance.json")
    ap.add_argument("--streams-per-conn", type=int, default=STREAMS_PER_CONN)
    ap.add_argument("--min-quote-volume", type=float, default=MIN_QUOTE_VOLUME)
    ap.add_argument("--top-exclude", type=int, default=TOP_EXCLUDE)
    ap.add_argument("--kinds", default="book,trade,mark")
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
    signal.signal(signal.SIGTERM, lambda *_: [p.terminate() for p in procs])  # дети закрывают файлы по SIGTERM
    while any(p.is_alive() for p in procs):
        time.sleep(STATS_PERIOD_S)
        write_health(a.root, a.health, pool)
    for p in procs:
        p.join()


if __name__ == "__main__":
    sys.exit(main())
