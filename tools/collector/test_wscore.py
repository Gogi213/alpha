"""Тесты общего ядра (wscore): на поддельном адаптере — ядро не знает биржу (В-142)."""
import asyncio
import json
import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from wscore import budget as budget_mod  # noqa: E402
from wscore import codec, health, path, stats  # noqa: E402
from wscore import session as sess  # noqa: E402
from wscore import shard as shard_mod  # noqa: E402
from wscore.writer import DayWriter  # noqa: E402

B = ("b", "AVAXUSDT", 1794418695171, 1794418695170, 12081389761850, "8.6790", "42", "8.6800", "285")
T = ("t", "AIOTUSDT", 1794418694042, 1794418693917, 92150638, "0.1742400", "410", 180333636, 180333637, True, "410")
M = ("m", "AVAXUSDT", 1794418695000, "8.68001", "8.68002", "8.67", "8.6801", "0.00010000", 1794441600000)
RX0 = 1794418695434654672


def with_u(u):
    return B[:4] + (u,) + B[5:]


class FakeAdapter:
    """Кадр — JSON-список записей ядра или строка `x` (не разобрано)."""

    name = "fake"
    sub_period_s = 0
    health_default = "/tmp/fake-health.json"

    def url(self, endpoint):
        return f"wss://fake/{endpoint}"

    def subscribe_messages(self, streams):
        return [json.dumps(streams)]

    def parse(self, frame):
        return None if frame == "x" else [tuple(r) for r in json.loads(frame)]

    def symbol(self, frame):
        return "avaxusdt" if frame != "x" else None

    def lag_ms(self, frame, rx_ns):
        return 100.0 if frame != "x" else None


def shard_for(tmp_path, streams=("avaxusdt@x",)):
    return shard_mod.Shard(FakeAdapter(), str(tmp_path), "book00", "public", list(streams), run_id=7)


def read(path):
    zstandard = pytest.importorskip("zstandard")
    return list(codec.decode(zstandard.ZstdDecompressor().stream_reader(path.open("rb"), read_across_frames=True).read().decode()))


def test_roundtrip_records_exact():
    e = codec.Encoder()
    b2 = with_u(B[4] + 66)[:6] + ("47",) + B[7:]
    t2 = T[:4] + (T[4] + 1,) + T[5:7] + (T[8] + 1, T[8] + 3, False, "410")
    rxs = [RX0, RX0 + 43_000, RX0 + 50_000, RX0 + 51_000, RX0 + 999_999_999, RX0 + 999_999_999]
    recs = [B, b2, T, t2, M, ("m", "AIOTUSDT") + M[2:]]
    out = list(codec.decode("".join(e.record(rx, r) for rx, r in zip(rxs, recs))))
    assert out[0] == {"t": "b", "rx_ns": RX0, "E": B[2], "T": B[3], "s": "AVAXUSDT", "u": B[4], "b": "8.6790", "B": "42", "a": "8.6800", "A": "285"}
    assert out[1]["u"] == b2[4] and out[1]["rx_ns"] == rxs[1] and out[1]["B"] == "47"
    assert (out[2]["a"], out[2]["f"], out[2]["l"], out[2]["m"]) == (T[4], T[7], T[8], True)
    assert (out[3]["a"], out[3]["f"], out[3]["l"], out[3]["m"]) == (t2[4], t2[7], t2[8], False)
    assert out[4]["T"] == M[8] and out[4]["r"] == "0.00010000" and out[4]["ap"] == "8.68002" and out[5]["s"] == "AIOTUSDT"


def test_trade_nq_kept_only_when_differs_from_q():
    e = codec.Encoder()
    rpi = T[:4] + (T[4] + 1,) + T[5:6] + ("410",) + T[7:9] + (True, "999")
    out = list(codec.decode(e.record(RX0, T) + e.record(RX0 + 1, rpi)))
    assert (out[0]["q"], out[0]["nq"]) == ("410", "410") and (out[1]["q"], out[1]["nq"]) == ("410", "999")


def test_raw_ping_control_records():
    e = codec.Encoder()
    text = e.control(100, "connected", "7 wss://x") + e.ping(5_000_100, 351_234) + e.raw(6_000_000_000, '{"result":null,"id":1}') + e.control(9_000_000_000, "closed", "7")
    assert list(codec.decode(text)) == [
        {"t": "c", "rx_ns": 100, "what": "connected", "detail": "7 wss://x"},
        {"t": "g", "rx_ns": 5_000_100, "rtt_us": 351_234},
        {"t": "r", "rx_ns": 6_000_000_000, "frame": '{"result":null,"id":1}'},
        {"t": "c", "rx_ns": 9_000_000_000, "what": "closed", "detail": "7"}]


def test_day_writer_cuts_days_and_marks_ok(tmp_path):
    w = DayWriter(str(tmp_path), "book00", 1)
    d1, d2 = 1794441599_900_000_000, 1794441600_100_000_000  # 2026-11-11 23:59:59.9 и 2026-11-12 00:00:00.1 UTC
    w.records(d1, [B], sample=True)
    w.records(d2, [with_u(B[4] + 1)])
    w.close()
    f1, f2 = tmp_path / "2026-11-11" / "book00-1.tsv.zst", tmp_path / "2026-11-12" / "book00-1.tsv.zst"
    assert (f1.parent / (f1.name + ".ok")).read_text() == "1\n" and f2.exists()
    r2 = read(f2)  # новые сутки кодируются с нуля: первая запись суток — абсолютные u и приём
    assert r2[0]["u"] == B[4] + 1 and r2[0]["rx_ns"] == d2


def test_flush_makes_data_readable_midfile(tmp_path):
    w = DayWriter(str(tmp_path), "trade00", 2)
    w.records(RX0, [T], sample=True)
    w.flush()  # кадр на диске, файл ещё открыт (A8)
    assert [r["t"] for r in read(next(tmp_path.rglob("trade00-2.tsv.zst")))] == ["t"]
    assert w.out_lags and w.out_lags[0][1] >= 0
    w.close()


def test_writer_drops_overlap_duplicates_and_keeps_order(tmp_path):
    """В-142: два соединения шарда (перекрытие смены пути) несут одни кадры — в файле одна упорядоченная запись на ключ."""
    w = DayWriter(str(tmp_path), "book00", 3)
    seq = [with_u(B[4] + i) for i in range(5)]
    marks = [M[:2] + (M[2] + 1000 * i,) + M[3:] for i in range(3)]
    for i, r in enumerate(seq):  # соединение A — все кадры
        w.records(RX0 + i * 10, [r])
    for i, r in enumerate(seq[2:] + [seq[0]]):  # соединение B — те же кадры позже, часть позади A
        w.records(RX0 + 100 + i, [r])
    for r in marks + marks[1:]:
        w.records(RX0 + 200, [r])
    w.close()
    recs = read(next(tmp_path.rglob("book00-3.tsv.zst")))
    assert [r["u"] for r in recs if r["t"] == "b"] == [r[4] for r in seq]
    assert [r["E"] for r in recs if r["t"] == "m"] == [r[2] for r in marks]
    assert w.dups == 4 + 2


def test_dedupe_survives_day_change(tmp_path):
    w = DayWriter(str(tmp_path), "book00", 4)
    d1, d2 = 1794441599_900_000_000, 1794441600_100_000_000
    w.records(d1, [B])
    w.records(d2, [B])  # тот же u приходит с другого соединения уже после полуночи — дубль
    w.records(d2 + 1, [with_u(B[4] + 1)])
    w.close()
    assert w.dups == 1
    assert [r["u"] for r in read(tmp_path / "2026-11-12" / "book00-4.tsv.zst")] == [B[4] + 1]


def test_aggregate_health_schema_and_pool_filter():
    now = 1_794_500_000_000
    f = {"shard": "book00", "ts_ms": now, "started_ms": now - 5000, "with_data": ["avaxusdt", "btcusdt"], "connected": 1, "ping_ms": [300.0, 340.0],
         "lag_in": [200.0, 230.0], "lag_out": [400.0], "timeouts": [now - 10, now - 2 * 3600_000], "gaps": [now - 20, now - 3 * 3600_000, now - 30 * 3600_000], "reconnects": [], "last_frame_ms": now - 5, "last_write_ms": now - 500}
    h = health.aggregate([f], now, ["AVAXUSDT", "ETHUSDT"], "fake")
    assert h["schema"] == 1 and h["exchange"] == "fake"
    assert h["symbols"] == {"subscribed": 2, "with_data_1m": 1}  # btcusdt вне пула не считается
    assert h["ws"]["timeouts_1h"] == 1 and h["ws"]["timeouts_24h"] == 2 and h["ws"]["reconnects_1h"] == 0
    assert h["ws"]["gaps_1h"] == 1 and h["ws"]["gaps_24h"] == 2  # TK-532: пауза данных — отдельный счётчик, не таймаут
    f.pop("gaps")
    assert health.aggregate([f], now, ["AVAXUSDT"], "fake")["ws"]["gaps_1h"] == 0  # фрагмент прежнего кода (без gaps) читается
    assert h["ws"]["ping_ms"]["max"] == 340.0 and h["lag_in_ms"]["window_s"] == 60 and h["lag_out_ms"]["p50"] == 400.0
    h0 = health.aggregate([], now, ["AVAXUSDT"], "fake")
    assert h0["lag_in_ms"]["p50"] is None and h0["ws"]["ping_ms"]["max"] is None and h0["symbols"]["with_data_1m"] == 0


def test_percentile_and_path_verdict():
    assert stats.percentile([], 0.5) is None and stats.percentile([5], 0.99) == 5 and stats.percentile(list(range(100)), 0.99) == 99
    assert path.path_verdict([]) and path.path_verdict([9e9] * (path.PROBE_MIN_SAMPLES - 1))  # мало выборок — не отбраковываем
    assert path.path_verdict([250.0] * 100)
    assert not path.path_verdict([path.PROBE_P50_MS + 100.0] * 100)  # p50 выше порога
    assert not path.path_verdict([250.0] * 90 + [2000.0] * 10)  # p99 выше порога
    assert path.path_verdict([250.0] * 199 + [2000.0])  # одиночный всплеск (0,5 %) — путь чистый


def test_pinger_marks_session_degraded_instead_of_aborting(tmp_path, monkeypatch):
    """E-025: нет pong — сессия помечается degraded (run() откроет замену без разрыва), соединение не обрывается."""
    monkeypatch.setattr(sess, "PING_PERIOD_S", 0.01)
    monkeypatch.setattr(sess, "PING_TIMEOUT_S", 0.05)
    shard = shard_for(tmp_path)
    aborted = []

    class FakeWs:
        transport = type("T", (), {"abort": lambda self: aborted.append(1)})()

        async def ping(self):
            return asyncio.get_running_loop().create_future()  # pong не придёт

    asyncio.run(asyncio.wait_for(shard.session.pinger(FakeWs(), 1), 2))
    assert aborted == [] and shard.watch.degraded == {1} and len(shard.m.timeouts) == 1
    assert [json.loads(ln)["ev"] for ln in open(shard.events_path)] == ["timeout", "ping_degraded"]


def test_judge_lags_marks_degraded_session_after_two_bad_windows(tmp_path):
    shard = shard_for(tmp_path)
    shard.watch.lags = {1: [100.0] * 20, 2: [100.0] * 20}
    shard.watch.age = {1: path.LAG_SKIP_WINDOWS, 2: path.LAG_SKIP_WINDOWS}  # окна пропуска после подключения прошли
    shard.watch.judge()
    assert not shard.watch.degraded
    for _ in range(path.LAG_BAD_WINDOWS):  # сессия 1 — путь испортился, сессия 2 — в норме
        shard.watch.lags[1] = [path.LAG_LIMIT_MS + 500.0] * 20
        shard.watch.lags[2] = [100.0] * 20
        shard.watch.judge()
    assert shard.watch.degraded == {1} and shard.watch.lags == {1: [], 2: []}


def test_run_switches_path_without_closing_old_session_first(tmp_path, monkeypatch):
    monkeypatch.setattr(shard_mod, "OVERLAP_S", 0.05)
    monkeypatch.setattr(stats, "STATS_PERIOD_S", 3600)  # stats_loop не мешает
    shard = shard_for(tmp_path)
    log = []

    async def fake_forever(seq, accepted=None, ent=None):
        log.append(("open", seq))
        try:
            if accepted is not None:
                await asyncio.sleep(0.1)  # окно проверки пути
                log.append(("accepted", seq))
                accepted.set()
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            log.append(("closed", seq))
            raise

    monkeypatch.setattr(shard, "run_session_forever", fake_forever)

    async def go():
        task = asyncio.create_task(shard.run())
        await asyncio.sleep(1.2)  # run() опрашивает раз в секунду
        shard.watch.degraded.add(log[0][1])
        await asyncio.sleep(2.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    s1 = log[0][1]  # номер первой сессии — время старта (с)
    assert log[:4] == [("open", s1), ("open", s1 + 1), ("accepted", s1 + 1), ("closed", s1)]  # старая закрыта только после приёма новой
    evs = [json.loads(ln) for ln in open(shard.events_path)]
    assert [(e["ev"], e["old_session"], e["new_session"]) for e in evs if e["ev"] == "path_switch"] == [("path_switch", s1, s1 + 1)]


def test_two_lanes_keep_a_warm_spare_and_replace_only_the_degraded_one(tmp_path, monkeypatch):
    """В-140: lanes=2 — два пути сразу; испортился путь одной линии -> заменяется только она, вторая живёт, поток не прерывается."""
    monkeypatch.setattr(shard_mod, "OVERLAP_S", 0.05)
    monkeypatch.setattr(stats, "STATS_PERIOD_S", 3600)
    shard = shard_mod.Shard(FakeAdapter(), str(tmp_path), "book00", "public", ["a"], run_id=7, lanes=2)
    log = []

    async def fake_forever(seq, accepted=None, ent=None):
        log.append(("open", seq))
        try:
            if accepted is not None:
                await asyncio.sleep(0.1)
                accepted.set()
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            log.append(("closed", seq))
            raise

    monkeypatch.setattr(shard, "run_session_forever", fake_forever)

    async def go():
        task = asyncio.create_task(shard.run())
        await asyncio.sleep(1.2)
        assert log == [("open", 7), ("open", 7 + shard_mod.LANE_SEQ_STEP)]  # обе линии открыты сразу, номера не пересекаются
        shard.watch.degraded.add(7)
        await asyncio.sleep(2.5)
        before_stop.extend(log)  # дальше — закрытие всего при остановке
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    before_stop = []
    asyncio.run(go())
    assert ("open", 8) in before_stop and ("closed", 7) in before_stop
    assert ("closed", 7 + shard_mod.LANE_SEQ_STEP) not in before_stop  # вторая линия не тронута


def test_rotation_by_age_replaces_before_closing_and_lanes_age_apart(tmp_path, monkeypatch):
    """Сутки без разрыва: по возрасту MAX_AGE_S линия открывает замену, закрывает старую после приёма и перекрытия (ev=rotated, не path_switch); линии стареют вразнобой — обе сразу не меняются."""
    monkeypatch.setattr(shard_mod, "MAX_AGE_S", 4)  # линия 1 сдвинута на половину возраста: стареет на 2 с, линия 0 — на 4 с
    monkeypatch.setattr(shard_mod, "OVERLAP_S", 0.05)
    monkeypatch.setattr(stats, "STATS_PERIOD_S", 3600)
    shard = shard_mod.Shard(FakeAdapter(), str(tmp_path), "book00", "public", ["a"], run_id=7, lanes=2)
    log = []

    async def fake_forever(seq, accepted=None, ent=None):
        log.append(("open", seq))
        try:
            if accepted is not None:
                await asyncio.sleep(0.1)
                log.append(("accepted", seq))
                accepted.set()
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            log.append(("closed", seq))
            raise

    monkeypatch.setattr(shard, "run_session_forever", fake_forever)

    async def go():
        task = asyncio.create_task(shard.run())
        await asyncio.sleep(3.5)  # линия 1 уже сменилась (2-3 с), линия 0 ещё нет (4+ с)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    spare = 7 + shard_mod.LANE_SEQ_STEP
    assert log[:2] == [("open", 7), ("open", spare)]
    assert log[2:5] == [("open", spare + 1), ("accepted", spare + 1), ("closed", spare)]  # старая закрыта после приёма новой
    assert sorted(log[5:]) == [("closed", 7), ("closed", spare + 1)]  # дальше — остановка
    evs = [json.loads(ln) for ln in open(shard.events_path)]
    assert [(e["ev"], e["old_session"], e["new_session"]) for e in evs] == [("rotated", spare, spare + 1)]


def test_reconnects_and_overlap_write_one_decodable_file(tmp_path, monkeypatch):
    """E-024 в новой форме (В-142): шард пишет один файл на запуск и сутки при любом числе попыток и соединений; повторённые соединением кадры — дубли, не пишутся."""
    monkeypatch.setattr(sess, "PING_PERIOD_S", 3600)
    real_sleep = asyncio.sleep

    async def fast_sleep(s, *a):
        await real_sleep(0 if s <= 5 else s)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    shard = shard_for(tmp_path)

    async def idle(seq):
        await real_sleep(3600)

    monkeypatch.setattr(shard.session, "gap_watch", idle)
    calls = []

    class FakeWs:
        transport = None

        async def send(self, msg):
            pass

        async def ping(self):
            fut = asyncio.get_running_loop().create_future()
            fut.set_result(None)
            return fut

        def __aiter__(self):
            async def gen():
                for i in range(3):
                    yield json.dumps([with_u(B[4] + i)])  # каждая попытка шлёт одни и те же u (как перекрытие)
                    await real_sleep(0.002)

            return gen()

    class Conn:
        async def __aenter__(self):
            return FakeWs()

        async def __aexit__(self, *a):
            return False

    def connect(url, **kw):
        calls.append(url)
        if len(calls) == 3:
            raise asyncio.CancelledError  # третья попытка — остановка теста
        return Conn()

    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=connect))

    async def fake_dial(url, max_rtt_ms=None, ip=None):
        return object(), 250.0

    monkeypatch.setattr(sess, "dial", fake_dial)
    t0 = time.time_ns()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(shard.run_session_forever(7))
    t1 = time.time_ns()
    shard.writer.close()
    files = sorted(tmp_path.rglob("book00-*.tsv.zst"))
    assert [f.name for f in files] == ["book00-7.tsv.zst"] and (files[0].parent / "book00-7.tsv.zst.ok").exists()
    recs = read(files[0])
    assert [r["u"] for r in recs if r["t"] == "b"] == [B[4], B[4] + 1, B[4] + 2]  # вторая попытка — те же u — дубли сняты писателем
    assert [r["what"] for r in recs if r["t"] == "c"].count("connected") == 2 and shard.writer.dups == 3
    assert all(t0 <= r["rx_ns"] <= t1 for r in recs)  # приём не «уплывает»


def test_rtt_reject_before_websocket_then_accept_after_max_rejects(tmp_path, monkeypatch):
    """Путь с RTT выше порога отбраковывается на TCP connect (до TLS и upgrade, websockets не вызывается); после PROBE_MAX_REJECTS подряд соединение принимается."""
    monkeypatch.setattr(sess, "PING_PERIOD_S", 3600)
    real_sleep = asyncio.sleep

    async def fast_sleep(s, *a):
        await real_sleep(0 if s <= 5 else s)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    shard = shard_for(tmp_path)
    dials, connects = [], []

    async def fake_dial(url, max_rtt_ms=None, ip=None):
        dials.append(max_rtt_ms)
        return (None, 360.0) if max_rtt_ms is not None else (object(), 360.0)

    def connect(url, **kw):
        connects.append(kw["sock"])
        raise asyncio.CancelledError  # принятое соединение — конец теста

    monkeypatch.setattr(sess, "dial", fake_dial)
    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=connect))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(shard.run_session_forever(7))
    shard.writer.close()
    n = shard_mod.PROBE_MAX_REJECTS
    assert dials == [sess.CONNECT_RTT_MAX_MS] * n + [None] and len(connects) == 1  # n отбраковок на connect, затем приём без порога
    evs = [json.loads(ln) for ln in open(shard.events_path)]
    assert [e["ev"] for e in evs].count("path_rejected_rtt") == n and evs[0]["rtt_ms"] == 360


def test_connect_budget_never_exceeds_limit_in_any_window():
    """Граница из кода: сколько бы ни просили (худший старт 34 линии x 13 попыток = 442 и повторы), в любом окне WINDOW_S выдано не больше LIMIT мест."""
    now = [1000.0]
    b = budget_mod.ConnectBudget(limit=5, window_s=300.0, clock=lambda: now[0])
    grants = []
    for _ in range(442):
        while (w := b.try_take()) > 0:
            now[0] += w
        grants.append(now[0])
    assert len(grants) == 442 and grants[4] == 1000.0 and grants[5] == 1300.0  # первые LIMIT сразу, шестое — через окно после первого
    assert all(grants[i + 5] - grants[i] >= 300.0 for i in range(len(grants) - 5))  # любые LIMIT+1 подряд выданных — не короче окна
    assert b.used() <= 5


def test_connect_budget_starts_empty_after_boot():
    """Часы monotonic после загрузки могут быть меньше окна: пустое кольцо не должно блокировать."""
    b = budget_mod.ConnectBudget(limit=3, window_s=300.0, clock=lambda: 5.0)
    assert [b.try_take() for _ in range(3)] == [0.0, 0.0, 0.0] and b.try_take() == pytest.approx(300.0)


def test_session_takes_a_budget_slot_before_every_dial(tmp_path, monkeypatch):
    """Каждый TCP connect (в т.ч. отбракованный по RTT) идёт через бюджет; ожидание — событие connect_wait."""
    monkeypatch.setattr(sess, "PING_PERIOD_S", 3600)
    order = []

    class FakeBudget:
        async def acquire(self):
            order.append("take")
            return 2.5

    async def fake_dial(url, max_rtt_ms=None, ip=None):
        order.append("dial")
        raise asyncio.CancelledError

    monkeypatch.setattr(sess, "dial", fake_dial)
    shard = shard_mod.Shard(FakeAdapter(), str(tmp_path), "book00", "public", ["a"], run_id=7, budget=FakeBudget())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(shard.session.run(7, probe=True))
    shard.writer.close()
    assert order == ["take", "dial"]
    assert [json.loads(ln)["ev"] for ln in open(shard.events_path)] == ["connect_wait"]


def test_accept_streaming_summary_equals_list_based_definition():
    """Сутки не помещаются в списки: приёмка считает перцентили по счётчику и паузу по занятым мс — те же числа, что по спискам (stats.percentile, соседние записи)."""
    pytest.importorskip("zstandard")
    import random
    from collections import Counter

    import accept_ws

    rnd = random.Random(5)
    for n in (1, 2, 7, 100, 1001):
        xs = [rnd.randint(-5, 400) for _ in range(n)]
        for p in (0.5, 0.99):
            assert accept_ws.count_percentile(Counter(xs), p) == stats.percentile(xs, p)
    assert accept_ws.count_percentile(Counter(), 0.5) is None
    since = 1_000_000
    rows = sorted((since + rnd.randint(0, 5000), rnd.randint(0, 900)) for _ in range(300))
    st = accept_ws.Stream(since, since + 5000)
    for rx, lg in rows:
        st.add(rx, lg)
    out = st.summary()
    assert out["n"] == 300 and out["max_pause_ms"] == max(b[0] - a[0] for a, b in zip(rows, rows[1:]))
    assert out["lag_ms"] == {"p50": stats.percentile([lg for _, lg in rows], 0.5), "p99": stats.percentile([lg for _, lg in rows], 0.99), "max": max(lg for _, lg in rows)}
    buckets = {}
    for rx, lg in rows:
        buckets.setdefault(rx // 10_000, []).append(lg)
    assert out["lag_p50_10s_max"] == max(stats.percentile(v, 0.5) for v in buckets.values())
    assert accept_ws.Stream(0, 10).summary()["max_pause_ms"] is None


def test_read_lines_streams_and_drops_unfinished_tail(tmp_path):
    zstandard = pytest.importorskip("zstandard")
    import accept_ws

    p = tmp_path / "x.tsv.zst"
    p.write_bytes(zstandard.ZstdCompressor().compress("a\nb\nc".encode()))  # последняя строка без \n — запись оборвалась
    assert list(accept_ws.read_lines(str(p))) == ["a\n", "b\n"]
    e = codec.Encoder()
    text = e.record(RX0, B) + e.record(RX0 + 5, with_u(B[4] + 1))
    p.write_bytes(zstandard.ZstdCompressor().compress(text.encode()))
    assert list(codec.decode(accept_ws.read_lines(str(p)))) == list(codec.decode(text)) and len(list(codec.decode(text))) == 2


def test_dial_measures_rtt_and_rejects_above_threshold():
    """dial: локальный сервер — сокет и RTT возвращаются; порог ниже RTT — (None, rtt), сокет закрыт."""
    from wscore import dial as dial_mod

    async def go():
        srv = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}/x"
        sock, rtt = await dial_mod.dial(url, None)
        assert sock is not None and rtt >= 0
        sock.close()
        none, rtt2 = await dial_mod.dial(url, -1.0)
        assert none is None and rtt2 >= 0
        srv.close()

    asyncio.run(go())


def test_timeout_event_carries_tcp_state_of_live_sessions(tmp_path):
    """TK-532 (В-140): событие таймаута несёт состояние TCP живых сессий шарда (retransmits/backoff/rto/last_data_recv) — паузу можно отнести к потерям пути или к молчанию биржи."""
    from wscore import dial as dial_mod

    if dial_mod.tcp_state(__import__("socket").socket()) is None:
        pytest.skip("TCP_INFO только на Linux")

    async def go():
        srv = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        sock, _ = await dial_mod.dial(f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}/x", None)
        shard = shard_for(tmp_path)
        shard.session.timeout("gap", 1)  # без живых сессий поля tcp нет
        shard.session.socks[7] = sock
        shard.session.timeout("gap", 7)
        sock.close()
        srv.close()
        return [json.loads(ln) for ln in open(shard.events_path)]

    first, second = asyncio.run(go())
    assert "tcp" not in first
    st = second["tcp"]["7"]
    assert set(st) == {"retransmits", "backoff", "rto_ms", "unacked", "lost", "retrans", "last_data_recv_ms", "rtt_ms", "total_retrans"} and st["retransmits"] == 0 and st["rto_ms"] >= 0


def test_error_event_names_the_cause_of_a_close_without_frame(tmp_path, monkeypatch):
    """TK-532 (В-140): обрыв без close-кадра пишет в событие первопричину (FIN/RST), а не только ConnectionClosedError(None, None, None)."""
    real_sleep = asyncio.sleep

    async def fast_sleep(s, *a):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    shard = shard_for(tmp_path)
    calls = []

    def connect(url, **kw):
        calls.append(url)
        if len(calls) == 2:
            raise asyncio.CancelledError
        try:
            raise ConnectionResetError("rst")
        except ConnectionResetError as c:
            raise OSError("closed") from c

    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=connect))

    async def fake_dial(url, max_rtt_ms=None, ip=None):
        return object(), 250.0

    monkeypatch.setattr(sess, "dial", fake_dial)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(shard.run_session_forever(7))
    shard.writer.close()
    ev = [json.loads(ln) for ln in (tmp_path / "events-book00.jsonl").read_text().splitlines()]
    err = [e for e in ev if e["ev"] == "error"]
    assert len(err) == 1 and err[0]["cause"] == "ConnectionResetError('rst')"


def test_peer_ip_returns_remote_address_or_none():
    from wscore import dial as dial_mod

    class S:
        def getpeername(self):
            return ("2600:9000::1", 443, 0, 0)

    assert dial_mod.peer_ip(S()) == "2600:9000::1" and dial_mod.peer_ip(object()) is None


def test_gap_is_its_own_event_and_counter_not_a_timeout(tmp_path):
    """TK-532 (В-140): пауза данных шарда при живом цикле — событие gap со счётчиком gaps, таймаутом считается только мёртвое соединение (нет pong)."""
    shard = shard_for(tmp_path)
    shard.session.gap(3, 120)
    ev = [json.loads(ln) for ln in open(shard.events_path)]
    assert [e["ev"] for e in ev] == ["gap"] and ev[0]["loop_late_ms"] == 120 and ev[0]["tcp_clean"] is True and ev[0]["session"] == 3
    assert len(shard.m.gaps) == 1 and len(shard.m.timeouts) == 0
    assert shard.m.fragment("book00", shard.writer)["gaps"] == list(shard.m.gaps)


def test_gap_watch_emits_one_gap_per_pause_with_loop_lateness(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "GAP_TICK_S", 0.02)
    shard = shard_for(tmp_path)
    shard.m.last_rx = time.monotonic() - 10  # кадров нет дольше GAP_TIMEOUT_S

    async def go():
        try:
            await asyncio.wait_for(shard.session.gap_watch(5), 0.3)
        except asyncio.TimeoutError:
            pass

    asyncio.run(go())
    ev = [json.loads(ln) for ln in open(shard.events_path)]
    assert [e["ev"] for e in ev] == ["gap"]  # одна пауза — одно событие
    assert ev[0]["loop_late_ms"] >= 0 and len(shard.m.timeouts) == 0


def trade_at(E, a):
    return ("t", "AIOTUSDT", E, E - 100, a, "0.1742400", "410", a, a, True, "410")


def test_fresh_counts_written_late_records_per_kind_without_duplicates_and_snapshots(tmp_path):
    """TK-532: мера приёмки — записанные записи с rx − E > 2 с и макс. лаг по видам; дубль второй линии, начальные снимки подписки (settled=False) и запись вовремя не считаются поздними."""
    from wscore import fresh

    now_ms = 1_794_500_000_000
    rx = now_ms * 1_000_000
    w = DayWriter(str(tmp_path), "trade00", 7)
    w.records(rx, [trade_at(now_ms - 3000, 1)])
    w.records(rx + 5_000_000, [trade_at(now_ms - 3000, 1)])  # тот же a с другой линии — дубль
    w.records(rx, [trade_at(now_ms - 100, 2)])
    w.records(rx, [trade_at(now_ms - 9000, 3)], settled=False)  # снимок при подписке с устаревшим ts
    w.records(rx, [("b", "AVAXUSDT", now_ms - 80, now_ms - 90, 5, "8.6", "4", "8.7", "2")])
    fr = w.fresh.fragment(now_ms)
    assert fr["trade"] == {"late_1h": 1, "late_24h": 1, "max_1h_ms": 3000, "max_24h_ms": 3000}
    assert fr["book"] == {"late_1h": 0, "late_24h": 0, "max_1h_ms": 80, "max_24h_ms": 80}
    assert fresh.LATE_MS == 2000 and "mark" not in fr
    w.close()


def test_fresh_windows_hour_day_and_expiry():
    from wscore.fresh import Fresh

    now = 1_794_500_000_000
    f = Fresh()
    f.add("t", now - 26 * 3600_000, 7000)  # старше суток — выбрасывается
    f.add("t", now - 2 * 3600_000, 5000)  # в сутках, вне часа
    f.add("t", now - 600_000, 2500)  # в часе
    f.add("t", now - 1000, 90)
    f.add("m", now - 1000, 40)
    fr = f.fragment(now)
    assert fr["trade"] == {"late_1h": 1, "late_24h": 2, "max_1h_ms": 2500, "max_24h_ms": 5000}
    assert fr["mark"] == {"late_1h": 0, "late_24h": 0, "max_1h_ms": 40, "max_24h_ms": 40}
    assert Fresh().fragment(now) == {}


def test_health_aggregate_merges_fresh_across_shards_and_reads_old_fragments():
    from wscore import fresh

    now = 1_794_500_000_000
    base = {"ts_ms": now, "started_ms": now - 5000, "with_data": [], "connected": 1, "ping_ms": [], "lag_in": [], "lag_out": [], "timeouts": [], "reconnects": [], "last_frame_ms": now, "last_write_ms": now}
    a = dict(base, shard="trade00", fresh={"trade": {"late_1h": 3, "late_24h": 5, "max_1h_ms": 4100, "max_24h_ms": 6400}})
    b = dict(base, shard="trade01", fresh={"trade": {"late_1h": 1, "late_24h": 1, "max_1h_ms": 2200, "max_24h_ms": 2200}, "mark": {"late_1h": 0, "late_24h": 0, "max_1h_ms": 90, "max_24h_ms": 120}})
    old = dict(base, shard="book00")  # фрагмент прежнего кода — без fresh
    h = health.aggregate([a, b, old], now, [], "fake")
    assert h["fresh"]["late_ms"] == fresh.LATE_MS
    assert h["fresh"]["kinds"]["trade"] == {"late_1h": 4, "late_24h": 6, "max_1h_ms": 4100, "max_24h_ms": 6400}
    assert h["fresh"]["kinds"]["mark"]["max_24h_ms"] == 120
    assert health.aggregate([], now, [], "fake")["fresh"]["kinds"] == {}


def test_session_read_skips_subscription_snapshots_in_fresh(tmp_path, monkeypatch):
    """Первые GRACE_S после подписки записи не идут в свежесть, дальше — идут."""
    monkeypatch.setattr(sess, "GRACE_S", 0.1)
    shard = shard_for(tmp_path)
    ms = lambda: int(time.time() * 1000)  # noqa: E731

    class Ws:
        async def __aiter__(self):  # noqa: D105
            yield json.dumps([trade_at(ms() - 5000, 1)])  # снимок сразу после подписки
            await asyncio.sleep(0.25)
            yield json.dumps([trade_at(ms() - 4000, 2)])  # после окна — настоящая задержка

    async def go():
        await shard.session._read(Ws(), 1, None, None)

    asyncio.run(go())
    fr = shard.writer.fresh.fragment(ms())
    assert fr["trade"]["late_24h"] == 1 and 3900 <= fr["trade"]["max_24h_ms"] < 4500
    shard.writer.close()


def test_entry_sink_logs_every_record_and_flags_late_ones_outside_grace(tmp_path):
    """Проба входов: лог всех записей (читается entrycmp), поздние — отдельным файлом; запись в окне после подписки в late не идёт."""
    import entrycmp
    from wscore.entry import Sink

    now_ms = 1_794_500_000_000
    rx = now_ms * 1_000_000
    s = Sink(str(tmp_path), "com")
    s.frame("trade00", rx, [trade_at(now_ms - 3000, 1), trade_at(now_ms - 50, 2)])
    s.frame("trade00", rx, [trade_at(now_ms - 6000, 3)], settled=False)
    s.take_stats()
    s.close()
    late = [json.loads(ln) for ln in open(tmp_path / "late-com.jsonl")]
    assert [(x["key"], x["lag_ms"]) for x in late] == [(1, 3000)]
    recs = list(entrycmp.read_recs(str(tmp_path), "com"))
    assert [(r[1], r[3], r[0] - r[4]) for r in recs] == [("t", 1, 3000), ("t", 2, 50), ("t", 3, 6000)]
    st = [json.loads(ln) for ln in open(tmp_path / "stats-com.jsonl")]
    assert st[0]["recs"] == 3 and st[0]["late"] == 1 and st[0]["lag_max_ms"] == 6000


def test_session_read_counts_unparsed_data_frames_in_fresh_as_raw(tmp_path, monkeypatch):
    """Кадр данных, который адаптер не разобрал (пишется сырым), попадает в свежесть видом raw; кадр без ts (lag_ms = None) — нет."""
    monkeypatch.setattr(sess, "GRACE_S", 0.0)
    shard = shard_for(tmp_path)
    lags = iter([4200.0, 90.0, None, 90.0, 50.0, 60.0])  # кадр 0: raw-свежесть, затем выборка лага (каждый 64-й кадр); кадр 1 без ts; кадр 2 вовремя; кадр 3 — копия кадра 0
    monkeypatch.setattr(shard.session.ad, "lag_ms", lambda frame, rx: next(lags, 90.0))
    monkeypatch.setattr(shard.session.ad, "parse", lambda frame: None)  # все кадры — не разобраны

    class Ws:
        async def __aiter__(self):  # noqa: D105
            for f in ("x", "x1", "x2", "x"):  # «x» — кадр 0 и его копия с другой линии (копия не считается второй доставкой)
                yield f

    asyncio.run(shard.session._read(Ws(), 1, None, None))
    fr = shard.writer.fresh.fragment(int(time.time() * 1000))
    assert fr["raw"]["late_24h"] == 1 and fr["raw"]["max_24h_ms"] == 4200  # копия кадра 0 (лаг 50, поздняя или нет) в счёт не вошла
    shard.writer.close()


def test_with_host_and_parse_entrance():
    from wscore import dial as d

    assert d.with_host("wss://stream.bybit.com/v5/public/linear", "stream.bybit.kz") == "wss://stream.bybit.kz/v5/public/linear"
    assert d.with_host("wss://h:9443/x", "y.z") == "wss://y.z:9443/x"
    assert d.parse_entrance("stream.bybit.com@13.226.244.52") == ("stream.bybit.com", "13.226.244.52")
    assert d.parse_entrance("stream.bybit.kz") == ("stream.bybit.kz", None)


def test_session_run_with_entrance_dials_its_host_and_pinned_ip(tmp_path, monkeypatch):
    """TK-532: линия с входом идёт на другое имя хоста и, если задан ip, на этот адрес; без входа — как прежде (вход адаптера, DNS)."""
    seen = []

    async def fake_dial(url, max_rtt_ms=None, ip=None):
        seen.append((url, ip))
        raise asyncio.CancelledError

    monkeypatch.setattr(sess, "dial", fake_dial)
    shard = shard_for(tmp_path)
    for kw in ({"entrance": ("stream.example.kz", "203.0.113.5")}, {"entrance": ("stream.example.ge", None)}, {}):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(shard.session.run(7, probe=True, **kw))
    shard.writer.close()
    assert seen == [("wss://stream.example.kz/public", "203.0.113.5"), ("wss://stream.example.ge/public", None), ("wss://fake/public", None)]


def test_lanes_use_their_own_entrance_and_unset_lane_keeps_adapter_entrance(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "STATS_PERIOD_S", 3600)
    shard = shard_mod.Shard(FakeAdapter(), str(tmp_path), "book00", "public", ["a"], run_id=7, lanes=2, entrances=[None, ("stream.example.kz", None)])
    got = {}

    async def fake_forever(seq, accepted=None, ent=None):
        got[seq] = ent.current()
        await asyncio.sleep(3600)

    monkeypatch.setattr(shard, "run_session_forever", fake_forever)

    async def go():
        task = asyncio.create_task(shard.run())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert got == {7: None, 7 + shard_mod.LANE_SEQ_STEP: ("stream.example.kz", None)}


def zstd_complete(path):
    """Как `zstd -t`: каждый кадр файла завершён (файл из нескольких кадров — все)."""
    zstandard = pytest.importorskip("zstandard")
    data = path.read_bytes()
    while data:
        d = zstandard.ZstdDecompressor().decompressobj()
        d.decompress(data)
        if not d.eof:
            return False
        data = d.unused_data
    return True


def test_stop_closes_sessions_before_writer_so_the_file_is_complete(tmp_path, monkeypatch):
    """TK-527 (дефект ядра): остановка (SIGTERM = отмена шарда) — сессии пишут `closed` ДО закрытия писателя; иначе `closed` открывал файл заново и оставлял кадр zstd незакрытым
    (`zstd -t` падал, `.ok` на строку меньше, приём последней строки считался от нуля). Файл завершён, `.ok` = число строк, последняя строка — closed, время приёма последней строки верное."""
    monkeypatch.setattr(sess, "PING_PERIOD_S", 3600)
    monkeypatch.setattr(stats, "STATS_PERIOD_S", 3600)
    shard = shard_for(tmp_path)

    async def idle(seq):
        await asyncio.sleep(3600)

    monkeypatch.setattr(shard.session, "gap_watch", idle)

    class FakeWs:
        async def send(self, msg):
            pass

        async def ping(self):
            fut = asyncio.get_running_loop().create_future()
            fut.set_result(None)
            return fut

        def __aiter__(self):
            async def gen():
                yield json.dumps([with_u(B[4])])
                await asyncio.sleep(3600)  # соединение живо до остановки

            return gen()

    class Conn:
        async def __aenter__(self):
            return FakeWs()

        async def __aexit__(self, *a):
            await asyncio.sleep(0.2)  # закрытие соединения (close_timeout) — повторный сигнал приходит внутри очистки
            return False

    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=lambda url, **kw: Conn()))

    async def fake_dial(url, max_rtt_ms=None, ip=None):
        return object(), 250.0

    monkeypatch.setattr(sess, "dial", fake_dial)

    async def go():
        task = asyncio.create_task(shard.run())
        await asyncio.sleep(0.3)
        stop = shard_mod.cancel_once(task)
        stop()
        await asyncio.sleep(0.05)  # очистка идёт (закрытие соединения занимает 0,2 с)
        stop()  # второй SIGTERM (systemd + terminate родителя) очистку не прерывает
        with pytest.raises(asyncio.CancelledError):
            await task

    t0 = time.time_ns()
    asyncio.run(go())
    t1 = time.time_ns()
    f = next(tmp_path.rglob("book00-7.tsv.zst"))
    assert zstd_complete(f)
    recs = read(f)
    assert [r["what"] for r in recs if r["t"] == "c"] == ["connected", "subscribed", "closed"] and recs[-1]["t"] == "c" and recs[-1]["what"] == "closed"
    assert int((f.parent / (f.name + ".ok")).read_text()) == len(recs)  # `.ok` — число строк, включая closed
    assert t0 <= recs[-1]["rx_ns"] <= t1  # приём последней строки — настоящее время, а не «от нуля нового кадра»
    shard.writer.control(time.time_ns(), "late", "после остановки")  # запись после остановки не открывает файл заново
    assert zstd_complete(f) and len(read(f)) == len(recs)


def test_writer_ignores_writes_after_final_close(tmp_path):
    w = DayWriter(str(tmp_path), "book00", 5)
    w.records(RX0, [B])
    w.close(final=True)
    w.records(RX0 + 1, [with_u(B[4] + 1)])
    w.control(RX0 + 2, "closed", "1")
    f = next(tmp_path.rglob("book00-5.tsv.zst"))
    assert zstd_complete(f) and [r["t"] for r in read(f)] == ["b"] and (f.parent / (f.name + ".ok")).read_text() == "1\n"


def test_raw_data_frame_copies_from_the_other_line_are_dropped_control_frames_are_kept(tmp_path):
    """Кадр данных (с временем биржи), который адаптер не разобрал (сделки RPI), с двух линий пишется один раз; ответы на подписку и pong (без времени) — все, как пришли.
    Копия вне окна RAW_SEEN_MS — новая доставка. Начальный снимок (settled=False) в свежесть не идёт, но дубли снимает."""
    from wscore import writer

    now_ms = 1_794_500_000_000
    rx = now_ms * 1_000_000
    frame = '{"topic":"publicTrade.X","ts":%d,"data":[{"RPI":true}]}' % (now_ms - 3000)
    ack = '{"success":true,"op":"subscribe"}'
    w = DayWriter(str(tmp_path), "trade00", 6)
    w.raw(rx, frame, lag_ms=3000.0)  # линия 1
    w.raw(rx + 4_000_000, frame, lag_ms=3004.0)  # линия 2: тот же кадр
    w.raw(rx + 5_000_000, ack)
    w.raw(rx + 6_000_000, ack)  # ответ на подписку второй линии — тот же текст, но не данные
    w.raw(rx + 7_000_000, frame + " ", lag_ms=10.0, settled=False)  # другой текст — другой кадр
    w.raw(rx + 8_000_000, frame + " ", lag_ms=10.0, settled=False)
    w.raw(rx + (writer.RAW_SEEN_MS + 1000) * 1_000_000, frame, lag_ms=3000.0)  # та же строка через минуту — новая доставка
    w.close()
    recs = read(next(tmp_path.rglob("trade00-6.tsv.zst")))
    assert [r["frame"] for r in recs if r["t"] == "r"] == [frame, ack, ack, frame + " ", frame]
    assert w.dups == 2
    assert w.fresh.fragment(now_ms + 70_000)["raw"]["late_24h"] == 2  # копия не считается второй доставкой; снимок (settled=False) — вне свежести


def test_dial_family_marker_takes_the_name_but_only_that_family():
    """Вход «хост@v4»/«хост@v6» — разрешить имя, но взять адрес этого семейства (общий выбор семейства в dial, не закреплённый адрес); «хост@ip» — как прежде этот адрес."""
    import socket

    from wscore import dial as d

    assert d.parse_entrance("stream.bybit.com@v4") == ("stream.bybit.com", "v4") and d.parse_entrance("stream.bybit.com@v6") == ("stream.bybit.com", "v6")

    async def go():
        srv = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        loop, seen = asyncio.get_running_loop(), []
        orig = loop.getaddrinfo

        async def spy(host, p, **kw):
            seen.append((host, kw.get("family", 0)))
            return await orig("127.0.0.1", p, type=kw["type"])  # слушатель один, v4: проверяем, что и с чем спросили

        loop.getaddrinfo = spy
        out = []
        for ip in ("v4", "v6", "203.0.113.5", None):
            sock, _ = await d.dial(f"wss://stream.example.com:{port}/x", None, ip)
            assert sock.family == socket.AF_INET
            sock.close()
        srv.close()
        return seen

    assert asyncio.run(go()) == [("stream.example.com", socket.AF_INET), ("stream.example.com", socket.AF_INET6), ("203.0.113.5", 0), ("stream.example.com", 0)]


def test_build_lines_cli_overrides_adapter_config_and_dash_is_adapter_entrance():
    from wscore.entrances import build_lines

    cfg = [["stream.a.com@v4", "stream.a.com@v6"], ["stream.b.kz", "stream.b.ge"]]
    assert build_lines([], cfg, 2) == [[("stream.a.com", "v4"), ("stream.a.com", "v6")], [("stream.b.kz", None), ("stream.b.ge", None)]]
    assert build_lines(["-", "stream.c.io,stream.c.ru@1.2.3.4"], cfg, 2) == [[None], [("stream.c.io", None), ("stream.c.ru", "1.2.3.4")]]
    assert build_lines(["stream.d.com"], [], 2) == [[("stream.d.com", None)], [None]]  # нет конфига — вход адаптера


def test_bybit_adapter_lines_have_backups_and_no_entrance_is_shared_between_lines():
    import bybit_ws
    from wscore.entrances import build_lines

    lines = build_lines([], bybit_ws.Adapter.entrances, 2)
    assert all(len(x) >= 2 for x in lines)  # у каждой линии есть запасной вход
    assert not set(lines[0]) & set(lines[1])  # линии не делят вход: независимые пути


def test_line_switches_entrance_after_repeated_connect_failures_and_not_after_a_long_session(tmp_path, monkeypatch):
    """Переход на запасной вход: FAIL_LIMIT коротких сессий подряд -> следующий вход по кругу (event entrance_switch, счётчик health); сессия дольше SHORT_SESSION_S счёт сбрасывает; один вход — перехода нет."""
    from wscore.entrances import FAIL_LIMIT, Entrances

    real_sleep = asyncio.sleep

    async def zero_sleep(s, *a):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", zero_sleep)
    A, B = ("stream.a.example", None), ("stream.b.example", "v4")
    shard = shard_for(tmp_path)
    calls = []

    async def failing_run(seq, probe=False, accepted=None, entrance=None):
        calls.append(entrance)
        if len(calls) == 2 * FAIL_LIMIT + 2:
            raise asyncio.CancelledError  # остановка теста
        raise OSError("refused")

    monkeypatch.setattr(shard.session, "run", failing_run)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(shard.run_session_forever(7, ent=Entrances([A, B], 1)))
    assert calls == [A] * FAIL_LIMIT + [B] * FAIL_LIMIT + [A, A]
    evs = [e for e in map(json.loads, open(shard.events_path)) if e["ev"] == "entrance_switch"]
    assert [(e["line"], e["reason"], e["from"], e["to"], e["fails"]) for e in evs] == [(1, "connect_fail", "stream.a.example", "stream.b.example@v4", FAIL_LIMIT), (1, "connect_fail", "stream.b.example@v4", "stream.a.example", FAIL_LIMIT)]
    assert len(shard.m.entrance_switches) == 2

    calls.clear()  # сессии живут дольше порога: счёт неудач каждый раз с нуля
    monkeypatch.setattr(shard_mod, "SHORT_SESSION_S", 0.05)

    async def long_then_fail(seq, probe=False, accepted=None, entrance=None):
        calls.append(entrance)
        if len(calls) == 3 * FAIL_LIMIT:
            raise asyncio.CancelledError
        await real_sleep(0.06)
        raise OSError("reset")

    monkeypatch.setattr(shard.session, "run", long_then_fail)
    n_events = len(shard.m.entrance_switches)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(shard.run_session_forever(8, ent=Entrances([A, B], 1)))
    assert set(calls) == {A} and len(shard.m.entrance_switches) == n_events

    calls.clear()  # линия с одним входом не переходит никуда
    monkeypatch.setattr(shard_mod, "SHORT_SESSION_S", 60.0)
    monkeypatch.setattr(shard.session, "run", failing_run)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(shard.run_session_forever(9, ent=Entrances([A], 0)))
    assert set(calls) == {A} and len(shard.m.entrance_switches) == n_events


def test_degraded_session_is_replaced_through_the_next_entrance_but_rotation_keeps_the_entrance(tmp_path, monkeypatch):
    """Задержка доставки (сессия degraded: лаг или нет pong) — замена идёт через следующий вход, событие entrance_switch раньше path_switch; плановая смена по возрасту вход не меняет."""
    from wscore.entrances import FAIL_LIMIT  # noqa: F401

    monkeypatch.setattr(shard_mod, "OVERLAP_S", 0.05)
    monkeypatch.setattr(stats, "STATS_PERIOD_S", 3600)
    A, B = ("stream.a.example", None), ("stream.b.example", None)
    log = []

    def make(name):
        sh = shard_mod.Shard(FakeAdapter(), str(tmp_path), name, "public", ["a"], run_id=7, entrances=[[A, B]])

        async def fake_forever(seq, accepted=None, ent=None):
            log.append((seq, ent.current()))
            if accepted is not None:
                await asyncio.sleep(0.05)
                accepted.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr(sh, "run_session_forever", fake_forever)
        return sh

    async def go(sh, degrade):
        task = asyncio.create_task(sh.run())
        await asyncio.sleep(1.2)
        if degrade:
            sh.watch.degraded.add(log[0][0])
        await asyncio.sleep(2.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    shard = make("book00")
    asyncio.run(go(shard, True))
    s1 = log[0][0]
    assert log[:2] == [(s1, A), (s1 + 1, B)]  # замена испорченной — через запасной вход
    evs = [json.loads(ln) for ln in open(shard.events_path)]
    names = [e["ev"] for e in evs if e["ev"] in ("entrance_switch", "path_switch")]
    assert names == ["entrance_switch", "path_switch"]
    sw = next(e for e in evs if e["ev"] == "entrance_switch")
    assert (sw["reason"], sw["from"], sw["to"], sw["old_session" if "old_session" in sw else "session"]) == ("late_delivery", "stream.a.example", "stream.b.example", s1)
    assert len(shard.m.entrance_switches) == 1

    log.clear()
    monkeypatch.setattr(shard_mod, "MAX_AGE_S", 0.5)  # плановая смена по возрасту
    shard2 = make("book01")
    asyncio.run(go(shard2, False))
    assert len(log) >= 2 and {e for _, e in log} == {A}
    assert not any(json.loads(ln)["ev"] == "entrance_switch" for ln in open(shard2.events_path))


def test_health_counts_entrance_switches_per_hour_and_day_and_reads_old_fragments():
    now = 1_794_500_000_000
    f = {"shard": "book00", "ts_ms": now, "started_ms": now - 5000, "with_data": [], "connected": 1, "ping_ms": [], "lag_in": [], "lag_out": [], "timeouts": [], "reconnects": [],
         "entrance_switches": [now - 10, now - 2 * 3600_000, now - 30 * 3600_000], "last_frame_ms": now, "last_write_ms": now}
    h = health.aggregate([f, dict(f, entrance_switches=[now - 5])], now, ["AVAXUSDT"], "fake")
    assert h["ws"]["entrance_switches_1h"] == 2 and h["ws"]["entrance_switches_24h"] == 3
    f.pop("entrance_switches")
    assert health.aggregate([f], now, ["AVAXUSDT"], "fake")["ws"]["entrance_switches_1h"] == 0  # фрагмент прежнего кода читается


def test_metrics_fragment_carries_entrance_switches(tmp_path):
    from wscore.metrics import Metrics

    m = Metrics()
    m.entrance_switch()
    frag = m.fragment("book00", DayWriter(str(tmp_path), "book00", 1))
    assert len(frag["entrance_switches"]) == 1
