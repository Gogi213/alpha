"""Тесты общего ядра (wscore): на поддельном адаптере — ядро не знает биржу (В-142)."""
import asyncio
import json
import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(__file__))
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
         "lag_in": [200.0, 230.0], "lag_out": [400.0], "timeouts": [now - 10, now - 2 * 3600_000], "reconnects": [], "last_frame_ms": now - 5, "last_write_ms": now - 500}
    h = health.aggregate([f], now, ["AVAXUSDT", "ETHUSDT"], "fake")
    assert h["schema"] == 1 and h["exchange"] == "fake"
    assert h["symbols"] == {"subscribed": 2, "with_data_1m": 1}  # btcusdt вне пула не считается
    assert h["ws"]["timeouts_1h"] == 1 and h["ws"]["timeouts_24h"] == 2 and h["ws"]["reconnects_1h"] == 0
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

    async def fake_forever(seq, accepted=None):
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

    async def fake_forever(seq, accepted=None):
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

    async def fake_forever(seq, accepted=None):
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
