#!/usr/bin/env python3
"""TK-250 (П1 плана TK-249): R1-клетки конвейера формы — один модуль вместо tk064-r1cells.py (thresholds/apply, FAM) и tk202-r1x.py (spec/apply, ctx).

  r1cell.py spec  --plan docs/registry/plan_cells.jsonl --out X.r1spec [--which x|all] [--hyp Г-39,Г-04] [--q 20,33] [--hash h1,h2]    # ПК
  r1cell.py apply --spec X.r1spec --pool /data/tk064/pool --t46 /data/tk046 --thr tk202-thresholds.json --out DIR [--months feb,mar]  # calc

apply пишет DIR/cells/<мес>/<хеш>.csv (формат tk064: symbol,day_utc,signal_index,t0_ns,net_bps,reason,exit_ns,qty; исходы из rounds.csv B1),
DIR/summary.json и DIR/thresholds.csv (порог каждой клетки по месяцам — след для аудита). Долларов нет: их даёт psim по клеткам.

Правила отбора — как в tk064/tk202 (проверка «байт в байт» с r1ext и r1x — docs/findings/tk250-*.md):
  порог месяца m = квантиль «ближайший ранг» по определённым сигналам B1 ВСЕХ предыдущих полных месяцев (январь — по самому январю, TK-115);
  hi — оставить v ≥ порог верхних q %, lo — v ≤ порог нижних q %; неопределённое отвергается; масса в нуле (П-11 §4): q33 = q67 = 0 в январе → порог по ненулевым;
  меры в лотах делятся на size_at_arm; ctx: страта оси на минуте t0, порог keep_q — по всей истории, не внутри страты.
Хеш клетки берётся из master.cell_hash (cellhash.cell_hash(cfg) из plan_cells.jsonl) — к способу счёта и знаку не привязан.
"""
import argparse, csv, glob, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct"]
SET = "t-bid-btc4h-q1"
OUT_HEAD = ["symbol", "day_utc", "signal_index", "t0_ns", "net_bps", "reason", "exit_ns", "qty"]

# --- общая таблица «колонка → делитель/класс»; знак — у семьи/гипотезы (одна колонка бывает hi у одной гипотезы и lo у другой) ---
COUNTS = frozenset({"nz_levels15", "frontrun_levels"})   # счётчики: целые, не делятся


def col_class(c):
    """'count' — счётчик как есть; 'lots' — доля от size_at_arm (имена *_lots, *_lots_*, trade_size_*); 'plain' — как есть (bp, мс, e2, …)."""
    if c in COUNTS:
        return "count"
    return "lots" if c.endswith("_lots") or "_lots_" in c or c.startswith("trade_size_") else "plain"


# семья tk064 -> (колонки, знак) — имя клетки r1ext: <семья> (одна колонка) | <семья>-<колонка>; + «-k<q>» (соответствие хеш ↔ клетка r1ext)
FAM = {
    "g59": ([f"tape_press_lots_{w}s" for w in (15, 30, 60)], "lo"), "g75": (["tape_all_lots_15s"], "hi"), "g70": (["vpin_bp"], "lo"),
    "g71": ([f"sign_ac_{w}s_bp" for w in (15, 60)], "lo"), "g68": ([f"obi{n}_bp" for n in (1, 5, 10, 50)], "hi"),
    "g69": ([f"ofi_{w}s_lots" for w in (10, 60)], "hi"), "g139": (["obi50_bp"], "lo"), "g67": ([f"best_flips_{w}s" for w in (15, 60)], "lo"),
    "g63": (["frontrun_delta_10s_lots"], "hi"), "g35": (["front_add_max_lots"], "lo"), "g42": (["size_share15_bp"], "hi"),
    "g43": ([f"best_move_{w}s_cbps" for w in (1, 10)], "hi"), "g44": (["opp_wall_ratio_bp"], "hi"),
    "g82": ([f"tape_burst_press_{w}s_bp" for w in (15, 30, 60)], "lo"), "g04": (["wall_add_max_lots"], "hi"), "g39": (["cancel_life_lots"], "lo"),
}
CTX_AXIS = {"Г-78": "pool1h", "Г-55": "btc1h", "Г-140": "pool1h"}   # ось контекста гипотезы (решение Судьи 12.10 04:30)
REGIME_COL = {"pool1h": "pool_ret_1h_bps", "btc1h": "btc_ret_1h_bps", "btc4h": "btc_ret_4h_bps"}


def r1ext_name(fam, col, q):
    """Имя клетки r1ext (/data/tk189/r1ext/cells/<мес>/<имя>.csv) для семьи tk064, колонки и доли q."""
    return f"{fam}-k{q}" if len(FAM[fam][0]) == 1 else f"{fam}-{col}-k{q}"


def nearest_rank(sorted_vals, q):
    n = len(sorted_vals)
    return sorted_vals[min(n, max(1, math.ceil(q * n))) - 1]


def threshold(src, sign, q):
    """Порог клетки по отсортированной истории src: hi — верхние q %, lo — нижние q %."""
    return nearest_rank(src, 1 - q / 100) if sign == "hi" else nearest_rank(src, q / 100)


def rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(l for l in f if not l.startswith("#")))


def num(s):
    return None if s is None or s == "" else float(s)


def day_files(pool, mon):
    """signals + signals-retry месяца: (сутки, signals.csv, rounds.csv); дубли по ключу снимает load_month (первым — signals)."""
    out = []
    for sub in ("signals", "signals-retry"):
        base = os.path.join(pool, f"m-{mon}", sub)
        for d in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            p = os.path.join(base, d, SET)
            if os.path.isfile(os.path.join(p, "signals.csv")):
                out.append((d, os.path.join(p, "signals.csv"), os.path.join(p, "rounds.csv")))
    return out


def load_month(pool, mon, cols):
    """-> [{key, t0 (мс), f{колонка: float|None}}]: только нужные колонки; лоты поделены на size_at_arm (нет размера — не определено)."""
    lvl = {}
    for p in glob.glob(os.path.join(pool, f"m-{mon}", "lvl", "*.csv")):
        for r in rows(p):
            lvl[(r["symbol"], int(r["arm_ms"]), int(r["price_tick"]))] = float(r["size_at_arm"])
    seen, sigs = set(), []
    for _, sp, _ in day_files(pool, mon):
        for r in rows(sp):
            key = (r["symbol"], r["day_utc"], int(r["signal_index"]))
            if key in seen:
                continue
            seen.add(key)
            t0 = int(r["t0_ns"]) // 1_000_000
            size = lvl.get((r["symbol"], t0, int(r["price_tick"])))
            f = {}
            for c in cols:
                v = num(r.get(c))
                if v is not None and col_class(c) == "lots":
                    v = v / size if size and size > 0 else None
                f[c] = v
            sigs.append({"key": key, "t0": t0, "f": f})
    return sigs


def zero_mass_cols(jan, cols):
    """П-11 §4: q33 = q67 = 0 по определённым значениям января; одно решение на колонку для всех месяцев."""
    zc = set()
    for c in cols:
        vals = sorted(s["f"][c] for s in jan if s["f"][c] is not None)
        if vals and nearest_rank(vals, 0.33) == 0 and nearest_rank(vals, 0.67) == 0:
            zc.add(c)
    return zc


def sign_of(master, hyp, col):
    """Знак клетки: решение по гипотезе (R1_SIGN) → иначе знак колонки r1ext (одна семья — один знак)."""
    return master.R1_SIGN.get(hyp, {}).get(col) or (master.R1EXT_SIGN.get(col) if hyp not in master.R1_SIGN else None)


def make_spec(plan, out, which="x", hyps=None, qs=None, hashes=None):
    """plan_cells.jsonl → .r1spec. which=x: как tk202 (колонка/знак вне r1ext и ctx×keep_q); all: любая R1-клетка плана (эквивалентность, крайние q)."""
    sys.path.insert(0, os.path.join(HERE, "..", "registry"))
    import cellhash as ch, master
    n = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["hash", "hyp", "col", "sign", "keep_q", "ctx_axis", "ctx_q"])
        for line in open(plan, encoding="utf-8"):
            d = json.loads(line)
            if d["protocol"] != "P-14" or d["fraction"] != 1:
                continue
            av = json.loads(d["axes"])
            if "col" not in av or "keep_q" not in av or (hyps and d["hyp"] not in hyps) or (qs and str(av["keep_q"]) not in qs):
                continue
            col = av["col"]
            sign = sign_of(master, d["hyp"], col) if which == "all" else master.R1_SIGN.get(d["hyp"], {}).get(col)
            if sign is None:
                continue
            if which == "x" and "ctx_q" not in av and col in master.R1EXT_COLS and master.R1EXT_SIGN.get(col) == sign:
                continue   # уже посчитано r1ext (В-213)
            h = ch.cell_hash(json.loads(d["cfg"]))
            if h != d["cell_hash"]:
                sys.exit(f"хеш cfg {h} != cell_hash плана {d['cell_hash']} ({d['hyp']} {av})")
            if hashes and h not in hashes:
                continue
            w.writerow([h, d["hyp"], col, sign, av["keep_q"], CTX_AXIS.get(d["hyp"], "") if "ctx_q" in av else "", av.get("ctx_q", "")])
            n += 1
    print("spec", n, file=sys.stderr)


def regime(t46, mon, day, cache={}):
    k = (mon, day)
    if k not in cache:
        try:
            with open(f"{t46}/{mon}/home/alpha/epochs/e-{mon}/study/regime/{day}.csv", encoding="utf-8") as ih:
                cache[k] = {int(r["minute_ms"]): r for r in csv.DictReader(ih)}
        except FileNotFoundError:
            cache[k] = {}
    return cache[k]


def strata(thr, axis, mo, k):
    c = thr["ctx"][axis].get(mo)
    if c is None:
        return None
    return {1: (None, c["q20"]), 2: (c["q20"], c["q40"]), 3: (c["q40"], c["q60"]), 4: (c["q60"], c["q80"]), 5: (c["q80"], None)}[k]


def keep_signals(sigs, col, sign, t, bounds, axis, t46, mon):
    """Ключи сигналов клетки: значение определено и по знаку не хуже порога; для ctx — ещё в страте оси на минуте t0."""
    kept = []
    for s in sigs:
        v = s["f"][col]
        if v is None or (v < t if sign == "hi" else v > t):
            continue
        if bounds is not None:
            row = regime(t46, mon, s["key"][1]).get(s["t0"] - s["t0"] % 60000)
            x = row.get(REGIME_COL[axis], "") if row else ""
            if x == "" or (bounds[0] is not None and float(x) < bounds[0]) or (bounds[1] is not None and float(x) > bounds[1]):
                continue
        kept.append(s["key"])
    return kept


def apply(a):
    spec = list(csv.DictReader(open(a.spec, encoding="utf-8")))
    cols = sorted({r["col"] for r in spec})
    thr = json.load(open(a.thr, encoding="utf-8")) if a.thr else {"ctx": {}}
    want = a.months.split(",") if a.months else MONTHS
    last = max(MONTHS.index(m) for m in want)
    data = {m: load_month(a.pool, m, cols) for m in MONTHS[:last + 1]}   # история = все более ранние месяцы
    data = {m: v for m, v in data.items() if v}
    jan = data["jan"]
    zc = zero_mass_cols(jan, cols)
    summary = {"zero_mass_columns": sorted(zc), "months": {}}
    thr_rows = []
    for i, mon in enumerate(MONTHS):
        if mon not in want or mon not in data:
            continue
        hist = jan if mon == "jan" else [s for m in MONTHS[:i] if m in data for s in data[m]]
        mo = f"2026-{i + 1:02d}"
        rounds = {}
        for _, _, rp in day_files(a.pool, mon):
            for r in rows(rp):
                rounds.setdefault((r["symbol"], r["day_utc"], int(r["signal_index"])), r)
        odir = os.path.join(a.out, "cells", mon)
        os.makedirs(odir, exist_ok=True)
        tcache, nk = {}, {}
        for r in spec:
            c, q = r["col"], int(r["keep_q"])
            if c not in tcache:
                allv = sorted(s["f"][c] for s in hist if s["f"][c] is not None)
                tcache[c] = [v for v in allv if v != 0] if c in zc else allv
            if not tcache[c]:
                continue
            t = threshold(tcache[c], r["sign"], q)
            bounds = strata(thr, r["ctx_axis"], mo, int(r["ctx_q"][1:])) if r["ctx_axis"] else None
            if r["ctx_axis"] and bounds is None:
                continue   # нет порогов страт месяца
            kept = keep_signals(data[mon], c, r["sign"], t, bounds, r["ctx_axis"], a.t46, mon)
            nk[r["hash"]] = len(kept)
            thr_rows.append([mon, r["hash"], c, r["sign"], q, "nz" if c in zc else "all", repr(t), len(tcache[c])])
            with open(os.path.join(odir, f"{r['hash']}.csv"), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(OUT_HEAD)
                for k in kept:
                    rr = rounds.get(k, {})
                    w.writerow([k[0], k[1], k[2], rr.get("t0_ns", ""), rr.get("net_bps", ""), rr.get("reason", ""), rr.get("exit_ns", ""), rr.get("qty", "")])
        summary["months"][mon] = {"n_b1": len(data[mon]), "n_cells": len(nk), "n_kept": nk}
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open(os.path.join(a.out, "thresholds.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["month", "hash", "col", "sign", "keep_q", "kind", "threshold", "n_hist"])
        w.writerows(thr_rows)
    print("ok apply", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["spec", "apply"])
    ap.add_argument("--plan")
    ap.add_argument("--spec")
    ap.add_argument("--pool", default="/data/tk064/pool")
    ap.add_argument("--t46", default="/data/tk046")
    ap.add_argument("--thr")
    ap.add_argument("--out", required=True)
    ap.add_argument("--which", choices=["x", "all"], default="x", help="spec: x — как tk202 (вне r1ext и ctx); all — любая R1-клетка плана")
    ap.add_argument("--hyp", help="spec: гипотезы через запятую (Г-39,Г-04)")
    ap.add_argument("--q", help="spec: доли keep_q через запятую")
    ap.add_argument("--hash", help="spec: хеши через запятую")
    ap.add_argument("--months", help="apply: только эти месяцы (история — все более ранние), через запятую")
    a = ap.parse_args()
    if a.mode == "spec":
        split = lambda s: set(s.split(",")) if s else None
        make_spec(a.plan, a.out, a.which, split(a.hyp), split(a.q), split(a.hash))
    else:
        os.makedirs(a.out, exist_ok=True)
        apply(a)


if __name__ == "__main__":
    main()
