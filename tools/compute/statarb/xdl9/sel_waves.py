#!/usr/bin/env python3
"""TK-493 S1 шаг 6 (ПК): параметры клеток (P-37-params-cells) и списки заданий для `statarb run` по волнам (П-37 r3.1 §5, §14, §18). Счёт НЕ запускается.

Волна 1: A2, C1, D2; волна 2: A1; волна 3: B1 (безусловна при зелёном G9b, Н25). Руки: a0 (stop_z = inf) и a1 (stop_z = 2 x entry_z), обе со --rearm (Н35);
D2 - только a0 (правило funding: стопа и --rearm нет). Торговое окно заданий: from_day 2479 (15.10), to_day 2500 (05.11 не включая), 21 сутки.
Бинарь счёта: ~/xdl9/bin/statarb-xdl9-4ef45cc8 (sha256 969db927...; код 6108b0bf + 4ef45cc8), без PGO.
Флаги каждого `run` (P-02, M-07, F-01..F-03): --rearm (кроме D2), --trades-out + --trades-ext + --trade-jobs ВСЕХ заданий группы, --open-out + --mtm-open,
--cells-out, --ext-out, --job-day-out, --daily-out, --min-span 1440. A1/A2/B1: пара рук одним --cells (многоклеточный проход); C1: --cells с двумя руками
(с --delay-x 1 ядро идёт одноклеточным проходом); D2: одиночная --cell, ряд d = r_Y - r_X готовит d2_diff_funding.py на этапе волны (фандинг торгового окна
читает ТОЛЬКО волна, F8), --funding-x - пустой каталог.
Режим 'form' (клон для sigma_форм и дымовых проб): те же команды на панелях формирования ~/xdl9/form, фандинге ~/xdl9/funding_form и рядах d формирования.
"""
import csv, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402
import sel_cells as SC  # noqa: E402

BIN = "$HOME/xdl9/bin/statarb-xdl9-4ef45cc8"
D2_WINDOW = 60          # НАЗНАЧЕНО без Судьи, подтвердить: при правиле funding window/min_obs/refresh ядром не используются (прогрев)
WAVE = {"A2": 1, "C1": 1, "D2": 1, "A1": 2, "B1": 3}
NL = chr(10)
HDR_CELLS = "cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh"


def stem_of(cid, exy, exx):
    return "%s__%s__%s" % (cid.replace("|", "_").replace("~", "-"), exy, exx)


def base_cell(cell):
    return cell.rsplit("|w", 1)[0] if cell.startswith("C1|") else cell


def build_cmd(kind, cell, exy, exx, cf, jf, st, w, mode, d2=None):
    """Одна группа запусков. mode 'trade' (волна) или 'form' (клон на формировании: sigma_форм, дымовые пробы)."""
    P = "$HOME/xdl9/trade" if mode == "trade" else "$HOME/xdl9/form"
    F = "$HOME/xdl9/funding" if mode == "trade" else "$HOME/xdl9/funding_form"
    O = ("out/w%d" % w) if mode == "trade" else "sigma_out"
    flags = "--min-span 1440 --threads ${THREADS:-4}"
    if mode == "trade":
        outs = ("--daily-out {O}/{st}.daily.csv --trades-out {O}/{st}.trades.csv --trades-ext --trade-jobs \"$(tj {jf})\" "
                "--open-out {O}/{st}.open.csv --mtm-open --cells-out {O}/{st}.cells_out.csv --ext-out {O}/{st}.ext.csv "
                "--job-day-out {O}/{st}.jobday.csv").format(O=O, st=st, jf=jf)
    else:
        outs = ("--daily-out {O}/{st}.daily.csv --cells-out {O}/{st}.cells_out.csv --ext-out {O}/{st}.ext.csv "
                "--job-day-out {O}/{st}.jobday.csv").format(O=O, st=st)
    if kind in ("A1", "A2"):
        core = "--kind pair --panels %s/%s --funding-y %s/%s --rearm --cells %s" % (P, exy, F, exy, cf)
    elif kind == "B1":
        core = "--kind basket --panels %s/%s --funding-y %s/%s --rearm --cells %s" % (P, exy, F, exy, cf)
    elif kind == "C1":
        core = ("--kind cross --panels %s/%s --panels-b %s/%s --funding-y %s/%s --funding-x %s/%s --delay-x 1 --rearm --cells %s"
                % (P, exy, P, exx, F, exy, F, exx, cf))
    else:   # D2: ряд d готовит d2_diff_funding.py; шарды - по общему символу Y у нескольких партнёров X
        d2dir = ("d2/" + st) if mode == "trade" else ("$HOME/xdl9/sel/d2form/" + st)
        body = ("%s statarb run --kind funding-pp --panels %s/%s --panels-b %s/%s --funding-y %s/$S --funding-x %s/empty_x --rule funding "
                "--funding-k %s --hold-payments %s --cell \"%s|a0\" --window %d --min-obs %d --entry-z 1.00 --exit-z 0 --stop-z inf --max-hold 0 "
                "--refresh %d --jobs $J %s %s"
                % (BIN, P, exy, P, exx, d2dir, d2dir, d2["k"], d2["hold"], cell, D2_WINDOW, D2_WINDOW, D2_WINDOW, flags,
                   outs.replace("\"$(tj %s)\"" % jf, "\"$(tj $J)\"").replace(st + ".", st + "__$S.")))
        if mode == "trade":
            gen = ("python3 $HOME/xdl9/tools/statarb/xdl9/d2_diff_funding.py --pairs %s --fund-y $HOME/xdl9/funding/%s --fund-x $HOME/xdl9/funding/%s "
                   "--out %s --from-day %d --to-day %d >> out/w%d/d2_diff.log && " % (cf, exy, exx, d2dir, S.D_TRADE0, S.D_END, w))
        else:
            gen = ""
        return "%sfor J in %s/shard*.jobs.csv; do S=$(basename $J .jobs.csv); [ $(wc -l < $J) -gt 1 ] || continue; %s; done" % (gen, d2dir, body)
    return "%s statarb run %s --jobs %s %s %s" % (BIN, core, jf, outs, flags)


PARAM_HDR = ["cell", "kind", "slug", "pairs", "hl_pairs_ok", "phi_outside_unit", "hl_median_min", "m", "window", "max_hold", "refresh", "entry_z", "stop_z_a1",
             "trades_at_entry_z_formation", "z_max", "grid_unreachable_share", "pairs_unreachable_share", "gross_ci90_or_d2_params",
             "sigma_formation_bps_day_per_active_pair", "status"]


def main():
    hl = {r["cell"]: r for r in S.read_csv(os.path.join(S.DOCS, "cells_hl.csv"))}
    ez = {r["cell"]: r for r in S.read_csv(os.path.join(S.DOCS, "entry_z.csv"))}
    d2 = {r["cell"]: r for r in S.read_csv(os.path.join(S.DOCS, "d2_params.csv"))}
    dp = os.path.join(S.DOCS, "d2_durations.csv")
    dur = {r["cell"]: r for r in S.read_csv(dp)} if os.path.exists(dp) else {}
    sg = {}
    sp = os.path.join(S.DOCS, "sigma_formation.csv")
    if os.path.exists(sp):
        sg = {r["cell"]: r["sigma_bps_per_day_per_active_pair"] for r in S.read_csv(sp)}
    wd = S.ensure(os.path.join(S.SEL, "waves"))
    man, params, notrade, sig_cmds = [], [], [], []
    for cell in sorted(hl):
        r = hl[cell]
        kind = r["kind"]
        bc = base_cell(cell)
        cs = cell.replace("|", "_")
        pf = os.path.join(S.SEL, "cellpairs", bc.replace("|", "_") + ".csv")
        row = {h: "" for h in PARAM_HDR}
        row.update(cell=cell, kind=kind, slug=r["slug"], pairs=r["pairs"], hl_pairs_ok=r["hl_pairs_ok"], phi_outside_unit=r["phi_outside_unit"],
                   hl_median_min=r["hl_median_min"], m=r["m"], z_max=r["z_max"], grid_unreachable_share=r["grid_unreachable_share"],
                   pairs_unreachable_share=r["pairs_unreachable_share"])
        if kind == "D2":
            d = d2.get(cell)
            if d is None or d["status"] != "параметры измерены":
                notrade.append([cell, kind, "нет пар с фандингом обеих ног (ряд d)"])
                row["status"] = "не торгуется: нет ряда d"
                params.append(row)
                continue
            dd = dur.get(cell, {})
            row.update(window=D2_WINDOW, max_hold=0, refresh=D2_WINDOW,
                       gross_ci90_or_d2_params="k=%s;hold_payments=%s;interval_h_y=%s;unequal_grid_share=%s;dur_median_min=%s;median_vs_interval=%s" % (
                           d["k"], d["hold_payments"], d["interval_h_y_median"], d["unequal_grid_share"], dd.get("dur_median_min", ""), dd.get("median_vs_interval", "")),
                       sigma_formation_bps_day_per_active_pair="a0=%s" % sg.get(cell + "|a0", ""), status="торгуется (G7 пройден в F6; max_hold = 0)")
            params.append(row)
            cells_rows = None
            hands = "a0"
            groups = {}
            for p in S.read_csv(pf):
                groups.setdefault((p["ex_y"], p["ex_x"]), []).append((p["y"], p["x"]))
            d2p = dict(k=d["k"], hold=d["hold_payments"])
        else:
            e = ez.get(cell)
            if not r["W_min"] or e is None or e["status"] != "торгуется":
                st_txt = e["status"] if e else "полураспад не измерен"
                notrade.append([cell, kind, st_txt])
                row.update(window=r["W_min"], max_hold=r["W_min"], refresh=r["W_min"], status=st_txt)
                params.append(row)
                continue
            W, z = int(r["W_min"]), float(e["entry_z"])
            cells_rows = [("%s|a0" % cell, W, W, "", "%.2f" % z, "0", "inf", W, W),
                          ("%s|a1" % cell, W, W, "", "%.2f" % z, "0", "%.2f" % (2 * z), W, W)]
            hands = "a0+a1"
            row.update(window=W, max_hold=W, refresh=W, entry_z=e["entry_z"], stop_z_a1="%.2f" % (2 * z), trades_at_entry_z_formation=e["trades_at_z"],
                       gross_ci90_or_d2_params="mean_gross=%s;lo90=%s;hi90=%s" % (e["mean_gross_bps"], e["gross_lo90"], e["gross_hi90"]),
                       sigma_formation_bps_day_per_active_pair="a0=%s;a1=%s" % (sg.get(cell + "|a0", ""), sg.get(cell + "|a1", "")), status="торгуется")
            params.append(row)
            groups = {}
            for p in S.read_csv(pf):
                if p["hl_min"] == "" and p["phi"] == "":
                    continue
                groups.setdefault((p["ex_y"], p["ex_x"]), []).append((p["y"], p["x"]))
            d2p = None
        w = WAVE[kind]
        S.ensure(os.path.join(wd, "w%d" % w))
        S.ensure(os.path.join(wd, "sigma"))
        cf = "w%d/%s.cells.csv" % (w, cs)
        if cells_rows:
            for target in (os.path.join(wd, cf), os.path.join(wd, "sigma", cs + ".cells.csv")):
                with open(target, "w", encoding="utf-8", newline=NL) as fh:
                    fh.write(HDR_CELLS + NL)
                    for rw in cells_rows:
                        fh.write(",".join(str(x) for x in rw) + NL)
        for (exy, exx), prs in sorted(groups.items()):
            st = stem_of(cell, exy, exx)
            if kind == "D2":
                jf_trade = "w%d/%s.pairs.csv" % (w, st)        # у D2 - список пар y,x: задания по шардам пишет d2_diff_funding.py
                with open(os.path.join(wd, jf_trade), "w", encoding="utf-8", newline=NL) as fh:
                    fh.write("y,x" + NL)
                    for y, x in prs:
                        fh.write("%s,%s%s" % (y, x, NL))
                jf_form = jf_trade
            else:
                jf_trade = "w%d/%s.jobs.csv" % (w, st)
                with open(os.path.join(wd, jf_trade), "w", encoding="utf-8", newline=NL) as fh:
                    fh.write("y,x,from_day,to_day" + NL)
                    for y, x in prs:
                        fh.write("%s,%s,%d,%d%s" % (y, x, S.D_TRADE0, S.D_END, NL))
                jf_form = "sigma/%s.jobs.csv" % st
                with open(os.path.join(wd, jf_form), "w", encoding="utf-8", newline=NL) as fh:
                    fh.write("y,x,from_day,to_day" + NL)
                    for y, x in prs:
                        fh.write("%s,%s,%d,%d%s" % (y, x, S.D_FORM0, S.D_TRADE0, NL))
            cmd = build_cmd(kind, cell, exy, exx, cf, jf_trade, st, w, "trade", d2p)
            man.append([w, kind, cell, exy, exx, hands, len(prs), cf, jf_trade, cmd])
            cf_form = ("sigma/%s.cells.csv" % cs) if cells_rows else cf
            sig_cmds.append([cell, kind, exy, exx, st, build_cmd(kind, cell, exy, exx, cf_form, jf_form, st, w, "form", d2p)])
    man.sort(key=lambda r: (r[0], r[1], r[2], r[3], r[4]))
    S.write_csv(os.path.join(S.DOCS, "waves_manifest.csv"), ["wave", "kind", "cell", "ex_y", "ex_x", "hands", "jobs", "cells_file", "jobs_file", "cmd"], man)
    S.write_csv(os.path.join(S.DOCS, "cells_not_traded.csv"), ["cell", "kind", "reason"], notrade)
    S.write_csv(os.path.join(S.SEL, "sigma_cmds.csv"), ["cell", "kind", "ex_y", "ex_x", "stem", "cmd"], sig_cmds)
    S.write_csv(os.path.join(S.ROOT, "docs", "research", "P-37-params-cells-2026-11-06.csv"), PARAM_HDR, [[rw[h] for h in PARAM_HDR] for rw in params])
    for w in (1, 2, 3):
        lines = ["#!/usr/bin/env bash",
                 "# TK-493 F8 (S1, П-37 r3.1): волна %d. Запуск - ТОЛЬКО по команде CEO (S1 не запускал); cd ~/xdl9/sel/waves; THREADS=4 nice -n 19 bash wave%d.sh" % (w, w),
                 "# Перед первым запуском: выписка В-213 «есть / считаем / стоимость»; каталоги out/ и d2/ не существуют.",
                 "set -u", "THREADS=${THREADS:-4}",
                 "tj() { seq -s, 0 $(( $(wc -l < \"$1\") - 2 )); }       # номера заданий 0..n-1 (в файле заголовок + n строк)",
                 "mkdir -p out/w%d" % w]
        lines += [m[-1] for m in man if m[0] == w]
        with open(os.path.join(wd, "wave%d.sh" % w), "w", encoding="utf-8", newline=NL) as fh:
            fh.write(NL.join(lines) + NL)
    # волна 4: контроль §11.8 «все пары каталога без отбора» - те же W, entry_z, max_hold, что у торгуемых клеток A1; задания пишет sel_allpairs_jobs.py на Deck
    w4 = ["#!/usr/bin/env bash",
          "# TK-493 F8 (S1, П-37 r3.1 §11.8): контроль «все пары каталога без отбора». Запуск - только по команде CEO; cd ~/xdl9/sel/waves; THREADS=4 nice -n 19 bash wave4.sh",
          "set -u", "THREADS=${THREADS:-4}", "mkdir -p out/w4 w4"]
    for m in man:
        if m[1] != "A1":
            continue
        cell, exy = m[2], m[3]
        cid = exy + "-" + cell.split("|")[2]
        st = "ALLPAIRS_" + stem_of(cell, exy, exy)
        w4.append("python3 $HOME/xdl9/tools/statarb/xdl9/sel_allpairs_jobs.py %s w4/%s.jobs.csv" % (cid, st))
        w4.append("%s statarb run --kind pair --panels $HOME/xdl9/trade/%s --funding-y $HOME/xdl9/funding/%s --rearm --cells %s --jobs w4/%s.jobs.csv "
                  "--daily-out out/w4/%s.daily.csv --cells-out out/w4/%s.cells_out.csv --ext-out out/w4/%s.ext.csv --mtm-open --min-span 1440 --threads ${THREADS:-4}"
                  % (BIN, exy, exy, m[7], st, st, st, st))
    with open(os.path.join(wd, "wave4.sh"), "w", encoding="utf-8", newline=NL) as fh:
        fh.write(NL.join(w4) + NL)
    n_by = {}
    for m in man:
        n_by.setdefault((m[0], m[1]), [0, 0])
        n_by[(m[0], m[1])][0] += 1
        n_by[(m[0], m[1])][1] += m[6]
    print("групп запусков", len(man), {k: v for k, v in sorted(n_by.items())}, "клеток не торгуется", len(notrade), Counter(x[1] for x in notrade))


if __name__ == "__main__":
    main()
