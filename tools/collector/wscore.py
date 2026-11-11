#!/usr/bin/env python3
"""Общее ядро WS-коллекторов G-022 (В-133, В-142): не знает биржу. Биржа — адаптер (`<биржа>_ws.py`, класс Adapter, контракт — внизу шапки).

Ядро: соединение + пинг + выбор и смена пути без разрыва, кодек и формат записи, суточная запись (один писатель на шард, дубли снимает писатель),
health-фрагменты и сводка, запуск шардов процессами. Формат на диске (В-133, выбран замером — docs/findings/tk527-binance-ws-2026-11-11.md): текстовые строки
с разделителем \\t, числа биржи — строками как пришли (В-172), метка приёма на каждой записи (В-137), время и счётчики — разностями (точно, без потерь), поток zstd,
один файл на шард за сутки UTC: `<root>/<день UTC>/<шард>-<запуск>.tsv.zst` (запуск = время старта процесса, с); закрытый день неизменяем, рядом `.ok`.
Записи (первый столбец — тип; `dns` — приём в нс минус приём предыдущей записи файла, первая запись — абсолютное значение):
  b dns lag ET sym du b B a A         L1: lag = приём_мс − E, ET = E − T, du = u − u предыдущей записи символа (первая в файле — абсолютное)
  t dns lag ET sym da p q lf f m nq   сделка: da = a − (a предыдущей+1), lf = l − f, f = f − (l предыдущей+1), m = 0/1, nq — только если ≠ q (иначе пусто)
  m dns lag sym p ap P i r T          mark/фандинг: p mark, i индекс, r ставка, T следующее начисление; ap, P — что есть у биржи, иначе пусто
  g dns rtt_us                        пинг WS: время до pong
  c dns what detail                   служебное: connected / subscribed / closed (detail — сессия и адрес)
  r dns <кадр>                        всё, что адаптер не разобрал точно (ответ на SUBSCRIBE, новые поля биржи): кадр как пришёл
Дубли: на смене пути два соединения шарда несут одни кадры; писатель пропускает запись, у которой ключ (b — u, t — a, m — E) не больше уже записанного по символу.
Поток записей символа поэтому строго упорядочен и без повторов; читателю дедуп не нужен.

Контракт адаптера (Adapter):
  name; health_default (путь health.json); streams_per_conn; min_quote_volume; top_exclude; kinds (имена видов в порядке запуска)
  load_pool(min_qv, top_exclude) -> (пул, исключённые)         REST биржи + правило пула
  plan(pool, kinds, per_conn) -> [(шард, endpoint, [stream])]  раскладка по соединениям
  url(endpoint) -> str; subscribe_messages(streams) -> [str]; sub_period_s (пауза между сообщениями подписки — лимит входящих сообщений биржи)
  parse(frame) -> [запись] | None   запись — кортеж ('b', s, E, T, u, bid, bsz, ask, asz) | ('t', s, E, T, a, p, q, f, l, m, nq) | ('m', s, E, p, ap, P, i, r, T)
  symbol(frame) -> str | None (ключ символа для «с данными за минуту», нижний регистр); lag_ms(frame, rx_ns) -> float | None (приём − время биржи)
Только Linux (srv).
"""
import argparse
import asyncio
import json
import multiprocessing as mp
import os
import signal
import time
from collections import deque

MAX_AGE_S = 23 * 3600  # соединение живёт 24 ч: новое открывается заранее, старое закрывается после перекрытия
OVERLAP_S = 20
SWITCH_WAIT_S = 120  # смена пути без разрыва: ждём приёма новой сессии (окно проверки), затем перекрытие OVERLAP_S
STATS_PERIOD_S = 10
FLUSH_PERIOD_S = 1.0  # кадр на диске не реже 10 с (A8); 1 с — лаг отдачи ≈ 0,5 с, размер +1 % (замер)
ZSTD_LEVEL = 1  # замер: на этих данных уровень 1 и меньше, и быстрее уровня 3
LAG_SAMPLE = 64  # каждый N-й кадр идёт в выборки лагов
LAG_LIMIT_MS = 2000  # назначаемое (подтвердить): p50 отставания выше — путь испортился (замер: норма ≈ RTT/2 ≈ 230 мс)
LAG_BAD_WINDOWS = 2  # подряд окон STATS_PERIOD_S выше LAG_LIMIT_MS
LAG_SKIP_WINDOWS = 2  # окна после подключения не считаются: снапшот подписки несёт старый E
PROBE_S = 15  # выбор пути: после подписки окно проверки лага; плохой путь (потери) отбрасывается до записи в «живые»
PROBE_SKIP_S = 4  # снапшот подписки несёт старый E
PROBE_P50_MS = 350  # назначаемое: замер 11.11 (73 принятых пути): 71 с p50 166–348 мс, два граничных 467–468 (один испортился через 17 с); плохой p50 2,6–20 с
PROBE_P99_MS = 1500
PROBE_MIN_SAMPLES = 5
PROBE_MAX_REJECTS = 8  # подряд отбракованных путей; дальше соединение принимается (сторож лага остаётся)
PING_PERIOD_S = 5.0
PING_TIMEOUT_S = 10.0  # здесь — порог «таймаут» страницы здоровья, назначаемое
GAP_TIMEOUT_S = 3.0  # пауза без кадров на соединении; замер: макс. окно 1,55 с (book) / 1,22 с (trade) за 984 окна — порог ≈ 2× (назначаемое)
WINDOW_S = 60  # окно лагов и «символ с данными за минуту»


def percentile(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def path_verdict(lags):
    """Чистый ли путь по лагам прихода окна проверки: мало выборок — не отбраковываем."""
    if len(lags) < PROBE_MIN_SAMPLES:
        return True
    return percentile(lags, 0.5) <= PROBE_P50_MS and percentile(lags, 0.99) <= PROBE_P99_MS


def day_of(t_s):
    return time.strftime("%Y-%m-%d", time.gmtime(t_s))


# --- кодек ---------------------------------------------------------------------------------------------------------------------------

class Encoder:
    """Запись -> строка формата. Состояние (предыдущие u/a/l, приём) — на файл; новый файл — новый Encoder."""

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
        return f"r\t{self._dns(rx_ns)}\t{f}\n"

    def record(self, rx_ns, rec):
        rms = rx_ns // 1_000_000
        k = rec[0]
        if k == "b":
            _, s, E, T, u, bid, bsz, ask, asz = rec
            du = u - self.last_u.get(s, 0)
            self.last_u[s] = u
            return f"b\t{self._dns(rx_ns)}\t{rms - E}\t{E - T}\t{s}\t{du}\t{bid}\t{bsz}\t{ask}\t{asz}\n"
        if k == "t":
            _, s, E, T, a, p, q, f, l, m, nq = rec
            da = a - self.last_a.get(s, 0)
            df = f - self.last_l.get(s, 0)
            self.last_a[s] = a + 1
            self.last_l[s] = l + 1
            return f"t\t{self._dns(rx_ns)}\t{rms - E}\t{E - T}\t{s}\t{da}\t{p}\t{q}\t{l - f}\t{df}\t{int(m)}\t{'' if nq == q else nq}\n"
        if k == "m":
            _, s, E, p, ap, P, i, r, T = rec
            return f"m\t{self._dns(rx_ns)}\t{rms - E}\t{s}\t{p}\t{ap}\t{P}\t{i}\t{r}\t{T}\n"
        raise ValueError(k)

    def ping(self, rx_ns, rtt_us):
        return f"g\t{self._dns(rx_ns)}\t{rtt_us}\n"

    def control(self, rx_ns, what, detail=""):
        return f"c\t{self._dns(rx_ns)}\t{what}\t{detail}\n"


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
            yield {"t": "t", "rx_ns": rx, "E": E, "T": E - int(f[3]), "s": f[4], "a": a, "p": f[6], "q": f[7], "f": first, "l": first + int(f[8]), "m": f[10] == "1", "nq": f[11] or f[7]}
        elif t == "m":
            yield {"t": "m", "rx_ns": rx, "E": rms - int(f[2]), "s": f[3], "p": f[4], "ap": f[5], "P": f[6], "i": f[7], "r": f[8], "T": int(f[9])}
        elif t == "g":
            yield {"t": "g", "rx_ns": rx, "rtt_us": int(f[2])}
        elif t == "c":
            yield {"t": "c", "rx_ns": rx, "what": f[2], "detail": f[3] if len(f) > 3 else ""}
        else:
            yield {"t": "r", "rx_ns": rx, "frame": f[2]}


def dedup_key(rec):
    """Ключ порядка записи символа: b — u, t — a, m — E (у каждой биржи по символу растёт)."""
    k = rec[0]
    return rec[4] if k in ("b", "t") else rec[2]


class DayWriter:
    """Единственный писатель шарда: файл за сутки UTC; смена суток — закрыть, `.ok` (число записей), новый файл с нуля (Encoder сбрасывается).
    Дубли соединений-перекрытий снимаются здесь (последний записанный ключ по (тип, символ) живёт через смену суток)."""

    def __init__(self, root, shard, run_id):
        import zstandard

        self.zstd = zstandard
        self.root, self.shard, self.run_id = root, shard, run_id
        self.f = None
        self.path = None
        self.day = None
        self.enc = None
        self.rows = 0
        self.dups = 0
        self.last_key = {}  # (тип, символ) -> ключ последней записанной записи
        self.last_flush = time.monotonic()
        self.samples = []  # recv_ns выбранных кадров, ещё не сброшенных на диск
        self.out_lags = deque()  # (monotonic, мс приём -> диск)
        self.last_write_ms = 0

    def _open(self, rx_ns):
        self.close()
        day = day_of(rx_ns / 1e9)
        path = os.path.join(self.root, day, f"{self.shard}-{self.run_id}.tsv.zst")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, "ab")
        self.c = self.zstd.ZstdCompressor(level=ZSTD_LEVEL).compressobj()
        self.path, self.day, self.enc, self.rows = path, day, Encoder(), 0

    def _emit(self, rx_ns, make, sample):
        if self.f is None or day_of(rx_ns / 1e9) != self.day:
            self._open(rx_ns)
        line = make(self.enc, rx_ns)
        self.rows += line.count("\n")
        out = self.c.compress(line.encode())
        if out:
            self.f.write(out)
        if sample:
            self.samples.append(rx_ns)
        if time.monotonic() - self.last_flush >= FLUSH_PERIOD_S:
            self.flush()

    def records(self, rx_ns, recs, sample=False):
        """Разобранные записи кадра; запись, ключ которой не больше записанного по символу, — дубль и не пишется."""
        for rec in recs:
            key = (rec[0], rec[1])
            k = dedup_key(rec)
            if k <= self.last_key.get(key, -1):
                self.dups += 1
                continue
            self.last_key[key] = k
            self._emit(rx_ns, lambda e, r, rec=rec: e.record(r, rec), sample)
            sample = False

    def raw(self, rx_ns, frame, sample=False):
        self._emit(rx_ns, lambda e, r: e.raw(r, frame), sample)

    def control(self, rx_ns, what, detail=""):
        self._emit(rx_ns, lambda e, r: e.control(r, what, detail), False)

    def ping(self, rx_ns, rtt_us):
        self._emit(rx_ns, lambda e, r: e.ping(r, rtt_us), False)

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


# --- шард: соединения, пинг, путь ---------------------------------------------------------------------------------------------------

class Shard:
    """Один WS-шард (процесс): набор потоков на одном соединении (с заменой по пути), один писатель."""

    def __init__(self, adapter, root, name, endpoint, streams):
        self.ad, self.root, self.name, self.endpoint, self.streams = adapter, root, name, endpoint, streams
        self.stats_path = os.path.join(root, f"stats-{name}.jsonl")
        self.events_path = os.path.join(root, f"events-{name}.jsonl")
        self.frag_path = os.path.join(root, "frag", f"{name}.json")
        self.started_ms = int(time.time() * 1000)
        self.writer = None
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

    def event(self, **kw):
        kw.update(ts_ms=int(time.time() * 1000), shard=self.name)
        with open(self.events_path, "a") as f:
            f.write(json.dumps(kw) + "\n")

    def timeout(self, what, session):
        self.timeouts.append(int(time.time() * 1000))
        self.event(ev="timeout", what=what, session=session)

    async def pinger(self, ws, seq):
        while True:
            await asyncio.sleep(PING_PERIOD_S)
            t0 = time.monotonic()
            try:
                waiter = await ws.ping()
                await asyncio.wait_for(waiter, PING_TIMEOUT_S)
            except asyncio.TimeoutError:
                self.timeout("ping", seq)
                # без ответа на ping путь испортился (замер ss: исходящий сегмент в бэкоффе RTO до 120 с): не рвём, а помечаем — run() откроет замену,
                # примет её окном проверки пути и закроет эту сессию после перекрытия (E-025: ping_reconnect на живом, но деградировавшем пути давал разрыв)
                self.event(ev="ping_degraded", session=seq)
                self.degraded.add(seq)
                return
            rtt = time.monotonic() - t0
            self.pings.append((time.monotonic(), rtt * 1000))
            self.writer.ping(time.time_ns(), round(rtt * 1e6))

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

        w = self.writer
        url = self.ad.url(self.endpoint)
        helpers = []
        try:
            async with websockets.connect(url, max_size=None, ping_interval=None, compression=None, close_timeout=1) as ws:
                self.event(ev="connected", session=seq, url=url)
                self.connected += 1
                w.control(time.time_ns(), "connected", f"{seq} {url}")
                self.lags[seq], self.age[seq], self.lag_bad[seq] = [], 0, 0
                helpers = [asyncio.create_task(self.pinger(ws, seq)), asyncio.create_task(self.gap_watch(seq))]
                for msg in self.ad.subscribe_messages(self.streams):
                    await ws.send(msg)
                    await asyncio.sleep(self.ad.sub_period_s)
                self.event(ev="subscribed", session=seq, streams=len(self.streams))
                w.control(time.time_ns(), "subscribed", f"{seq} {len(self.streams)}")
                n = 0
                t_sub = time.monotonic()
                if not probe and accepted is not None:
                    accepted.set()
                probe_lags = []
                async for frame in ws:
                    rx = time.time_ns()
                    sample = n % LAG_SAMPLE == 0
                    recs = self.ad.parse(frame)
                    if recs is None:
                        w.raw(rx, frame, sample)
                    else:
                        w.records(rx, recs, sample)
                    self.msgs += 1
                    self.bytes += len(frame)
                    mono = time.monotonic()
                    if self.last_rx is not None:
                        self.max_gap = max(self.max_gap, mono - self.last_rx)
                    self.last_rx = mono
                    self.last_frame_ms = rx // 1_000_000
                    n += 1
                    sym = self.ad.symbol(frame)
                    if sym is not None:
                        self.sym_seen[sym] = mono
                    if sample:
                        self.sample_lag(frame, rx, mono, seq)
                    if probe:
                        if mono - t_sub >= PROBE_SKIP_S:
                            lag = self.ad.lag_ms(frame, rx)
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
            w.control(time.time_ns(), "closed", str(seq))
            for d in (self.lags, self.age, self.lag_bad):
                d.pop(seq, None)
            self.degraded.discard(seq)

    def sample_lag(self, frame, rx_ns, mono, seq):
        lag = self.ad.lag_ms(frame, rx_ns)
        if lag is not None:
            self.lags.setdefault(seq, []).append(lag)
            self.in_lags.append((mono, lag))

    async def run_session_forever(self, seq, accepted=None):
        """Одна сессия с переподключением при сбое."""
        backoff = 1.0
        rejects = 0
        while True:
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
        w = self.writer
        return {
            "shard": self.name, "ts_ms": int(time.time() * 1000), "started_ms": self.started_ms,
            "with_data": sorted(s for s, t in self.sym_seen.items() if now - t <= WINDOW_S),
            "connected": self.connected, "ping_ms": [round(v, 1) for _, v in self.pings],
            "lag_in": [round(v, 1) for _, v in self.in_lags], "lag_out": [round(v, 1) for _, v in w.out_lags] if w else [],
            "timeouts": list(self.timeouts), "reconnects": list(self.reconnects),
            "last_frame_ms": self.last_frame_ms, "last_write_ms": w.last_write_ms if w else 0,
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
                "dups": self.writer.dups if self.writer else 0,
            }
            with open(self.stats_path, "a") as f:
                f.write(json.dumps(line) + "\n")
            self.judge_lags()
            self.msgs = self.bytes = 0
            self.max_gap = 0.0
            self.cpu0, t_prev = cpu, now
            self.write_fragment()

    async def run(self):
        """Смена сессии без разрыва: замена открывается и принимается (окно проверки пути) до закрытия старой; на перекрытии оба соединения несут одни кадры
        в одного писателя (дубли снимает он). Причины: возраст MAX_AGE_S (rotated) или путь испортился (path_switch)."""
        seq = cur_seq = int(time.time())  # номер сессии = время старта процесса (с); он же имя файла писателя
        self.writer = DayWriter(self.root, self.name, seq)
        stats = asyncio.create_task(self.stats_loop())
        cur = asyncio.create_task(self.run_session_forever(seq))
        born = time.monotonic()
        try:
            while True:
                await asyncio.sleep(1.0)
                bad = cur_seq in self.degraded
                if not bad and time.monotonic() - born < MAX_AGE_S:
                    continue
                seq += 1
                accepted = asyncio.Event()
                nxt = asyncio.create_task(self.run_session_forever(seq, accepted))
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
            self.writer.close()


def shard_main(adapter, root, name, endpoint, streams):
    sh = Shard(adapter, root, name, endpoint, streams)

    async def amain():
        task = asyncio.current_task()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)  # остановка: закрыть файлы суток
        try:
            await sh.run()
        except asyncio.CancelledError:
            sh.event(ev="stopped")

    asyncio.run(amain())


# --- health ------------------------------------------------------------------------------------------------------------------------------

def aggregate(frags, now_ms, pool, exchange):
    """Фрагменты шардов -> health.json (схема 1, docs/findings/collector-health-format-2026-11-11.md)."""

    def pct(xs):
        return {"p50": percentile(xs, 0.5), "p99": percentile(xs, 0.99), "window_s": WINDOW_S}

    pings = [v for f in frags for v in f["ping_ms"]]
    with_data = {s for f in frags for s in f["with_data"]} & {x.lower() for x in pool}
    hour, day = now_ms - 3600_000, now_ms - 24 * 3600_000
    tos = [t for f in frags for t in f["timeouts"]]
    rcs = [t for f in frags for t in f["reconnects"]]
    return {
        "schema": 1, "exchange": exchange, "ts_ms": now_ms,
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


def write_health(root, health_path, pool, exchange):
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
        json.dump(aggregate(frags, now_ms, pool, exchange), f)
    os.replace(tmp, health_path)


def collector_main(adapter):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--health", default=adapter.health_default)
    ap.add_argument("--streams-per-conn", type=int, default=adapter.streams_per_conn)
    ap.add_argument("--min-quote-volume", type=float, default=adapter.min_quote_volume)
    ap.add_argument("--top-exclude", type=int, default=adapter.top_exclude)
    ap.add_argument("--kinds", default=",".join(adapter.kinds))
    ap.add_argument("--pool-only", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.root, exist_ok=True)
    pool, top = adapter.load_pool(a.min_quote_volume, a.top_exclude)
    with open(os.path.join(a.root, f"pool-{day_of(time.time())}.json"), "w") as f:
        json.dump({"ts_ms": int(time.time() * 1000), "min_quote_volume": a.min_quote_volume, "top_excluded": top, "pool": pool}, f)
    print(f"pool {len(pool)} top_excluded {top}", flush=True)
    if a.pool_only:
        return
    shards = adapter.plan(pool, a.kinds.split(","), a.streams_per_conn)
    procs = [mp.Process(target=shard_main, args=(adapter, a.root, n, e, s), name=n) for n, e, s in shards]
    for p in procs:
        p.start()
    print(f"shards {len(procs)}: " + ", ".join(f"{n}={len(s)}" for n, _, s in shards), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: [p.terminate() for p in procs])  # дети закрывают файлы по SIGTERM
    while any(p.is_alive() for p in procs):
        time.sleep(STATS_PERIOD_S)
        write_health(a.root, a.health, pool, adapter.name)
    for p in procs:
        p.join()
