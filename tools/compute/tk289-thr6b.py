#!/usr/bin/env python3
"""TK-289 П-18 ось 6б: пороги strength_min по месяцам — квантили силы (±20 bps, strength_w20_pct) по сигналам B1 своего месяца.
Сигналы B1 = signals.csv клетки p18a1v0_0289 (оси 1–5 меняют форму, не набор age=2700,side=bid,btc4h_max=-44.55 — набор сигналов одинаков),
уникальные (символ, сутки, t0_ns, price_tick); сила — из approaches-<SYM>.csv журнала тачей той же суток (склейка price_tick + arm_ms = t0_ns/1e6).
Порог q: nearest_rank(отсортированная сила, q/100) (как r1cell.threshold «lo»); сила не определена (< 0 или пусто) в выборку не входит.
--s67: вместо силы ±20 bps — сила владельца В-67 S = size_at_arm / flow_1h_lots × 100 (ключ набора flow=; flow_1h_lots = 0 — не определена),
квантили QS67 = 10/25/50/75/90/95 (распределение для решения Судьи «шире/гуще» 6в, P-18-strength-v67).
Запуск: tk289-thr6b.py <каталог f1/out> <выход .json> <выход .csv> [--days N] [--s67] -> {"2026-01": {"10": x, ...}} + P-18-thresholds.csv (месяц,q,порог,n,склеено,не склеено)."""
import csv, glob, json, math, os, sys

QS = (10, 20, 33, 50, 67, 80, 90, 95)
QS67 = (10, 25, 50, 75, 90, 95)
CELL = "p18a1v0_0289"
JRN = ("/srv/alpha-sda/jrn", "/data/p12r3a/jrn")


def nearest_rank(v, q):
    return v[min(len(v), max(1, math.ceil(q * len(v)))) - 1]


def appr_path(day, sym):
    for j in JRN:
        p = f"{j}/{day}/wt/sub/D20/{day}/approaches-{sym}.csv"
        if os.path.exists(f"{j}/{day}/done") and os.path.exists(p):
            return p
    for j in JRN:
        p = f"{j}/{day}/wt/sub/D20/{day}/approaches-{sym}.csv"
        if os.path.exists(p):
            return p
    return None


def main():
    src, out_json, out_csv = sys.argv[1:4]
    lim = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 0
    s67 = "--s67" in sys.argv
    qs = QS67 if s67 else QS
    by_month, stat = {}, {}
    dirs = sorted(glob.glob(f"{src}/run-*"))
    if lim:
        dirs = dirs[:lim]
    for d in dirs:
        for sp in glob.glob(f"{d}/out/*/{CELL}-*/signals.csv"):
            day = sp.split("/")[-3]; ym = day[:7]
            sig, rows = {}, [l for l in open(sp, encoding="utf-8") if not l.startswith("#")]
            for r in csv.DictReader(rows):
                sig[(r["symbol"], r["t0_ns"], r["price_tick"])] = 1
            by_sym = {}
            for s, t0, pt in sig:
                by_sym.setdefault(s, set()).add((pt, int(t0) // 1_000_000))
            st = stat.setdefault(ym, {"sig": 0, "joined": 0, "nonbid": 0, "young": 0, "undef": 0})
            vals = by_month.setdefault(ym, [])
            for s, keys in by_sym.items():
                p = appr_path(day, s)
                st["sig"] += len(keys)
                if not p:
                    continue
                for r in csv.DictReader(open(p, encoding="utf-8")):
                    k = (r["price_tick"], int(r["arm_ms"]))
                    if k not in keys:
                        continue
                    keys.discard(k); st["joined"] += 1
                    if r["side"] != "bid":
                        st["nonbid"] += 1
                    if int(r["age_ms"]) < 2_700_000:
                        st["young"] += 1
                    if s67:
                        fl = int(r["flow_1h_lots"])
                        x = "" if fl <= 0 else str(int(r["size_at_arm"]) / fl * 100.0)
                    else:
                        x = r["strength_w20_pct"]
                    if x == "" or float(x) < 0:
                        st["undef"] += 1
                        continue
                    vals.append(float(x))
    res, rows = {}, []
    for ym in sorted(by_month):
        v = sorted(by_month[ym]); st = stat[ym]
        if not v:
            continue
        res[ym] = {str(q): nearest_rank(v, q / 100) for q in qs}
        for q in qs:
            rows.append([ym, q, res[ym][str(q)], len(v), st["joined"], st["sig"] - st["joined"], st["nonbid"], st["young"], st["undef"]])
        print(ym, "сигналов", st["sig"], "склеено", st["joined"], "не-bid", st["nonbid"], "моложе 2700", st["young"], "сила не определена", st["undef"], "n", len(v), flush=True)
    json.dump(res, open(out_json, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["month", "q", "s67_pct" if s67 else "strength_min_pct", "n", "joined", "unjoined", "nonbid", "young", "undef"]); w.writerows(rows)


main()
