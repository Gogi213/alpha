"""TK-620: оценка 28 клеток §13.31 (стопы / отмена сценария / стоп за проторговкой; лонг + шорт): парная ΔΣ по суткам против базы своей стороны, Холм m = 28,
ворота G_620 g1–g4, разложение «добавлено / убрано / общие», risk:reward, просадка, серия убытков, стопов в сутки, плато.
  python stops_redesign.py --cells docs/research/freeze/tk620-cells.json --out-root /data/p42-tk620/out --ref-root /data/p42-tk599/out \
      --symbols /data/p42-t566/symbols.txt --funding /data/p42-metrics/funding --report <отчёт.md> [--json <итог.json>] [--nboot N]
Повтор на честной вселенной (§13.31 п. 7): `--repeat <итог 566 .json> --ref-symbols /data/p42-t566/symbols.txt --no-control` — только клетки, прошедшие G_620 на 566;
печатается «повторилась на ЧВ» (ΔΣ > 0 в целом, ≥ 4 из 5 месяцев, Δ_сделка + SE ≥ 0, g4), без Холма; пересечение монет с 566.
Каталоги клеток — `<out-root>/<окно>/<id>/<side>-trades.csv`; контроль — базы побайтно против `<ref-root>/<окно>/<ячейка TK-599>/`. Сутки позже 13.11 не читаются (`retitr_full`)."""
import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_full as rf  # noqa: E402
import short_autopsy_base as sb  # noqa: E402
import short_levers as sl  # noqa: E402
import tk620_rr  # noqa: E402

MON = rf.MONTH_NAMES
CONTROL = {"L_base": ("c53a116125b39", "long"), "S_base": ("c10fbba7be522", "short")}
FILES = ("trades.csv", "daily.csv", "coinday.csv", "entryrej.csv")


def ingest(root, cid, side, coins, rates):
    """Ряд клетки по правилам G6/G3 (как `short_levers.ingest`, но со стороной)."""
    tables, daily = {}, {}
    for w in rf.WINDOWS:
        d = os.path.join(root, w.name, cid)
        tables[w.name] = rf.read_trades_csv(os.path.join(d, side + "-trades.csv"))
        dp = os.path.join(d, side + "-daily.csv")
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


def rows_of(root, cid, side):
    return sb.spliced_rows(lambda w: os.path.join(root, w, cid, side + "-trades.csv"))


def control(out_root, ref_root):
    """Базы `L_base`, `S_base` побайтно против готовых `out/` TK-599; пусто — ворота пройдены."""
    bad = []
    for cid, (vid, side) in CONTROL.items():
        for w in rf.WINDOWS:
            for fn in FILES:
                a, b = os.path.join(out_root, w.name, cid, side + "-" + fn), os.path.join(ref_root, w.name, vid, side + "-" + fn)
                if not os.path.exists(a) or not os.path.exists(b):
                    bad.append("нет файла: %s или %s" % (a, b))
                elif sl.md5(a) != sl.md5(b):
                    bad.append("различие %s/%s/%s-%s против TK-599 %s" % (cid, w.name, side, fn, vid))
    return bad


def extra(rows, ser):
    """Максимальная просадка по ряду суток ($), серия убытков (подряд убыточных сделок по времени входа), стопов (`stop`+`nt`) в сутки, R:R."""
    day, _ = rf.day_sums(ser)
    cum = np.cumsum(day)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:] - cum).max()) if len(cum) else 0.0
    run = best = 0
    for r in rows:
        run = run + 1 if float(r["pnl"]) < 0 else 0
        best = max(best, run)
    rr = tk620_rr.rr_stats(rows)
    return dict(max_dd=dd, loss_streak=best, stops_per_day=rr["stops"]["n"] / rf.NDAYS, rr=rr)


def gates(res, holm_ok):
    return sl.gates(res, holm_ok)


def fmt_cell(c, x, d):
    gs = "".join("✓" if v else "✗" for v in x["g"])
    e = x["extra"]
    rr = e["rr"]
    return "| %s | %d | %+.2f | %+.2f ± %.2f | %+.2f | %.4f | %s | %+.3f (%+.3f ± %.3f) | %+.2f | %s | %s | %+.1f / %+.1f | +%d / %+.1f | −%d / %+.1f | %+.1f | %+.1f | %.2f %% | %.2f | %.0f | %d | %.3f | %+.2f / %+.2f | %.1f %% | %s |" % (
        c, x["n"], x["sum"], x["dsum"], x["se_sum"], x["z"], x["p"], "да" if x["holm"] else "нет", x["per_trade"], x["d_mean"], x["se_mean"], x["per_day"],
        " | ".join(sl.fmt_month(m) for m in x["months"]), gs, x["no_top2_days"], x["no_top2_coins"], d["add_n"], d["add_s"], d["rem_n"], d["rem_s"], d["com_d"], x["pess_dsum"],
        x["per_trade"] / tk620_rr.MARGIN * 100, rr["rr"], e["max_dd"], e["loss_streak"], e["stops_per_day"], rr["stops"]["mean_loss"], rr["stops"]["max_loss"],
        rr["stops"]["share"] * 100, x["plateau"] or ("слабая" if x["weak"] else ""))


def main(a):
    cj = json.load(open(a.cells, encoding="utf-8"))
    cells = cj["cells"]
    base_of = cj["base"]
    ids = [c["id"] for c in cells]
    side_of = {c["id"]: c["side"] for c in cells}
    if a.repeat:
        passed566 = json.load(open(a.repeat, encoding="utf-8"))["passed"]
        ids = [c for c in ids if c in passed566]
    if not a.no_control:
        bad = control(a.out_root, a.ref_root)
        if bad:
            raise rf.GuardError("контроль побайтно: %d расхождений, первое: %s" % (len(bad), bad[0]))
    text = open(a.symbols, encoding="utf-8").read().rstrip("\n")
    coins = [x for x in text.replace("\n", ",").split(",") if x]
    rates = rf.load_funding_rates(a.funding, coins)
    base, base_rows = {}, {}
    for side, bid in base_of.items():
        base[side] = ingest(a.out_root, bid, side, coins, rates)
        base_rows[side] = rows_of(a.out_root, bid, side)
    res = {}
    for cid in ids:
        side = side_of[cid]
        ser = ingest(a.out_root, cid, side, coins, rates)
        rows = rows_of(a.out_root, cid, side)
        res[cid] = sl.evaluate(ser, base[side], len(coins), a.nboot)
        res[cid]["dec"] = sl.decompose(base_rows[side], rows)
        res[cid]["extra"] = extra(rows, ser)
    if a.repeat:
        res = {c: res[c] for c in ids}
        for c in ids:
            x = res[c]
            x["g"] = (True, sum(1 for (d, _, _) in x["months"] if d > 0) >= sl.G2_MONTHS, bool(x["d_mean"] + x["se_mean"] >= 0), bool(x["no_top2_days"] > 0 and x["no_top2_coins"] > 0))
            x["holm"] = True
            x["pass"] = bool(x["dsum"] > 0 and all(x["g"]))   # «повторилась на ЧВ»: ΔΣ > 0 в целом + g2, g3, g4 (без Холма)
            x["weak"] = False
            x["plateau"] = ""
    else:
        rej = rf.holm_reject([res[c]["p"] for c in ids])
        nb = plateau_neighbours(cells)
        for c, ok in zip(ids, rej):
            res[c]["holm"] = bool(ok)
            res[c]["g"] = gates(res[c], ok)
            res[c]["pass"] = all(res[c]["g"])
            res[c]["weak"] = (not res[c]["g"][0]) and res[c]["z"] >= sl.WEAK_Z and all(res[c]["g"][1:])
        for c in ids:
            ns = nb.get(c, [])
            res[c]["plateau"] = "" if not res[c]["pass"] else ("без плато" if not ns else ("плато" if all(res[n]["dsum"] > 0 for n in ns) else "острый пик"))
    head = ["# TK-620: %d клеток §13.31 (стопы / отмена сценария), ΔΣ против базы своей стороны, 01.07–13.11, найдено на этих данных\n" % len(ids)]
    for side, bid in base_of.items():
        bs = float(base[side].val.sum())
        br = tk620_rr.rr_stats(base_rows[side])
        head.append("База %s (%s): Σ %.2f $, %d сделок, %+.3f $/сделку, %.2f $/сут.; R:R %.2f, стоп-выходы %d (%.1f %%), средний убыток стопа %+.2f $ (%.2f %% маржи), максимальный %+.2f $ (%.2f %% маржи)." % (
            bid, side, bs, len(base[side].val), bs / len(base[side].val), bs / rf.NDAYS, br["rr"], br["stops"]["n"], br["stops"]["share"] * 100,
            br["stops"]["mean_loss"], br["stops"]["mean_loss_pct"], br["stops"]["max_loss"], br["stops"]["max_loss_pct"]))
    head.append("\n%s, блоки 4 сут., %d повторов. Контроль баз побайтно против TK-599: %s.\n" % (
        "Повтор на ЧВ (Холм не пересчитывается)" if a.repeat else "Холм m = %d (односторонне, 5 %%)" % len(ids), a.nboot, "не применялся" if a.no_control else "пройден"))
    out = head + ["| клетка | сделок | Σ $ | ΔΣ ± SE | z | p | Холм | $/сделку (Δ ± SE) | $/сут. | " + " | ".join("Δ " + m for m in MON) +
                  " | g1 g2 g3 g4 | без топ-2 суток / монет | + n / Σ | − n / Σ | общие ΔΣ | ΔΣ при фандинге B | $/сделку в % маржи | R:R | макс. просадка $ | серия убытков | стопов/сут. | стоп: средний / макс. убыток $ | стоп-выходов | плато |",
                  "|---|---:|---:|---|---:|---:|---|---|---:|" + "---|" * len(MON) + "---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in ids:
        out.append(fmt_cell(c, res[c], res[c]["dec"]))
    passed = [c for c in ids if res[c]["pass"]]
    weak = [c for c in ids if res[c]["weak"]]
    out.append("\n%s: %s. «Слабые» (z ≥ 1,64, g2–g4, Холм не отвергает): %s.\n" % ("Повторились на ЧВ" if a.repeat else "Прошли G_620", ", ".join(passed) or "никто", ", ".join(weak) or "никто"))
    if a.repeat and a.ref_symbols:
        s566 = set(x for x in open(a.ref_symbols, encoding="utf-8").read().replace("\n", ",").split(",") if x)
        tot = inn = 0
        for c in ids:
            for r in rows_of(a.out_root, c, side_of[c]):
                tot += 1
                inn += r["sym"] in s566
        out.append("Пересечение монет ЧВ с 566: сделок клеток на монетах из 566 — %d из %d (%.1f %%).\n" % (inn, tot, 100.0 * inn / tot if tot else float("nan")))
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    open(a.report, "w", encoding="utf-8").write("\n".join(out) + "\n")
    if a.json:
        json.dump({"cells": res, "passed": passed, "weak": weak}, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print("cells", len(ids), "passed", passed, "weak", weak)


def plateau_neighbours(cells):
    """Соседи по оси (§13.31 п. 5): поле `neighbours` клетки в `tk620-cells.json`."""
    return {c["id"]: c.get("neighbours", []) for c in cells}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--ref-root")
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--funding", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--json")
    ap.add_argument("--nboot", type=int, default=rf.NBOOT)
    ap.add_argument("--no-control", action="store_true")
    ap.add_argument("--repeat")
    ap.add_argument("--ref-symbols")
    args = ap.parse_args()
    if not args.no_control and not args.ref_root:
        ap.error("--ref-root обязателен без --no-control")
    main(args)
