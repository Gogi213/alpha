"""TK-617 v3: сетка жёсткого стопа на L_base / S_base — описательная таблица по клеткам и месяцам (движок не запускается).
  python tk617_stopgrid.py --out-root /data/p42-tk617/stopgrid/out --ref-root /data/p42-tk599/out --report stopgrid.md [--json stopgrid.json]
Каталоги клеток — `<out-root>/<окно>/<id>/{long,short}-trades.csv` (id `L_*` → long, `S_*` → short). Контроль: `L_base` / `S_base` побайтно против TK-599
(`c53a116125b39` / `c10fbba7be522`). Склейка окон и ворота — `retitr_full` / `short_autopsy_base.spliced_rows` (как TK-618). P&L сделки = gross − fees, без фандинга."""
import argparse
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_full as rf  # noqa: E402
import short_autopsy_base as sb  # noqa: E402

MON = rf.MONTH_NAMES
POS, LEV = 500.0, 2.0                       # ставка $500 (В-89…В-91), плечо 2 → маржа $250
ACCT = 2500.0                               # счёт $2500
CONTROL = {"L_base": ("c53a116125b39", "long"), "S_base": ("c10fbba7be522", "short")}
FILES = {"long": ("long-trades.csv", "long-daily.csv", "long-coinday.csv", "long-entryrej.csv"),
         "short": ("short-trades.csv", "short-daily.csv", "short-coinday.csv", "short-entryrej.csv")}


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def side_of(cid):
    return "long" if cid.startswith("L_") else "short"


def guards(root, cid):
    side = side_of(cid)
    tables = {w.name: rf.read_trades_csv(os.path.join(root, w.name, cid, side + "-trades.csv")) for w in rf.WINDOWS}
    rf.check_windows(rf.WINDOWS)
    rf.check_forced(tables)
    rf.check_data_end(tables)
    wins = {w.name: w for w in rf.WINDOWS}
    for a, b, lo, hi in rf.G3_RANGES:
        diff = rf.overlap_diff(tables[a], wins[a], tables[b], wins[b], lo, hi)
        if diff:
            raise rf.GuardError("G3 %s: швы %s–%s: %d различий, первое: %s" % (cid, a, b, len(diff), diff[0]))


def control(out_root, ref_root):
    bad = []
    for cid, (vid, side) in CONTROL.items():
        for w in rf.WINDOWS:
            for f in FILES[side]:
                a, b = os.path.join(out_root, w.name, cid, f), os.path.join(ref_root, w.name, vid, f)
                if not (os.path.exists(a) and os.path.exists(b)):
                    bad.append("%s %s %s: нет файла" % (cid, w.name, f))
                elif md5(a) != md5(b):
                    bad.append("%s %s %s: md5 различается" % (cid, w.name, f))
    return bad


def stats(pnl):
    n = len(pnl)
    if n == 0:
        return dict(n=0, sum=0.0, mean=float("nan"))
    return dict(n=n, sum=float(pnl.sum()), mean=float(pnl.mean()))


def cell(rows):
    pnl = np.array([float(r["pnl"]) for r in rows])
    kind = np.array([r["kind"] for r in rows])
    month = np.array([r["month"] for r in rows])
    st = kind == "stop"
    win, loss = pnl > 0, pnl < 0
    o = dict(n=len(pnl), sum=float(pnl.sum()), mean=float(pnl.mean()), stops=int(st.sum()), stop_sum=float(pnl[st].sum()),
             win_rate=float(win.mean()), avg_win=float(pnl[win].mean()) if win.any() else float("nan"),
             avg_loss=float(pnl[loss].mean()) if loss.any() else float("nan"),
             per_stop=float(pnl[st].mean()) if st.any() else float("nan"),
             kinds={k: [int((kind == k).sum()), float(pnl[kind == k].sum())] for k in sorted(set(kind))})
    o["rr"] = o["avg_win"] / abs(o["avg_loss"]) if loss.any() and win.any() else float("nan")
    o["months"] = {m: dict(stats(pnl[month == m]), stops=int((st & (month == m)).sum())) for m in MON}
    return o


def main(a):
    bad = control(a.out_root, a.ref_root)
    if bad:
        raise rf.GuardError("контроль побайтно: %d расхождений, первое: %s" % (len(bad), bad[0]))
    ids = []
    for line in open(a.variants, encoding="utf-8"):
        if line.strip() and not line.startswith("#"):
            ids.append(line.split()[0])
    res = {}
    for cid in ids:
        guards(a.out_root, cid)
        side = side_of(cid)
        res[cid] = cell(sb.spliced_rows(lambda w, cid=cid, side=side: os.path.join(a.out_root, w, cid, side + "-trades.csv")))
    out = ["# TK-617 v3: сетка жёсткого стопа на `L_base` / `S_base`, 01.07–13.11 (136 сут.), найдено на этих данных\n",
           "Движок `alpha-master` a112684a, окна wa/wb/wc, склейка и ворота как TK-618; контроль `L_base`/`S_base` побайтно против TK-599 (`c53a116125b39` / `c10fbba7be522`): пройден. "
           "P&L сделки = gross − fees (без фандинга). Ставка $%.0f; плечо %.0f → маржа $%.0f; «%% маржи» = убыток на стоп / маржа, «%% счёта» = убыток на стоп / $%.0f. "
           "Ключи: `s` — жёсткий стоп, %% от цены входа (база 5); `sn` — стоп в NATR (расстояние = clamp(k·NATR, 0,5 %%, `s`)); `ls` — стоп за дальним краем зоны + k·NATR (не глубже `s`). Медиан нет.\n"
           % (POS, LEV, POS / LEV, ACCT)]
    for side, title in (("L", "L_base"), ("S", "S_base")):
        out.append("\n## %s\n" % title)
        out.append("| клетка | сделок | Σ $ | $/сделку | стопов (доля) | Σ стопов $ | выигрыш % | ср. выигрыш $ | ср. убыток $ | R:R | убыток на стоп $ | % маржи (плечо 2) | % счёта | trail / rt / be / stop |")
        out.append("|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
        for cid in [c for c in ids if c.startswith(side + "_")]:
            x = res[cid]
            k = x["kinds"]
            out.append("| %s | %d | %+.2f | %+.3f | %d (%.1f%%) | %+.1f | %.1f | %+.2f | %+.2f | %.2f | %s | %s | %s | %s |" % (
                cid, x["n"], x["sum"], x["mean"], x["stops"], 100.0 * x["stops"] / x["n"], x["stop_sum"], 100.0 * x["win_rate"], x["avg_win"], x["avg_loss"], x["rr"],
                ("%+.2f" % x["per_stop"]) if x["stops"] else "—", ("%.1f%%" % (100.0 * abs(x["per_stop"]) / (POS / LEV))) if x["stops"] else "—",
                ("%.2f%%" % (100.0 * abs(x["per_stop"]) / ACCT)) if x["stops"] else "—",
                " / ".join("%d" % k.get(n, [0, 0])[0] for n in ("trail", "rt", "be", "stop"))))
        out.append("\nПо месяцам: Σ $ / $ на сделку / сделок / стопов.\n")
        out.append("| клетка | " + " | ".join(MON) + " |")
        out.append("|---|" + "---|" * len(MON))
        for cid in [c for c in ids if c.startswith(side + "_")]:
            out.append("| %s | " % cid + " | ".join("%+.1f / %+.2f / %d / %d" % (res[cid]["months"][m]["sum"], res[cid]["months"][m]["mean"], res[cid]["months"][m]["n"], res[cid]["months"][m]["stops"]) for m in MON) + " |")
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    open(a.report, "w", encoding="utf-8").write("\n".join(out) + "\n")
    if a.json:
        json.dump(res, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("cells", len(ids))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--ref-root", required=True)
    ap.add_argument("--variants", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--json")
    main(ap.parse_args())
