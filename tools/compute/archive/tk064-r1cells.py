#!/usr/bin/env python3
"""TK-064 п.5: клетки R1 как фильтры на signals.csv пула (r2-spec §2 и §4 R1, П-11 §3).

  tk064-r1cells.py thresholds --pool /data/tk064/pool --out DIR     # e-thresholds-<мес>.csv: только колонки признаков, без исхода
  tk064-r1cells.py coverage   --pool /data/tk064/pool --out DIR     # coverage-r1.json: охват, масса в нуле (январь), группы совпавших; исходы НЕ читает
  tk064-r1cells.py apply      --pool /data/tk064/pool --out DIR     # cells/<мес>/<клетка>.csv (с исходами из rounds.csv)

Порог месяца m = квантиль по определённым сигналам B1 ВСЕХ предыдущих полных месяцев (янв — калибровка, клетки с фев).
Меры *_lots делятся на size_at_arm (lvl/<сутки>.csv, лоты книги — безразмерно). Пустая клетка = не определено:
пороговая клетка её отвергает, «пропуск»-клетки оставляют. Квантиль — ближайший ранг без интерполяции.
"""
import argparse, csv, glob, json, math, os, sys
from collections import defaultdict

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct"]
LEVELS = [80, 67, 50, 33, 20]  # доля «оставляем»
SET = "t-bid-btc4h-q1"

# семья -> (колонки-варианты, делить на level_qty, +1 больше лучше / -1 меньше лучше)
FAM = {
    "g59": ([f"tape_press_lots_{w}s" for w in (15, 30, 60)], True, -1),
    "g75": (["tape_all_lots_15s"], True, +1),
    "g70": (["vpin_bp"], False, -1),
    "g71": ([f"sign_ac_{w}s_bp" for w in (15, 60)], False, -1),
    "g68": ([f"obi{n}_bp" for n in (1, 5, 10, 50)], False, +1),
    "g69": ([f"ofi_{w}s_lots" for w in (10, 60)], True, +1),
    "g139": (["obi50_bp"], False, -1),
    "g67": ([f"best_flips_{w}s" for w in (15, 60)], False, -1),
    "g63": (["frontrun_delta_10s_lots"], True, +1),
    "g35": (["front_add_max_lots"], True, -1),
    "g42": (["size_share15_bp"], False, +1),
    "g43": ([f"best_move_{w}s_cbps" for w in (1, 10)], False, +1),
    "g44": (["opp_wall_ratio_bp"], False, +1),
    "g82": ([f"tape_burst_press_{w}s_bp" for w in (15, 30, 60)], False, -1),
    "g04": (["wall_add_max_lots"], True, +1),
    "g39": (["cancel_life_lots"], True, -1),
}
ZERO_FAMS_COLS = sorted({c for cols, _, _ in FAM.values() for c in cols})
BIN = {  # клетка -> (колонка, предикат «оставить»; None = не определено -> оставить)
    "g100-micro0": ("micro_off_cbps", lambda v: v is not None and v > 0),
    "g34-notmoved": ("born_shift_cbps", lambda v: v is None or v >= 0),
    "g40-retest": ("prev_death_outcome", lambda v: v == 1),
    "g58-cancel0": ("cancel_60m_lots", lambda v: v is None or v > 0),
    "g05-c3": ("cancel_3s_lots", lambda v: v is None or v <= 0),
    "g05-c1": ("cancel_1s_lots", lambda v: v is None or v <= 0),
    "g61-le60": ("since_far_ms", lambda v: v is not None and v <= 60000),
    "g61-5to20": ("since_far_ms", lambda v: v is not None and 5000 <= v <= 20000),
    "g06-slow": ("since_far_ms", lambda v: v is not None and v > 60000),
}
FEATS = sorted({c for cols, _, _ in FAM.values() for c in cols} | {c for c, _ in BIN.values()}
               | {"obi5_bp", "ofi_60s_lots", "sign_ac_60s_bp", "tape_burst_press_30s_bp", "tape_press_avg_30s_e2",
                  "since_far_ms", "tape_press_lots_30s"})
LOTS = {c for cols, d, _ in FAM.values() if d for c in cols} | {"ofi_60s_lots", "tape_press_lots_30s"}


def rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        lines = [l for l in f if not l.startswith("#")]
    return list(csv.DictReader(lines))


def day_files(pool, mon):
    """signals + signals-retry месяца: (сутки, путь signals.csv, путь rounds.csv) — объединение, дубли по ключу снимает load_month."""
    out = []
    for sub in ("signals", "signals-retry"):
        for d in sorted(os.listdir(os.path.join(pool, f"m-{mon}", sub)) if os.path.isdir(os.path.join(pool, f"m-{mon}", sub)) else []):
            p = os.path.join(pool, f"m-{mon}", sub, d, SET)
            if os.path.isfile(os.path.join(p, "signals.csv")):
                out.append((d, os.path.join(p, "signals.csv"), os.path.join(p, "rounds.csv")))
    return out


def num(s):
    return None if s is None or s == "" else float(s)


def load_month(pool, mon):
    """-> список сигналов: dict(key, feats{col: float|None}); size_at_arm подставлен; без колонок исхода."""
    lvl, dups = {}, set()
    for p in glob.glob(os.path.join(pool, f"m-{mon}", "lvl", "*.csv")):
        for r in rows(p):
            k = (r["symbol"], int(r["arm_ms"]), int(r["price_tick"]))
            if k in lvl:
                dups.add(k)
            lvl[k] = float(r["size_at_arm"])
    seen, sigs = set(), []
    for d, sp, _ in day_files(pool, mon):
        for r in rows(sp):
            key = (r["symbol"], r["day_utc"], int(r["signal_index"]))
            if key in seen:
                continue
            seen.add(key)
            size = lvl.get((r["symbol"], int(r["t0_ns"]) // 1_000_000, int(r["price_tick"])))
            f = {}
            for c in FEATS:
                v = num(r.get(c))
                if v is not None and c in LOTS:
                    v = v / size if size and size > 0 else None
                f[c] = v
            sigs.append({"key": key, "f": f, "lvl_ok": size is not None})
    return sigs, len(dups)


def zero_cols(jan):
    """П-11 §4: масса в нуле = терциль вырождается (q33 = q67 = 0) по определённым значениям января-калибровки; одно решение на все месяцы."""
    out, info = set(), {}
    for c in ZERO_FAMS_COLS:
        vals = sorted(s["f"][c] for s in jan if s["f"][c] is not None)
        if not vals:
            info[c] = {"jan_n_defined": 0, "zero_form": False}
            continue
        q33, q67 = nearest_rank(vals, 0.33), nearest_rank(vals, 0.67)
        z = q33 == 0 and q67 == 0
        info[c] = {"jan_n_defined": len(vals), "jan_zero_share": round(sum(1 for v in vals if v == 0) / len(vals), 4),
                   "q33": q33, "q67": q67, "zero_form": z}
        if z:
            out.add(c)
    return out, info


def nearest_rank(sorted_vals, q):
    n = len(sorted_vals)
    return sorted_vals[min(n, max(1, math.ceil(q * n))) - 1]


def thresholds(history):
    """history: сигналы предыдущих месяцев -> {(колонка, доля): порог по ненулевым определённым}, {колонка: (n_def, n_nz)}."""
    thr, cnt = {}, {}
    for c in FEATS:
        vals = sorted(s["f"][c] for s in history if s["f"][c] is not None)
        nz = [v for v in vals if v != 0]
        cnt[c] = (len(vals), len(nz))
        for p in LEVELS:
            for kind, src in (("all", vals), ("nz", nz)):
                if src:
                    thr[(c, p, kind, "lo")] = nearest_rank(src, p / 100)  # лучшие = малые значения
                    thr[(c, p, kind, "hi")] = nearest_rank(src, 1 - p / 100) if p < 100 else src[0]  # лучшие = большие
    return thr, cnt


def cells_for_month(thr, zc):
    """-> {имя клетки: предикат(feats)}; без порога (нет истории по колонке) клетка не строится."""
    cells = {}

    def add(name, fn):
        cells[name] = fn

    for fam, (cols, _, sign) in FAM.items():
        for c in cols:
            if fam == "g82" and c.endswith("60s_bp"):
                continue  # колонка burst на 60 с не заведена в R1
            tag = fam if len(cols) == 1 else f"{fam}-{c}"
            for p in LEVELS:
                kind = "nz" if c in zc else "all"
                key = (c, p, kind, "hi" if sign > 0 else "lo")
                if key not in thr:
                    continue
                t = thr[key]
                if sign > 0:
                    add(f"{tag}-k{p}", lambda f, c=c, t=t: f[c] is not None and f[c] >= t)
                else:
                    add(f"{tag}-k{p}", lambda f, c=c, t=t: f[c] is not None and f[c] <= t)
            if c in zc:
                if sign > 0:
                    add(f"{tag}-nz", lambda f, c=c: f[c] is not None and f[c] > 0)
                else:
                    add(f"{tag}-z0", lambda f, c=c: f[c] is not None and f[c] == 0)
    for name, (c, fn) in BIN.items():
        add(name, lambda f, c=c, fn=fn: fn(f[c]))
    for p in LEVELS:
        k = lambda c, d, p=p: thr.get((c, p, "all", d))
        a, b, d_ = k("obi5_bp", "hi"), k("ofi_60s_lots", "hi"), k("sign_ac_60s_bp", "lo")
        if None not in (a, b, d_):
            add(f"g73-conj-k{p}", lambda f, a=a, b=b, d_=d_: None not in (f["obi5_bp"], f["ofi_60s_lots"], f["sign_ac_60s_bp"])
                and f["obi5_bp"] >= a and f["ofi_60s_lots"] >= b and f["sign_ac_60s_bp"] <= d_)
        # пропуск «худшей» комбинации: хвосты доли (100−p) %, т.е. то, что клетка k{p} не оставила бы
        w = thr.get(("tape_burst_press_30s_bp", p, "all", "lo"))  # граница лучших = малых; худший хвост — выше неё
        e = thr.get(("tape_press_avg_30s_e2", 100 - p, "all", "lo")) if p < 100 else None
        if w is not None and e is not None:
            add(f"g83-conj-k{p}", lambda f, w=w, e=e: not (f["tape_burst_press_30s_bp"] is not None and f["tape_press_avg_30s_e2"] is not None
                and f["tape_burst_press_30s_bp"] > w and f["tape_press_avg_30s_e2"] <= e))
        pr = thr.get(("tape_press_lots_30s", 100 - p, "all", "lo")) if p < 100 else None
        if pr is not None:
            add(f"g64-drift-k{p}", lambda f, pr=pr: not (f["since_far_ms"] is not None and f["since_far_ms"] > 60000
                and f["tape_press_lots_30s"] is not None and f["tape_press_lots_30s"] <= pr))
    return cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["thresholds", "coverage", "apply"])
    ap.add_argument("--pool", default="/data/tk064/pool")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jan-self", action="store_true",
                    help="TK-115, решение Судьи 09.10 (PRECEDENTS 61b2aabd): только январь, порог по B1-сигналам самого января (признаки, без исходов)")
    a = ap.parse_args()
    months = ["jan"] if a.jan_self else MONTHS
    os.makedirs(a.out, exist_ok=True)
    loaded = {m: load_month(a.pool, m) for m in months}
    data = {m: v[0] for m, v in loaded.items()}
    zc, zinfo = zero_cols(data["jan"])
    cov = {"zero_mass_rule": "П-11 §4: q33 = q67 = 0 по определённым значениям января-калибровки; одно решение на все месяцы",
           "zero_mass_columns": sorted(zc), "zero_mass_by_column": zinfo,
           "defined_share": {c: {m: round(sum(1 for s in data[m] if s["f"][c] is not None) / len(data[m]), 4) if data[m] else None
                                 for m in months} for c in FEATS},
           "undefined_lt30pct": {m: sorted(c for c in FEATS if data[m] and sum(1 for s in data[m] if s["f"][c] is not None) / len(data[m]) < 0.30)
                                 for m in months},
           "lvl_duplicate_keys": {m: loaded[m][1] for m in months},
           "new_instruments_rows": {m: {sym: sum(1 for s in data[m] if s["key"][0] == sym) for sym in ("TRXUSDT", "XAUUSDT", "CLUSDT")}
                                    for m in months},
           "months": {}}
    for i, mon in enumerate(MONTHS):
        if a.jan_self != (mon == "jan"):
            continue  # без --jan-self январь — только калибровка; с ним — только январь
        hist = data["jan"] if a.jan_self else [s for m in MONTHS[:i] for s in data[m]]
        thr, cnt = thresholds(hist)
        if a.mode == "thresholds":
            with open(os.path.join(a.out, f"e-thresholds-{mon}.csv"), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["col", "keep_pct", "kind", "dir", "threshold", "n_defined_hist", "n_nonzero_hist"])
                for (c, p, kind, d), t in sorted(thr.items()):
                    w.writerow([c, p, kind, d, repr(t), cnt[c][0], cnt[c][1]])
            continue
        cells = cells_for_month(thr, zc)
        sigs = data[mon]
        odir = os.path.join(a.out, "cells", mon)
        rounds = {}
        if a.mode == "apply":
            os.makedirs(odir, exist_ok=True)
            for d, _, rp in day_files(a.pool, mon):
                for r in rows(rp):
                    rounds.setdefault((r["symbol"], r["day_utc"], int(r["signal_index"])), r)
        kept_sets, n_kept = {}, {}
        for name, fn in sorted(cells.items()):
            kept = [s["key"] for s in sigs if fn(s["f"])]
            kept_sets.setdefault(tuple(kept), []).append(name)
            n_kept[name] = len(kept)
            if a.mode != "apply":
                continue
            with open(os.path.join(odir, f"{name}.csv"), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["symbol", "day_utc", "signal_index", "t0_ns", "net_bps", "reason", "exit_ns", "qty"])
                for k in kept:
                    r = rounds.get(k, {})
                    w.writerow([k[0], k[1], k[2], r.get("t0_ns", ""), r.get("net_bps", ""), r.get("reason", ""), r.get("exit_ns", ""), r.get("qty", "")])
        cov["months"][mon] = {"n_b1": len(sigs), "n_lvl_missing": sum(1 for s in sigs if not s["lvl_ok"]),
                              "n_cells": len(cells), "n_distinct_keep_sets": len(kept_sets),
                              "identical_cells": [v for v in kept_sets.values() if len(v) > 1], "n_kept": n_kept}
    if a.mode == "coverage":
        with open(os.path.join(a.out, "coverage-r1.json"), "w", encoding="utf-8") as f:
            json.dump(cov, f, ensure_ascii=False, indent=1)
    print("ok", a.mode, file=sys.stderr)


if __name__ == "__main__":
    main()
