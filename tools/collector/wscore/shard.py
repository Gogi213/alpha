"""Шард (один процесс): писатель + сессии одного набора потоков, переподключение, смена пути без разрыва.
Делает: Shard.run — `lanes` независимых линий (1 или 2: вторая — тёплый запас на другом пути, В-140), у каждой живая сессия, замена при degraded или возрасте MAX_AGE_S (замена принята окном проверки -> перекрытие OVERLAP_S -> старая закрыта),
run_session_forever — переподключение при сбое, stats_loop — строка статистики и фрагмент health раз в STATS_PERIOD_S. Не разбирает кадры и не знает формат (session, writer).
"""
import asyncio
import json
import os
import signal
import time

from wscore import health, stats
from wscore.metrics import Metrics
from wscore.path import PROBE_MAX_REJECTS, PathWatch
from wscore.session import Session
from wscore.writer import DayWriter

MAX_AGE_S = 23 * 3600  # соединение живёт 24 ч: новое открывается заранее, старое закрывается после перекрытия
OVERLAP_S = 20  # на перекрытии оба соединения несут одни кадры в одного писателя (дубли снимает он)
SWITCH_WAIT_S = 120  # ждём приёма новой сессии (окно проверки), затем перекрытие
LANE_SEQ_STEP = 1_000_000  # номера сессий линий не пересекаются


class Shard:
    def __init__(self, adapter, root, name, endpoint, streams, run_id=None, lanes=1):
        self.root, self.name, self.lanes = root, name, lanes
        self.run_id = run_id if run_id is not None else int(time.time())  # номер запуска = время старта процесса (с); он же имя файла писателя
        self.stats_path = os.path.join(root, f"stats-{name}.jsonl")
        self.events_path = os.path.join(root, f"events-{name}.jsonl")
        self.writer = DayWriter(root, name, self.run_id)
        self.m = Metrics()
        self.watch = PathWatch()
        self.session = Session(adapter, endpoint, streams, self.writer, self.m, self.watch, self.event)

    def event(self, **kw):
        kw.update(ts_ms=int(time.time() * 1000), shard=self.name)
        with open(self.events_path, "a") as f:
            f.write(json.dumps(kw) + "\n")

    async def run_session_forever(self, seq, accepted=None):
        """Одна сессия с переподключением при сбое; отбракованный путь — новое соединение сразу (до PROBE_MAX_REJECTS подряд), не переподключение."""
        backoff = 1.0
        rejects = 0
        while True:
            t0 = time.monotonic()
            try:
                if await self.session.run(seq, probe=rejects < PROBE_MAX_REJECTS, accepted=accepted):
                    rejects += 1
                    await asyncio.sleep(0.5)
                    self.m.pause()
                    continue
                rejects = 0
                self.event(ev="closed", session=seq)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — любой сбой сети: запись события и повтор
                self.event(ev="error", session=seq, err=repr(e)[:200])
            self.m.reconnect()
            backoff = 1.0 if time.monotonic() - t0 > 60 else min(backoff * 2, 30.0)
            await asyncio.sleep(backoff)
            self.m.pause()

    async def stats_loop(self):
        while True:
            await asyncio.sleep(stats.STATS_PERIOD_S)
            line = self.m.take_line(self.watch.window_lags(), self.writer.dups)
            with open(self.stats_path, "a") as f:
                f.write(json.dumps(line) + "\n")
            self.watch.judge()
            health.write_fragment(self.root, self.name, self.m.fragment(self.name, self.writer))

    async def lane(self, seq, age_shift_s=0.0):
        """Одна линия: живая сессия и смена без разрыва. Причины: возраст MAX_AGE_S (rotated) или путь испортился (path_switch: лаг или нет pong)."""
        cur_seq = seq
        cur = asyncio.create_task(self.run_session_forever(seq))
        born = time.monotonic() - age_shift_s
        while True:
            await asyncio.sleep(1.0)
            bad = cur_seq in self.watch.degraded
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

    async def run(self):
        """Линии шарда пишут в одного писателя; при `lanes=2` поток идёт с двух путей сразу, дубли снимает писатель, смена возраста — вразнобой."""
        stats_task = asyncio.create_task(self.stats_loop())
        try:
            await asyncio.gather(*(self.lane(self.run_id + k * LANE_SEQ_STEP, k * MAX_AGE_S / self.lanes) for k in range(self.lanes)))
        finally:
            stats_task.cancel()
            self.writer.close()


def shard_main(adapter, root, name, endpoint, streams, lanes=1):
    sh = Shard(adapter, root, name, endpoint, streams, lanes=lanes)

    async def amain():
        task = asyncio.current_task()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)  # остановка: закрыть файлы суток
        try:
            await sh.run()
        except asyncio.CancelledError:
            sh.event(ev="stopped")

    asyncio.run(amain())
