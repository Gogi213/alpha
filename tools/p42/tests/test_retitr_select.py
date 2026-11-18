"""Тесты правила отбора §13.21 / §13.21.1 (retitr_report.py) и генератора клеток (retitr_cells.py) на синтетических данных.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_retitr_select.py` — выполняет все test_* и печатает итог.
"""
import contextlib
import csv
import io
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import retitr_cells as rc  # noqa: E402
import retitr_report as rr  # noqa: E402


def mk(cid, d, se=1.0, n_a=100, n_b=100, **kw):
    sp = rc.spec_from_id(cid)
    assert sp is not None, cid
    return rr.Cand(cid, sp, n_a, n_b, d, se, **kw)


def e1(deltas, se=1.0, n=100):
    """E1: клетки s ∈ {1, 1.5, 2, 3, 4, 6, 8} (5 — база, не посчитана)."""
    return [mk("E1_s%g" % s, d, se, n, n) for s, d in deltas.items()]


def dec(cands, mt=70):
    return rr.decide(cands, mt)


def one(res, group):
    return next(d for d in res["decisions"] if d["group"] == group)


# ---- соседи, сглаживание, плато ------------------------------------------------------------------------------------------

def test_neighbours_exclude_base_and_smoothing():
    res = dec(e1({1: -5, 1.5: -4, 2: -1, 3: 1, 4: 2, 6: 1.5, 8: -3}, se=1.2))
    d = one(res, "s")
    assert d["chosen"] == "E1_s4", d
    assert d["neighbours"]["E1_s4"] == ["E1_s3", "E1_s6"], d["neighbours"]["E1_s4"]  # у S = 4 соседи 3 и 6 — не 5 (база)
    assert abs(d["smoothed"]["E1_s4"] - np.mean([1, 2, 1.5])) < 1e-12              # база Δ = 0 в сглаживание не входит
    assert d["accepted"] and "без плато" not in d["flags"] and d["selected"]
    assert d["extend"] is None


def test_plateau_fail_low_neighbour():
    # выбранная +5, SE 1: сосед +1 < 5 − 1 → плато нарушено
    res = dec(e1({1: -9, 1.5: -9, 2: -9, 3: 1, 4: 5, 6: 9, 8: -9}, se=1.0))
    d = one(res, "s")
    # сглаженный счёт: s6 = mean(5, 9, −9) = 1.67; s4 = mean(1, 5, 9) = 5 → выбрана s4; соседи 3 (+1) и 6 (+9): +1 < 4 → отказ
    assert d["chosen"] == "E1_s4" and not d["accepted"] and "плато" in d["reason"], d
    assert not res["accepted"]


def test_plateau_fail_negative_neighbour_and_pass():
    res = dec(e1({1: -9, 1.5: -9, 2: -9, 3: -0.5, 4: 5, 6: 6, 8: -9}, se=10.0))
    d = one(res, "s")
    assert d["chosen"] == "E1_s4" and not d["accepted"], d      # сосед s3 < 0 — плато нарушено даже при большом SE
    res = dec(e1({1: -9, 1.5: -9, 2: -9, 3: 4.2, 4: 5, 6: 6, 8: -9}, se=1.0))
    d = one(res, "s")
    assert d["chosen"] == "E1_s4" and d["accepted"], d          # 4.2 ≥ 5 − 1 и 6 ≥ 4
    assert d["flags"] == []


def test_delta_nonpositive_not_accepted():
    res = dec(e1({1: -3, 1.5: -3, 2: -3, 3: -2, 4: -1, 6: -2, 8: -3}))
    d = one(res, "s")
    assert not d["accepted"] and "Δ_A ≤ 0" in d["reason"]


def test_edge_extend_flags():
    res = dec(e1({1: 4, 1.5: 3, 2: 1, 3: -1, 4: -2, 6: -2, 8: -3}, se=2.0))
    d = one(res, "s")
    assert d["chosen"] == "E1_s1" and d["neighbours"]["E1_s1"] == ["E1_s1.5"]   # у края один сосед
    assert d["accepted"] and d["extend"] == 0.5 and res["extend"] == [0.5]
    res = dec(e1({1: -3, 1.5: -3, 2: -3, 3: -2, 4: 1, 6: 3, 8: 4}, se=2.0))
    d = one(res, "s")
    assert d["chosen"] == "E1_s8" and d["accepted"] and d["extend"] == 12.0
    # край, но Δ_A ≤ 0 — расширения нет
    res = dec(e1({1: -1, 1.5: -2, 2: -3, 3: -3, 4: -3, 6: -3, 8: -3}))
    assert one(res, "s")["extend"] is None


def test_no_neighbour_accepted_without_plateau():
    res = dec([mk("E1_s3", 2.0)])                              # одна посчитанная клетка оси
    d = one(res, "s")
    assert d["accepted"] and "без плато" in d["flags"] and d["neighbours"]["E1_s3"] == []
    res = dec([mk("NT_step", 1.0)])                            # одиночная клетка
    d = one(res, "NT_step")
    assert d["accepted"] and "без плато" in d["flags"]
    res = dec([mk("NT_step", -1.0)])
    assert not one(res, "NT_step")["accepted"]


def test_pair_axes_take_best_without_smoothing():
    res = dec([mk("T_th3", 1.0), mk("T_th8", 3.0), mk("T_q25", -1.0), mk("T_q75", -2.0)])
    d = one(res, "t_high")
    assert d["chosen"] == "T_th8" and d["accepted"] and "без плато" in d["flags"]
    assert d["neighbours"]["T_th3"] == []                      # у T / P соседей нет
    d = one(res, "q")
    assert not d["accepted"]
    assert [x["chosen"] for x in res["accepted"]] == ["T_th8"]


def test_min_trades_mask_and_ineligible_neighbour():
    cs = e1({1: -9, 1.5: -9, 2: -9, 3: 2, 4: 5, 6: 4, 8: -9}, se=3.0)
    # лучшая по сглаженному (s4) — 69 сделок в B → не выбирается; клетка остаётся соседом остальных
    cs = [rr.Cand(c.cid, c.spec, c.n_a, 69 if c.cid == "E1_s4" else c.n_b, c.d_a, c.se) for c in cs]
    d = one(dec(cs), "s")
    assert d["chosen"] != "E1_s4", d
    assert "E1_s4" in d["neighbours"]["E1_s3"] + d["neighbours"]["E1_s6"]
    cs0 = [rr.Cand(c.cid, c.spec, 50, 100, c.d_a, c.se) for c in cs]   # все клетки без сделок в A — выбора нет
    d = one(dec(cs0), "s")
    assert d["chosen"] is None and not d["accepted"]


def test_stop_family_one_winner():
    cs = [mk("Els_ls1", 2.0), mk("NS_c3", 3.0), mk("NS_c5", 2.5), mk("NS_lin", 6.0), mk("NS_geo", -1.0), mk("NS_step", 0.5), mk("NT_step", 1.0)]
    res = dec(cs)
    sel = {d["group"]: d["selected"] for d in res["decisions"]}
    # NS_lin (6.0) — наибольшая Δ_A среди принятых → единственный победитель семейства «stop»; NT независим
    assert sel["NS_lin"] and not sel["Els_ls1"] and not sel["NS_step"] and not sel["NS_geo"] and not sel["sn"], sel
    assert sel["NT_step"]
    assert sorted(d["chosen"] for d in res["accepted"]) == ["NS_lin", "NT_step"]
    d = one(res, "sn")                                          # sn-ось (NS_c3, NS_c5): соседи — друг друга; 2.5 ≥ 3 − 1 → принята, но не победитель
    assert d["accepted"] and d["chosen"] == "NS_c3" and not d["selected"]
    cs = [mk("Els_ls1", 1.0), mk("NS_c3", 4.0), mk("NS_c5", 3.5), mk("NS_lin", -2.0), mk("NS_geo", 3.9), mk("NS_step", -3.0)]
    assert [d["chosen"] for d in dec(cs)["accepted"]] == ["NS_c3"]
    assert dec([mk("Els_ls1", -1.0), mk("NS_c3", -4.0), mk("NS_c5", -3.5)])["accepted"] == []


# ---- вето по портфелю ----------------------------------------------------------------------------------------------------------

def sigc(cid, d, port_d, port_se, se=1.0):
    return mk(cid, d, se, mode="sig", port_d=port_d, port_se=port_se)


def test_veto_blocks_and_passes():
    # сигнальная Δ_A +5 принята по правилу; портфельная −3 при SE 2 (< −2) → вето
    res = dec([sigc("NT_step", 5.0, -3.0, 2.0)])
    d = one(res, "NT_step")
    assert d["vetoed"] and not d["accepted"] and not d["selected"] and "вето по портфелю" in d["flags"] and res["accepted"] == []
    assert d["port_d"] == -3.0 and d["port_se"] == 2.0            # портфельная Δ_A печатается и у не принятого
    # −1·SE ровно — проходит (≥); выше — проходит
    assert one(dec([sigc("NT_step", 5.0, -2.0, 2.0)]), "NT_step")["selected"]
    assert one(dec([sigc("NT_step", 5.0, 0.7, 2.0)]), "NT_step")["selected"]
    # вето — не второй критерий: не принятое по правилу остаётся не принятым при любой портфельной Δ_A
    d = one(dec([sigc("NT_step", -1.0, 9.0, 1.0)]), "NT_step")
    assert not d["selected"] and not d["vetoed"]
    # портфельные клетки (P, F): вето не применяется
    d = one(dec([mk("P_n03", 2.0, mode="port", port_d=-9.0, port_se=1.0)]), "natr_min")
    assert d["selected"] and not d["vetoed"]


def test_veto_inside_ordered_axis_no_fallback_and_family():
    # ось s: выбранная s4 вето → ось не принимается (второго значения нет)
    cs = e1({1: -9, 1.5: -9, 2: -9, 3: 4.2, 4: 5, 6: 6, 8: -9}, se=1.0)
    for c in cs:
        c.mode, c.port_d, c.port_se = "sig", (-5.0 if c.cid == "E1_s4" else 1.0), 1.0
    res = dec(cs)
    d = one(res, "s")
    assert d["chosen"] == "E1_s4" and d["vetoed"] and res["accepted"] == []
    # стоп-семейство: ветированный сильнейший не мешает следующему по Δ_A (вето — до выбора победителя)
    res = dec([sigc("NS_lin", 6.0, -9.0, 1.0), sigc("Els_ls1", 2.0, 1.0, 1.0)])
    assert [d["chosen"] for d in res["accepted"]] == ["Els_ls1"]


# ---- E2: совместная сетка ---------------------------------------------------------------------------------------------------

def e2_cands(side, s_star, acc, f):
    """E2-клетки (без равных базе) с Δ_A = f(s, rr, bm)."""
    out = []
    for cid, _ in rc.e2_cells(side, s_star, acc):
        sp = rc.spec_from_id(cid)
        c = sp["coords"]
        out.append(rr.Cand(cid, sp, 100, 100, f(c["s"], c["rr"], int(c["bm"])), 1.0))
    return out


def test_e2_skip_equal_to_base_and_counts():
    assert len(rc.e2_cells("L", 3)) == 17 and len(rc.e2_cells("S", 3)) == 17
    assert len(rc.e2_cells("L", 1)) == 11 and len(rc.e2_cells("S", 8)) == 11     # край — один сосед
    ids = [c for c, _ in rc.e2_cells("L", 3)]
    assert "E2_s3_r2_bm0" not in ids and "E2_s3_r2_bm1" in ids                  # (S*, rr = 2, bm базы) = база стадии (в E1 посчитана)
    ids = [c for c, _ in rc.e2_cells("S", 4)]
    assert "E2_s4_r2_bm1" not in ids and "E2_s3_r1_bm1" in ids and not any(i.startswith("E2_s5") for i in ids)   # у S = 4 соседи 3 и 6
    assert any(i.startswith("E2_s6") for i in ids)
    ids = [c for c, _ in rc.e2_cells("L", 4, "s=4 bm=1")]                      # принятый bm=1 сдвигает базу: (S*, 2, bm=1) равна базе стадии
    assert "E2_s4_r2_bm1" not in ids and "E2_s4_r2_bm0" in ids and len(ids) == 17
    ids = [c for c, _ in rc.e2_cells("L", 5, "")]                              # S* = 5 (в E1 ничего не принято): (5, 2, база) = сама база
    assert "E2_s5_r2_bm0" not in ids and len(ids) == 17


def test_e2_grid_selection_plateau_and_bm_separate():
    def f(s, r, bm):                                # пик в (s=4, rr=3, bm=1); при bm = 0 всюду хуже
        return (3.0 if bm == 1 else -1.0) + {3: 0.5, 4: 1.0, 6: 0.2}[s] + {1: 0.0, 2: 0.5, 3: 1.0}[r]
    res = dec(e2_cands("L", 4, "s=4", f))
    d = one(res, "E2")
    assert d["chosen"] == "E2_s4_r3_bm1", d
    nb = d["neighbours"]["E2_s4_r3_bm1"]
    # вдоль s: 3 и 6 (при rr = 3, bm = 1); вдоль rr: 2 (клетка (4, 2, bm = 1) посчитана; (4, 2, bm = 0) — база стадии, пропущена); bm не смешивается
    assert sorted(nb) == sorted(["E2_s3_r3_bm1", "E2_s6_r3_bm1", "E2_s4_r2_bm1"]), nb
    assert all(n.endswith("bm1") for n in nb)
    assert d["accepted"] and res["accepted"][0]["chosen"] == "E2_s4_r3_bm1"

    def g(s, r, bm):                                # плато нарушено: пик (4, 3, 1) — наибольший сглаженный счёт, но сосед вдоль rr отрицателен
        return {(4, 3, 1): 6.0, (6, 3, 1): 6.0, (3, 3, 1): 5.0, (4, 2, 1): -0.5}.get((s, r, bm), 0.0)
    d = one(dec(e2_cands("L", 4, "s=4", g)), "E2")
    assert d["chosen"] == "E2_s4_r3_bm1" and not d["accepted"] and "плато" in d["reason"] and "E2_s4_r2_bm1" in d["reason"], d


# ---- ключи, слияние, сравнение ----------------------------------------------------------------------------------------------

def test_merge_and_canon():
    assert rc.merge_keys("a=1 b=2", "b=3 c=4") == "a=1 b=3 c=4"
    assert rc.merge_keys("", "") == ""
    assert rc.merge_keys("fa=x fo=lo fv=1", "fa=y fo=hi fv=2", "fa=x fo=lo fv=5") == "fa=x fo=lo fv=5 fa=y fo=hi fv=2"
    assert rc.canon("s=5 rr=2.0 bm=0") == rc.canon("")
    assert rc.canon("s=3") != rc.canon("s=5.5") and rc.canon("s=3 bm=1") != rc.canon("s=3")
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "v.txt")
        n = rc.emit("L", ["Els", "NS", "NT"], p, "s=3")
        rows = open(p, encoding="utf-8").read().splitlines()
        assert n == 7 and rows[0].startswith("L_base ") and rows[0].endswith(" s=3")
        assert rows[1].startswith("Els_ls1 ") and rows[1].endswith(" s=3 ls=1")
        assert len(rows) == 8
        assert rc.emit("S", ["E1"], p) == 7
        n = rc.emit("L", ["asm"], p, json.dumps({"cumulative_keys": "s=1", "extend": [{"axis": "s", "value": 0.5}]}))
        rows = open(p, encoding="utf-8").read().splitlines()
        assert rows[0].startswith("L_base ") and " s=" not in rows[0] and rows[1].startswith("L_C ") and rows[1].endswith(" s=1")
        assert rows[2].startswith("Ext_s0.5 ") and rows[2].endswith(" s=0.5")


def test_sigmode_twins_emit():
    assert rc.SIGMODE == "p=100000 c=0 m=1000000 s_glob=1000000 ww=0 busy=0"
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "v.txt")
        n = rc.emit("S", ["E1", "P"], p, "", None, "both")
        rows = open(p, encoding="utf-8").read().splitlines()
        ids = [r.split()[0] for r in rows]
        assert n == 7 + 6 and ids[0] == "S_base"
        twins = [i for i in ids if i.endswith("_sig")]
        assert twins == ["S_base_sig"] + ["E1_s%g_sig" % s for s in (1, 1.5, 2, 3, 4, 6, 8)]     # P — портфельная стадия, двойников нет
        r = next(x for x in rows if x.startswith("E1_s3_sig "))
        assert r.endswith(" s=3") and rc.SIGMODE in r
        assert next(x for x in rows if x.startswith("E1_s3 ")).count("p=100000") == 0
        assert rc.emit("L", ["T"], p, "s=4", None, "only") == 6
        rows = open(p, encoding="utf-8").read().splitlines()
        assert len(rows) == 7 and all(x.split()[0].endswith("_sig") for x in rows)
        assert " s=4 " in rows[1] and rc.SIGMODE in rows[1]


def test_legacy_cli_counts():
    assert len(rc.cells_simple()) == 26
    assert sum(n for _, n, _ in rc.STAGES) == 60


# ---- Холм ------------------------------------------------------------------------------------------------------------------------

def test_holm_table():
    files = [dict(stage="E2", computed=["E2_a", "E2_b", "E2_c"], skipped_equal_to_base=["E2_c"]),
             dict(stage="E1", computed=["E1_s3", "E1_s4", "L_base", "E1_s3_sig", "L_base_sig"]),
             dict(stage="Els+NS+NT", computed=["Els_ls1", "E1_s3"])]
    lines, m = rr.holm_table("long", files)
    # E1: E1_s3, E1_s4 (контроли и двойник E1_s3_sig не считаются) = 2; Els+NS+NT: Els_ls1 (E1_s3 уже посчитана) = 1; E2: E2_a, E2_b = 2 (E2_c равна базе)
    assert m == 694 + 2 + 1 + 2, (m, lines)
    assert rr.holm_table("short", [])[1] == 687


# ---- синтетические прогоны ---------------------------------------------------------------------------------------------------------

B_NET, B_DAY = "987654.321", "123456.789"


def write_cell(d, trades, garbage=False):
    """trades: [(sym, entry_ms, entry_day, entry_min, net)]. Сутки/монеты A — суммы по сделкам; значения B — сентинелы (или мусор)."""
    os.makedirs(d, exist_ok=True)
    daily, coin = np.zeros(30), {}
    for sym, ems, day, emin, net in trades:
        if day < 30:
            daily[day] += net
            coin.setdefault(sym, np.zeros(30))[day] += net
    bval = "NaNBOOM" if garbage else None
    with open(os.path.join(d, "long-trades.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sym", "level", "sig_min", "entry_min", "exit_min", "entry_px_e8", "kind", "takes", "gross", "fees", "funding", "net", "entry_day", "sig_ms", "entry_ms", "exit_ms"])
        for sym, ems, day, emin, net in trades:
            w.writerow([sym, 0, emin, emin, emin, 1, "stop", 0, 0, 0, 0, ("%.6f" % net) if day < 30 else (bval or B_NET), day, ems, ems, ems])
    with open(os.path.join(d, "long-daily.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "pnl"])
        for i in range(60):
            w.writerow([i, ("%.6f" % daily[i]) if i < 30 else (bval or B_DAY)])
    with open(os.path.join(d, "long-coinday.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sym", "day", "pnl"])
        for sym, arr in coin.items():
            for i in range(30):
                if arr[i] != 0:
                    w.writerow([sym, i, "%.6f" % arr[i]])
            w.writerow([sym, 31, bval or B_DAY])


def gen_trades(seed, n_a=120, n_b=110, shift=0.0, noise=0.0, drop=0, base_trades=None):
    """Набор сделок: сигналы фиксированы seed; net = базовый + shift + шум. drop — число сигналов, пропущенных (портфельная рука)."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n_a + n_b):
        day = (i % 30) if i < n_a else 30 + (i % 30)
        out.append(("S%d" % (i % 25), 1_000_000 + i * 977, day, 14 * 1440 + day * 1440 + (i * 7) % 1400, float(rng.normal(0, 5))))
    out2 = []
    rng2 = np.random.default_rng(seed + 1000)
    for j, (sym, ems, day, emin, net) in enumerate(out):
        if j < drop:
            continue
        out2.append((sym, ems, day, emin, net + shift + (float(rng2.normal(0, noise)) if noise else 0.0)))
    return out2


def synth_run(td, garbage=False, port_shifts=None, sig_shifts=None):
    """L_base, L_base_sig и клетки E1_s2/3/4/6 с двойниками. Сигнальные пары — на одинаковых сигналах; портфельные — со сдвигом и пропуском сигналов."""
    run = os.path.join(td, "run")
    port_shifts = port_shifts or {"E1_s2": -1.0, "E1_s3": 0.6, "E1_s4": 1.0, "E1_s6": 0.8}
    sig_shifts = sig_shifts or {"E1_s2": -0.8, "E1_s3": 0.5, "E1_s4": 0.9, "E1_s6": 0.7}
    write_cell(os.path.join(run, "L_base"), gen_trades(1, drop=0), garbage)
    write_cell(os.path.join(run, "L_base_sig"), gen_trades(2), garbage)
    for i, cid in enumerate(sorted(port_shifts)):
        write_cell(os.path.join(run, cid), gen_trades(1, shift=port_shifts[cid], noise=0.4), garbage)
        write_cell(os.path.join(run, cid + "_sig"), gen_trades(2, shift=sig_shifts[cid], noise=0.4), garbage)
    return run


def run_main(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rr.main(argv)
    return buf.getvalue()


def test_stage_loader_never_parses_b():
    with tempfile.TemporaryDirectory() as td:
        write_cell(os.path.join(td, "c"), gen_trades(5, 90, 80), garbage=True)      # B-значения (net, pnl, coinday) — не числа: разбор упал бы
        c = rr.load_stage_cell(os.path.join(td, "c"), "long")
        assert c.n_a == 90 and c.n_b == 80 and len(c.daily_a) == 30 and np.isfinite(c.daily_a).all()
        assert len(c.keys) == 170 and len(c.trades_a) == 90 and c.coin_a
        assert {f.name for f in rr.dataclasses.fields(c)} == {"cid", "n_a", "n_b", "sum_a", "mean_a", "pos_a", "daily_a", "keys", "trades_a", "coin_a"}   # B-денег нет
        try:
            rr.load_full(os.path.join(td, "c"), "long")
        except ValueError:
            pass
        else:
            raise AssertionError("load_full должен был упасть на мусоре B")


def test_stage_output_has_no_b_pnl_and_uses_signal_mode():
    with tempfile.TemporaryDirectory() as td:
        run = synth_run(td)
        orig = rr.load_full

        def boom(*a, **k):
            raise AssertionError("режим stage вызвал полную загрузку (B)")
        rr.load_full = boom
        try:
            out = run_main(["stage", "--side", "long", "--run", run, "--stage", "E1", "--btc", ""])
        finally:
            rr.load_full = orig
        js = open(os.path.join(run, "accepted-E1-long.json"), encoding="utf-8").read()
        for s in ("987654", "123456", "987654.321", "123456.789"):
            assert s not in out and s not in js, s
        assert "сделок A 120, B 110 (число)" in out and "Δ_A[сигнал]" in out and "портфельная Δ_A" in out and "кластер по монетам" in out
        acc = json.loads(js)
        assert acc["n_computed"] == 4 and set(acc["cells"]) == {"E1_s2", "E1_s3", "E1_s4", "E1_s6"}      # двойники — не отдельные клетки
        assert all(c["mode"] == "sig" for c in acc["cells"].values())
        # критерий — сигнальная Δ_A: у E1_s4 сдвиг сигнальной руки +0.9 на сделку × 120 сделок / 30 сут. = +3.6 $/сут. (портфельная +1.0 → +4.0)
        c4 = acc["cells"]["E1_s4"]
        assert abs(c4["d_a"] - 3.6) < 0.3 and abs(c4["port_d"] - 4.0) < 0.3 and abs(c4["d_a"] - c4["port_d"]) > 0.15, c4
        assert c4["cluster"] and abs(c4["cluster"]["mean"] - c4["d_a"]) < 1e-4
        assert acc["accepted"] and acc["accepted"][0]["mode"] == "sig" and acc["accepted"][0]["port_d"] is not None
        for c in acc["cells"].values():
            assert set(c) == {"mode", "n_a", "n_b", "sum_a", "mean_a", "pos_a", "d_a", "se", "lo", "hi", "port_d", "port_se", "port_lo", "port_hi", "cluster", "cuped", "eligible"}
        assert acc["cumulative_keys"] == acc["keys"]
        assert rc.load_accepted(os.path.join(run, "accepted-E1-long.json"))["cumulative_keys"] == acc["cumulative_keys"]


def test_stage_signal_pairing_assert():
    with tempfile.TemporaryDirectory() as td:
        run = synth_run(td)
        # у двойника E1_s3 другой набор сигналов (пропущены 3) → остановка
        write_cell(os.path.join(run, "E1_s3_sig"), gen_trades(2, shift=0.5, drop=3))
        try:
            run_main(["stage", "--side", "long", "--run", run, "--stage", "E1", "--btc", ""])
        except SystemExit as e:
            assert "одинаковых сигналах" in str(e) and "E1_s3" in str(e), e
        else:
            raise AssertionError("ожидалась остановка: наборы сигналов различаются")
        # нет двойника → остановка
        import shutil
        run2 = synth_run(os.path.join(td, "x"))
        shutil.rmtree(os.path.join(run2, "E1_s6_sig"))
        try:
            run_main(["stage", "--side", "long", "--run", run2, "--stage", "E1", "--btc", ""])
        except SystemExit as e:
            assert "двойника" in str(e), e
        else:
            raise AssertionError("ожидалась остановка: нет двойника")


def test_stage_veto_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        # сигнально выбирается E1_s6 (край посчитанных значений, +0.7 на сделку); портфельно у s6 −4 на сделку при шуме 0.4 → портфельная Δ_A ≈ −16 $/сут. ≪ −SE → вето
        run = synth_run(td, port_shifts={"E1_s2": -1.0, "E1_s3": 0.6, "E1_s4": 1.0, "E1_s6": -4.0})
        out = run_main(["stage", "--side", "long", "--run", run, "--stage", "E1", "--btc", ""])
        acc = json.load(open(os.path.join(run, "accepted-E1-long.json"), encoding="utf-8"))
        d = next(x for x in acc["decisions"] if x["group"] == "s")
        assert d["chosen"] == "E1_s6" and d["vetoed"] and not d["selected"] and acc["accepted"] == [] and acc["keys"] == "" and acc["extend"] == [], d
        assert "СРАБОТАЛО" in out and "портфельная Δ_A выбранного" in out
        # портфельный режим для стадии P: критерий — портфельная Δ_A, двойники не нужны
        run2 = os.path.join(td, "run2")
        write_cell(os.path.join(run2, "L_base"), gen_trades(1))
        write_cell(os.path.join(run2, "P_n03"), gen_trades(1, shift=0.6, noise=0.4))
        write_cell(os.path.join(run2, "P_n06"), gen_trades(1, shift=-0.5, noise=0.4))
        out = run_main(["stage", "--side", "long", "--run", run2, "--stage", "P", "--btc", ""])
        acc = json.load(open(os.path.join(run2, "accepted-P-long.json"), encoding="utf-8"))
        assert acc["keys"] == "natr_min=0.3" and all(c["mode"] == "port" for c in acc["cells"].values()) and "Δ_A[портфель]" in out


def test_stage_garbage_b_does_not_crash_stage():
    with tempfile.TemporaryDirectory() as td:
        run = synth_run(td, garbage=True)
        out = run_main(["stage", "--side", "long", "--run", run, "--stage", "E1", "--btc", ""])
        assert "NaNBOOM" not in out and "принятые ключи стадии" in out


def test_stage_keys_from_variants_file_and_chain():
    with tempfile.TemporaryDirectory() as td:
        run = synth_run(td)
        vf = os.path.join(td, "v.txt")
        rc.emit("L", ["E1"], vf, None, None, "both")
        run_main(["stage", "--side", "long", "--run", run, "--stage", "E1", "--variants", vf, "--base-id", "L_base", "--btc", ""])
        acc = json.load(open(os.path.join(run, "accepted-E1-long.json"), encoding="utf-8"))
        assert acc["accepted"]
        for a in acc["accepted"]:
            assert a["keys"] == "s=%s" % a["cell"][len("E1_s"):]


# ---- сводные функции: кластер, CUPED, подтверждающий тест ----------------------------------------------------------------------------

def test_coin_cluster_deterministic():
    rng = np.random.default_rng(3)
    mk_cell = lambda a: rr.StageCell("x", 0, 0, 0.0, 0.0, 0.0, np.zeros(30), frozenset(), {}, a)    # noqa: E731
    base = mk_cell({"a": rng.normal(0, 1, 30), "b": rng.normal(0, 1, 30)})
    cell = mk_cell({"a": base.coin_a["a"] + 1.0, "b": base.coin_a["b"] + 2.0, "c": rng.normal(0, 1, 30)})
    r1, r2 = rr.coin_cluster(cell, base, 400), rr.coin_cluster(cell, base, 400)
    assert r1 == r2 and r1["n_coins"] == 3 and r1["lo"] < r1["mean"] < r1["hi"]
    assert rr.coin_cluster(cell, mk_cell(None)) is None


def test_cuped_variance_share():
    rng = np.random.default_rng(4)
    n = 600
    close = np.cumprod(1 + rng.normal(0, 0.001, 3000)) * 1e8
    mins = rng.integers(0, 2900, n)
    x = np.array([rr.btc_ret(close, m) for m in mins])
    days = np.arange(n) % 30
    items_corr = {i: (int(days[i]), int(mins[i]), float(2.0 * x[i] * 1e3 + rng.normal(0, 0.5))) for i in range(n)}
    items_ind = {i: (int(days[i]), int(mins[i]), float(rng.normal(0, 0.5))) for i in range(n)}
    fc, fi = rr.cuped_fit(items_corr, close), rr.cuped_fit(items_ind, close)
    raw, adj, share = rr.cuped_apply(items_corr, close, fc, 0, 30)
    assert share > 0.9 and abs(raw.mean() - adj.mean()) < 1e-9       # в A центрирование в выборке: среднее то же, дисперсия снята
    assert rr.detstat.stat(adj)["se"] < rr.detstat.stat(raw)["se"]
    _, _, share_i = rr.cuped_apply(items_ind, close, fi, 0, 30)
    assert share_i < 0.05
    assert rr.cuped_fit({0: (0, 5, 1.0)}, close) is None               # мало сделок
    # union_delta: сделка только у одной руки — Δ = её P&L
    u = rr.union_delta({"k1": (0, 10, 3.0), "k2": (1, 20, 1.0)}, {"k1": (0, 10, 1.0), "k3": (2, 30, 4.0)})
    assert u == {"k1": (0, 10, 2.0), "k2": (1, 20, 1.0), "k3": (2, 30, -4.0)}


def _coin_from_days(day, ncoin, seed):
    rng = np.random.default_rng(seed)
    w = rng.dirichlet(np.ones(ncoin))
    return {"C%d" % i: day * w[i] for i in range(ncoin)}


def test_confirm_verdicts():
    rng = np.random.default_rng(7)
    day = rng.normal(8.0, 6.0, 31)
    v = rr.confirm_verdict(day, _coin_from_days(day, 6, 1))
    st = v["st"]
    assert st["lower"] < st["mean"] < st["upper"] and v["verdict"] == "подтверждено на августе", v
    # среднее > 0 держится только за счёт одних суток → без 2 лучших суток знак −: не подтверждено
    d2 = np.full(31, -1.0)
    d2[3], d2[9] = 60.0, 55.0
    v2 = rr.confirm_verdict(d2, _coin_from_days(d2, 6, 2))
    assert v2["st"]["mean"] > 0 and v2["no_best_days"] < 0 and v2["verdict"] != "подтверждено на августе"
    # за счёт одной монеты → без 2 лучших монет знак −: не подтверждено
    d3 = np.full(31, 2.0)
    coin = {"A": np.full(31, 5.0), "B": np.full(31, 1.0), "C": np.full(31, -3.0), "D": np.full(31, -1.0)}
    v3 = rr.confirm_verdict(d3, coin)
    assert v3["no_best_coins"] == 2.0 - 6.0 and v3["verdict"] != "подтверждено на августе", v3
    # устойчиво отрицательно → опровергнуто (по §10)
    d4 = rng.normal(-8.0, 3.0, 31)
    v4 = rr.confirm_verdict(d4, _coin_from_days(d4, 6, 3))
    assert v4["verdict"] == "опровергнуто", v4
    # около нуля → не ясно
    d5 = rng.normal(0.0, 9.0, 31)
    assert rr.confirm_verdict(d5, _coin_from_days(d5, 6, 4))["verdict"] == "не ясно"
    # односторонняя граница детерминирована
    assert rr.onesided(day) == rr.onesided(day)


def test_confirm_cli_reads_window_and_guard():
    with tempfile.TemporaryDirectory() as td:
        cand = os.path.join(td, "aug", "L_C")
        rng = np.random.default_rng(11)
        trades = [("S%d" % (i % 5), 5_000 + i, i % 31, (i % 31) * 1440, float(rng.normal(1.0, 3.0))) for i in range(200)]   # строки daily 0…30 = 01–31.08 при P42_T0 = 2026-07-18
        os.makedirs(cand)
        daily, coin = np.zeros(60), {}
        with open(os.path.join(cand, "long-trades.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["sym", "entry_min", "net", "entry_day", "entry_ms"])
            for sym, ems, day, emin, net in trades:
                w.writerow([sym, emin, "%.6f" % net, day, ems])
                daily[day] += net
                coin.setdefault(sym, np.zeros(60))[day] += net
        with open(os.path.join(cand, "long-daily.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["day", "pnl"])
            for i in range(60):
                w.writerow([i, "%.6f" % daily[i]])
        with open(os.path.join(cand, "long-coinday.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["sym", "day", "pnl"])
            for sym, arr in coin.items():
                for i in range(60):
                    if arr[i]:
                        w.writerow([sym, i, "%.6f" % arr[i]])
        out = run_main(["confirm", "--side", "long", "--cand", cand, "--days", "0:31", "--t0", "2026-07-18", "--expect-dates", "2026-08-01:2026-08-31"])
        assert "подтверждающий тест m = 1, односторонне 5 %" in out and "вердикт (m = 1, односторонне 5 %)" in out and "31 сут." in out
        out2 = run_main(["confirm", "--side", "long", "--cand", cand, "--days", "0:31", "--t0", "2026-07-18", "--expect-dates", "2026-08-01:2026-08-31"])
        assert "уже прочитана" in out2


TESTS = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    bad = 0
    for name, fn in TESTS:
        try:
            fn()
            print("ok   ", name)
        except Exception as e:  # noqa: BLE001
            bad += 1
            import traceback
            print("FAIL ", name, repr(e))
            traceback.print_exc()
    print("%d тестов, %d провалено" % (len(TESTS), bad))
    sys.exit(1 if bad else 0)
