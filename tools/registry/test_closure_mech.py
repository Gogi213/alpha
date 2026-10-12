import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
import closure_mech
from closures import g124_profit_throttle as g124

D = 86_400_000


class T(unittest.TestCase):
    def test_scale1_is_identity(self):  # «выкл = прежний»: scale 1 не меняет сделок (вне домена, прямой вызов)
        c = [(i * D // 3, 1.0 if i % 2 else -0.5) for i in range(30)]
        self.assertEqual(g124.run(c, None, 90, 3, 1.0), c)

    def test_throttle_after_big_day(self):
        c = [(0, 1.0), (D, 0.5), (2 * D, 10.0), (3 * D, 5.0), (4 * D, 5.0)]
        out = g124.run(c, None, 90, 1, 0.0)
        self.assertEqual([u for _, u in out], [1.0, 0.5, 10.0, 0.0, 5.0])

    def test_domain_refusal(self):
        with self.assertRaises(SystemExit):
            list(closure_mech.grid_points(g124.PARAMS, "q=91"))
        self.assertEqual(len(list(closure_mech.grid_points(g124.PARAMS))), 4 * 5 * 3)

    def test_ctx_refuses_undeclared_source(self):
        with self.assertRaises(SystemExit):
            closure_mech.Ctx("2026-01", ()).signals()

    def test_ctx_reads_declared(self):
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "2026-01", "2026-01-05", "t-bid-btc4h-q1"); os.makedirs(t)
            open(os.path.join(t, "signals.csv"), "w").write("# c\nsymbol,day_utc,signal_index\nAAA,2026-01-05,0\n")
            self.assertEqual(closure_mech.Ctx("2026-01", ("signals",), s_v=d).signals()[0]["symbol"], "AAA")

    def test_non_closes_kind_refused_until_chain(self):
        class P: KIND = "keep"
        with self.assertRaises(SystemExit):
            closure_mech.run_all(P, {})

    def test_write_keep_format(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "k.csv"); closure_mech.write_keep(p, {("B", "2026-01-05", "f", "2"), ("A", "2026-01-05", "f", "1")})
            self.assertEqual(open(p).read(), "symbol,day_utc,signal_index,form\nA,2026-01-05,1,f\nB,2026-01-05,2,f\n")

    def test_run_keep_identity_and_foreign_keys(self):
        from closures import keep_all
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "2026-01", "2026-01-05", "t-bid-btc4h-q1"); os.makedirs(t)
            f = closure_mech.B1_FORM
            open(os.path.join(t, "signals.csv"), "w").write("symbol,day_utc,form,signal_index\nAAA,2026-01-05," + f + ",0\nAAA,2026-01-05,other,1\n")
            open(os.path.join(t, "rounds.csv"), "w").write("symbol,day_utc,form,signal_index,net_bps,reason\nAAA,2026-01-05," + f + ",0,5.0,deadline\n")
            seen = []

            def fake_chain(mon, work, keep=None):
                os.makedirs(work, exist_ok=True); seen.append(keep)
                out = {}
                for c in (0, 3):
                    out[c] = os.path.join(work, f"c{c}.json"); json.dump({"v": {mon: {str(c): [[1, 2.5]]}}}, open(out[c], "w"))
                return out
            orig = closure_mech.Ctx.__init__
            closure_mech.Ctx.__init__ = lambda self, mon, allowed, s_v=None, study=None: orig(self, mon, allowed, s_v=d)
            try:
                rows = closure_mech.run_keep(keep_all, "2026-01", os.path.join(d, "w"), chain_fn=fake_chain)
            finally:
                closure_mech.Ctx.__init__ = orig
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r["signals_before"] == r["signals_kept"] == 1 and r["sum_before"] == r["sum_after"] == 2.5 for r in rows))
            self.assertEqual(open(seen[-1]).read().splitlines()[1], "AAA,2026-01-05,0," + f)

    def test_g50_excludes_after_boundary(self):
        from closures import g50_tf_boundary as g
        S = 1_000_000_000
        data = [{"symbol": "A", "day_utc": "d", "form": "f", "signal_index": str(i), "t0_ns": str(t * S)} for i, t in enumerate((60, 63, 90, 125))]
        out = g.run(data, None, tf=60, window=5)
        self.assertEqual([k[3] for k in out], ["2"])  # 60 и 63 (0 и 3 с), 125 (5 с) исключены; 90 (30 с) остаётся
        self.assertEqual(g.run(data, None, tf=60, window=300), [])

    def test_g126_counts_distinct_coins_causally(self):
        from closures import g126_approach_brake as g
        S = 1_000_000_000
        mk = lambda i, sym, t: {"symbol": sym, "day_utc": "d", "form": "f", "signal_index": str(i), "t0_ns": str(t * S)}
        data = [mk(0, "A", 100), mk(1, "B", 105), mk(2, "C", 108), mk(3, "A", 500)]
        out = {k[3] for k in g.run(data, None, N=3, window=10)}
        self.assertEqual(out, {"0", "1", "3"})   # C — третья монета в окне: пропуск; A в 500 один

    def test_h14_rules_and_unlinked_signal_kept(self):
        from closures import p07_h14_approach_no as g
        class C:
            def approaches(self, sym, day):
                return {(1000, "7"): {"approach_index": "0"}, (2000, "7"): {"approach_index": "2"}}
        mk = lambda i, t, pt: {"symbol": "A", "day_utc": "d", "form": "f", "signal_index": str(i), "t0_ns": str(t * 1_000_000), "price_tick": pt}
        data = [mk(0, 1000, "7"), mk(1, 2000, "7"), mk(2, 3000, "7")]   # номера 1, 3, без связи
        ids = lambda r: [k[3] for k in g.run(data, C(), rule=r)]
        self.assertEqual(ids("k1"), ["0", "2"]); self.assertEqual(ids("k3"), ["0", "1", "2"])
        self.assertEqual(ids("n2"), ["1", "2"]); self.assertEqual(ids("n4"), ["2"])
        self.assertEqual(ids("e1"), ["0", "2"]); self.assertEqual(ids("e3"), ["1", "2"]); self.assertEqual(ids("e10"), ["2"])   # без связи со стеной не режется

    def test_h9_pause_blocks_after_close_and_respects_busy(self):
        from closures import p07_h9_pause as g
        m = 60 * 1_000_000_000
        mk = lambda i, t, ex, idle: {"symbol": "A", "day_utc": "d", "form": "f", "signal_index": str(i), "t0_ns": str(t * m), "step": "x",
                                     "idle_ns": str(idle * m), "residual": "", "round_exit_ns": str(ex * m) if ex else ""}
        data = [mk(0, 0, 10, 10), mk(1, 12, 20, 20), mk(2, 40, 0, 40)]   # круг 0 закрыт на 10-й мин
        ids = lambda p: [k[3] for k in g.run(data, None, pause=p)]
        self.assertEqual(ids(1), ["0", "1", "2"]); self.assertEqual(ids(5), ["0", "2"])   # пауза 5: 12 < 10+5, сигнал 1 снят; сигнал 2 вне паузы

    def test_g26_buckets_causal(self):
        from closures import g26_vol_family as g
        day0 = g._day_start_ms("2026-03-02")
        sig = [(day0 - (300 - i) * 60000, float(i)) for i in range(300)] + [(day0 + 60000, 1000.0), (day0 + 120000, 0.5)]
        class C:
            def sigma(self, sym): return sig
        mk = lambda i, ms: {"symbol": "A", "day_utc": "2026-03-02", "form": "f", "signal_index": str(i), "t0_ns": str((day0 + ms) * 1_000_000)}
        data = [mk(0, 61000), mk(1, 121000)]   # σ 1000 (верхняя), 0.5 (нижняя); будущее в границы не входит
        ids = lambda **p: [k[3] for k in g.run(data, C(), **p)]
        self.assertEqual(ids(nq=3, bucket=2), ["0"]); self.assertEqual(ids(nq=3, bucket=0), ["1"])
        self.assertEqual(ids(nq=3, bucket=1), []); self.assertEqual(ids(nq=3, bucket=4), [])

    def test_g148_leverage_and_drawdown(self):
        from closures import g148_leverage as g
        data = [(0, 10.0), (1000, -30.0), (2000, 5.0)]
        fills = g.run(data, None, L=2)
        self.assertEqual([u for _, u in fills], [20.0, -60.0, 10.0])
        st = g.stats(fills)
        self.assertEqual(st["max_dd_usd"], -60.0); self.assertEqual(st["worst_day_usd"], -30.0)

    def test_g118_macd_trail_causal_and_early_only(self):
        from closures import g118_macd_trail as g
        day = 86_400_000
        # сутки -1: пила (даёт порог), сутки 0: рост 200 минут, затем обвал
        px = [100.0 + (i % 10) for i in range(1440)] + [100.0 + i * 0.5 for i in range(200)] + [200.0 - i * 2 for i in range(100)]
        kl = [(i * 60_000 + 0 * day, p, p, p, p) for i, p in enumerate(px)]

        class C:
            def klines(self, sym):
                return kl
        rd = {"symbol": "X", "day_utc": "d", "form": "f", "signal_index": "1", "t0_ns": str((1440 + 30) * 60_000 * 1_000_000),
              "exit_ns": str((1440 + 295) * 60_000 * 1_000_000)}
        out = g.run([rd], C(), q=50)
        k = ("X", "d", "f", "1")
        self.assertIn(k, out); ns, px_, taker, why = out[k]
        self.assertTrue(taker and why == "macd_trail" and (1440 + 200) * 60_000_000_000 < ns < int(rd["exit_ns"]))
        rd["exit_ns"] = str((1440 + 150) * 60_000 * 1_000_000)      # исходный выход раньше обвала — не трогаем
        self.assertEqual(g.run([rd], C(), q=50), {})

    def test_g130_stop_round_branches(self):
        from closures import g130_stop_round as g
        m = 60_000
        base = [(i * m, 100.0, 100.0, 100.0, 100.0) for i in range(300)]
        sig = {"symbol": "X", "day_utc": "d", "form": "f", "signal_index": "1", "entry_px": "100.0", "price_tick": "100000"}   # тик 1e-3
        rd = {"symbol": "X", "day_utc": "d", "form": "f", "signal_index": "1", "dir": "1", "entry_vwap": "100.0", "reason": "stop",
              "t0_ns": "0", "exit_ns": str(10 * m * 1_000_000)}

        class C:
            def __init__(s, kl): s.kl = kl
            def klines(s, sym): return s.kl
            def signals(s): return [sig]
        # S0 = 98.0; j=2,d=0: круглое 100 тиков (0,1) -> 98.0 ; d=100 тиков не в домене, проверяем d=21: 98.0 - 0.021 = 97.979
        kl = list(base); kl[20] = (20 * m, 100.0, 100.0, 97.9, 98.0)
        c1 = C(kl); v = g.run([rd], c1, d=21, j=2)[("X", "d", "f", "1")]
        self.assertAlmostEqual(v[1], 97.979, places=6); self.assertEqual((v[2], v[3]), (True, "stop_round"))
        self.assertEqual(g.run([rd], c1, d=0, j=2)[("X", "d", "f", "1")][1], 98.0)          # S'=S0 при d=0, круглом S0
        kl2 = list(base); kl2[15] = (15 * m, 100.0, 101.2, 100.0, 101.0)                         # активация трейла раньше
        c2 = C(kl2); self.assertEqual(g.run([rd], c2, d=3, j=2)[("X", "d", "f", "1")][3], "trail_floor")
        c3 = C(base); v3 = g.run([rd], c3, d=3, j=2)[("X", "d", "f", "1")]                                # ничего - close дедлайна (минута 239)
        self.assertEqual(v3[3], "deadline_round"); self.assertEqual(v3[0], 240 * m * 1_000_000)
        self.assertEqual(g.run([{**rd, "reason": "deadline"}], c1, d=3, j=2), {})            # не стоп - не трогаем
        self.assertEqual(g.snap_tick(1.0006e-4), 1e-4); self.assertEqual(g.snap_tick(0.0051), 0.005)

    def test_g144_beta_hedge_report(self):
        from closures import g144_beta_hedge as g
        m = 60_000
        import math
        btc = [(i * m, 100.0 * math.exp(0.001 * math.sin(i / 7.0) * (i % 5))) for i in range(3000)]
        coin = [(i * m, 0, 0, 0, 50.0 * btc[i][1] / 100.0) for i in range(3000)]          # бета = 1 к BTC

        class C:
            def klines(s, sym): return coin if sym == "X" else []
            def btc(s): return btc
        rd = lambda sym: {"symbol": sym, "t0_ns": str(2000 * m * 1_000_000), "exit_ns": str(2100 * m * 1_000_000), "dir": "1", "net_bps": "10.0"}
        c = C()
        out = g.run([rd("X"), rd("Y")], c, bd=1)[0]
        self.assertEqual((out["n"], out["no_coin_klines"]), (1, 1)); self.assertAlmostEqual(out["beta_mean"], 1.0, places=2)
        rb = (btc[2100][1] / btc[2000][1] - 1) * 1e4
        self.assertAlmostEqual(out["mean_bps_after"], 10.0 - rb - 2 * g.TAKER_BPS * out["beta_mean"], places=1)

    def test_g19_markout_by_rank(self):
        from closures import g19_markout_rank as g
        m = 60_000
        kl = [(i * m, 0, 0, 0, 100.0 + i) for i in range(50)]

        class C:
            def klines(s, sym): return kl if sym == "X" else []
        rd = lambda sym, lf: {"symbol": sym, "t0_ns": str(10 * m * 1_000_000), "dir": "1", "entry_vwap": "110.0", "legs_filled": lf}
        out = g.run([rd("X", "2"), rd("X", "3"), rd("Y", "1")], C(), h=5)
        by = {o["rank"]: o for o in out}
        self.assertEqual((by["2"]["n"], by["all"]["n"], by["all"]["no_klines_rounds"]), (1, 2, 1))
        self.assertAlmostEqual(by["2"]["mean_bps"], (114.0 / 110.0 - 1) * 1e4, places=2)       # минута 14 закрыта к t0+5

    def test_g80_depth_excursion_bins(self):
        from closures import g80_depth_excursion as g
        m = 60_000
        kl = [(i * m, 0, 0, 99.0 if i == 5 else 100.0, 100.0) for i in range(30)]
        sig = [{"symbol": "X", "day_utc": "d", "form": "f", "signal_index": str(i), "price_tick": "7"} for i in range(4)]

        class C:
            def klines(s, sym): return kl
            def signals(s): return sig
            def approaches(s, sym, day): return {(10 * m, "7"): {"depth_behind_lots": "50"}}
        rd = lambda i: {"symbol": "X", "day_utc": "d", "form": "f", "signal_index": str(i), "t0_ns": str(10 * m * 1_000_000),
                        "exit_ns": str(20 * m * 1_000_000), "entry_vwap": "100.0", "reason": "stop"}
        c = C()
        out = g.run([rd(0), rd(1)], c, q=3)                       # t0 минуты 10, низ 99 в минуте 5 - вне окна [10;20]: выброс 0
        self.assertEqual(sum(o["n"] for o in out), 2); self.assertEqual(out[0]["excursion_mean_bps"], 0.0)
        kl[12] = (12 * m, 0, 0, 98.0, 100.0)
        self.assertAlmostEqual(g.run([rd(0)], c, q=3)[-1]["excursion_mean_bps"], 200.0)

    def test_g111_target_from_prior_impulse(self):
        from closures import g111_impulse_target as g
        m = 60_000
        kl = [(i * m, 10.0, 12.0 if i < 10 else 10.5, 10.0 if i < 10 else 9.9, 10.0) for i in range(40)]     # импульс 10..12 до минуты 10
        kl[20] = (20 * m, 10.0, 11.1, 10.0, 10.0)

        class C:
            def klines(self, sym):
                return kl
        rd = {"symbol": "X", "day_utc": "d", "form": "f", "signal_index": "1", "dir": "1", "entry_vwap": "10.0",
              "t0_ns": str(10 * m * 1_000_000), "exit_ns": str(30 * m * 1_000_000)}
        out = g.run([rd], C(), w=15, f=0.5)                  # цель 10+0.5*2=11 -> касание в минуте 20
        v = out[("X", "d", "f", "1")]
        self.assertAlmostEqual(v[1], 11.0); self.assertEqual((v[2], v[3]), (False, "impulse_target")); self.assertEqual(v[0], 20 * m * 1_000_000)
        self.assertEqual(g.run([rd], C(), w=15, f=0.75), {})   # цель 11,5 не достигнута
        rd["exit_ns"] = str(15 * m * 1_000_000)               # исходный выход раньше касания
        self.assertEqual(g.run([rd], C(), w=15, f=0.5), {})

    def test_g18_report_rows(self):
        from closures import g18_fee_share as g
        rd = [{"dir": "1", "entry_vwap": "100", "exit_px": "101", "net_bps": "97.48", "qty": "5", "reason": "trail"},
              {"dir": "1", "entry_vwap": "100", "exit_px": "99", "net_bps": "-102.52", "qty": "5", "reason": "stop"}]
        rows = {r["reason"]: r for r in g.run(rd, None, off=0)}
        self.assertEqual(rows["all"]["n"], 2); self.assertAlmostEqual(rows["trail"]["cost_bps_sum"], 2.52, places=3)
        self.assertAlmostEqual(rows["all"]["cost_usd"], 0.25, places=2)

    def test_rounds_net_formula_and_tree(self):
        f = closure_mech.B1_FORM
        self.assertAlmostEqual(closure_mech.net_bps(1, 100.0, 101.0, True), 100.0 - 1.26 - 3.15)
        self.assertAlmostEqual(closure_mech.net_bps(1, 100.0, 100.0, False), -2.52)
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "2026-01", "2026-01-05", "t-bid-btc4h-q1"); os.makedirs(t)
            open(os.path.join(t, "extra.txt"), "w").write("x")
            hdr = "symbol,day_utc,form,signal_index"
            open(os.path.join(t, "signals.csv"), "w").write(f"# c\n{hdr},exit_ns\nA,2026-01-05,{f},0,5\nA,2026-01-05,{f},1,6\n")
            open(os.path.join(t, "rounds.csv"), "w").write(f"# c\n{hdr},dir,entry_vwap,exit_px,net_bps,reason,exit_ns\nA,2026-01-05,{f},0,1,100.0,99.0,-1.0,stop,5\n")
            ctx = closure_mech.Ctx("2026-01", ("signals", "rounds"), s_v=d)
            rep = {("A", "2026-01-05", f, "0"): (9, 101.0, True, "take")}
            closure_mech.build_tree(ctx, "2026-01", os.path.join(d, "sv"), rep)
            o = os.path.join(d, "sv", "2026-01", "2026-01-05", "t-bid-btc4h-q1")
            r = open(os.path.join(o, "rounds.csv")).read().splitlines()
            self.assertEqual(r[0], "# c"); self.assertTrue(r[2].endswith(",101.0000000000,%.6f,take,9" % (100.0 - 1.26 - 3.15)), r[2])
            self.assertEqual(open(os.path.join(o, "signals.csv")).read().splitlines()[2:], [f"A,2026-01-05,{f},0,9", f"A,2026-01-05,{f},1,6"])
            self.assertTrue(os.path.islink(os.path.join(o, "extra.txt")))

    def test_ctx_study_paths_by_month(self):
        c = closure_mech.Ctx("2026-03", ("sigma",))
        self.assertTrue(c.study.endswith("/tk046/mar/study") and c.epoch_study.endswith("/mar/home/alpha/epochs/e-mar/study"))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            p, o = os.path.join(d, "c.json"), os.path.join(d, "o.csv")
            json.dump({"v": {"2026-01": {"0": [[0, 1.0], [D, 2.0]]}}}, open(p, "w"))
            closure_mech.main(["--closes", p, "--mechanic", "g124_profit_throttle", "--grid", "q=90;D=1;scale=0.0", "--out", o])
            self.assertEqual(len(open(o).read().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
