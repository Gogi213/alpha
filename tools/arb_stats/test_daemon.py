"""Служба целиком на синтетических файлах book (zstd, растут между проходами): проход, перезапуск с диска, bootstrap — против полного счёта."""
import io
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
zstandard = pytest.importorskip("zstandard")
import analyze  # noqa: E402
import arb_daemon  # noqa: E402
import arb_inc  # noqa: E402
import arb_live  # noqa: E402
import common  # noqa: E402
import parse_inc  # noqa: E402
import test_inc as ti  # noqa: E402

MIN = 60_000
RUN = 1794656001
RUNS = {"gate": common.MINRUN_EX["gate"]}      # у Gate метка постоянного запуска — своя (common.minrun); пробный запуск раньше неё пропускается
SYMS = {   # монета -> биржи
    "AAA": ("binance", "bybit", "okx", "bingx", "gate"),
    "BBB": ("binance", "bybit", "okx"),
    "CCC": ("bybit", "bingx"),
    "DDD": ("binance",),            # одна биржа — пар нет
    "EEE": ("okx", "gate"),         # пара только с Gate
}
NAME = {"binance": "{}USDT", "bybit": "{}USDT", "okx": "{}-USDT-SWAP", "bingx": "{}-USDT", "gate": "{}_USDT"}
META = {"gate": {"quanto_multiplier": "0.1"}}   # монет в контракте: у остальных биржа отдаёт размер в монете (OKX — ctVal, здесь 1)


def line(delta_ns, lag, sym, bid, ask, bsz, asz):
    return f"b\t{delta_ns}\t{lag}\t-\t{sym}\t-\t{bid!r}\t{bsz!r}\t{ask!r}\t{asz!r}\n".encode()


class Env:
    def __init__(self, tmp, monkeypatch, seed=1):
        self.tmp, self.live = tmp, tmp / "live"
        self.day = datetime.fromtimestamp(ti.T_BEGIN / 1000, timezone.utc).date().isoformat()
        self.start = ti.T_BEGIN + 70 * MIN
        monkeypatch.setattr(arb_daemon, "START_MS", self.start)
        monkeypatch.setattr(arb_live, "START_MS", self.start)
        monkeypatch.setattr(parse_inc, "DECOMP", ["cat"])
        monkeypatch.setattr(common, "SINCE", {})            # время теста — до первого дня данных Gate
        rng = np.random.default_rng(seed)
        self.rows = {ex: [] for ex in common.EXCHANGES}      # (rx, e, имя символа, bid, ask, bsz, asz)
        self.paths, self.plain_paths, self.plain = {}, {}, {}
        for ex in common.EXCHANGES:
            root = tmp / ex / "data"
            monkeypatch.setitem(common.ROOTS, ex, str(root))
            (root / self.day).mkdir(parents=True)
            pool = [NAME[ex].format(c) for c, exs in SYMS.items() if ex in exs]
            (root / f"pool-{self.day}.json").write_text(json.dumps({"pool": pool, "meta": {s: META.get(ex, {"ctVal": 1}) for s in pool}}))
            run = RUNS.get(ex, RUN)
            self.paths[ex] = root / self.day / f"book00-{run}.tsv.zst"
            self.plain_paths[ex] = tmp / "plain" / ex / self.day / f"book00-{run}.tsv.zst"
            self.plain_paths[ex].parent.mkdir(parents=True)
            self.paths[ex].write_bytes(b"")
            self.plain[ex] = b""
            if ex == "gate":
                (root / self.day / f"book00-{run - 115}.tsv.zst").write_bytes(b"not zstd: trial run, must be skipped")
        for k, (coin, exs) in enumerate(SYMS.items()):
            sh = ti.shocks(rng, 9, 0.25)
            for ex in exs:
                basis = ti.basis_of(sh) if ex != "binance" else (lambda t: np.zeros(len(t)))
                r = ti.make_symbol(rng, 4000, 100.0, basis)
                for i in range(len(r["e"])):
                    self.rows[ex].append((int(r["rx"][i]), int(r["e"][i]), NAME[ex].format(coin), float(r["bid"][i]), float(r["ask"][i]), 1.0 + i % 7, 2.0 + i % 5))
        self.cctx = {ex: zstandard.ZstdCompressor(level=1).compressobj() for ex in common.EXCHANGES}
        self.sent = {ex: 0 for ex in common.EXCHANGES}
        for ex in common.EXCHANGES:
            self.rows[ex].sort(key=lambda x: x[0])
        self.prev_rx = {ex: None for ex in common.EXCHANGES}

    def grow(self, now):
        """Дописывает в файлы все строки с приёмом ≤ now (как сборщик: блок zstd сбрасывается, кадр остаётся открытым)."""
        for ex in common.EXCHANGES:
            rows = self.rows[ex]
            buf = io.BytesIO()
            i = self.sent[ex]
            while i < len(rows) and rows[i][0] <= now:
                rx, e, sym, bid, ask, bsz, asz = rows[i]
                if self.prev_rx[ex] is None:
                    buf.write(f"c\t{(rx - 1) * 1_000_000}\tconnected\t-\n".encode())
                    self.prev_rx[ex] = rx - 1
                buf.write(line((rx - self.prev_rx[ex]) * 1_000_000, rx - e, sym, bid, ask, bsz, asz))
                self.prev_rx[ex] = rx
                i += 1
            self.sent[ex] = i
            data = buf.getvalue()
            if data:
                self.plain[ex] += data
                with open(self.paths[ex], "ab") as f:
                    f.write(self.cctx[ex].compress(data) + self.cctx[ex].flush(zstandard.COMPRESSOBJ_FLUSH_BLOCK))
                self.plain_paths[ex].write_bytes(self.plain[ex])

    def reference(self, t_b):
        """Полный счёт по всем записям, пришедшим к моменту: разбор тем же consume, чистка и analyze_pair целиком."""
        cmap, coins = arb_daemon.coin_map(self.day)
        raw = {}
        for ex in common.EXCHANGES:
            want = {s.encode(): s for (x, s) in cmap if x == ex}
            st = {"off": 0, "cum": 0, "last": {}}
            arrs = {}
            parse_inc.consume(io.BytesIO(self.plain[ex]), st, want, 0, arrs)
            for sb, a in arrs.items():
                name = want[sb]
                coin, mult, unit = cmap[(ex, name)]
                e, rx, bid, ask, dep = arb_daemon.to_sym_arrays(
                    {"e": np.frombuffer(a[0], "i8"), "rx": np.frombuffer(a[1], "i8"), "bid": np.frombuffer(a[3], "f8"), "ask": np.frombuffer(a[4], "f8"),
                     "bsz": np.frombuffer(a[5], "f8"), "asz": np.frombuffer(a[6], "f8")}, mult, unit)
                raw[(ex, name)] = {"e": e, "rx": rx, "bid": bid, "ask": ask, "dep": dep}
        out = {}
        t0 = max(self.start, t_b - arb_live.WINDOW_MS)
        cleaned = {k: arb_inc.core.clean(dict(v)) for k, v in raw.items()}
        alive = {ex: set() for ex in common.EXCHANGES}
        for (ex, _), c in cleaned.items():
            alive[ex] |= ti.alive_minutes(c, t_b)
        for coin, per in coins.items():
            exs = [ex for ex in common.EXCHANGES if ex in per and (ex, per[ex][0]) in raw]
            clean = {ex: cleaned[(ex, per[ex][0])] for ex in exs}
            exs = [ex for ex in exs if len(clean[ex]["e"]) >= 20]
            for i in range(len(exs)):
                for j in range(i + 1, len(exs)):
                    a, b = exs[i], exs[j]
                    ma, mb = float(np.median((clean[a]["bid"] + clean[a]["ask"]) / 2)), float(np.median((clean[b]["bid"] + clean[b]["ask"]) / 2))
                    if not 0.9 < ma / mb < 1.1:
                        continue
                    r = arb_inc.core.analyze_pair(clean[a], clean[b], t0, t_b)
                    if r is None:
                        continue
                    analyze.G.update({"t0": t0, "t1": t_b})
                    d = arb_live.hook(*r)
                    if d:
                        d["S"], d["B"], d["C"] = ti.ref_chart(clean[a], clean[b], t0, t_b, alive[a], alive[b])
                        out[(coin, a, b)] = d
        return out


def same(det, ref, ctx):
    assert set(det) == set(ref), (ctx, sorted(set(det) ^ set(ref)))
    for k in ref:
        ti.assert_same(det[k], ref[k], (ctx, k))


def ticks(env, start_min, end_min, svc_box, restart_at=(), compare_at=()):
    k = start_min
    while k <= end_min:
        now = ti.T_BEGIN + k * MIN + 15_000
        env.grow(now)
        if k in restart_at:
            svc_box[0] = arb_daemon.Service(str(env.live), now_ms=now)      # новая служба: состояние с диска, распаковщики греются с начала файла
        svc_box[0].tick(now)
        if k in compare_at:
            t_b = (now - arb_daemon.MARGIN_MS) // MIN * MIN
            _, det = arb_daemon.results(svc_box[0].W, t_b)
            same(det, env.reference(t_b), k)
        k += 5


def test_service_matches_full_pass_with_restarts(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    env.live.mkdir()
    arb_daemon.save_world(str(env.live), arb_daemon.new_world())
    box = [arb_daemon.Service(str(env.live), now_ms=ti.T_BEGIN)]
    ticks(env, 70, 175, box, restart_at=(105, 140), compare_at=(70, 100, 105, 110, 140, 175))
    doc = json.loads((env.live / "arb.json").read_text())
    assert doc["period"] == arb_daemon.PERIOD_S and doc["written"] > 0 and doc["rows"]
    got = {(r["c"], r["a"], r["b"]) for r in doc["rows"]}      # Gate — пятая биржа: все её пары с остальными четырьмя, «… → Gate»
    assert {("AAA", ex, "gate") for ex in ("binance", "bybit", "okx", "bingx")} <= got and ("EEE", "okx", "gate") in got
    assert not any(a == "gate" for _, a, _ in got) and doc["names"]["gate"] == "Gate"
    assert box[0].W.passes >= 20 and sum(s.late for s in box[0].W.syms.values()) == 0


def test_bootstrap_from_old_chunks_then_service(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, seed=2)
    env.live.mkdir()
    now1 = ti.T_BEGIN + 120 * MIN + 15_000
    env.grow(now1)
    # прежний почасовой разбор: куски и состояния (чтение несжатых копий тех же байт)
    (env.live / "state").mkdir()
    cmap, _ = arb_daemon.coin_map(env.day)
    for ex in common.EXCHANGES:
        want = {s.encode(): s for (x, s) in cmap if x == ex}
        parse_inc.run(ex, env.day, str(env.plain_paths[ex]), want, env.live / "state", env.live / "parsed", 0)
    W = arb_daemon.bootstrap(str(env.live), par=1, now_ms=now1)
    t_boot = W.t_a
    assert t_boot <= now1 - arb_daemon.MARGIN_MS
    _, det = arb_daemon.results(W, t_boot)
    same(det, env.reference(t_boot), "bootstrap")
    box = [arb_daemon.Service(str(env.live), now_ms=now1)]
    ticks(env, 125, 170, box, compare_at=(125, 170))


def test_gate_units_and_trial_run_skipped(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    cmap, coins = arb_daemon.coin_map(env.day)
    assert cmap[("gate", "AAA_USDT")] == ("AAA", 1.0, 0.1)       # единица размера Gate — quanto_multiplier (монет в контракте)
    assert cmap[("okx", "AAA-USDT-SWAP")][2] == 1.0 and cmap[("binance", "AAAUSDT")][2] == 1.0
    assert coins["EEE"].keys() == {"okx", "gate"}
    assert common.unit_of("gate", {"quanto_multiplier": "0.01"}) == 0.01 and common.unit_of("gate", None) == 1.0
    assert common.unit_of("okx", {"ctVal": "0.1"}) == 0.1 and common.unit_of("bybit", {"ctVal": "5"}) == 1.0
    bid, ask = np.array([2.0]), np.array([3.0])
    dep = arb_daemon.to_sym_arrays({"e": np.array([1]), "rx": np.array([1]), "bid": bid, "ask": ask, "bsz": np.array([10.0]), "asz": np.array([100.0])}, 1.0, 0.1)[4]
    assert dep[0] == pytest.approx(min(10 * 2.0, 100 * 3.0) * 0.1)
    files = {Path(j[2]).name for j in arb_live.discover([env.day]) if j[0] == "gate"}
    assert files == {f"book00-{common.MINRUN_EX['gate']}.tsv.zst"}       # пробный запуск Gate (метка раньше MINRUN_EX) не читается
    monkeypatch.setattr(common, "SINCE", {"gate": "2026-11-15"})
    assert common.load_pool("gate", "2026-11-14") == ([], {}, {}) and common.load_pool("gate", env.day) == ([], {}, {})      # до первого дня — биржи в счёте нет (пулов нет)
    assert common.norm("gate", "BTC_USDT") == ("BTC", 1.0) and common.norm("gate", "1000PEPE_USDT") == ("PEPE", 1000.0) and common.norm("gate", "币安人生_USDT")[0] == "币安人生"


def test_rebuild_from_raw_files_gives_all_pairs_incl_gate(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, seed=3)
    now1 = ti.T_BEGIN + 120 * MIN + 15_000
    env.grow(now1)
    monkeypatch.setattr(arb_live, "discover", lambda days: [(ex, env.day, str(p), p.stat().st_size) for ex, p in env.plain_paths.items()])   # несжатые копии (DECOMP = cat)
    monkeypatch.setattr(time, "time", lambda: now1 / 1000)
    prev = (datetime.fromisoformat(env.day) - timedelta(days=1)).date().isoformat()      # начало окна прогрева — накануне: пулы того дня нужны парсеру
    for ex in common.EXCHANGES:
        root = Path(common.ROOTS[ex])
        (root / f"pool-{prev}.json").write_text((root / f"pool-{env.day}.json").read_text())
    env.live.mkdir()
    (env.live / "state.pkl").write_bytes(b"x")
    with pytest.raises(SystemExit):
        arb_daemon.main("rebuild", str(env.live), 1)                       # занятый каталог не трогаем
    (env.live / "state.pkl").unlink()
    arb_daemon.main("rebuild", str(env.live), 1)
    W = arb_daemon.load_world(str(env.live))
    _, det = arb_daemon.results(W, W.t_a)
    same(det, env.reference(W.t_a), "rebuild")
    assert {("AAA", ex, "gate") for ex in ("binance", "bybit", "okx", "bingx")} <= set(det) and ("EEE", "okx", "gate") in det
    import state_report
    _, coins = arb_daemon.coin_map(env.day)
    rep = state_report.report(W, coins)
    assert rep["coins_with_exchange"] == 2 and rep["pairs_with_exchange"] == 5 and rep["excluded"] == [] and rep["not_ready"] == []
    assert rep["pairs_by_exchange"]["gate"] == 5
    W.syms[("gate", "EEE_USDT")].mid *= 5                      # тот же тикер, другой актив: пара отброшена и названа в сводке
    assert state_report.report(W, coins)["excluded"] == [["EEE", "okx", "gate", pytest.approx(0.2, rel=0.3)]]


# середины Sym.mid живого состояния srv 15.11.2026: тикер — разные активы на биржах (ANTHROPIC, BB, ON) → пары не считаются; BARD — один актив (по минутам ratio p50 0,998)
MIDS = {
    "ANTHROPIC": {"okx": 102.295, "bingx": 1341.95},
    "BB": {"binance": 0.0055185, "okx": 8.687, "bingx": 0.0054935},
    "ON": {"binance": 0.08365, "bybit": 69.04, "bingx": 0.08405},
    "BARD": {"binance": 0.12125, "bybit": 0.121005, "okx": 0.12045, "bingx": 0.12075},
}


def test_pair_ok_excludes_tickers_that_are_different_assets():
    from types import SimpleNamespace as NS
    kept = {}
    for coin, mids in MIDS.items():
        exs = [e for e in common.EXCHANGES if e in mids]
        kept[coin] = {(a, b) for i, a in enumerate(exs) for b in exs[i + 1:] if arb_daemon.pair_ok(NS(mid=mids[a]), NS(mid=mids[b]))}
    assert kept["ANTHROPIC"] == set()
    assert kept["BB"] == {("binance", "bingx")}
    assert kept["ON"] == {("binance", "bingx")}
    assert len(kept["BARD"]) == 6
