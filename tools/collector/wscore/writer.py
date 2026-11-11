"""Суточный писатель шарда: единственный на шард, файл за сутки UTC, снятие дублей.
Делает: DayWriter — записи кадра, служебные записи, flush не реже FLUSH_PERIOD_S (A8), закрытие суток с `.ok` (число записей).
Не знает биржу и соединения: дубли соединений-перекрытий снимает здесь (ключ — codec.dedup_key); закрытый день неизменяем.
Файл: `<root>/<день UTC>/<шард>-<запуск>.tsv.zst` (запуск = время старта процесса, с).
"""
import os
import time
from collections import deque

from wscore import stats
from wscore.codec import Encoder, dedup_key

FLUSH_PERIOD_S = 1.0  # кадр на диске не реже 10 с (A8); 1 с — лаг отдачи ≈ 0,5 с, размер +1 % (замер)
ZSTD_LEVEL = 1  # замер: на этих данных уровень 1 и меньше, и быстрее уровня 3


def day_of(t_s):
    return time.strftime("%Y-%m-%d", time.gmtime(t_s))


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
        self.c = None
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
        while self.out_lags and now - self.out_lags[0][0] > stats.WINDOW_S:
            self.out_lags.popleft()
        self.last_flush = now

    def close(self):
        if self.f is not None:
            self.f.write(self.c.flush())
            self.f.close()
            with open(self.path + ".ok", "w") as ok:
                ok.write(f"{self.rows}\n")
            self.f = None
