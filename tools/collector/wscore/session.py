"""Одно WS-соединение: подключение, подписка, чтение кадров, пинг, сторож паузы.
Делает: Session.run — от connect до закрытия (место в бюджете подключений — до TCP connect); кадр -> parse адаптера -> писатель, счётчики, выборка лага, окно проверки пути (PathProbe).
Не решает, когда менять путь и переподключаться, — это shard; не знает формат записи (писатель) и биржу (адаптер). Нет pong — сессия degraded, не обрыв (E-025).

Контракт адаптера (Adapter, `<биржа>_ws.py`):
  name; health_default (путь health.json); streams_per_conn; min_quote_volume; top_exclude; kinds (имена видов в порядке запуска)
  load_pool(min_qv, top_exclude) -> (пул, исключённые)         REST биржи + правило пула
  plan(pool, kinds, per_conn) -> [(шард, endpoint, [stream])]  раскладка по соединениям
  url(endpoint) -> str; subscribe_messages(streams) -> [str]; sub_period_s (пауза между сообщениями подписки — лимит входящих сообщений биржи)
  parse(frame) -> [запись] | None   запись — кортеж ('b', s, E, T, u, bid, bsz, ask, asz) | ('t', s, E, T, a, p, q, f, l, m, nq) | ('m', s, E, p, ap, P, i, r, T)
  symbol(frame) -> str | None (ключ символа для «с данными за минуту», нижний регистр); lag_ms(frame, rx_ns) -> float | None (приём − время биржи)
"""
import asyncio
import time
from collections import deque

from wscore.dial import CONNECT_RTT_MAX_MS, dial, hostname, peer_ip, tcp_state, with_host
from wscore.fresh import GRACE_S
from wscore.path import PathProbe

PING_PERIOD_S = 5.0
PING_TIMEOUT_S = 10.0  # здесь — порог «таймаут» страницы здоровья, назначаемое
LAG_SAMPLE = 64  # каждый N-й кадр идёт в выборки лагов
GAP_TICK_S = 0.5  # период сторожа паузы данных


class Session:
    def __init__(self, adapter, endpoint, streams, writer, metrics, watch, event, budget=None):
        self.ad, self.endpoint, self.streams = adapter, endpoint, streams
        self.writer, self.m, self.watch, self.event = writer, metrics, watch, event
        self.budget = budget  # общий бюджет подключений процесса (budget.ConnectBudget); None — без ограничителя
        self.socks = {}  # номер живой сессии -> её сокет (для tcp_state в событии таймаута)

    def _tcp(self):
        """Состояние TCP всех живых сессий шарда: пир не подтверждает наши сегменты (retransmits) или молчит при чистом TCP (В-140: причина замером)."""
        return {k: tcp_state(s) for k, s in self.socks.items()}

    def timeout(self, what, seq):
        """Соединение мертво: нет pong за PING_TIMEOUT_S. Считается таймаутом (health timeouts_*)."""
        self.m.timeout()
        tcp = self._tcp()
        self.event(ev="timeout", what=what, session=seq, **({"tcp": tcp} if tcp else {}))

    def gap(self, seq, loop_late_ms):
        """Пауза данных шарда дольше GAP_TIMEOUT_S, цикл событий жив (loop_late_ms — худшее опоздание его тика за паузу). TK-532: доставку задерживает пир (RST/ACK-less на стороне CDN, соседний
        Binance-трафик хоста без пауз, после паузы кадры идут со временем биржи внутри паузы, потерь a/u/E нет) — событие gap и счётчик gaps_*, не timeout; лаг пачки виден в lag_in."""
        self.m.gap()
        tcp = self._tcp()
        clean = all(v and v["retransmits"] == 0 and v["lost"] == 0 for v in tcp.values())
        self.event(ev="gap", session=seq, loop_late_ms=loop_late_ms, tcp_clean=clean, **({"tcp": tcp} if tcp else {}))

    async def pinger(self, ws, seq):
        while True:
            await asyncio.sleep(PING_PERIOD_S)
            t0 = time.monotonic()
            try:
                waiter = await ws.ping()
                await asyncio.wait_for(waiter, PING_TIMEOUT_S)
            except asyncio.TimeoutError:
                self.timeout("ping", seq)
                # без ответа на ping путь испортился (замер ss: исходящий сегмент в бэкоффе RTO до 120 с): не рвём, а помечаем — shard откроет замену,
                # примет её окном проверки пути и закроет эту сессию после перекрытия (E-025: ping_reconnect на живом, но деградировавшем пути давал разрыв)
                self.event(ev="ping_degraded", session=seq)
                self.watch.degraded.add(seq)
                return
            rtt = time.monotonic() - t0
            self.m.ping(time.monotonic(), rtt * 1000)
            self.writer.ping(time.time_ns(), round(rtt * 1e6))

    async def gap_watch(self, seq):
        """Пауза данных шарда дольше GAP_TIMEOUT_S — одно событие gap на паузу; тик 0,5 с, опоздание тика (loop_late_ms) отличает паузу пира от остановки нашего процесса."""
        late = deque(maxlen=8)  # опоздания тиков за последние ≈ 4 с
        prev = time.monotonic()
        while True:
            await asyncio.sleep(GAP_TICK_S)
            now = time.monotonic()
            late.append(max(0.0, now - prev - GAP_TICK_S))
            prev = now
            if self.m.gap_tick(now):
                self.gap(seq, round(max(late) * 1000))

    async def _subscribe(self, ws, seq):
        for msg in self.ad.subscribe_messages(self.streams):
            await ws.send(msg)
            await asyncio.sleep(self.ad.sub_period_s)
        self.event(ev="subscribed", session=seq, streams=len(self.streams))
        self.writer.control(time.time_ns(), "subscribed", f"{seq} {len(self.streams)}")

    async def run(self, seq, probe=False, accepted=None, entrance=None):
        """Сессия до закрытия; True — путь отбракован (RTT рукопожатия или окно проверки лага; probe), соединение закрыто до приёма в «живые». accepted — Event: путь принят.
        entrance — (хост, ip или None): вход линии вместо входа адаптера (TK-532: независимый вход — другая точка присутствия CDN или другое имя биржи)."""
        import websockets

        url = self.ad.url(self.endpoint)
        ip = None
        if entrance is not None:
            url, ip = with_host(url, entrance[0]), entrance[1]
        if self.budget is not None:
            waited = await self.budget.acquire()  # единственный путь к TCP connect: подключений за окно ≤ LIMIT при любых отбраковках и заменах
            if waited >= 1.0:
                self.event(ev="connect_wait", session=seq, waited_s=round(waited, 1))
        sock, rtt = await dial(url, CONNECT_RTT_MAX_MS if probe else None, ip)
        if sock is None:
            self.event(ev="path_rejected_rtt", session=seq, rtt_ms=round(rtt))
            return True
        helpers = []
        self.socks[seq] = sock
        try:
            async with websockets.connect(url, sock=sock, server_hostname=hostname(url), max_size=None, ping_interval=None, compression=None, close_timeout=1) as ws:
                self.event(ev="connected", session=seq, url=url, rtt_ms=round(rtt), ip=peer_ip(sock))
                self.m.connected += 1
                self.writer.control(time.time_ns(), "connected", f"{seq} {url}")
                self.watch.open(seq)
                helpers = [asyncio.create_task(self.pinger(ws, seq)), asyncio.create_task(self.gap_watch(seq))]
                await self._subscribe(ws, seq)
                if not probe and accepted is not None:
                    accepted.set()
                return await self._read(ws, seq, PathProbe(time.monotonic()) if probe else None, accepted)
        finally:
            self.socks.pop(seq, None)
            for h in helpers:
                h.cancel()
            self.m.connected = max(0, self.m.connected - 1)
            self.writer.control(time.time_ns(), "closed", str(seq))
            self.watch.close(seq)

    async def _read(self, ws, seq, probe, accepted):
        """Чтение кадров; probe — PathProbe, пока путь проверяется (None — не проверяется). Возвращает True, если путь отбракован."""
        ad, w, m = self.ad, self.writer, self.m
        n = 0
        settle = time.monotonic() + GRACE_S  # до этого момента записи не идут в свежесть (начальные снимки подписки)
        async for frame in ws:
            rx = time.time_ns()
            mono = time.monotonic()
            sample = n % LAG_SAMPLE == 0
            recs = ad.parse(frame)
            if recs is None:
                w.raw(rx, frame, sample, ad.lag_ms(frame, rx) if mono >= settle else None)  # кадр без ts (ответ на подписку, pong) — не данные: lag None, свежесть не считается
            else:
                w.records(rx, recs, sample, mono >= settle)
            m.frame(len(frame), rx, mono, ad.symbol(frame))
            n += 1
            if sample:
                lag = ad.lag_ms(frame, rx)
                if lag is not None:
                    self.watch.sample(seq, lag)
                    m.lag(mono, lag)
            if probe is not None:
                if probe.collecting(mono):
                    probe.add(ad.lag_ms(frame, rx))
                if probe.finished(mono):
                    clean = probe.clean()
                    self.event(ev="path_ok" if clean else "path_rejected", session=seq, **probe.summary())
                    if not clean:
                        return True
                    probe = None
                    if accepted is not None:
                        accepted.set()
        return False
