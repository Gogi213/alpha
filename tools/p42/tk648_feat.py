#!/usr/bin/env python3
"""П-42 §13.48 (TK-648): глубина книги Binance USD-M `bookDepth` как признак П-42 на h775 — данные и признаки кандидатов баз. НЕ читает P&L.
  python3 tk648_feat.py need                 — какие монето-сутки архива нужны: строки таблиц кандидатов h775 (окна wb / wc, обе стороны) с сигналом в ω → need.csv
  python3 tk648_feat.py dl [--workers 6]     — докачка нужных монето-суток в общий склад /data/p42-book/zip (готовые пропускаются) → cov-need.csv
  python3 tk648_feat.py probe [--workers 8]  — охват контрактов: есть ли архив у каждого из 775 контрактов на 15.09 / 15.10 / 12.11 (HEAD, без скачивания) → probe.csv
  python3 tk648_feat.py build                — признаки TK-595 (`bookdepth_feat.features_at`, без изменений) на всех строках таблиц wb / wc → feat/<окно>-<сторона>.csv
  python3 tk648_feat.py gate [--n 300]       — G-B2 (selftest TK-595) + G-B3 (усечение снимков позже sig − 30 с не меняет признаки) на ≥ 300 строках, зерно 648
ω — сутки сигнала 14.09–13.11 без 08.10 (архива нет у всех монет); сутки ≥ 14.11 не запрашиваются и не читаются (охрана `check_day`)."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bookdepth_dl as bdl  # noqa: E402
import bookdepth_feat as bf  # noqa: E402
import retitr_ftab as rfx  # noqa: E402
import tk604_stats as st  # noqa: E402

ROOT = "/data/p42-tk648"
BOOK = "/data/p42-book"
ZIP = os.path.join(BOOK, "zip")
FEAT34 = "/data/p42-tk634/feat/h775"
UNIV = "/data/p42-tk621/universe"
WINDOWS = (st.WB, st.WC)                       # ω целиком внутри зачёта wb (26.08–02.10) и wc (03.10–13.11); wa (до 25.08) ω не задевает
OMEGA_LO, OMEGA_HI = dt.date(2026, 9, 14), dt.date(2026, 11, 13)
HOLES = (dt.date(2026, 10, 8),)                # 404 у всех монет (TK-595, G-B1)
LAST_OK = dt.date(2026, 11, 13)
PROBE_DAYS = ("2026-09-15", "2026-10-15", "2026-11-12")
SIDES = ("L", "S")
FEATS = tuple(bf.FEATS)
EXTRA = ("bd_age_s", "bd_mode")                # возраст снимка на сигнале, с; режим метки суток снимка (A = 1, B = 0; G-B1)


def say(*a):
    print(*a, flush=True)


def q(*a):
    p = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def check_day(d):
    if d > LAST_OK:
        raise SystemExit("охрана: сутки %s позже 13.11 — не читаются (проверка вперёд)" % d)


def omega_days():
    out, d = [], OMEGA_LO
    while d <= OMEGA_HI:
        if d not in HOLES:
            out.append(d)
        d += dt.timedelta(1)
    return out


def in_omega(day):
    return OMEGA_LO <= day <= OMEGA_HI and day not in HOLES


def abs_s(w, rel_ms):
    return w.t0_ms / 1000.0 + np.asarray(rel_ms, dtype=np.int64) / 1000.0


def utc_day(s):
    return dt.datetime.fromtimestamp(float(s), dt.timezone.utc).date()


def table(wname, side):
    """Ключи строк таблицы кандидатов h775 (тот же порядок, что у `--ftab` TK-634 / TK-639) и столбец turn1h (f12)."""
    sym, ms, raw = rfx.read_ftab(os.path.join(FEAT34, "%s-%s.csv" % (wname, side)))
    return list(sym), np.asarray(ms, dtype=np.int64), np.asarray(raw["turn1h"], dtype=float)


def classes():
    c = {}
    for k in ("crypto", "tradfi"):
        for s in open(os.path.join(UNIV, "symbols-%s.txt" % k), encoding="utf-8").read().replace("\n", ",").split(","):
            if s:
                c[s] = k
    return c


# ---- need / dl / probe -------------------------------------------------------------------------------------------------------

def cmd_need(a):
    pairs, rows = set(), 0
    for w in WINDOWS:
        for side in SIDES:
            sym, ms, _ = table(w.name, side)
            for s_, t in zip(sym, abs_s(w, ms)):
                d = utc_day(t)
                if not in_omega(d):
                    continue
                rows += 1
                for x in (t, t - 3600):            # как `bookdepth_feat._one_sym`: сутки сигнала и сутки часом раньше
                    dd = utc_day(x)
                    check_day(dd)
                    pairs.add((s_, dd.isoformat()))
    with open(q("need.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,day\n")
        for s_, d in sorted(pairs):
            f.write("%s,%s\n" % (s_, d))
    have = sum(1 for s_, d in pairs if os.path.exists(os.path.join(ZIP, s_, "%s-bookDepth-%s.zip" % (s_, d))))
    say("need: строк кандидатов в ω %d (wb + wc, обе стороны, окна перекрываются); монето-суток %d, монет %d; уже в складе %d" % (rows, len(pairs), len({p[0] for p in pairs}), have))


def cmd_dl(a):
    pairs = [tuple(r) for r in csv.reader(open(q("need.csv"), encoding="utf-8"))][1:]
    for _, d in pairs:
        check_day(dt.date.fromisoformat(d))
    res = []
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(lambda p: bdl.fetch(BOOK, p[0], p[1]), pairs), 1):
            res.append(r)
            if i % 500 == 0:
                say("%d/%d" % (i, len(pairs)))
            if i % 200 == 0 and os.statvfs(BOOK).f_bavail * os.statvfs(BOOK).f_frsize < 45e9:
                raise SystemExit("сторож места: свободно < 45 ГБ")
    with open(q("cov-need.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,day,status,bytes\n")
        for r in res:
            f.write("%s,%s,%s,%d\n" % r)
    cnt = {}
    for r in res:
        cnt[r[2]] = cnt.get(r[2], 0) + 1
    say("dl: %s" % cnt)
    return 1 if any(k.startswith("err") for k in cnt) else 0


def head(sym, day, retries=3):
    name = "%s-bookDepth-%s.zip" % (sym, day)
    url = "%s/%s/%s" % (bdl.BASE, urllib.parse.quote(sym), urllib.parse.quote(name))
    err = "err"
    for _ in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as r:
                return sym, day, "ok", int(r.headers.get("Content-Length") or 0)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return sym, day, "404", 0
            err = "err:%d" % e.code
        except Exception as e:  # сеть, таймаут
            err = "err:%s" % type(e).__name__
    return sym, day, err, 0


def cmd_probe(a):
    cls = classes()
    contracts = {r["symbol"]: r for r in csv.DictReader(open(os.path.join(UNIV, "contracts.csv"), encoding="utf-8"))}
    jobs = [(s_, d) for s_ in sorted(cls) for d in PROBE_DAYS]
    with cf.ThreadPoolExecutor(a.workers) as ex:
        res = list(ex.map(lambda p: head(*p), jobs))
    with open(q("probe.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,class,day,status,bytes,first_agg_day,last_agg_day\n")
        for s_, d, stt, b in res:
            c = contracts.get(s_, {})
            f.write("%s,%s,%s,%s,%d,%s,%s\n" % (s_, cls[s_], d, stt, b, c.get("first_agg_day", ""), c.get("last_agg_day", "")))
    tab = {}
    for s_, d, stt, _ in res:
        c = contracts.get(s_, {})
        live = c.get("first_agg_day", "9") <= d <= c.get("last_agg_day", "0")          # контракт торговался в эти сутки (aggTrades есть)
        k = (cls[s_], d)
        t = tab.setdefault(k, dict(contracts=0, live=0, live_ok=0, ok=0, err=0))
        t["contracts"] += 1
        t["live"] += live
        t["live_ok"] += live and stt == "ok"
        t["ok"] += stt == "ok"
        t["err"] += stt.startswith("err")
    json.dump({"%s %s" % k: v for k, v in sorted(tab.items())}, open(q("res", "probe.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for k, v in sorted(tab.items()):
        say("probe %-6s %s: контрактов %d, торговались %d, у них архив есть %d (%.1f %%); архив есть всего %d; ошибок %d" % (
            k[0], k[1], v["contracts"], v["live"], v["live_ok"], 100 * v["live_ok"] / max(v["live"], 1), v["ok"], v["err"]))


# ---- признаки ----------------------------------------------------------------------------------------------------------------

def day_mode(t):
    """Режим метки по суткам (G-B1): A — метки на 2…6 с после узла 30-секундной сетки, B — на узле (0…1 с). Доля меток с `ts mod 30 ≥ 2` > 0,5 → A (1), иначе B (0)."""
    r = np.mod(np.round(t).astype(np.int64), 30)
    days = (np.floor(t / 86400.0)).astype(np.int64)
    out = {}
    for d in np.unique(days):
        m = days == d
        out[int(d)] = 1.0 if float((r[m] >= 2).mean()) > 0.5 else 0.0
    return out


def one_sym(args):
    sym, rows = args                                  # rows: [(row_id, sig_s, long_side, turn1h)]
    days = set()
    for _, s, _, _ in rows:
        for x in (s, s - 3600):
            d = utc_day(x)
            check_day(d)
            days.add(d.isoformat())
    tm = bf.load_sym(ZIP, sym, sorted(days))
    out = []
    modes = day_mode(tm[0]) if tm is not None else {}
    for rid, s, long_side, turn in rows:
        if tm is None:
            out.append((rid, {k: np.nan for k in FEATS + EXTRA}))
            continue
        f = bf.features_at(tm[0], tm[1], s, long_side, turn)
        i = int(np.searchsorted(tm[0], s - bf.LAG_S, side="right")) - 1
        age = s - tm[0][i] if i >= 0 else np.nan
        f["bd_age_s"] = float(age) if i >= 0 and age <= bf.MAX_AGE_S else np.nan
        f["bd_mode"] = modes.get(int(np.floor(tm[0][i] / 86400.0)), np.nan) if i >= 0 and age <= bf.MAX_AGE_S else np.nan
        out.append((rid, f))
    return out


def cmd_build(a):
    from concurrent.futures import ProcessPoolExecutor
    info = {}
    for w in WINDOWS:
        for side in SIDES:
            sym, ms, turn = table(w.name, side)
            s_abs = abs_s(w, ms)
            by = {}
            for i, (s_, t) in enumerate(zip(sym, s_abs)):
                if in_omega(utc_day(t)):
                    by.setdefault(s_, []).append((i, float(t), side == "L", float(turn[i])))
            vals = {k: np.full(len(sym), np.nan) for k in FEATS + EXTRA}
            with ProcessPoolExecutor(a.procs) as ex:
                for res in ex.map(one_sym, list(by.items()), chunksize=4):
                    for rid, f in res:
                        for k in FEATS + EXTRA:
                            vals[k][rid] = f[k]
            path = q("feat", "%s-%s.csv" % (w.name, side))
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("sym,sig_ms," + ",".join(FEATS + EXTRA) + "\n")
                for i in range(len(sym)):
                    f.write("%s,%d,%s\n" % (sym[i], int(ms[i]), ",".join("" if not np.isfinite(vals[k][i]) else repr(float(vals[k][i])) for k in FEATS + EXTRA)))
            n_om = sum(len(v) for v in by.values())
            info["%s-%s" % (w.name, side)] = dict(rows=len(sym), rows_omega=n_om, defined={k: int(np.isfinite(vals[k]).sum()) for k in FEATS + EXTRA}, sha256=st.sha256_file(path))
            say("build %s-%s: строк %d, в ω %d, определено %s" % (w.name, side, len(sym), n_om, {k: int(np.isfinite(vals[k]).sum()) for k in FEATS}))
    json.dump(info, open(q("feat", "info.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def cmd_gate(a):
    bf.selftest()                                     # G-B2: причинное правило `ts + 30 с ≤ sig`, зеркало шорта, дыра > 600 с
    rng = np.random.default_rng(648)
    cand = []
    for w in WINDOWS:
        for side in SIDES:
            sym, ms, turn = table(w.name, side)
            for i, (s_, t) in enumerate(zip(sym, abs_s(w, ms))):
                if in_omega(utc_day(t)):
                    cand.append((s_, float(t), side == "L", float(turn[i])))
    pick = [cand[i] for i in rng.choice(len(cand), size=min(a.n, len(cand)), replace=False)]
    same = differ = nodata = 0
    for s_, t, long_side, turn in pick:
        days = sorted({utc_day(x).isoformat() for x in (t, t - 3600)})
        tm = bf.load_sym(ZIP, s_, days)
        if tm is None:
            nodata += 1
            continue
        x = bf.features_at(tm[0], tm[1], t, long_side, turn)
        y = bf.features_at(tm[0], tm[1], t, long_side, turn, trunc=True)
        ok = all((np.isnan(x[k]) and np.isnan(y[k])) or x[k] == y[k] for k in FEATS)
        same += ok
        differ += not ok
    r = dict(n=len(pick), same=same, differ=differ, no_archive=nodata, seed=648)
    json.dump(r, open(q("gates", "gb3.json"), "w", encoding="utf-8"), indent=1)
    say("G-B3: %s" % r)
    return 1 if differ else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("need", "dl", "probe", "build", "gate"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--n", type=int, default=300)
    a = ap.parse_args()
    return {"need": cmd_need, "dl": cmd_dl, "probe": cmd_probe, "build": cmd_build, "gate": cmd_gate}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
