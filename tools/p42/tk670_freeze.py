#!/usr/bin/env python3
"""П-42 §13.70 (TK-670): заморозка вперёд кандидата `D_S` (E4 / D шорт, TK-668) — доказательства до коммита `tk670-frozen.json`; на srv, только чтение.
  proof  — суток ≥ 14.11 у D / шорт нет и не читалось: (1) деревья окон wa / wb / wc — максимальные сутки по ИМЕНАМ файлов (содержимое не открывается); (2) маркеры `READ-*` вперёд;
           (3) опись файлов результатов TK-668 (путь, размер, время изменения; содержимое не открывается) и диапазон суток каждого окна; (4) каталоги `*_lx1` где бы то ни было в `/data`
  alpha  — на ГОТОВЫХ сделках выборки (≤ 13.11, движок не запускается): `S_mk1` / `L_base` / `D_S` — описание, пересечение сигналов и корреляция суточных P&L; основание деления α
  python3 tk670_freeze.py proof [--out f.json] | alpha [--out f.json]"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", ".claude", "skills", "alpha-research", "scripts"))

LAST_DAY = datetime.date(2026, 11, 13)
WINDOWS = {"wa": ("2026-06-17", "2026-07-01", "2026-08-25"), "wb": ("2026-07-25", "2026-08-26", "2026-10-02"), "wc": ("2026-09-01", "2026-10-03", "2026-11-13")}   # T0, зачёт с, зачёт по
TRADE_END = 73                                             # последние сутки данных окна = T0 + 73
TREES = "/data/p42-tk621"
READ_DIRS = ("/data/p42-tk616/fwd", "/data/p42-tk626")
TK668 = "/data/p42-tk668"
SKIP_RESULT_DIRS = ("code", "tests", "__pycache__")
SCAN_ROOT = "/data"
SCAN_PRUNE = ("tick-store", "collect", "chron", "pages")   # склад тиков и коллекторы: каталогов результатов движка в них нет
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def utc(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tree_days():
    """Максимальные сутки по именам файлов деревьев окон (содержимое не открывается). Окно: данные до T0 + 73 ≤ 13.11."""
    out = {}
    for w, (t0, _lo, hi) in WINDOWS.items():
        last_data = datetime.date.fromisoformat(t0) + datetime.timedelta(days=TRADE_END)
        row = {"t0": t0, "credit_to": hi, "data_to": str(last_data), "data_to_le_13_11": last_data <= LAST_DAY}
        for sub in ("aggtrades", "klines"):
            base = os.path.join(TREES, w, sub)
            mx, mn, n, coins = None, None, 0, 0
            for sym in sorted(os.listdir(base)):
                p = os.path.join(base, sym)
                if not os.path.isdir(p):
                    continue
                coins += 1
                for name in os.listdir(p):
                    m = _DATE.search(name)
                    if m:
                        n += 1
                        dd = datetime.date.fromisoformat(m.group(1))
                        mx = dd if mx is None or dd > mx else mx
                        mn = dd if mn is None or dd < mn else mn
            row[sub] = {"coins": coins, "files": n, "first_day": str(mn), "last_day": str(mx), "ok_le_13_11": bool(mx is not None and mx <= LAST_DAY)}
        out[w] = row
    return out


def read_markers():
    """`READ-*` под каталогами чтения вперёд (обход без открытия файлов)."""
    found, seen = [], {}
    for root in READ_DIRS:
        seen[root] = os.path.isdir(root)
        for dp, _dn, fn in os.walk(root):
            for f in fn:
                if f.startswith("READ-"):
                    found.append(os.path.join(dp, f))
    return {"dirs": seen, "markers": found}


def result_files():
    """Опись файлов результатов TK-668: путь, размер, время изменения; содержимое не открывается."""
    rows, tops = [], {}
    for dp, dn, fn in os.walk(TK668):
        dn[:] = sorted(d for d in dn if d not in SKIP_RESULT_DIRS)
        for f in sorted(fn):
            p = os.path.join(dp, f)
            st = os.stat(p)
            rel = os.path.relpath(p, TK668).replace(os.sep, "/")
            rows.append((rel, st.st_size, st.st_mtime))
            t = tops.setdefault(rel.split("/")[0] if "/" in rel else ".", {"files": 0, "bytes": 0, "first_mtime": st.st_mtime, "last_mtime": st.st_mtime})
            t["files"] += 1
            t["bytes"] += st.st_size
            t["first_mtime"], t["last_mtime"] = min(t["first_mtime"], st.st_mtime), max(t["last_mtime"], st.st_mtime)
    ds = [{"path": r, "bytes": s, "mtime_utc": utc(m)} for r, s, m in rows if re.search(r"(^|/)S_lx1(/|$)", r)]
    return {"root": TK668, "files": len(rows), "by_top_dir": {k: {"files": v["files"], "bytes": v["bytes"], "first_mtime_utc": utc(v["first_mtime"]), "last_mtime_utc": utc(v["last_mtime"])} for k, v in sorted(tops.items())},
            "ds_files": ds, "ds_dirs": sorted({os.path.dirname(x["path"]) for x in ds})}


def lx1_dirs():
    """Каталоги клеток `S_lx1` / `L_lx1…3` на srv (обход до глубины 5, склады тиков и коллекторы пропущены)."""
    hits, base_depth = [], SCAN_ROOT.rstrip("/").count("/")
    for dp, dn, _fn in os.walk(SCAN_ROOT):
        depth = dp.count("/") - base_depth
        dn[:] = sorted(d for d in dn if d not in SCAN_PRUNE and not os.path.islink(os.path.join(dp, d))) if depth < 5 else []
        for d in dn:
            if re.search(r"^[SL]_lx[123]$", d):                 # ключ `lx` источника уровней TK-668; `lx4` старых каталогов — задержка
                hits.append(os.path.join(dp, d))
    return {"root": SCAN_ROOT, "max_depth": 5, "pruned": list(SCAN_PRUNE), "dirs": hits}


def cmd_proof(a):
    trees = tree_days()
    if not all(r["data_to_le_13_11"] and r["aggtrades"]["ok_le_13_11"] and r["klines"]["ok_le_13_11"] for r in trees.values()):
        raise SystemExit("STOP: в деревьях окон есть сутки позже 13.11")
    marks = read_markers()
    if marks["markers"]:
        raise SystemExit("STOP: маркеры чтения вперёд: %s" % marks["markers"][:5])
    res = result_files()
    lx = lx1_dirs()
    outside = [d for d in lx["dirs"] if not d.startswith(TK668 + "/")]
    out = {"now_utc": utc(datetime.datetime.now(datetime.timezone.utc).timestamp()), "last_day": str(LAST_DAY), "trees": trees, "read_markers": marks, "tk668_results": res,
           "lx1_dirs": lx, "lx1_dirs_outside_tk668": outside, "ok": not outside}
    return emit(out, a)


# ---- основание деления α -----------------------------------------------------------------------------------------------------------------

def cmd_alpha(a):
    import numpy as np
    import tk604_stats as st
    import tk641_report as t
    gmk = t.GMK
    t.CELLS["S_mk1"] = ("short", lambda w: "%s/out/%s/cd0838447e287/short-trades.csv" % (gmk, w))
    t.CELLS["D_S"] = ("short", lambda w: "%s/e4/cells/%s/S_lx1/short-trades.csv" % (TK668, w))
    names = ("S_base", "S_mk1", "D_S", "L_base", "L_n24", "L_mk2")
    t.guards()
    R = {c: t.load(c) for c in names}
    if any(r["day"] >= st.NDAYS for c in R for r in R[c]):
        raise SystemExit("STOP: сутки позже 13.11")
    desc = {c: t.describe(R[c]) for c in names}
    short, longs = ("S_base", "S_mk1", "D_S"), ("L_base", "L_n24", "L_mk2")
    pairs = [(x, y) for i, x in enumerate(short) for y in short[i + 1:]] + [(x, y) for i, x in enumerate(longs) for y in longs[i + 1:]] + [("D_S", "L_base"), ("S_mk1", "L_base"), ("S_base", "L_base")]
    ov = {}
    for x, y in pairs:
        same = (x in short) == (y in short)
        ov["%s|%s" % (x, y)] = t.overlap(R[x], R[y], same)
    D = {c: t.daily(R[c]) for c in names}
    corr = {c: {d: float(np.corrcoef(D[c], D[d])[0, 1]) for d in names} for c in names}
    # Σ P&L долей: что даёт S_mk1 сверх S_base (сигналы только S_mk1) и что даёт L_base
    out = {"windows": "01.07–13.11, 136 суток, h775, 500 $", "desc": {c: {k: desc[c][k] for k in ("n", "sum", "mean", "per_day", "sharpe", "lo90", "hi90", "rr", "stops", "maxdd")} for c in names},
           "overlap": ov, "corr_daily": corr, "note": "выборка, найдено на этих данных (D_S) / G-626c (S_mk1); движок не запускался; сутки позже 13.11 не читались"}
    return emit(out, a)


def emit(out, a):
    js = json.dumps(out, indent=1, ensure_ascii=False, default=float)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(js)
    print(js)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["proof", "alpha"])
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    return {"proof": cmd_proof, "alpha": cmd_alpha}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
