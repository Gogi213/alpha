#!/usr/bin/env python3
"""TK-493 S1 (Steam Deck, только stdlib): параметры клеток D2 по РАЗНОСТНОМУ ряду фандинга ФОРМИРОВАНИЯ (П-37 r3.1 §5, §6.3, Н21, Н36).
Платежи только с time_ms < 15.10 00:00 UTC: вход - ~/xdl9/funding_form/<биржа> (sel_deck_prep.py fundcut), торговое окно не читается.

  python3 sel_d2.py all   - для каждой клетки D2 (cellpairs/D2_*.csv, пары Y/X с общим активом):
    1. ряд d = r_Y - r_X на метках Y (d2_diff_funding.py; --funding-x пуст): ~/xdl9/sel/d2form/<клетка>__<exY>__<exX>/shardN/;
       пары без файла ставок исключаются (причина в d2_excluded.csv); доля пар с неравными сетками выплат - в d2_params.csv;
    2. k - из {1, 3, 6}: минимум средней квадратичной ошибки прогноза следующего значения d трейлинг-средним k прошлых значений (пул по парам клетки,
       только пары с >= 7 значениями d); hold_payments - медианная длина эпизодов постоянного знака d в метках Y (пул эпизодов клетки), не меньше 1;
    3. max_hold = 0 (выключен); window = min_obs = refresh = 60 (ядром при правиле funding не используются; НАЗНАЧЕНО без Судьи, подтвердить);
    4. прогон ядра 4ef45cc8 по формированию (панели ~/xdl9/form, окно 01.10-14.10), сделки всех заданий -> длительности позиций (только времена,
       без P&L): медиана, квартили, доля сделок короче одного интервала выплаты Y, число позиций, не закрытых к концу окна.
Выходы: ~/xdl9/sel/d2_params.csv, d2_excluded.csv, d2_durations.csv (копируются на ПК в docs/findings/xdl9-selection-s1-2026-11-06/).
"""
import csv, glob, gzip, math, os, statistics, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import d2_diff_funding as DD  # noqa: E402

HOME = os.path.expanduser("~/xdl9")
SEL = os.path.join(HOME, "sel")
KS = (1, 3, 6)
MIN_VALUES = max(KS) + 1
D_FORM0, D_TRADE0 = 2465, 2479
BIN = os.path.join(HOME, "bin", "statarb-xdl9-4ef45cc8")
D2_WINDOW = 60


def read_d(path):
    """-> (d: list[float], interval_h: list[float]) по меткам Y."""
    d, itv = [], []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        next(rd)
        for r in rd:
            d.append(float(r[1]))
            try:
                itv.append(float(r[2]))
            except (ValueError, IndexError):
                pass
    return d, itv


def mse_k(r, k):
    if len(r) <= k:
        return 0.0, 0
    s = n = 0
    run = sum(r[:k])
    for j in range(k, len(r)):
        e = r[j] - run / k
        s += e * e
        n += 1
        run += r[j] - r[j - k]
    return s, n


def episodes(d):
    sg = [1 if x > 0 else -1 for x in d if x != 0]
    out, run = [], 1
    for i in range(1, len(sg)):
        if sg[i] == sg[i - 1]:
            run += 1
        else:
            out.append(run)
            run = 1
    if sg:
        out.append(run)
    return out


def cell_groups():
    """-> {(cid, ex_y, ex_x): [(y, x)]} по cellpairs/D2_*.csv."""
    g = {}
    for f in sorted(glob.glob(os.path.join(SEL, "cellpairs", "D2_*.csv"))):
        cid = "|".join(os.path.basename(f)[:-4].split("_", 2))      # D2_<биржи>_<полоса>: полоса может содержать "_" (eq_us)
        with open(f, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                g.setdefault((cid, r["ex_y"], r["ex_x"]), []).append((r["y"], r["x"]))
    return g


def stem(cid, exy, exx):
    return "%s__%s__%s" % (cid.replace("|", "_").replace("~", "-"), exy, exx)


def run_all():
    os.makedirs(os.path.join(SEL, "d2form"), exist_ok=True)
    groups = cell_groups()
    per_cell = {}
    excl = []
    for (cid, exy, exx), prs in sorted(groups.items()):
        out = os.path.join(SEL, "d2form", stem(cid, exy, exx))
        pairs_csv = out + ".pairs.csv"
        with open(pairs_csv, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("y,x\n")
            for y, x in prs:
                fh.write("%s,%s\n" % (y, x))
        man, rc = DD.run(DD.read_pairs(pairs_csv), os.path.join(HOME, "funding_form", exy), os.path.join(HOME, "funding_form", exx), out, D_FORM0, D_TRADE0)
        if rc:
            raise SystemExit("d2_diff_funding: масса не сошлась в %s" % out)
        st = per_cell.setdefault(cid, dict(pairs=0, used=0, unequal=0, sse={k: [0.0, 0] for k in KS}, eps=[], itv=[], groups=[]))
        for m in man:
            st["pairs"] += 1
            if m["status"] != "ok":
                excl.append([cid, exy, m["y"], exx, m["x"], m["status"]])
                continue
            d, itv = read_d(os.path.join(out, "shard%s" % m["shard"], m["y"] + ".csv.gz"))
            if len(d) < MIN_VALUES:
                excl.append([cid, exy, m["y"], exx, m["x"], "значений d на формировании меньше %d" % MIN_VALUES])
                continue
            st["used"] += 1
            st["unequal"] += int(m["grid_equal"] != 1 and m["grid_equal"] != "1")
            st["eps"].extend(episodes(d))
            if itv:
                st["itv"].append(statistics.median(itv))
            for k in KS:
                s, n = mse_k(d, k)
                st["sse"][k][0] += s
                st["sse"][k][1] += n
        st["groups"].append((exy, exx, out, sorted(glob.glob(os.path.join(out, "shard*.jobs.csv")))))
    rows = []
    for cid, st in sorted(per_cell.items()):
        if not st["used"]:
            rows.append([cid, st["pairs"], 0, "", "", "", "", "", "нет пар с фандингом обеих ног"])
            continue
        mse = {k: st["sse"][k][0] / max(1, st["sse"][k][1]) for k in KS}
        kbest = min(KS, key=lambda k: (mse[k], k))
        hold = max(1, int(round(statistics.median(st["eps"])))) if st["eps"] else 1
        ih = statistics.median(st["itv"]) if st["itv"] else float("nan")
        rows.append([cid, st["pairs"], st["used"], kbest, hold, "%g" % ih, "%.4f" % (st["unequal"] / st["used"]),
                     "|".join("%.6g" % mse[k] for k in KS), "параметры измерены"])
        st["k"], st["hold"], st["ih"] = kbest, hold, ih
    with open(os.path.join(SEL, "d2_params.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cell", "pairs", "pairs_with_d", "k", "hold_payments", "interval_h_y_median", "unequal_grid_share", "mse_k1|k3|k6", "status"])
        w.writerows(rows)
    with open(os.path.join(SEL, "d2_excluded.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cell", "ex_y", "y", "ex_x", "x", "reason"])
        w.writerows(excl)
    # прогон ядра по формированию: длительности позиций
    dur = []
    os.makedirs(os.path.join(SEL, "d2form_out"), exist_ok=True)
    for cid, st in sorted(per_cell.items()):
        if "k" not in st:
            continue
        trades_all, open_end, done = [], 0, 0
        for exy, exx, out, shards in st["groups"]:
            for jf in shards:
                sh = os.path.basename(jf)[:-9]               # shardN
                n = sum(1 for _ in open(jf, encoding="utf-8")) - 1
                tag = os.path.basename(out) + "__" + sh
                cellrow = os.path.join(SEL, "d2form_out", tag + ".cells.csv")
                with open(cellrow, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write("cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh\n")
                    fh.write("%s|a0,%d,%d,,1.00,0,inf,0,%d\n" % (cid, D2_WINDOW, D2_WINDOW, D2_WINDOW))
                tr = os.path.join(SEL, "d2form_out", tag + ".trades.csv")
                co = os.path.join(SEL, "d2form_out", tag + ".cells_out.csv")
                cmd = ["nice", "-n", "19", BIN, "statarb", "run", "--kind", "funding-pp", "--panels", os.path.join(HOME, "form", exy),
                       "--panels-b", os.path.join(HOME, "form", exx), "--funding-y", os.path.join(out, sh.replace(".jobs", "")),
                       "--funding-x", os.path.join(out, "empty_x"), "--rule", "funding", "--funding-k", str(st["k"]),
                       "--hold-payments", str(st["hold"]), "--cells", cellrow, "--jobs", jf,
                       "--daily-out", os.path.join(SEL, "d2form_out", tag + ".daily.csv"), "--trades-out", tr,
                       "--trade-jobs", ",".join(str(i) for i in range(n)), "--cells-out", co, "--min-span", "1440", "--threads", "4"]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
                with open(tr, encoding="utf-8", newline="") as fh:
                    rd = csv.DictReader(fh)
                    for r in rd:
                        trades_all.append(int(float(r["fill_out"])) - int(float(r.get("fill_in", r.get("fill", 0)))) if "fill_in" in r else None)
                with open(co, encoding="utf-8", newline="") as fh:
                    for r in csv.DictReader(fh):
                        open_end += int(float(r.get("open_at_end", 0) or 0))
                        done += int(float(r.get("done", 0) or 0))
        vals = sorted(v for v in trades_all if v is not None)
        ih_min = st["ih"] * 60
        if vals:
            q = lambda p: vals[min(len(vals) - 1, int(p * len(vals)))]
            dur.append([cid, len(vals), q(0.25), q(0.5), q(0.75), "%g" % ih_min, "%.4f" % (sum(1 for v in vals if v < ih_min) / len(vals)),
                        open_end, "медиана >= интервала" if q(0.5) >= ih_min else "МЕДИАНА < ИНТЕРВАЛА"])
        else:
            dur.append([cid, 0, "", "", "", "%g" % ih_min, "", open_end, "нет закрытых сделок на формировании"])
    with open(os.path.join(SEL, "d2_durations.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cell", "closed_positions", "dur_p25_min", "dur_median_min", "dur_p75_min", "interval_y_min", "share_shorter_than_interval",
                    "open_at_end_positions", "median_vs_interval"])
        w.writerows(dur)
    print("клеток D2", len(rows), "исключено пар", len(excl), "клеток с длительностями", len(dur))


if __name__ == "__main__":
    {"all": run_all}[sys.argv[1]]()
