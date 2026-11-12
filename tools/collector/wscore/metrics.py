"""Счётчики одного шарда за окно: приход кадров, пауза, лаги, пинг, таймауты, паузы данных, переподключения.
Делает: накапливает; `take_line` — строка статистики окна (со сбросом), `fragment` — вклад шарда в health. Не пишет файлов и не знает соединений.
Время окон — монотонное (`mono`), время событий — стенное (мс).
"""
import time
from collections import deque

from wscore import stats

GAP_TIMEOUT_S = 3.0  # пауза данных шарда (событие gap, не timeout: TK-532); замер: макс. окно 1,55 с (book) / 1,22 с (trade) за 984 окна — порог ≈ 2× (назначаемое)
DAY_MS = 24 * 3600 * 1000


class Metrics:
    def __init__(self):
        self.started_ms = int(time.time() * 1000)
        self.msgs = 0
        self.bytes = 0
        self.max_gap = 0.0
        self.last_rx = None
        self.last_frame_ms = 0
        self.in_gap = False
        self.connected = 0
        self.in_lags = deque()  # (monotonic, лаг прихода мс) за WINDOW_S
        self.pings = deque()  # (monotonic, rtt мс)
        self.sym_seen = {}  # символ -> monotonic последнего кадра
        self.timeouts = deque()  # ts_ms: соединение мертво (нет pong за PING_TIMEOUT_S)
        self.gaps = deque()  # ts_ms: пауза данных шарда дольше GAP_TIMEOUT_S при живом цикле событий (TK-532: доставка задержана пиром, потерь нет)
        self.reconnects = deque()  # ts_ms
        self.entrance_switches = deque()  # ts_ms: линия перешла на запасной вход (повторные неудачи подключения или задержка доставки)
        self.cpu0 = time.process_time()
        self.t_prev = time.monotonic()

    def frame(self, nbytes, rx_ns, mono, sym):
        self.msgs += 1
        self.bytes += nbytes
        if self.last_rx is not None:
            self.max_gap = max(self.max_gap, mono - self.last_rx)
        self.last_rx = mono
        self.last_frame_ms = rx_ns // 1_000_000
        if sym is not None:
            self.sym_seen[sym] = mono

    def lag(self, mono, lag_ms):
        self.in_lags.append((mono, lag_ms))

    def ping(self, mono, rtt_ms):
        self.pings.append((mono, rtt_ms))

    def timeout(self):
        self.timeouts.append(int(time.time() * 1000))

    def gap(self):
        self.gaps.append(int(time.time() * 1000))

    def reconnect(self):
        self.reconnects.append(int(time.time() * 1000))

    def entrance_switch(self):
        self.entrance_switches.append(int(time.time() * 1000))

    def pause(self):
        """Пауза переподключения — в разрыв считается событием, не max_gap."""
        self.last_rx = None

    def gap_tick(self, mono):
        """True — началась новая пауза без кадров дольше GAP_TIMEOUT_S (один таймаут на паузу)."""
        if self.last_rx is None:
            return False
        gap = mono - self.last_rx
        if gap > GAP_TIMEOUT_S and not self.in_gap:
            self.in_gap = True
            return True
        if gap <= GAP_TIMEOUT_S:
            self.in_gap = False
        return False

    def take_line(self, window_lags, dups):
        """Строка статистики окна; окно сбрасывается."""
        now, cpu = time.monotonic(), time.process_time()
        line = {
            "ts_ms": int(time.time() * 1000), "msgs": self.msgs, "bytes": self.bytes, "max_gap_ms": round(self.max_gap * 1000),
            "lag_p50_ms": stats.percentile(window_lags, 0.5), "lag_p99_ms": stats.percentile(window_lags, 0.99),
            "cpu_frac": round((cpu - self.cpu0) / (now - self.t_prev), 3), "dups": dups,
        }
        self.msgs = self.bytes = 0
        self.max_gap = 0.0
        self.cpu0, self.t_prev = cpu, now
        return line

    def _trim(self, now):
        for dq in (self.in_lags, self.pings):
            while dq and now - dq[0][0] > stats.WINDOW_S:
                dq.popleft()
        cut = int(time.time() * 1000) - DAY_MS
        for dq in (self.timeouts, self.gaps, self.reconnects, self.entrance_switches):
            while dq and dq[0] < cut:
                dq.popleft()

    def fragment(self, shard, writer):
        now = time.monotonic()
        self._trim(now)
        return {
            "shard": shard, "ts_ms": int(time.time() * 1000), "started_ms": self.started_ms,
            "with_data": sorted(s for s, t in self.sym_seen.items() if now - t <= stats.WINDOW_S),
            "connected": self.connected, "ping_ms": [round(v, 1) for _, v in self.pings],
            "lag_in": [round(v, 1) for _, v in self.in_lags], "lag_out": [round(v, 1) for _, v in writer.out_lags],
            "timeouts": list(self.timeouts), "gaps": list(self.gaps), "reconnects": list(self.reconnects), "entrance_switches": list(self.entrance_switches),
            "last_frame_ms": self.last_frame_ms, "last_write_ms": writer.last_write_ms,
            "fresh": writer.fresh.fragment(int(time.time() * 1000)),
        }
