#!/usr/bin/env python3
"""TK-202 (В-227 пакет B, М1): клетки R1 для колонок вне r1ext и ctx×keep_q (Г-78/55/140) — фильтры на signals.csv пула, как tk064-r1cells.py.

  tk202-r1x.py spec  --plan docs/registry/plan_cells.jsonl --out spec.csv      # ПК: клетки «к счёту» (колонка+знак+q [+ ось/страта ctx]) из плана Летописи
  tk202-r1x.py apply --spec spec.csv --pool /data/tk064/pool --t46 /data/tk046 --thr tk202-thresholds.json --out DIR   # calc: DIR/cells/<мес>/<хеш>.csv

Порог месяца m = квантиль keep_q по определённым сигналам B1 ВСЕХ предыдущих полных месяцев (январь — по самому январю, --jan-self TK-115);
знак (П-14 §3.1 Q-3, R1_SIGN): hi — оставить верхние q %, lo — нижние; ближайший ранг; правило массы в нуле П-11 §4 (q33=q67=0 в январе → порог по ненулевым).
Меры в лотах (имена *_lots и trade_size_*) делятся на size_at_arm (lvl/<сутки>.csv); счётчики (nz_levels15, frontrun_levels) — как есть.
ctx: страта = значение оси (pool1h/btc1h, bps) на минуте t0 (floor, как touch_contexts) из <t46>/<мес>/home/alpha/epochs/e-<мес>/study/regime/<сутки>.csv
в границах q20/40/60/80 из tk202-thresholds.json (замкнутые интервалы; крайние — односторонние); порог keep_q — по ВСЕЙ истории B1 (не внутри страты); нет значения оси — сигнал отвергнут.
Формат клетки как в tk064-r1cells.py apply (с исходами из rounds.csv)."""
import argparse, csv, importlib.util, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(HERE, "tk064-r1cells.py"), os.path.join(HERE, "archive", "tk064-r1cells.py")):
    if os.path.isfile(_p):
        _spec = importlib.util.spec_from_file_location("r1c", _p)
        r1c = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(r1c)
        break
else:
    sys.exit("нет tk064-r1cells.py рядом")

CTX_AXIS = {"Г-78": "pool1h", "Г-55": "btc1h", "Г-140": "pool1h"}   # ось контекста гипотезы (решение Судьи 04:30: pool1h/btc1h)
REGIME_COL = {"pool1h": "pool_ret_1h_bps", "btc1h": "btc_ret_1h_bps", "btc4h": "btc_ret_4h_bps"}
COUNTS = {"nz_levels15", "frontrun_levels"}


def is_lots(c):
    return c not in COUNTS and (c.endswith("_lots") or "_lots_" in c or c.startswith("trade_size_"))


def make_spec(plan, out):
    sys.path.insert(0, os.path.join(HERE, "..", "registry"))
    import master
    n = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["hash", "hyp", "col", "sign", "keep_q", "ctx_axis", "ctx_q"])
        for line in open(plan, encoding="utf-8"):
            d = json.loads(line)
            if d["protocol"] != "P-14" or d["fraction"] != 1:
                continue
            av = json.loads(d["axes"])
            if "col" not in av or "keep_q" not in av or d["hyp"] not in master.R1_SIGN:
                continue
            col, sign = av["col"], master.R1_SIGN[d["hyp"]].get(av["col"])
            if sign is None:
                continue
            if "ctx_q" not in av and col in master.R1EXT_COLS and master.R1EXT_SIGN.get(col) == sign:
                continue   # уже посчитано r1ext (В-213)
            w.writerow([d["cell_hash"], d["hyp"], col, sign, av["keep_q"], CTX_AXIS.get(d["hyp"], "") if "ctx_q" in av else "", av.get("ctx_q", "")])
            n += 1
    print("spec", n, file=sys.stderr)


def load_month(pool, mon, cols):
    """-> [{key, t0_ms, f{col: float|None}}]: как tk064-r1cells.load_month, но с t0 и только нужными колонками."""
    lvl = {}
    import glob
    for p in glob.glob(os.path.join(pool, f"m-{mon}", "lvl", "*.csv")):
        for r in r1c.rows(p):
            lvl[(r["symbol"], int(r["arm_ms"]), int(r["price_tick"]))] = float(r["size_at_arm"])
    seen, sigs = set(), []
    for d, sp, _ in r1c.day_files(pool, mon):
        for r in r1c.rows(sp):
            key = (r["symbol"], r["day_utc"], int(r["signal_index"]))
            if key in seen:
                continue
            seen.add(key)
            t0 = int(r["t0_ns"]) // 1_000_000
            size = lvl.get((r["symbol"], t0, int(r["price_tick"])))
            f = {}
            for c in cols:
                v = r1c.num(r.get(c))
                if v is not None and is_lots(c):
                    v = v / size if size and size > 0 else None
                f[c] = v
            sigs.append({"key": key, "t0": t0, "f": f})
    return sigs


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
    lo, hi = {1: (None, c["q20"]), 2: (c["q20"], c["q40"]), 3: (c["q40"], c["q60"]), 4: (c["q60"], c["q80"]), 5: (c["q80"], None)}[k]
    return lo, hi


def apply(a):
    spec = list(csv.DictReader(open(a.spec, encoding="utf-8")))
    cols = sorted({r["col"] for r in spec})
    thr = json.load(open(a.thr, encoding="utf-8"))
    data = {m: load_month(a.pool, m, cols) for m in r1c.MONTHS}
    data = {m: v for m, v in data.items() if v}
    jan = data["jan"]
    zc = set()
    for c in cols:
        vals = sorted(s["f"][c] for s in jan if s["f"][c] is not None)
        if vals and r1c.nearest_rank(vals, 0.33) == 0 and r1c.nearest_rank(vals, 0.67) == 0:
            zc.add(c)
    summary = {"zero_mass_columns": sorted(zc), "months": {}}
    for i, mon in enumerate(r1c.MONTHS):
        if mon not in data:
            continue
        hist = jan if mon == "jan" else [s for m in r1c.MONTHS[:i] if m in data for s in data[m]]
        mo = f"2026-{i + 1:02d}"
        rounds = {}
        for d, _, rp in r1c.day_files(a.pool, mon):
            for r in r1c.rows(rp):
                rounds.setdefault((r["symbol"], r["day_utc"], int(r["signal_index"])), r)
        odir = os.path.join(a.out, "cells", mon)
        os.makedirs(odir, exist_ok=True)
        tcache, nk = {}, {}
        for r in spec:
            c, q = r["col"], int(r["keep_q"])
            if (c, "all") not in tcache:
                allv = sorted(s["f"][c] for s in hist if s["f"][c] is not None)
                tcache[(c, "all")] = allv
                tcache[(c, "nz")] = [v for v in allv if v != 0]
            src = tcache[(c, "nz" if c in zc else "all")]
            if not src:
                continue
            t = r1c.nearest_rank(src, 1 - q / 100) if r["sign"] == "hi" else r1c.nearest_rank(src, q / 100)
            bounds = strata(thr, r["ctx_axis"], mo, int(r["ctx_q"][1:])) if r["ctx_axis"] else None
            if r["ctx_axis"] and bounds is None:
                continue   # нет порогов страт месяца
            kept = []
            for s in data[mon]:
                v = s["f"][c]
                if v is None or (v < t if r["sign"] == "hi" else v > t):
                    continue
                if bounds is not None:
                    row = regime(a.t46, mon, s["key"][1]).get(s["t0"] - s["t0"] % 60000)
                    x = row.get(REGIME_COL[r["ctx_axis"]], "") if row else ""
                    if x == "" or (bounds[0] is not None and float(x) < bounds[0]) or (bounds[1] is not None and float(x) > bounds[1]):
                        continue
                kept.append(s["key"])
            nk[r["hash"]] = len(kept)
            with open(os.path.join(odir, f"{r['hash']}.csv"), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["symbol", "day_utc", "signal_index", "t0_ns", "net_bps", "reason", "exit_ns", "qty"])
                for k in kept:
                    rr = rounds.get(k, {})
                    w.writerow([k[0], k[1], k[2], rr.get("t0_ns", ""), rr.get("net_bps", ""), rr.get("reason", ""), rr.get("exit_ns", ""), rr.get("qty", "")])
        summary["months"][mon] = {"n_b1": len(data[mon]), "n_cells": len(nk), "n_kept": nk}
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
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
    a = ap.parse_args()
    if a.mode == "spec":
        make_spec(a.plan, a.out)
    else:
        os.makedirs(a.out, exist_ok=True)
        apply(a)


if __name__ == "__main__":
    main()
