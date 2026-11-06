#!/usr/bin/env python3
"""TK-493 F7 шаг 6 (ПК): параметры клеток (в P-37-params-cells) и списки заданий для `statarb run` по волнам. Счёт волн НЕ запускается.

Волна 1: A2, C1, D2 (рука a0; для A2 и C1 ещё рука a1 = stop_z 2 x entry_z в той же группе клеток); волна 2: A1 (a0, a1); волна 3: B1 (a0, a1; условная).
Торговое окно заданий: from_day = 2479 (15.10), to_day = 2500 (05.11 не включая). Панели торговли - ~/xdl9/trade/<биржа> (США-контракты с маской кассы),
фандинг - ~/xdl9/funding/<биржа>. Клетки без entry_z (безубыточной точки на формировании нет) в волны не входят - они результат (cells_not_traded.csv).
Бинарь: ~/xdl9/bin/statarb-xdl9-02c6a0df (tk493-xdl9; C1 нужен --delay-x, D2 и фандинг - исправление E-010); итоговый PGO-бинарь выбирает CEO после G5/G6.
"""
import csv, hashlib, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

BIN = "$HOME/xdl9/bin/statarb-xdl9-02c6a0df"
D2_WINDOW = 60          # НАЗНАЧЕНО без Судьи, подтвердить: при правиле funding window/min_obs ядром не используются (прогрев)
WAVE = {"A2": 1, "C1": 1, "D2": 1, "A1": 2, "B1": 3}


def stem_of(cid, ex_y, ex_x):
    return "%s__%s__%s" % (cid.replace("|", "_").replace("~", "-"), ex_y, ex_x)


def main():
    hl = {r["cell"]: r for r in S.read_csv(os.path.join(S.DOCS, "cells_hl.csv"))}
    ez = {r["cell"]: r for r in S.read_csv(os.path.join(S.DOCS, "entry_z.csv"))}
    d2 = {r["cell"]: r for r in S.read_csv(os.path.join(S.DOCS, "d2_params.csv"))}
    sg = {}
    sp = os.path.join(S.DOCS, "sigma_formation.csv")
    if os.path.exists(sp):
        sg = {r["cell"]: r["sigma_bps_per_day_per_pair"] for r in S.read_csv(sp)}
    wd = S.ensure(os.path.join(S.SEL, "waves"))
    man, params, notrade = [], [], []
    for cid in sorted(hl):
        r = hl[cid]
        kind = r["kind"]
        cs = cid.replace("|", "_")
        if kind == "D2":
            d = d2[cid]
            if d["status"] != "параметры измерены":
                notrade.append([cid, kind, "нет пар с фандингом обеих ног"])
                params.append([cid, kind, r["slug"], r["pairs"], "", "", "", "", "", "", "", "", "", "", "", "не торгуется: нет фандинга"])
                continue
            k, h, mh = int(d["k"]), int(d["hold_payments"]), int(d["max_hold_min"])
            cells_rows = [("%s|a0" % cid, D2_WINDOW, D2_WINDOW, "", "1.00", "0", "inf", mh, D2_WINDOW)]
            pf = os.path.join(S.SEL, "cellpairs", cs + ".d2ok.csv")
            params.append([cid, kind, r["slug"], d["pairs_with_funding"], "", "", "", D2_WINDOW, mh, D2_WINDOW, "", "", "",
                           "k=%d;hold_payments=%d;interval_h_y=%s" % (k, h, d["interval_h_y_median"]),
                           "a0=%s" % sg.get(cid + "|a0", ""), "торгуется (G7 пройден в F6)"])
            hands = "a0"
        else:
            e = ez.get(cid)
            if not r["W_min"] or e is None or e["status"] != "торгуется":
                notrade.append([cid, kind, e["status"] if e else "полураспад не измерен"])
                params.append([cid, kind, r["slug"], r["pairs"], r["hl_pairs_ok"], r["phi_outside_unit"], r["hl_median_min"],
                               r["W_min"], r["W_min"], r["W_min"], "", "", "", "", "", e["status"] if e else "полураспад не измерен"])
                continue
            W, z = int(r["W_min"]), float(e["entry_z"])
            cells_rows = [("%s|a0" % cid, W, W, "", "%.2f" % z, "0", "inf", W, W),
                          ("%s|a1" % cid, W, W, "", "%.2f" % z, "0", "%.2f" % (2 * z), W, W)]
            pf = os.path.join(S.SEL, "cellpairs", cs + ".csv")
            params.append([cid, kind, r["slug"], r["pairs"], r["hl_pairs_ok"], r["phi_outside_unit"], r["hl_median_min"], W, W, W,
                           "%.2f" % z, "%.2f" % (2 * z), e["trades_at_z"], "",
                           "a0=%s;a1=%s" % (sg.get(cid + "|a0", ""), sg.get(cid + "|a1", "")), "торгуется"])
            hands = "a0+a1"
        w = WAVE[kind]
        wdir = S.ensure(os.path.join(wd, "w%d" % w))
        cf = "%s.cells.csv" % cs
        with open(os.path.join(wdir, cf), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh\n")
            for row in cells_rows:
                fh.write(",".join(str(x) for x in row) + "\n")
        groups = {}
        for p in S.read_csv(pf):
            if kind != "D2" and p["hl_min"] == "" and p["phi"] == "":
                continue
            groups.setdefault((p["ex_y"], p["ex_x"]), []).append((p["y"], p["x"]))
        for (exy, exx), prs in sorted(groups.items()):
            st = stem_of(cid, exy, exx)
            jf = st + ".jobs.csv"
            with open(os.path.join(wdir, jf), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("y,x,from_day,to_day\n")
                for y, x in prs:
                    fh.write("%s,%s,%d,%d\n" % (y, x, S.D_TRADE0, S.D_END))
            tj = ",".join(str(i) for i in range(len(prs)))
            common = ("--cells w%d/%s --jobs w%d/%s --daily-out out/w%d/%s.daily.csv --trades-out out/w%d/%s.trades.csv --trade-jobs %s "
                      "--cells-out out/w%d/%s.cells_out.csv --mtm-open --min-span 1440 --threads 8"
                      % (w, cf, w, jf, w, st, w, st, tj, w, st))
            if kind in ("A1", "A2"):
                core = "--kind pair --panels $HOME/xdl9/trade/%s --funding-y $HOME/xdl9/funding/%s" % (exy, exy)
            elif kind == "B1":
                core = "--kind basket --panels $HOME/xdl9/trade/%s --funding-y $HOME/xdl9/funding/%s" % (exy, exy)
            elif kind == "C1":
                core = ("--kind cross --panels $HOME/xdl9/trade/%s --panels-b $HOME/xdl9/trade/%s --funding-y $HOME/xdl9/funding/%s "
                        "--funding-x $HOME/xdl9/funding/%s --delay-x 1" % (exy, exx, exy, exx))
            else:   # D2
                dd = d2[cid]
                core = ("--kind funding-pp --panels $HOME/xdl9/trade/%s --panels-b $HOME/xdl9/trade/%s --funding-y $HOME/xdl9/funding/%s "
                        "--funding-x $HOME/xdl9/funding/%s --funding-k %s --hold-payments %s" % (exy, exx, exy, exx, dd["k"], dd["hold_payments"]))
            cmd = "%s statarb run %s %s" % (BIN, core, common)
            man.append([w, kind, cid, exy, exx, hands, len(prs), cf, jf, cmd])
    man.sort(key=lambda r: (r[0], r[1], r[2], r[3], r[4]))
    S.write_csv(os.path.join(S.DOCS, "waves_manifest.csv"), ["wave", "kind", "cell", "ex_y", "ex_x", "hands", "jobs", "cells_file", "jobs_file", "cmd"], man)
    S.write_csv(os.path.join(S.DOCS, "cells_not_traded.csv"), ["cell", "kind", "reason"], notrade)
    hdr = ["cell", "kind", "slug", "pairs", "hl_pairs_ok", "phi_outside_unit", "hl_median_min", "window", "max_hold", "refresh",
           "entry_z", "stop_z_a1", "trades_at_entry_z_formation", "d2_params", "sigma_formation_bps_day", "status"]
    S.write_csv(os.path.join(S.DOCS, "..", "..", "research", "P-37-params-cells-2026-11-06.csv"), hdr, params)
    for w in (1, 2, 3):
        lines = ["#!/usr/bin/env bash", "# TK-493 F8: волна %d (cd ~/xdl9/sel/waves; запускает CEO, F7 не запускал)" % w, "mkdir -p out/w%d" % w]
        lines += [m[-1] for m in man if m[0] == w]
        S.ensure(os.path.join(wd, "w%d" % w))
        with open(os.path.join(wd, "wave%d.sh" % w), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
    n_by = {}
    for m in man:
        n_by.setdefault((m[0], m[1]), [0, 0])
        n_by[(m[0], m[1])][0] += 1; n_by[(m[0], m[1])][1] += m[6]
    print("групп запусков", len(man), {k: v for k, v in sorted(n_by.items())}, "клеток не торгуется", len(notrade))


if __name__ == "__main__":
    main()
