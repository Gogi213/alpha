"""arb_turnover.py на синтетических файлах trade (zstd, растут между проходами): проходы с дозаписью, перезапуск с диска, bootstrap — против пересчёта «в лоб»."""
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
zstandard = pytest.importorskip("zstandard")
import arb_daemon  # noqa: E402
import arb_live  # noqa: E402
import arb_turnover  # noqa: E402
import common  # noqa: E402
import test_inc as ti  # noqa: E402

MIN = 60_000
STAMP = ti.T_BEGIN // 1000 - 600                     # метка запуска сборщика (с): файлы trade<N>-<метка>.tsv.zst
WINDOW = 50 * MIN
SYMS = {                                             # монета -> биржи (DDD — одна биржа: в карту монет не входит, её сделки в файлах игнорируются)
    "AAA": ("binance", "bybit", "okx", "bingx", "gate"),
    "BBB": ("binance", "okx"),
    "CCC": ("bybit", "bingx"),
    "DDD": ("binance",),
}
NAME = {"binance": "{}USDT", "bybit": "{}USDT", "okx": "{}-USDT-SWAP", "bingx": "{}-USDT", "gate": "{}_USDT"}
CTVAL = {"AAA": 0.1, "BBB": 10.0, "CCC": 1.0, "DDD": 1.0}      # OKX: монет в контракте (ctVal)
QUANTO = {"AAA": 0.01, "BBB": 1.0, "CCC": 1.0, "DDD": 1.0}      # Gate: монет в контракте (quanto_multiplier, в пуле строкой)
LANES = ("00", "01")                                 # два файла на биржу: сделки символа идут то в один, то в другой


class Env:
    def __init__(self, tmp, monkeypatch, seed=1):
        self.tmp, self.live = tmp, tmp / "live"
        self.live.mkdir()
        self.day = datetime.fromtimestamp(ti.T_BEGIN / 1000, timezone.utc).date().isoformat()
        self.start = ti.T_BEGIN + 20 * MIN
        for obj, name, val in ((arb_live, "START_MS", self.start), (common, "MINRUN", STAMP), (common, "MINRUN_EX", {"gate": STAMP}), (common, "SINCE", {}),
                               (arb_live, "WINDOW_MS", WINDOW), (arb_turnover, "WINDOW_MS", WINDOW), (arb_live, "HOLD", str(tmp / "no-hold"))):
            monkeypatch.setattr(obj, name, val)
        rng = np.random.default_rng(seed)
        self.rows = {ex: [] for ex in common.EXCHANGES}   # (rx, лаг, E−T, имя символа, цена, количество, монета)
        self.paths = {}
        for ex in common.EXCHANGES:
            root = tmp / ex / "data"
            monkeypatch.setitem(common.ROOTS, ex, str(root))
            (root / self.day).mkdir(parents=True)
            pool = [NAME[ex].format(c) for c, exs in SYMS.items() if ex in exs]
            meta = {NAME[ex].format(c): ({"quanto_multiplier": str(QUANTO[c])} if ex == "gate" else {"ctVal": CTVAL[c]}) for c, exs in SYMS.items() if ex in exs}
            (root / f"pool-{self.day}.json").write_text(json.dumps({"pool": pool, "meta": meta}))
            self.paths[ex] = {ln: root / self.day / f"trade{ln}-{STAMP}.tsv.zst" for ln in LANES}
            for p in self.paths[ex].values():
                p.write_bytes(b"")
                if ex == "gate":     # пробный запуск Gate (метка раньше постоянного) — не zstd: читать его нельзя, discover обязан пропустить
                    (p.parent / p.name.replace(str(STAMP), str(STAMP - 115))).write_bytes(b"trial run, must be skipped")
            for coin, exs in SYMS.items():
                if ex not in exs:
                    continue
                n = 1500
                rx = np.sort(rng.integers(ti.T_BEGIN - 10 * MIN, ti.T_BEGIN + 180 * MIN, n))
                lag, et = rng.integers(1, 5000, n), rng.integers(0, 200, n)    # лаг до 5 с: часть сделок по времени T попадает в минуту раньше минуты приёма
                p, q = 100 * (1 + rng.normal(0, 0.01, n)), rng.uniform(0.01, 5, n)
                for i in range(n):
                    self.rows[ex].append((int(rx[i]), int(lag[i]), int(et[i]), NAME[ex].format(coin), float(p[i]), float(q[i]), coin))
            self.rows[ex].sort(key=lambda r: r[0])
        self.cctx = {(ex, ln): zstandard.ZstdCompressor(level=1).compressobj() for ex in common.EXCHANGES for ln in LANES}
        self.sent = {ex: 0 for ex in common.EXCHANGES}
        self.prev_rx = {(ex, ln): None for ex in common.EXCHANGES for ln in LANES}

    def grow(self, now):
        """Дописывает в файлы все сделки с приёмом ≤ now (как сборщик: блок zstd сбрасывается, кадр остаётся открытым)."""
        for ex in common.EXCHANGES:
            bufs = {ln: io.BytesIO() for ln in LANES}
            rows, i = self.rows[ex], self.sent[ex]
            while i < len(rows) and rows[i][0] <= now:
                rx, lag, et, sym, p, q, _ = rows[i]
                ln = LANES[i % 2]
                if self.prev_rx[(ex, ln)] is None:
                    bufs[ln].write(f"c\t{(rx - 1) * 1_000_000}\tconnected\t-\n".encode())
                    self.prev_rx[(ex, ln)] = rx - 1
                bufs[ln].write(f"t\t{(rx - self.prev_rx[(ex, ln)]) * 1_000_000}\t{lag}\t{et}\t{sym}\t0\t{p!r}\t{q!r}\t0\t1\t{i % 2}\t\n".encode())
                self.prev_rx[(ex, ln)] = rx
                i += 1
            self.sent[ex] = i
            for ln, buf in bufs.items():
                data = buf.getvalue()
                if data:
                    c = self.cctx[(ex, ln)]
                    with open(self.paths[ex][ln], "ab") as f:
                        f.write(c.compress(data) + c.flush(zstandard.COMPRESSOBJ_FLUSH_BLOCK))

    def close_files(self):
        for ex in common.EXCHANGES:
            for ln, p in self.paths[ex].items():
                Path(str(p) + ".ok").write_bytes(b"")

    def reference(self, now):
        """Пересчёт в лоб по всем сделкам, пришедшим к моменту: окно — по времени сделки T = приём − лаг − (E−T); OKX × ctVal, Gate × quanto_multiplier."""
        t1 = (now - arb_turnover.MARGIN_MS) // MIN * MIN
        t0 = max(arb_live.START_MS, t1 - WINDOW)
        out = {coin: {ex: 0.0 for ex in exs} for coin, exs in SYMS.items() if len(exs) > 1}
        for ex, rows in self.rows.items():
            for rx, lag, et, _, p, q, coin in rows:
                if rx > now or coin not in out:
                    continue
                if t0 // MIN <= (rx - lag - et) // MIN < t1 // MIN:
                    out[coin][ex] += p * q * (CTVAL[coin] if ex == "okx" else QUANTO[coin] if ex == "gate" else 1.0)
        return t0, t1, out


def tick(env, svc, k):
    now = ti.T_BEGIN + k * MIN + 30_000
    env.grow(now)
    svc.tick(now)
    return now


def verify(env, now, ctx):
    doc = json.loads((env.live / "turnover.json").read_text())
    t0, t1, ref = env.reference(now)
    assert (doc["t0"], doc["t1"]) == (t0, t1), ctx
    assert set(doc["coin"]) == set(ref), ctx
    for coin, per in ref.items():
        assert set(doc["coin"][coin]) == set(per), (ctx, coin)
        for ex, v in per.items():
            assert abs(doc["coin"][coin][ex] - v) <= 1.0, (ctx, coin, ex, doc["coin"][coin][ex], v)
    assert any(v > 0 for per in doc["coin"].values() for v in per.values()), ctx


def run(env, svc_box, start, end, restart_at=(), compare_every=5):
    for k in range(start, end + 1, 5):
        if k in restart_at:
            svc_box[0] = arb_turnover.Service(str(env.live), now_ms=ti.T_BEGIN + k * MIN + 30_000, par=1)     # состояние с диска, распаковщики греются с начала файлов
        now = tick(env, svc_box[0], k)
        if (k - start) % compare_every == 0 or k == end:
            verify(env, now, ("tick", k))


def test_passes_with_appends_equal_full_recount_and_restart_from_disk(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    env.grow(ti.T_BEGIN - 9 * MIN)                      # в файлах почти ничего — bootstrap пуст, дальше только дозапись
    svc_box = [arb_turnover.Service(str(env.live), now_ms=ti.T_BEGIN - 9 * MIN, par=1)]
    run(env, svc_box, 30, 175, restart_at=(70, 120), compare_every=5)
    assert svc_box[0].S["parse"] and all(r.st["off"] > 0 for r in svc_box[0].readers.values())


def test_bootstrap_then_tail_equals_full_recount(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, seed=2)
    now0 = ti.T_BEGIN + 100 * MIN + 30_000
    env.grow(now0)                                      # в файлах уже 110 минут записи: службу поднимает bootstrap
    svc_box = [arb_turnover.Service(str(env.live), now_ms=now0, par=1)]
    assert svc_box[0].S["parse"] and all(st["off"] > 0 for st in svc_box[0].S["parse"].values())
    run(env, svc_box, 100, 175, restart_at=(140,), compare_every=5)


def test_closed_file_is_not_read_again(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, seed=3)
    env.grow(ti.T_BEGIN + 30 * MIN)
    svc_box = [arb_turnover.Service(str(env.live), now_ms=ti.T_BEGIN + 30 * MIN, par=1)]
    run(env, svc_box, 35, 175, compare_every=20)        # все сделки (приём до +180 мин) уже в файлах
    env.grow(ti.T_BEGIN + 181 * MIN)
    env.close_files()
    now = tick(env, svc_box[0], 185)                    # закрытые файлы дочитываются до конца и помечаются done
    verify(env, now, "after close")
    svc = svc_box[0]
    assert svc.readers == {} or all(r.st["done"] for r in svc.readers.values())
    assert all(st["done"] for st in svc.S["parse"].values())
    for ex in common.EXCHANGES:
        for p in env.paths[ex].values():
            p.write_bytes(b"not a zstd frame")          # повторное чтение упало бы на разборе
    now = tick(env, svc, 190)
    verify(env, now, "garbage after close")
    svc2 = arb_turnover.Service(str(env.live), now_ms=now, par=1)     # и после перезапуска закрытые файлы не открываются
    now = tick(env, svc2, 195)
    verify(env, now, "restart after close")


def test_window_edges_follow_trade_time_not_receive_time():
    t = arb_turnover
    bins = {}
    m0 = 1000
    # сделка принята в минуту 1010, но по времени T (приём − лаг − (E−T)) — в 1009
    arrs = {b"S": (np.array([1010 * MIN + 500], "i8").tobytes(), None, np.array([2.0], "f8").tobytes(), np.array([3.0], "f8").tobytes(), None, np.array([800], "i4").tobytes())}
    t.ingest(bins, m0, "okx", arrs, {b"S": 0.5})
    key = ("okx", "S")
    assert t.window_sum(bins, m0, key, 1009, 1010) == pytest.approx(3.0)       # 2 * 3 * 0,5
    assert t.window_sum(bins, m0, key, 1010, 1011) == 0.0
