"""TK-618 шаг 4: оценка 34 клеток шорта по §13.30 (парная ΔΣ по суткам против базы, Холм m = 34, ворота G_618 g1–g4, разложение «добавлено / убрано / общие»).
  python short_levers.py --cells tk618-cells.json --out-root /data/p42-tk618/out --ref-root /data/p42-tk599/out --symbols /data/p42-t566/symbols.txt
                         --funding /data/p42-metrics/funding --report <отчёт.md> [--json <итог.json>] [--nboot N]
Каталоги клеток — `<out-root>/<окно>/<id клетки>/short-trades.csv` (id = первое слово строки variants-файла); контроль: `S_base`, `S_rf_0.3` побайтно против
`<ref-root>/<окно>/<vid TK-599>/` (`control` — отдельная команда). Движок не запускается; сутки позже 13.11 не читаются (ворота `retitr_full`)."""
import argparse
import hashlib
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_full as rf  # noqa: E402
import short_autopsy_base as sb  # noqa: E402

MON = rf.MONTH_NAMES
SIDE = "short"
FLOOR_MEAN = 0.30             # пол $/сделку — только справка (§13.30 п. 5: g3 = Δ_сделка + SE ≥ 0)
G2_MONTHS = 4                 # (g2) месяцев из 5 с ΔΣ > 0
WEAK_Z = 1.64                 # «слабые»: z_Σ ≥ 1,64 при g2–g4
CONTROL = {"S_base": "c10fbba7be522", "S_rf_0.3": "c775084b92bbb"}
CONTROL_FILES = ("short-trades.csv", "short-daily.csv", "short-coinday.csv", "short-entryrej.csv")


def norm_sf(z):
    """1 − Φ(z)."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def ingest(root, cid, coins, rates):
    """Ряд клетки по правилам G6/G3 (`retitr_full.splice`, `check_forced`, `check_data_end`, швы окон) и сырые таблицы окон."""
    tables, daily = {}, {}
    for w in rf.WINDOWS:
        d = os.path.join(root, w.name, cid)
        tables[w.name] = rf.read_trades_csv(os.path.join(d, SIDE + "-trades.csv"))
        dp = os.path.join(d, SIDE + "-daily.csv")
        daily[w.name] = rf.read_daily_csv(dp) if os.path.exists(dp) else None
    rf.check_windows(rf.WINDOWS)
    ser = rf.splice(tables, coins, rf.WINDOWS, daily, rates)
    rf.check_forced(tables)
    rf.check_data_end(tables)
    wins = {w.name: w for w in rf.WINDOWS}
    for a, b, lo, hi in rf.G3_RANGES:
        diff = rf.overlap_diff(tables[a], wins[a], tables[b], wins[b], lo, hi)
        if diff:
            raise rf.GuardError("G3 %s: швы %s–%s: %d различий, первое: %s" % (cid, a, b, len(diff), diff[0]))
    return ser


def rows_of(root, cid):
    return sb.spliced_rows(lambda w: os.path.join(root, w, cid, SIDE + "-trades.csv"))


def decompose(base_rows, cell_rows):
    """По ключу (sym, sig_ms): добавлено (в клетке нет у базы), убрано (у базы нет в клетке), общие (ΔΣ по общим ключам)."""
    def agg(rows):
        out = {}
        for r in rows:
            k = (r["sym"], r["sig_ms"])
            n, s = out.get(k, (0, 0.0))
            out[k] = (n + 1, s + float(r["pnl"]))
        return out
    b, c = agg(base_rows), agg(cell_rows)
    add = [v for k, v in c.items() if k not in b]
    rem = [v for k, v in b.items() if k not in c]
    com = [k for k in c if k in b]
    return dict(add_n=sum(v[0] for v in add), add_s=sum(v[1] for v in add), rem_n=sum(v[0] for v in rem), rem_s=sum(v[1] for v in rem),
                com_keys=len(com), com_d=sum(c[k][1] - b[k][1] for k in com))


def top2_removed(d_units):
    """ΔΣ после удаления 2 единиц (суток или монет) с наибольшим вкладом в ΔΣ."""
    v = np.sort(np.asarray(d_units, dtype=float))[::-1]
    return float(d_units.sum() - v[:2].sum())


def evaluate(c, r, ncoins, nboot):
    Sc, Nc = rf.day_sums(c)
    Sr, Nr = rf.day_sums(r)
    st = rf.delta_stat(Sc, Nc, Sr, Nr, nboot)
    z_sum = st.dsum / st.se_sum if st.se_sum > 0 else (math.inf if st.dsum > 0 else (-math.inf if st.dsum < 0 else 0.0))
    p = norm_sf(z_sum)
    mdsum = []
    for k in range(len(MON)):
        d = np.nonzero(rf.MONTH_OF_DAY == k)[0]
        mdsum.append((float(Sc[d].sum() - Sr[d].sum()), int(Nc[d].sum()), int(Nr[d].sum())))
    dday = Sc - Sr
    dcoin_c = np.bincount(c.sym, weights=c.val, minlength=ncoins)
    dcoin_r = np.bincount(r.sym, weights=r.val, minlength=ncoins)
    dcoin = dcoin_c - dcoin_r
    pc = rf.sens_arrays(c, r, None)
    pess = float(pc[0].sum() - pc[2].sum())
    return dict(n=int(Nc.sum()), sum=float(Sc.sum()), dsum=st.dsum, se_sum=st.se_sum, z=float(z_sum), p=float(p), lower_mean=st.lower, d_mean=st.d, se_mean=st.se,
                per_trade=float(Sc.sum() / Nc.sum()) if Nc.sum() > 0 else float("nan"), per_day=float(Sc.sum() / rf.NDAYS),
                months=mdsum, no_top2_days=top2_removed(dday), no_top2_coins=top2_removed(dcoin), top2_days_share=float((np.sort(dday)[::-1][:2].sum()) / st.dsum) if st.dsum > 0 else float("nan"),
                pess_dsum=pess)


def gates(res, holm_ok):
    g1 = bool(holm_ok and res["dsum"] > 0)
    g2 = sum(1 for (d, _, _) in res["months"] if d > 0) >= G2_MONTHS
    g3 = bool(res["d_mean"] + res["se_mean"] >= 0)
    g4 = bool(res["no_top2_days"] > 0 and res["no_top2_coins"] > 0)
    return g1, g2, g3, g4


def plateau_neighbours(cells):
    """Соседи §13.30 п. 5: t1h, reentry, цепочка K, rf (B_K1–C_rf_0.4–C_rf_0.5), трейл; остальные — «без плато»."""
    nb = {"A_t1h_0.25": ["A_t1h_0.1"], "A_t1h_0.1": ["A_t1h_0.25"], "A_re1": ["A_re2"], "A_re2": ["A_re1"],
          "C_rf_0.4": ["B_K1", "C_rf_0.5"], "C_rf_0.5": ["C_rf_0.4"],
          "C_tr_r1.5": ["C_tr_a3_r1.5"], "C_tr_a3_r1.5": ["C_tr_r1.5"]}
    for j in range(1, 7):
        nb["B_K%d" % j] = ["B_K%d" % k for k in (j - 1, j + 1) if 1 <= k <= 6]
    nb["B_K1"] = ["B_K2", "C_rf_0.4"]
    return nb


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def control(out_root, ref_root):
    """Побайтовая сверка `S_base`, `S_rf_0.3` с готовыми `out/` TK-599 (§13.30 п. 3); возвращает список расхождений (пусто — ворота пройдены)."""
    bad = []
    for cid, vid in CONTROL.items():
        for w in rf.WINDOWS:
            for fn in CONTROL_FILES:
                a, b = os.path.join(out_root, w.name, cid, fn), os.path.join(ref_root, w.name, vid, fn)
                if not os.path.exists(a) or not os.path.exists(b):
                    bad.append("нет файла: %s или %s" % (a, b))
                elif md5(a) != md5(b):
                    bad.append("различие %s/%s/%s против TK-599 %s" % (cid, w.name, fn, vid))
    return bad


def fmt_month(m):
    return "%+.1f (%d/%d)" % m


def main(a):
    cj = json.load(open(a.cells, encoding="utf-8"))
    cells = cj["cells"]
    ids = [c["id"] for c in cells]
    bad = control(a.out_root, a.ref_root)
    if bad:
        raise rf.GuardError("контроль побайтно: %d расхождений, первое: %s" % (len(bad), bad[0]))
    text = open(a.symbols, encoding="utf-8").read().rstrip("\n")
    coins = [x for x in text.replace("\n", ",").split(",") if x]
    rates = rf.load_funding_rates(a.funding, coins)
    base = ingest(a.out_root, "S_base", coins, rates)
    base_rows = rows_of(a.out_root, "S_base")
    res = {}
    for cid in ids:
        ser = ingest(a.out_root, cid, coins, rates)
        res[cid] = evaluate(ser, base, len(coins), a.nboot)
        res[cid]["dec"] = decompose(base_rows, rows_of(a.out_root, cid))
    rej = rf.holm_reject([res[c]["p"] for c in ids])
    nb = plateau_neighbours(cells)
    for c, ok in zip(ids, rej):
        res[c]["holm"] = bool(ok)
        res[c]["g"] = gates(res[c], ok)
        res[c]["pass"] = all(res[c]["g"])
        res[c]["weak"] = (not res[c]["g"][0]) and res[c]["z"] >= WEAK_Z and all(res[c]["g"][1:])
    for c in ids:
        if not res[c]["pass"]:
            res[c]["plateau"] = ""
            continue
        ns = nb.get(c)
        res[c]["plateau"] = "без плато" if not ns else ("плато" if all(res[n]["dsum"] > 0 for n in ns) else "острый пик")
    bS = float(base.val.sum())
    out = ["# TK-618: 34 клетки шорта, ΔΣ против `S_base` (П-42 §13.30), 01.07–13.11, найдено на этих данных\n",
           "База: Σ %.2f $, %d сделок, %+.3f $/сделку, %.2f $/сут. Холм m = 34 (односторонне, 5 %%), блоки 4 сут., %d повторов. Контроль `S_base`, `S_rf_0.3` побайтно против TK-599: пройден.\n"
           % (bS, len(base.val), bS / len(base.val), bS / rf.NDAYS, a.nboot),
           "| клетка | сделок | Σ $ | ΔΣ ± SE | z | p | Холм | $/сделку (Δ ± SE) | пол 0,30 | $/сут. | " + " | ".join("Δ " + m for m in MON) + " | g1 g2 g3 g4 | без топ-2 суток / монет | + n / Σ | − n / Σ | общие ΔΣ | ΔΣ при фандинге B | плато |",
           "|---|---:|---:|---|---:|---:|---|---|---|---:|" + "---|" * len(MON) + "---|---|---|---|---|---|---|"]
    for c in ids:
        x, d = res[c], res[c]["dec"]
        gs = "".join("✓" if v else "✗" for v in x["g"])
        out.append("| %s | %d | %+.2f | %+.2f ± %.2f | %+.2f | %.4f | %s | %+.3f (%+.3f ± %.3f) | %s | %+.2f | %s | %s | %+.1f / %+.1f | +%d / %+.1f | −%d / %+.1f | %+.1f | %+.1f | %s |" % (
            c, x["n"], x["sum"], x["dsum"], x["se_sum"], x["z"], x["p"], "да" if x["holm"] else "нет", x["per_trade"], x["d_mean"], x["se_mean"], "да" if x["per_trade"] >= FLOOR_MEAN else "нет", x["per_day"],
            " | ".join(fmt_month(m) for m in x["months"]), gs, x["no_top2_days"], x["no_top2_coins"], d["add_n"], d["add_s"], d["rem_n"], d["rem_s"], d["com_d"],
            x["pess_dsum"], x["plateau"] or ("слабая" if x["weak"] else "")))
    passed = [c for c in ids if res[c]["pass"]]
    weak = [c for c in ids if res[c]["weak"]]
    out.append("\nПрошли G_618: %s. «Слабые» (z ≥ 1,64, g2–g4, Холм не отвергает): %s.\n" % (", ".join(passed) or "никто", ", ".join(weak) or "никто"))
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    open(a.report, "w", encoding="utf-8").write("\n".join(out) + "\n")
    if a.json:
        json.dump({"base_sum": bS, "base_n": int(len(base.val)), "cells": res, "passed": passed, "weak": weak}, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("cells", len(ids), "passed", passed, "weak", weak)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--ref-root", required=True)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--funding", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--json")
    ap.add_argument("--nboot", type=int, default=rf.NBOOT)
    main(ap.parse_args())
