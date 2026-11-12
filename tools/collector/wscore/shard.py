"""Шард (один процесс): писатель + сессии одного набора потоков, переподключение, смена пути без разрыва.
Делает: Shard.run — `lanes` независимых линий (1 или 2: вторая — тёплый запас на другом пути, В-140), у каждой живая сессия, замена при degraded или возрасте MAX_AGE_S (замена принята окном проверки -> перекрытие OVERLAP_S -> старая закрыта);
у линии есть список входов (`entrances.Entrances`): при FAIL_LIMIT неудачных подключениях подряд или при замене испорченной сессии линия переходит на следующий вход (событие `entrance_switch`, счётчик в health),
run_session_forever — переподключение при сбое, stats_loop — строка статистики и фрагмент health раз в STATS_PERIOD_S. Не разбирает кадры и не знает формат (session, writer).
"""
import asyncio
import json
import os
import signal
import time

from wscore import health, stats
from wscore.entrances import FAIL_LIMIT, SHORT_SESSION_S, Entrances, label
from wscore.metrics import Metrics
from wscore.path import PROBE_MAX_REJECTS, PathWatch
from wscore.session import Session
from wscore.writer import DayWriter

MAX_AGE_S = 23 * 3600  # соединение живёт 24 ч: новое открывается заранее, старое закрывается после перекрытия
OVERLAP_S = 20  # на перекрытии оба соединения несут одни кадры в одного писателя (дубли снимает он)
SWITCH_WAIT_S = 120  # ждём приёма новой сессии (окно проверки), затем перекрытие
LANE_SEQ_STEP = 1_000_000  # номера сессий линий не пересекаются


class Shard:
    def __init__(self, adapter, root, name, endpoint, streams, run_id=None, lanes=1, budget=None, entrances=None):
        self.root, self.name, self.lanes = root, name, lanes
        # входы линии k — entrances[k]: список входов (хост, адрес или None) по порядку предпочтения, один вход или None (вход адаптера); переход по кругу — entrances.Entrances
        ents = entrances or []
        self.lines = [Entrances(ents[k] if k < len(ents) and isinstance(ents[k], list) else [ents[k] if k < len(ents) else None], k) for k in range(lanes)]
        self.run_id = run_id if run_id is not None else int(time.time())  # номер запуска = время старта процесса (с); он же имя файла писателя
        self.stats_path = os.path.join(root, f"stats-{name}.jsonl")
        self.events_path = os.path.join(root, f"events-{name}.jsonl")
        self.writer = DayWriter(root, name, self.run_id)
        self.m = Metrics()
        self.watch = PathWatch()
        self.session = Session(adapter, endpoint, streams, self.writer, self.m, self.watch, self.event, budget)
        self.sessions = set()  # живые задачи сессий (spawn)

    def event(self, **kw):
        kw.update(ts_ms=int(time.time() * 1000), shard=self.name)
        with open(self.events_path, "a") as f:
            f.write(json.dumps(kw) + "\n")

    def switch_entrance(self, ent, seen, reason, seq, **kw):
        """Линия `ent` уходит со входа `seen` на следующий (если ещё на нём): событие entrance_switch и счётчик health."""
        sw = ent.switch(seen)
        if sw is not None:
            self.m.entrance_switch()
            self.event(ev="entrance_switch", line=ent.line, reason=reason, session=seq, **{"from": label(sw[0]), "to": label(sw[1])}, **kw)

    async def run_session_forever(self, seq, accepted=None, ent=None):
        """Одна сессия с переподключением при сбое; отбракованный путь — новое соединение сразу (до PROBE_MAX_REJECTS подряд), не переподключение.
        Вход — текущий вход линии `ent` на каждом подключении; FAIL_LIMIT коротких сессий подряд (сбой или закрытие биржей быстрее SHORT_SESSION_S) — переход на запасной вход."""
        backoff = 1.0
        rejects = 0
        fails = 0
        while True:
            entrance = ent.current() if ent is not None else None
            kw = {"entrance": entrance} if entrance is not None else {}
            t0 = time.monotonic()
            try:
                if await self.session.run(seq, probe=rejects < PROBE_MAX_REJECTS, accepted=accepted, **kw):
                    rejects += 1
                    await asyncio.sleep(0.5)
                    self.m.pause()
                    continue
                rejects = 0
                self.event(ev="closed", session=seq)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — любой сбой сети: запись события и повтор
                # cause — первопричина обрыва без close-кадра (EOFError — FIN от биржи/CDN, ConnectionResetError — RST): по одному repr(e) «ConnectionClosedError(None, None, None)» их не отличить
                self.event(ev="error", session=seq, err=repr(e)[:200], cause=repr(e.__cause__)[:120] if e.__cause__ is not None else None)
            self.m.reconnect()
            lived = time.monotonic() - t0
            fails = 0 if lived > SHORT_SESSION_S else fails + 1
            if ent is not None and fails >= FAIL_LIMIT:
                self.switch_entrance(ent, entrance, "connect_fail", seq, fails=fails)
                fails = 0
            backoff = 1.0 if lived > SHORT_SESSION_S else min(backoff * 2, 30.0)
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

    async def lane(self, seq, age_shift_s=0.0, k=0):
        """Одна линия: живая сессия и смена без разрыва. Причины: возраст MAX_AGE_S (rotated) или путь испортился (path_switch: лаг или нет pong). Вход линии — self.lines[k]; при замене испорченной сессии линия переходит на следующий вход."""
        ent = self.lines[k]
        cur_seq = seq
        cur = self.spawn(self.run_session_forever(seq, ent=ent))
        born = time.monotonic() - age_shift_s
        while True:
            await asyncio.sleep(1.0)
            bad = cur_seq in self.watch.degraded
            if not bad and time.monotonic() - born < MAX_AGE_S:
                continue
            seq += 1
            if bad:
                self.switch_entrance(ent, ent.current(), "late_delivery", cur_seq)
            accepted = asyncio.Event()
            nxt = self.spawn(self.run_session_forever(seq, accepted, ent=ent))
            try:
                await asyncio.wait_for(accepted.wait(), SWITCH_WAIT_S)
            except asyncio.TimeoutError:
                self.event(ev="switch_timeout", session=seq)
            await asyncio.sleep(OVERLAP_S)
            cur.cancel()
            self.event(ev="path_switch" if bad else "rotated", old_session=cur_seq, new_session=seq)
            cur, cur_seq, born = nxt, seq, time.monotonic()

    def spawn(self, coro):
        """Задача сессии: учитывается, чтобы остановка дождалась её закрытия (строка `closed` пишется до закрытия писателя)."""
        t = asyncio.create_task(coro)
        self.sessions.add(t)
        t.add_done_callback(self.sessions.discard)
        return t

    async def run(self):
        """Линии шарда пишут в одного писателя; при `lanes=2` поток идёт с двух путей сразу, дубли снимает писатель, смена возраста — вразнобой.
        Остановка: отменить линии, статистику и все сессии, дождаться их (каждая пишет `closed`), и только потом закрыть писателя — иначе `closed` откроет файл заново и оставит кадр zstd незакрытым."""
        stats_task = asyncio.create_task(self.stats_loop())
        lanes = [asyncio.create_task(self.lane(self.run_id + k * LANE_SEQ_STEP, k * MAX_AGE_S / self.lanes, k)) for k in range(self.lanes)]
        try:
            await asyncio.gather(*lanes)
        finally:
            for t in (stats_task, *lanes, *self.sessions):
                t.cancel()
            await asyncio.gather(stats_task, *lanes, *self.sessions, return_exceptions=True)
            self.writer.close(final=True)


def cancel_once(task):
    """Обработчик остановки: первый сигнал отменяет задачу шарда, повторные игнорируются. Шард получает SIGTERM дважды — от systemd (каждому процессу группы) и от родителя (terminate); второй cancel
    прерывал бы очистку (сессии пишут `closed`, писатель закрывает файл): файл оставался без `.ok` с незавершённым кадром zstd (замер на живом Bybit 12.11)."""
    stopped = []

    def stop():
        if not stopped:
            stopped.append(True)
            task.cancel()

    return stop


def shard_main(adapter, root, name, endpoint, streams, lanes=1, budget=None, entrances=None):
    sh = Shard(adapter, root, name, endpoint, streams, lanes=lanes, budget=budget, entrances=entrances)

    async def amain():
        task = asyncio.current_task()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, cancel_once(task))  # остановка: закрыть файлы суток
        try:
            await sh.run()
        except asyncio.CancelledError:
            sh.event(ev="stopped")

    asyncio.run(amain())
