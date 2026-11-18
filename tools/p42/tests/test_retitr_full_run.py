"""Тесты драйвера счёта и отчёта TK-599 (retitr_full_run.py, retitr_full_report.py, `--base-date` у retitr_ftab.py) — без движка и без P&L: раннер подпроцессов подменён
`FakeSrv` (синтетический мир `World` из test_retitr_full.py). Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_retitr_full_run.py`.
"""
import hashlib
import io
import json
import os
import pickle
import sys
import tempfile
import traceback
import types
from contextlib import redirect_stdout

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import retitr_cells as rc  # noqa: E402
import retitr_ftab as rfx  # noqa: E402
import retitr_full as rf  # noqa: E402
import retitr_full_report as rep  # noqa: E402
import retitr_full_run as rr  # noqa: E402
from test_retitr_full import World, raises  # noqa: E402

NB = 800
COINS = ["C%02d" % i for i in range(12)]
CAND_COINS = ("AAAUSDT", "BBBUSDT", "CCCUSDT")
SPEC = rf.Spec.load(rr.GRIDS)


def fval(name, sym, ms):
    """Значение признака кандидата — функция ключа (один и тот же кандидат в разных потоках даёт одно значение)."""
    if name.startswith("depth_"):
        return float("nan")
    if name == "loi_turn" and sym == "AAAUSDT" and ms % rf.DAY_MS < 6000:        # 1/6 кандидатов без значения — G5 снимает признак
        return float("nan")
    return int(hashlib.md5(("%s|%s|%d" % (name, sym, ms)).encode()).hexdigest()[:6], 16) % 1000 / 10.0


class FakeSrv:
    """Подмена раннера: разбирает команды движка и `retitr_ftab.py build`, пишет файлы так, как их пишет srv."""

    def __init__(self, world=None, fail=None, perturb=None):
        self.world, self.fail, self.perturb = world, fail, perturb
        self.calls, self.base_dates, self.engine_variants = [], [], 0

    @staticmethod
    def arg(cmd, flag):
        return cmd[cmd.index(flag) + 1] if flag in cmd else None

    def run(self, cmd, env=None, log=None):
        env = env or {}
        self.calls.append((list(cmd), dict(env)))
        if self.fail and self.fail(cmd, env):
            if log:
                os.makedirs(os.path.dirname(log), exist_ok=True)
                with open(log, "a", encoding="utf-8") as f:
                    f.write("строка журнала\nпоследняя строка: ОШИБКА\n")
            return 7
        if cmd[0] == sys.executable:
            return self.ftab(cmd)
        return self.engine(cmd, env)

    def ftab(self, cmd):
        side, sigsx, out = cmd[3], self.arg(cmd, "--sigsx"), self.arg(cmd, "--out")
        self.base_dates.append(self.arg(cmd, "--base-date"))
        rows = [ln.split(",") for ln in open(sigsx, encoding="utf-8").read().splitlines()[1:]]
        names = [n for n, _ in rfx.FEATURES[side]]
        syms, ms = [r[0] for r in rows], np.array([int(r[2]) for r in rows], dtype=np.int64)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        rfx.write_ftab(out, syms, ms, names, [np.array([fval(n, s, int(m)) for s, m in zip(syms, ms)]) for n in names])
        return 0

    def engine(self, cmd, env):
        vfile, out, ftab = self.arg(cmd, "--variants"), self.arg(cmd, "--out"), self.arg(cmd, "--ftab")
        t0 = env["P42_T0"]
        win = next(w for w in rf.WINDOWS + rf.CONV_WINDOWS if w.t0 == t0)
        for ln in open(vfile, encoding="utf-8").read().splitlines():
            vid, _, keys = ln.partition(" ")
            d = os.path.join(out, vid)
            os.makedirs(d, exist_ok=True)
            if env.get("P42_SIGSX_ONLY"):
                self.sigsx(d, keys, win)
            else:
                self.trades(d, keys, win, ftab)
                self.engine_variants += 1
        return 0

    @staticmethod
    def stream_rows(keys, win):
        n = 3 if "natr_min=0.3" in keys.split() else 2                      # поток зависит от ключей пула
        return [(s, day * rf.DAY_MS + 5000 + 1000 * k) for s in CAND_COINS for day in range(rf.GRID_START_DAY, rf.TRADE_END + 1) for k in range(n)]

    def sigsx(self, d, keys, win):
        for side in ("long", "short"):
            with open(os.path.join(d, side + "-sigsx.csv"), "w", encoding="utf-8", newline="") as f:
                f.write("sym,level,sig_ms,dist_e8,level_px_e8,born,end,first_bar,natr_e8,members\n")
                for s, ms in self.stream_rows(keys, win):
                    f.write("%s,0,%d,0,0,0,0,0,0,\n" % (s, ms))

    def trades(self, d, keys, win, ftab):
        ser = self.world(types.SimpleNamespace(keys=keys, sig="s_glob=1000000" in keys.split()))
        t0d = (win.t0d - rf.D0).days
        m = (ser.day >= t0d + rf.GRID_START_DAY) & (ser.day <= t0d + rf.TRADE_END)         # данные окна кончаются T0+73 (§13.24.3)
        sel = np.nonzero(m)[0]
        rows = []
        for i in sel:
            e = (int(ser.day[i]) - t0d) * rf.DAY_MS + 3_600_000
            g = float(ser.val[i])
            if self.perturb and self.perturb(win, int(ser.day[i])):
                g += 1.0
            rows.append((COINS[int(ser.sym[i])], e, g))
        daily = {}
        for _, e, g in rows:
            daily[e // rf.DAY_MS - rf.GRID_START_DAY] = daily.get(e // rf.DAY_MS - rf.GRID_START_DAY, 0.0) + g
        meta = {}
        for side in ("long", "short"):
            with open(os.path.join(d, side + "-trades.csv"), "w", encoding="utf-8", newline="") as f:
                f.write("sym,level,sig_min,entry_min,exit_min,entry_px_e8,kind,takes,gross,fees,funding,net,entry_day,sig_ms,entry_ms,exit_ms\n")
                for s, e, g in rows:
                    f.write("%s,0,0,0,0,0,stop,0,%.6f,0.000000,0.000000,%.6f,0,0,%d,%d\n" % (s, g, g, e, e + 600_000))
            with open(os.path.join(d, side + "-daily.csv"), "w", encoding="utf-8", newline="") as f:
                f.write("day,pnl\n")
                for i, v in sorted(daily.items()):
                    f.write("%d,%.6f\n" % (i, v))
            if "fa=" in keys:
                meta[side] = {"filter_[cands,dropped,no_key,no_value]": [100, 10, 0 if ftab and os.path.exists(ftab) else 100, 0]}
            else:
                meta[side] = {}
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)


def make(tmp, world=None, runner=None, chunk=None):
    sym = os.path.join(tmp, "symbols.txt")
    with open(sym, "w", encoding="utf-8") as f:
        f.write(",".join(COINS) + "\n")
    runner = runner or FakeSrv(world)
    return rr.make_ctx(os.path.join(tmp, "root"), runner=runner, nboot=NB, chunk=chunk, symbols=sym, rates={}, bin_path="/x/alpha-master", prod="/x/py", metrics=False), runner


def drive_one(ctx, gen):
    return rf.drive({"x": gen}, ctx.ev)["x"]


# ---- команды ----------------------------------------------------------------------------------------------------------------------------

def test_require_go_and_show_cmd():
    assert "TK599_GO" in raises(rr.GuardError, rr.require_go, {})
    rr.require_go({"TK599_GO": "1"})
    assert "…(" in rr.show_cmd(["a", "x" * 500]) and rr.show_cmd(["a", "b"], {"P42_T0": "2026"}) == "P42_T0=2026 a b"


def test_engine_command_shape():
    with tempfile.TemporaryDirectory() as tmp:
        ctx, _ = make(tmp)
        w = rf.WINDOWS[1]
        cmd, env = ctx.engine.command(w, "/v/b1.txt", "/o/wb", "/f/u.csv")
        assert cmd == ["/x/alpha-master", "breakout", "tick", "--cache", os.path.join(ctx.paths.root, "wb", "cache"), "--trades", os.path.join(ctx.paths.root, "wb", "aggtrades"),
                       "--variants", "/v/b1.txt", "--symbols", ",".join(COINS), "--out", "/o/wb", "--threads", "6", "--ftab", "/f/u.csv"]
        assert env == {"P42_T0": "2026-07-25"}
        cmd, env = ctx.engine.command(w, "/v/b1.txt", "/o/wb", None, sigsx_only=True)
        assert "--ftab" not in cmd and env == {"P42_T0": "2026-07-25", "P42_SIGSX_ONLY": "1", "P42_SIGSX": "14:73"}


def test_groups_split_and_chunks():
    with tempfile.TemporaryDirectory() as tmp:
        ctx, _ = make(tmp, chunk=2)
        r = [rf.Req("L", "s=%d" % i, False) for i in range(1, 6)]
        f = [rf.Req("L", "s=3 fa=maxmv1 fo=lo fv=1.0", False), rf.Req("S", "s=3 fa=tpm15 fo=lo fv=2.0", False), rf.Req("S", "s=4 fa=tpm15 fo=lo fv=2.0", False)]
        g = ctx.engine.groups(r + f)
        assert [(s, len(p)) for s, p in g] == [(None, 2), (None, 2), (None, 1), ("L", 1), ("S", 2)]
        assert rr.Engine.stream_need(f[0]) == rf.FtabNeed("L", "s=3") and rr.Engine.stream_need(f[1]).hash != rr.Engine.stream_need(f[0]).hash
        assert list(rr.chunks([1, 2, 3], 0)) == [[1, 2, 3]]


def test_launch_three_windows_marks_and_skips_done():
    with tempfile.TemporaryDirectory() as tmp:
        ctx, srv = make(tmp, World(lambda k, s, d: 0.0))
        reqs = [rf.Req("L", rf.cfg_keys(SPEC, "L", {}), False), rf.Req("S", rf.cfg_keys(SPEC, "S", {}), False)]
        ctx.engine.launch(reqs)
        eng = [(c, e) for c, e in srv.calls]
        assert [e["P42_T0"] for _, e in eng] == [w.t0 for w in rf.WINDOWS]                         # окна последовательно, T0 каждого
        assert all("--ftab" not in c and "P42_SIGSX_ONLY" not in e for c, e in eng)
        for w in rf.WINDOWS:
            for q in reqs:
                assert os.path.exists(os.path.join(ctx.paths.out(w.name), q.vid, rr.DONE_MARK))
        n = len(srv.calls)
        ctx.engine.launch(reqs)
        assert len(srv.calls) == n                                                                 # посчитанное не пересчитывается


def test_launch_failure_is_guard_error_with_log_tail():
    with tempfile.TemporaryDirectory() as tmp:
        ctx, srv = make(tmp, World(lambda k, s, d: 0.0), runner=FakeSrv(World(lambda k, s, d: 0.0), fail=lambda c, e: e.get("P42_T0") == "2026-07-25"))
        msg = raises(rr.GuardError, ctx.engine.launch, [rf.Req("L", "s=3", False)])
        assert "окно wb" in msg and "код возврата 7" in msg and "ОШИБКА" in msg
        assert not os.path.exists(os.path.join(ctx.paths.out("wa"), rf.Req("L", "s=3", False).vid, rr.DONE_MARK))   # метка — только после всех окон


# ---- таблицы признаков -------------------------------------------------------------------------------------------------------------------

def _tab(path, rows, names=("oi_c24", "maxmv1")):
    rfx.write_ftab(path, [r[0] for r in rows], np.array([r[1] for r in rows], dtype=np.int64), list(names), [np.array([r[2 + j] for r in rows], dtype=float) for j in range(len(names))])


def test_merge_union():
    with tempfile.TemporaryDirectory() as tmp:
        u, a, b, c = (os.path.join(tmp, n) for n in ("u.csv", "a.csv", "b.csv", "c.csv"))
        _tab(a, [("X", 10, 1.0, float("nan")), ("Y", 20, 2.0, 5.0)])
        assert rr.merge_union(u, a) == (2, 2)
        _tab(b, [("Y", 20, 2.0, 5.0), ("X", 10, 1.0, float("nan")), ("Z", 5, 3.0, 4.0)])        # перекрытие с равными значениями (NaN = NaN) + новая строка
        assert rr.merge_union(u, b) == (3, 1)
        syms, ms, cols = rfx.read_ftab(u)
        assert syms == ["X", "Y", "Z"] or list(zip(syms, ms)) == sorted(zip(syms, ms))
        assert list(zip(syms, map(int, ms))) == sorted(zip(syms, map(int, ms))) and len(syms) == 3
        before = open(u, encoding="utf-8").read()
        _tab(c, [("Y", 20, 2.5, 5.0)])
        assert "расходятся" in raises(rr.GuardError, rr.merge_union, u, c)
        assert open(u, encoding="utf-8").read() == before                                           # при ошибке union не тронут
        _tab(c, [("Y", 20, 2.0)], names=("oi_c24",))
        assert "столбцы" in raises(rr.GuardError, rr.merge_union, u, c)


def test_ftab_store_build_union_base_date_and_ready():
    with tempfile.TemporaryDirectory() as tmp:
        ctx, srv = make(tmp, World())
        need = rf.FtabNeed("L", rf.cfg_keys(SPEC, "L", {}))
        assert not ctx.store.ready(need)
        ctx.store.build([need, need])
        assert ctx.store.ready(need)
        assert srv.base_dates == [w.t0 for w in rf.WINDOWS]                                        # базовая дата сборки признаков = T0 своего окна
        envs = [e for c, e in srv.calls if c[0] == sys.executable]
        assert [e["TK594_TRADES"] for e in envs] == [ctx.paths.trades(w.name) for w in rf.WINDOWS] and all(e["TK594_METRICS"] == ctx.paths.metrics_all for e in envs)
        sx = [(c, e) for c, e in srv.calls if e.get("P42_SIGSX_ONLY")]
        assert [e["P42_T0"] for _, e in sx] == [w.t0 for w in rf.WINDOWS] and all(e["P42_SIGSX"] == "14:73" for _, e in sx)
        for w in rf.WINDOWS:
            syms, ms, cols = rfx.read_ftab(ctx.paths.union(w.name, "L"))
            assert len(syms) == len(CAND_COINS) * (rf.TRADE_END + 1 - rf.GRID_START_DAY) * 2 and set(cols) == {n for n, _ in rfx.FEATURES["L"]}
        n = len(srv.calls)
        ctx.store.build([need])
        assert len(srv.calls) == n                                                                 # готовое не собирается повторно
        need2 = rf.FtabNeed("L", rf.cfg_keys(SPEC, "L", {}) + " natr_min=0.3")                    # другой поток: ключи новые, общие значения те же
        ctx.store.build([need2])
        syms, ms, _ = rfx.read_ftab(ctx.paths.union("wa", "L"))
        assert len(syms) == len(CAND_COINS) * (rf.TRADE_END + 1 - rf.GRID_START_DAY) * 3
        days, cols = ctx.store.candidates(need)                                                    # зачётные диапазоны без перекрытий
        assert (np.bincount(days, minlength=rf.NDAYS) == len(CAND_COINS) * 2).all()                # каждые сутки 0…135 — ровно в одном окне


def test_metrics_tree_links_both_sources_and_requires_done():
    with tempfile.TemporaryDirectory() as tmp:
        paths = rr.Paths(os.path.join(tmp, "root"))
        main, jj = os.path.join(tmp, "main"), paths.metrics_jun_jul
        for d, names in ((main, ["2026-07-18", "2026-07-19"]), (jj, ["2026-06-17", "2026-07-17"])):
            os.makedirs(os.path.join(d, "BTCUSDT"))
            for n in names:
                open(os.path.join(d, "BTCUSDT", "BTCUSDT-metrics-%s.zip" % n), "w").close()
                open(os.path.join(d, "BTCUSDT", "BTCUSDT-metrics-%s.zip.sha256" % n), "w").close()
        os.makedirs(os.path.join(main, "funding", "BTCUSDT"))
        os.makedirs(os.path.join(jj, "log"))
        assert "не закончена" in raises(rr.GuardError, rr.metrics_tree, paths)
        assert rr.metrics_tree(paths, (main, jj)) == 4 and len(os.listdir(os.path.join(paths.metrics_all, "BTCUSDT"))) == 4
        assert not os.path.exists(os.path.join(paths.metrics_all, "funding"))
        assert rr.metrics_tree(paths, (main, jj)) == 0                                             # повторный вызов ничего не меняет


def test_provider_cuts_use_training_days_and_gate5():
    with tempfile.TemporaryDirectory() as tmp:
        ctx, srv = make(tmp, World())
        g5 = rr.gate5(ctx.store, ctx.spec)
        assert g5["L"]["maxmv1"]["ok"] and g5["L"]["oi_c24"]["ok"] and not g5["L"]["loi_turn"]["ok"] and "depth_mean_bp" not in g5["L"]      # исключены спеком / снят G5
        assert all(len(v["shares"]) == 5 for v in g5["L"].values())
        avail = rr.avail_of(g5)
        assert avail["L"] == {"maxmv1", "oi_c24"}
        prov = rr.FtabProvider(ctx.ev, ctx.store, ctx.spec, avail)
        om = rf.month_days([0, 1])
        keys = rf.cfg_keys(SPEC, "L", {})
        cuts = drive_one(ctx, prov("L", keys, om))
        assert set(cuts) == {("maxmv1", "lo"), ("oi_c24", "hi")}
        # независимый расчёт: кандидаты зачётных диапазонов, обучающие сутки июль–август
        vals, dd = [], []
        for w in rf.WINDOWS:
            for sym in CAND_COINS:
                for day in range(rf.GRID_START_DAY, rf.TRADE_END + 1):
                    for k in range(2):
                        ms = day * rf.DAY_MS + 5000 + 1000 * k
                        a = (w.t0d - rf.D0).days + day
                        if (w.lod - rf.D0).days <= a <= (w.hid - rf.D0).days:
                            vals.append(fval("maxmv1", sym, ms))
                            dd.append(a)
        vals, dd = np.array(vals), np.array(dd)
        for p, fv in cuts[("maxmv1", "lo")].items():
            assert np.isclose(fv, np.quantile(vals[np.isin(dd, om)], p / 100.0))
        assert cuts[("maxmv1", "lo")][10] < cuts[("maxmv1", "lo")][25] and cuts[("oi_c24", "hi")][10] > cuts[("oi_c24", "hi")][25]
        n = len(srv.calls)
        assert drive_one(ctx, prov("L", keys, om)) == cuts and len(srv.calls) == n                 # кэш: ни таблицы, ни квантили заново


# ---- ворота -----------------------------------------------------------------------------------------------------------------------------

def test_gate12_hashes_and_manifests():
    with tempfile.TemporaryDirectory() as tmp:
        paths = rr.Paths(os.path.join(tmp, "root"))
        b, s, g, m = (os.path.join(tmp, n) for n in ("bin", "sym", "grids", "man"))
        for p, txt in ((b, "B"), (s, "S"), (g, "G")):
            open(p, "w").write(txt)
        open(m, "w", encoding="utf-8").write("== wa\nline1 x\nbinary b\n== wb\nline2\n== wc\nline3\n== wbx\nline4\n== wcx\nline5\n")
        rec = {"bin_sha256": rr.file_hashes(b)[0], "bin_md5": rr.file_hashes(b)[1], "symbols_sha256": rr.file_hashes(s)[0], "grids_sha256": rr.file_hashes(g)[0],
               "manifests_sha256": rr.file_hashes(m)[0]}
        for w, txt in (("wa", "line1 x\nbinary b\n"), ("wb", "line2\n"), ("wc", "line3\n"), ("wbx", "line4\n"), ("wcx", "line5\n")):
            os.makedirs(paths.j(w))
            open(paths.j(w, "MANIFEST"), "w", encoding="utf-8").write(txt)
        r = rr.gate12(paths, b, s, g, m, rec)
        assert r["fail"] == [] and r["G2_manifest_wb"]
        open(paths.j("wb", "MANIFEST"), "w", encoding="utf-8").write("line2 changed\n")
        open(s, "w").write("S2")
        r = rr.gate12(paths, b, s, g, m, rec)
        assert set(r["fail"]) == {"G2_symbols_sha256", "G2_manifest_wb"}
        assert rr.parse_manifests("== wa\na\n\nb\n== wb\nc\n") == {"wa": ["a", "b"], "wb": ["c"]}


def test_gate3_overlap_difference_stops():
    w = World(lambda k, s, d: 0.0)
    with tempfile.TemporaryDirectory() as tmp:
        ctx, srv = make(tmp, w)
        res = rr.gate3(ctx.paths, ctx.engine, ctx.spec, ctx.engine_conv)
        assert res["L"]["diff"] == [] and res["S"]["diff"] == [] and res["L"]["conv"] == [] and res["S"]["conv"] == [] and res["L"]["forced_ok"] == 0
        assert rr.g3_failures(res) == {}
        t0s = {e["P42_T0"] for _, e in srv.calls}
        assert t0s == {w_.t0 for w_ in rf.WINDOWS + rf.CONV_WINDOWS}                               # базы посчитаны на пяти окнах
        ramp = res["L"]["ramp"]
        assert set(ramp) == {"wa–wb 2026-08-11…2026-08-25", "wb–wc 2026-09-18…2026-10-02", "conv wb–wbx 2026-08-26…2026-09-25", "conv wc–wcx 2026-10-03…2026-11-02"}
        assert all(v["n_diff"] == 0 and v["n_trades"] > 0 for v in ramp.values())
    with tempfile.TemporaryDirectory() as tmp:
        srv = FakeSrv(w, perturb=lambda win, day: win.name == "wb" and day == 57)                  # зачётный кусок перекрытия wa∩wb: абс. сутки 56…58 — 26–28.08
        ctx, _ = make(tmp, runner=srv)
        res = rr.gate3(ctx.paths, ctx.engine, ctx.spec, ctx.engine_conv)
        assert res["L"]["diff"] and all("wa–wb" in x for x in res["L"]["diff"]) and res["L"]["conv"], res["L"]["conv"]     # wb ≠ wbx на тех же сутках — сходимость тоже нарушена
        bad = rr.g3_failures(res)
        assert set(bad) == {"L", "S"} and rr.g3_failures({"L": {"diff": [], "forced_ok": 0}}) == {}
    with tempfile.TemporaryDirectory() as tmp:
        srv = FakeSrv(w, perturb=lambda win, day: win.name == "wb" and day == 55)                  # разгон wb (25.08): не ворота, только диагностика
        ctx, _ = make(tmp, runner=srv)
        res = rr.gate3(ctx.paths, ctx.engine, ctx.spec, ctx.engine_conv)
        assert res["L"]["diff"] == [] and res["L"]["ramp"]["wa–wb 2026-08-11…2026-08-25"]["n_diff"] >= 1
    with tempfile.TemporaryDirectory() as tmp:
        srv = FakeSrv(w, perturb=lambda win, day: win.name == "wcx" and day == 100)                # wcx ≠ wc в зачётном диапазоне wc (абс. сутки 94…135), G3 чист
        ctx, _ = make(tmp, runner=srv)
        res = rr.gate3(ctx.paths, ctx.engine, ctx.spec, ctx.engine_conv)
        assert res["L"]["diff"] == [] and res["L"]["conv"] and all("wc–wcx" in x for x in res["L"]["conv"]) and set(rr.g3_failures(res)) == {"L", "S"}


# ---- `--base-date` у retitr_ftab -----------------------------------------------------------------------------------------------------------

def test_ftab_base_date_argument_and_default():
    seen = {}
    old_run = rfx.subprocess.run
    rfx.subprocess.run = lambda cmd, check=False: seen.setdefault("cmds", []).append(cmd)
    try:
        rfx.run_tk594("/p", "e.parquet", "o.parquet", "deriv", 4)
        rfx.run_tk594("/p", "e.parquet", "o.parquet", "flow", 4, "2026-09-21")
    finally:
        rfx.subprocess.run = old_run
    c0, c1 = seen["cmds"]
    assert c0[c0.index("--base-date") + 1] == "2026-09-01" and c1[c1.index("--base-date") + 1] == "2026-09-21"          # поведение TK-595 не меняется
    old = rfx.cmd_build
    got = {}
    rfx.cmd_build = lambda a: got.update(vars(a))
    try:
        rfx.main(["build", "L", "--sigsx", "s", "--work", "w", "--out", "o"])
        assert got["base_date"] == "2026-09-01"
        rfx.main(["build", "S", "--sigsx", "s", "--work", "w", "--out", "o", "--base-date", "2026-06-17"])
        assert got["base_date"] == "2026-06-17"
    finally:
        rfx.cmd_build = old


def test_plan_prints_commands_without_running():
    with tempfile.TemporaryDirectory() as tmp:
        buf = io.StringIO()
        a = types.SimpleNamespace(root=os.path.join(tmp, "root"), bin="", threads=6, chunk=0, prod="/p", procs=6)
        with redirect_stdout(buf):
            assert rr.cmd_plan(a) == 0
        t = buf.getvalue()
        assert "P42_SIGSX_ONLY=1" in t and "--base-date 2026-09-01" in t and "--ftab" in t and "P42_T0=2026-07-18" in t and "P42_T0=2026-08-25" in t
        assert "breakout tick" in t and "<566 монет" in t and not os.path.exists(os.path.join(tmp, "root"))


# ---- сквозной прогон на синтетике + отчёт -------------------------------------------------------------------------------------------------

def test_end_to_end_run_all_report_and_frozen():
    def eff(k, s, d):
        e = 0.0
        if k.get("rr") == "3" and k.get("rs") != "2":                                                # эффект только у лонга (у базы шорта rs=2)
            e += 1.5
        if k.get("fa") == "maxmv1":
            e += 1.2
        return e
    with tempfile.TemporaryDirectory() as tmp:
        w = World(eff, per_day=4)
        ctx, srv = make(tmp, w)
        g5 = rr.gate5(ctx.store, ctx.spec)
        c595 = {"L_C": rf.cfg_keys(SPEC, "L", {}) + " rr=3", "S_C": rf.cfg_keys(SPEC, "S", {})}
        res = rr.run_all(ctx, g5, {"G12": {"fail": []}, "G3": {"L": {"diff": [], "forced_ok": 0}, "S": {"diff": [], "forced_ok": 0}}, "G5": g5}, c595)
        assert set(res["outcomes"]) == {"%s:%s" % (s, f) for s in "LS" for f in ("f1", "f2", "f3", "f4", "full")}
        o = res["outcomes"]["L:full"]
        assert o.final["rr"]["id"] == "rr_3" and o.final["F"]["id"].startswith("F_maxmv1_lo_") and o.converged
        assert res["freeze"]["L"][0] == "C*" and res["freeze"]["S"][0] == "база" and res["wf"]["L"]["confirmed"]
        assert "fa=maxmv1 fo=lo fv=" in rf.cfg_keys(SPEC, "L", o.final) and rf.cfg_keys(SPEC, "L", o.final).count("fa=") == 1               # один фильтр признака
        # каждая конфигурация — один раз на окно (повторных вызовов движка по готовому нет)
        eng_calls = [c for c, e in srv.calls if c[0] != sys.executable and not e.get("P42_SIGSX_ONLY")]
        assert srv.engine_variants == 3 * len([h for h in os.listdir(ctx.paths.out("wa")) if h.startswith("c")])
        nfilt = 0
        for c in eng_calls:                                                                          # вызов с фильтром — только с фильтрами и с --ftab, остальные — без
            lines = open(c[c.index("--variants") + 1], encoding="utf-8").read().splitlines()
            has = ["fa=" in ln for ln in lines]
            assert all(has) == any(has) and ("--ftab" in c) == any(has)
            nfilt += any(has)
        assert nfilt >= 3
        js = json.dumps(rr.jsonable(rr.summary(res, ctx.spec)), ensure_ascii=False)
        assert '"freeze"' in js
        pkl, md, fz = (os.path.join(tmp, n) for n in ("results.pkl", "r.md", "frozen.json"))
        pickle.dump(res, open(pkl, "wb"))
        assert rep.main([pkl, "--md", md, "--frozen", fz, "--commit", "abc123"]) == 0
        text = open(md, encoding="utf-8").read()
        for s in ("# П-42 §13.24", "Ограничение области: сочетания двух и более признаков не проверялись", "## Шаг 0", "F_maxmv1_lo_", "Проход 1", "Совместная сетка",
                  "Проверка процедуры вперёд", "Исход заморозки", "abc123", "Чтение вперёд 17–30.11 здесь не сделано", "§13.24.2", "G5", "**снят**"):
            assert s in text, s
        j = json.load(open(fz, encoding="utf-8"))
        assert j["scope"] == rep.SCOPE_LIMIT and j["version"] == rep.FROZEN_VERSION
        L, S = j["sides"]["L"], j["sides"]["S"]
        assert L["decision"] == "C*" and "rr=3" in L["keys"] and L["axes"]["rr"] == "rr_3" and L["axes"]["F"].startswith("F_maxmv1_lo_") and L["keys"].count("fa=") == 1
        assert S["decision"] == "база" and S["keys"] == S["base_keys"] and S["axes"] == {}


# ---- §13.24.5: чувствительность порога минимума сделок --------------------------------------------------------------------------------

def thin_world(eff):
    """Сделки только в чётные сутки окна (по 1): в месяц 15–16 сделок, в 01–13.11 — 6. Порог N = 10 и 15 пропускает, N = 20 и 25 — нет."""
    w = World(eff, per_day=1)
    m = (w.day % 2) == 0
    w.day, w.sym, w.base = w.day[m], w.sym[m], w.base[m]
    return w


def test_parse_ns_and_require_main_done():
    assert rr.parse_ns("20,10,15,25") == [20, 10, 15, 25] and rr.parse_ns("20") == [20]
    for bad in ("10,20", "20,20,10", "20,30", "", "x"):
        try:
            rr.parse_ns(bad)
        except (rr.GuardError, ValueError):
            continue
        raise AssertionError("parse_ns принял %r" % bad)
    with tempfile.TemporaryDirectory() as tmp:
        paths, frozen = rr.Paths(os.path.join(tmp, "root")), os.path.join(tmp, "frozen.json")
        os.makedirs(paths.j("results"))
        assert "run.rc" in raises(rr.GuardError, rr.require_main_done, paths, frozen)                  # основной прогон ещё идёт
        open(paths.j("run.rc"), "w").write("3\n")
        assert "results.pkl" in raises(rr.GuardError, rr.require_main_done, paths, frozen)             # прогон упал — результатов нет
        open(paths.j("results", "results.pkl"), "wb").write(b"x")
        assert "заморозка" in raises(rr.GuardError, rr.require_main_done, paths, frozen)               # заморозки нет — sens не стартует
        open(frozen, "w").write("{}\n")
        pkl, sha = rr.require_main_done(paths, frozen)
        assert pkl == paths.j("results", "results.pkl") and sha == rf.sha256_file(frozen)


def test_same_summary():
    a = {"x": [1.0, float("nan"), "s", True, None], "y": {"z": 3}}
    assert rr.same_summary(a, {"x": [1.0 + 1e-14, float("nan"), "s", True, None], "y": {"z": 3}}) == []
    assert len(rr.same_summary(a, {"x": [1.0 + 1e-6, float("nan"), "s", True, None], "y": {"z": 3}})) == 1
    assert len(rr.same_summary(a, {"x": [1.0, float("nan"), "t", True, None], "y": {"z": 3}})) == 1
    assert len(rr.same_summary(a, {"x": [1.0, float("nan"), "s", True, None]})) == 1                    # нет ключа
    assert len(rr.same_summary({"l": [1, 2]}, {"l": [1]})) == 1


def test_sens_window_guard():
    """§13.24.5 п. 8: sens — только wa/wb/wc и даты ≤ 13.11; окно чтения wd, окна сходимости и любая дата позже 13.11 — GuardError до первого прогона."""
    rf.check_sens_windows(rf.WINDOWS)
    wd = rf.Window("wd", "2026-11-03", "2026-11-17", "2026-11-30")
    late = rf.Window("wc", "2026-09-01", "2026-10-03", "2026-11-14")
    far = rf.Window("wc", "2026-09-02", "2026-10-03", "2026-11-13")                 # T0 + 73 = 14.11
    for bad in (rf.WINDOWS + (wd,), rf.CONV_WINDOWS, rf.WINDOWS[:2], rf.WINDOWS[:2] + (late,), rf.WINDOWS[:2] + (far,)):
        raises(rr.GuardError, rf.check_sens_windows, bad)
    with tempfile.TemporaryDirectory() as tmp:
        ctx, srv = make(tmp, thin_world(lambda k, s, d: 0.0))
        ctx.ev.windows = rf.WINDOWS + (wd,)
        assert "17–30.11" in raises(rr.GuardError, rr.sens_run, ctx, {}, [20])
        assert srv.calls == [] and ctx.ev.batches == []                                                    # ни одного запуска
        ctx.ev.windows = rf.WINDOWS
        ctx.store.windows = rf.WINDOWS + (wd,)
        raises(rr.GuardError, rr.sens_run, ctx, {}, [20])
        assert srv.calls == []


def test_sens_run_control_override_and_report():
    def eff(k, s, d):
        return 1.5 if (k.get("rr") == "3" and k.get("rs") != "2") else 0.0                                # эффект только у лонга
    with tempfile.TemporaryDirectory() as tmp:
        ctx, srv = make(tmp, thin_world(eff))
        g5 = rr.gate5(ctx.store, ctx.spec)
        c595 = {"L_C": rf.cfg_keys(SPEC, "L", {}), "S_C": rf.cfg_keys(SPEC, "S", {})}
        with rf.min_trades_override(rf.CONTROL_N):
            main = rr.run_all(ctx, g5, None, c595)                                                           # основной прогон: N = 20 → условие (а) режет клетки (15–16 сделок в месяц)
        assert main["outcomes"]["L:full"].final == {} and main["freeze"]["L"][0] == "база"
        out = os.path.join(tmp, "res")
        n_calls = len(srv.calls)
        sens = rr.sens_run(ctx, g5, [20, 10, 15, 25], control=main, out_dir=out, gates=None, c595=c595, meta={"frozen_sha256": "ab" * 32})
        assert rf.MIN_TRADES == (20, 20, 20, 20, 8) and sens["ns"] == [20, 10, 15, 25]
        assert sens["control"]["equal"] and sens["control"]["diff"] == [] and sens["control"]["engine_variants"] == 0 and sens["by_n"]["20"]["engine_variants"] == 0
        assert sens["min_trades"]["10"] == [10, 10, 10, 10, 4] and sens["min_trades"]["25"] == [25, 25, 25, 25, 10]
        L = {n: sens["by_n"][str(n)]["L"] for n in (10, 15, 20, 25)}
        assert L[20]["full"] == {} and L[25]["full"] == {}                                                    # порог 20 и 25 режет все клетки по (а)
        assert L[10]["full"].get("rr") == "rr_3" and L[15]["full"].get("rr") == "rr_3"                         # 15–16 сделок в месяц проходят N = 10 и 15
        assert L[10]["cells"]["dropped_a"] < L[20]["cells"]["dropped_a"] and L[20]["cells"]["total"] > 0
        assert sens["by_n"]["10"]["engine_variants"] > 0 and L[10]["full_totals"]["final"]["n"] == L[10]["full_totals"]["base"]["n"] > 0
        assert os.path.exists(os.path.join(out, "sens-10.pkl")) and json.load(open(os.path.join(out, "sens.json"), encoding="utf-8"))["ns"] == [20, 10, 15, 25]
        assert len(srv.calls) > n_calls                                                                       # новые прогоны — только у N ≠ 20
        # отчёт: раздел чувствительности, в файл заморозки не попадает
        pkl, md, fz, sj = (os.path.join(tmp, n) for n in ("results.pkl", "r.md", "frozen.json", "sens.json"))
        pickle.dump(main, open(pkl, "wb"))
        json.dump(sens, open(sj, "w", encoding="utf-8"))
        assert rep.main([pkl, "--md", md, "--frozen", fz, "--sens", sj]) == 0
        text = open(md, encoding="utf-8").read()
        for s in ("## Чувствительность порога минимума сделок", "«также нужно протитровать минимальное количество сделок от 10-15-20-25»", "N = 10 — 10/10/10/10/4", "(контроль, вердикт)",
                  "`rr`: база → rr_3", "m_сем", "совпал с `results.pkl`", "abababababababab"):
            assert s in text, s
        assert "sens" not in open(fz, encoding="utf-8").read()
        # контроль не совпал: сводка записана, затем остановка
        bad = pickle.loads(pickle.dumps(main))
        bad["wf"]["L"]["m"] += 1
        raises(rr.GuardError, rr.sens_run, ctx, g5, [20, 10], control=bad, out_dir=os.path.join(tmp, "res2"), gates=None, c595=c595)
        j = json.load(open(os.path.join(tmp, "res2", "sens.json"), encoding="utf-8"))
        assert j["ns"] == [20] and j["control"]["equal"] is False and "L/m" in j["control"]["diff"][0]


def test_level_rows_and_sigma():
    ser = rf.make_series([0, 0, 31, 62, 135], [0, 1, 2, 3, 4], [1.0, 3.0, -2.0, 4.0, 6.0])
    rows = rep.level_rows(ser)
    assert [r[0] for r in rows] == list(rep.MONTH_RU) + ["всего"]
    assert rows[0][1:3] == (4.0, 2) and rows[1][1:3] == (-2.0, 1) and rows[2][1:3] == (4.0, 1) and rows[4][1:3] == (6.0, 1) and rows[5][1:3] == (12.0, 5)
    assert np.isclose(rows[0][3], 2.0) and np.isclose(rows[0][4], 4.0 / 31) and np.isclose(rep.trade_sigma(ser), np.std([1, 3, -2, 4, 6], ddof=1))
    assert rep.frozen_json.__doc__ and rep.f2(float("nan")) == "—"


def main():
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for n, f in tests:
        try:
            f()
            print("ok   ", n)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL ", n, "-", type(e).__name__, e)
            tb = traceback.extract_tb(e.__traceback__)
            print("       %s:%d  %s" % (os.path.basename(tb[-1].filename), tb[-1].lineno, tb[-1].line))
    print("%d тестов, %d провалов" % (len(tests), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
