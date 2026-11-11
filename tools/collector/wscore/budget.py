"""Общий для всех шардов процесса бюджет подключений: не больше LIMIT TCP-подключений к бирже за скользящие WINDOW_S секунд.
Делает: ConnectBudget.acquire — ждёт, пока в окне есть место, и занимает его; ровно один вызов перед каждым TCP connect (session). Кольцо из LIMIT отметок времени в разделяемой памяти (mp.Array) под mp.Lock.
Не знает биржу и соединения: лимит биржи (Binance: 300 подключений за 5 мин на IP) и запас — число и обоснование в адаптере/runner. Граница из кода: подключений в любом окне WINDOW_S ≤ LIMIT, что бы ни делали отбраковка, замены и переподключения.
"""
import asyncio
import multiprocessing as mp
import time

WINDOW_S = 300.0  # окно лимита биржи на IP (Binance USD-M: 300 подключений / 5 мин)
LIMIT = 200  # назначаемое (Судье): 2/3 лимита биржи 300; запас 100 — на неучтённое (другие клиенты с этого IP, граница окна на стороне биржи, ручные пробы)


class ConnectBudget:
    def __init__(self, limit=LIMIT, window_s=WINDOW_S, clock=time.monotonic):
        self.limit, self.window_s, self.clock = limit, window_s, clock  # monotonic на Linux — одни часы у всех процессов машины
        self.ring = mp.Array("d", [float("-inf")] * limit, lock=False)
        self.head = mp.Value("i", 0, lock=False)
        self.lock = mp.Lock()

    def try_take(self):
        """0.0 — место занято (отметка «сейчас» записана); иначе — через сколько секунд освободится самое старое место."""
        with self.lock:
            now = self.clock()
            oldest = self.ring[self.head.value]
            if now - oldest >= self.window_s:
                self.ring[self.head.value] = now
                self.head.value = (self.head.value + 1) % self.limit
                return 0.0
            return oldest + self.window_s - now

    def used(self):
        """Занято мест в окне сейчас (для статистики и проверок)."""
        with self.lock:
            now = self.clock()
            return sum(1 for t in self.ring if now - t < self.window_s)

    async def acquire(self):
        """Занять место в окне; возвращает, сколько секунд ждали."""
        waited = 0.0
        while True:
            w = self.try_take()
            if w <= 0:
                return waited
            step = min(w + 0.01, 5.0)
            await asyncio.sleep(step)
            waited += step
