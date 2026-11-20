#!/usr/bin/env python3
"""П-42 §13.37 (TK-639): число сделок и оборот монеты в окнах 1 / 6 / 12 / 24 ч на кандидатах баз h775 / o566 (Rust `alpha-feat tsize`) и ворота G-n39, G-reg. P&L НЕ читает.
  python3 tk639_feat.py build --uni h775|o566 --window wa|wb|wc --side L|S [--procs 4]  — /data/p42-tk639/feat/<вс>/<окно>-<сторона>.csv (+ .info.json)
  python3 tk639_feat.py gate [--k 150] [--edge 40] [--min 200] [--seed 639]                          — G-n39 (независимый пересчёт по zip), G-reg (n60 / n1440 = n60 / n24 TK-638 на всех строках), контекст (оборот против turn1h / turn24h f12) → res/gate-n39.json
Причинность: окна — минуты строго раньше минуты сигнала t = sig_ms // 60000: [(t − W)·60 000, t·60 000), W = 60 / 360 / 720 / 1 440 (как `tcount` TK-638 и ключи пула `tick`)."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import random
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ROOT = "/data/p42-tk639"
FEAT34 = "/data/p42-tk634/feat"
FEAT38 = "/data/p42-tk638/feat"
BIN = os.environ.get("TK639_BIN", "/data/p42-tk639/bin/alpha-feat")
STORE = "/data/p42-tk599/tick-store"
MIN_MS = 60_000
DAY_MS = 86_400_000
WIN_MIN = (60, 360, 720, 1440)
SIDES = ("L", "S")
UNIS = ("h775", "o566")
HEAD = ["sym", "sig_ms"] + ["n%d" % w for w in WIN_MIN] + ["v%d" % w for w in WIN_MIN] + ["m%d" % w for w in WIN_MIN]
RTOL = 1e-9       # G-n39: относительное расхождение оборота Rust и Python


def feat_path(root, uni, wname, side):
    return os.path.join(root, uni, "%s-%s.csv" % (wname, side))


def read_sizes(path):
    """Выход tsize: (список монет, {столбец: массив}); n — int64, v — float64, m — bool."""
    rows = []
    with open(path, encoding="utf-8") as f:
        head = f.readline().strip().split(",")
        if head != HEAD:
            raise ValueError("%s: заголовок %r" % (path, head))
        for line in f:
            rows.append(line.rstrip("\n").split(","))
    col = {h: [r[i] for r in rows] for i, h in enumerate(HEAD)}
    out = dict(sig_ms=np.array(col["sig_ms"], dtype=np.int64))
    for w in WIN_MIN:
        out["n%d" % w] = np.array(col["n%d" % w], dtype=np.int64)
        out["v%d" % w] = np.array(col["v%d" % w], dtype=np.float64)
        out["m%d" % w] = np.array(col["m%d" % w], dtype=np.int64).astype(bool)
    return col["sym"], out


def cmd_build(a):
    import tk604_stats as st
    import tk625_run as r5
    u = r5.Uni(a.uni)
    w = next(x for x in st.TRADE_WINDOWS if x.name == a.window)
    src = feat_path(FEAT34, u.name, w.name, a.side)
    out = feat_path(os.path.join(ROOT, "feat"), u.name, w.name, a.side)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    trades = os.path.join(u.wdir(w.name), "aggtrades")
    md5 = hashlib.md5(open(BIN, "rb").read()).hexdigest()
    cmd = [BIN, "tsize", "--eps", src, "--out", out, "--base-date", w.t0, "--trades", trades, "--store", STORE, "--threads", str(a.procs)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit("tsize rc=%d: %s" % (p.returncode, p.stderr[-2000:]))
    sym, c = read_sizes(out)
    with open(src, encoding="utf-8") as f:
        n_src = sum(1 for _ in f) - 1
    if len(sym) != n_src:
        sys.exit("строк в выходе %d ≠ кандидатов %d" % (len(sym), n_src))
    info = dict(uni=u.name, window=w.name, side=a.side, rows=len(sym), src=src, base_date=w.t0, trades=trades, bin_md5=md5, **{"m%d_rows" % k: int(c["m%d" % k].sum()) for k in WIN_MIN},
                summary=p.stdout.strip()[-1500:], sha256=hashlib.sha256(open(out, "rb").read()).hexdigest())
    json.dump(info, open(out + ".info.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in info.items() if k != "summary"}, ensure_ascii=False))


# ---- ворота -----------------------------------------------------------------------------------------------------------------

def date_str(abs_day):
    return (datetime.date(1970, 1, 1) + datetime.timedelta(days=int(abs_day))).isoformat()


def day_arrays(root, sym, abs_day, cache):
    """(времена мс, обороты price·quantity) суток из zip независимо от Rust: `unzip -p | cut -d, -f2,3,6`; None — файла нет."""
    key = (root, sym, abs_day)
    if key in cache:
        return cache[key]
    name = "%s-aggTrades-%s" % (sym, date_str(abs_day))
    path = os.path.join(root, sym, name + ".zip")
    if not os.path.exists(path):
        cache[key] = None
        return None
    p = subprocess.run("unzip -p '%s' '%s.csv' | tail -n +2 | cut -d, -f2,3,6 | tr '\\n' ','" % (path, name), shell=True, capture_output=True, check=True)
    txt = p.stdout.decode("ascii").strip(",")
    if not txt:
        cache[key] = (np.zeros(0, dtype=np.int64), np.zeros(0))
        return cache[key]
    x = np.array(txt.split(","), dtype=np.float64).reshape(-1, 3)
    cache[key] = (x[:, 2].astype(np.int64), x[:, 0] * x[:, 1])
    return cache[key]


def recount(root, sym, sig_abs, cache):
    """({W: (n, v, m)}) по определению п. 4: минуты строго раньше минуты сигнала; файла суток, задетых окном, нет → m = 1."""
    end = sig_abs // MIN_MS * MIN_MS
    out = {}
    for w in WIN_MIN:
        lo = end - w * MIN_MS
        n, v, miss = 0, 0.0, False
        for d in range(lo // DAY_MS, (end - 1) // DAY_MS + 1):
            day = day_arrays(root, sym, d, cache)
            if day is None:
                miss = True
                continue
            t, x = day
            sel = (t >= lo) & (t < end)
            n += int(sel.sum())
            v += float(x[sel].sum())
        out[w] = (n, v, miss)
    return out


def rel_close(a, b):
    return abs(a - b) <= RTOL * max(1.0, abs(a), abs(b))


def cmd_gate(a):
    import retitr_ftab as rfx
    import tk604_stats as st
    import tk625_run as r5
    import tk638_feat as t38
    rng = random.Random(a.seed)
    res = dict(seed=a.seed, k=a.k, edge=a.edge, min=a.min, rtol=RTOL, unis={})
    ok_all = True
    for un in UNIS:
        u = r5.Uni(un)
        rows = []          # (окно, сторона, строка таблицы, sig_ms абсолютный)
        tabs = {}
        reg = dict(rows=0, bad_n60=0, bad_n1440=0, bad_m60=0, bad_m1440=0, bad_keys=0)
        undefined = {w: 0 for w in WIN_MIN}
        miss = {w: 0 for w in WIN_MIN}
        total = 0
        ctx = dict(n=0, rel1h=[], rel24h=[])
        cov = {}
        for w in st.TRADE_WINDOWS:
            for s in SIDES:
                sym, ms, raw = rfx.read_ftab(feat_path(FEAT34, un, w.name, s))
                csym, c = read_sizes(feat_path(os.path.join(ROOT, "feat"), un, w.name, s))
                if list(sym) != list(csym) or not np.array_equal(ms, c["sig_ms"]):
                    reg["bad_keys"] += 1
                    sys.exit("%s %s-%s: ключи tsize ≠ таблица признаков f12" % (un, w.name, s))
                ksym, k38 = t38.read_counts(feat_path(FEAT38, un, w.name, s))
                if list(ksym) != list(csym) or not np.array_equal(k38["sig_ms"], c["sig_ms"]):
                    reg["bad_keys"] += 1
                tabs[(w.name, s)] = (sym, ms, raw, c)
                n = len(sym)
                total += n
                reg["rows"] += n
                reg["bad_n60"] += int((c["n60"] != k38["n60"]).sum())
                reg["bad_n1440"] += int((c["n1440"] != k38["n24"]).sum())
                reg["bad_m60"] += int((c["m60"] != k38["m60"]).sum())
                reg["bad_m1440"] += int((c["m1440"] != k38["m24"]).sum())
                for k in WIN_MIN:
                    miss[k] += int(c["m%d" % k].sum())
                    undefined[k] += int((c["n%d" % k] == 0).sum())
                ok1 = ~c["m60"] & (c["n60"] > 0) & np.isfinite(raw["turn1h"]) & (raw["turn1h"] > 0)
                ok24 = ~c["m1440"] & (c["n1440"] > 0) & np.isfinite(raw["turn24h"]) & (raw["turn24h"] > 0)
                ctx["n"] += n
                ctx["rel1h"].append(np.abs(c["v60"][ok1] / raw["turn1h"][ok1] - 1.0))
                ctx["rel24h"].append(np.abs(c["v1440"][ok24] / raw["turn24h"][ok24] - 1.0))
                absms = ms + w.t0_ms
                mon = np.array([datetime.datetime.fromtimestamp(x / 1000, datetime.timezone.utc).strftime("%Y-%m") for x in absms])
                for m in sorted(set(mon)):
                    kk = cov.setdefault(m, dict(rows=0, **{"m%d" % k: 0 for k in WIN_MIN}))
                    sel = mon == m
                    kk["rows"] += int(sel.sum())
                    for k in WIN_MIN:
                        kk["m%d" % k] += int((c["m%d" % k] & sel).sum())
                for i in range(n):
                    rows.append((w.name, s, i, int(absms[i])))
        # выбор: k случайных + edge у начала суток (первые 12 ч 1 мин UTC — окна 1 / 6 / 12 ч переходят границу суток)
        pool_all = list(range(len(rows)))
        pool_edge = [i for i in pool_all if rows[i][3] % DAY_MS < (12 * 60 + 1) * MIN_MS]
        pick = set(rng.sample(pool_all, min(a.k, len(pool_all))))
        pick |= set(rng.sample(pool_edge, min(a.edge, len(pool_edge))))
        while len(pick) < min(a.min, len(pool_all)):          # протокол п. 7: ≥ 200 различных кандидатов на вселенную (выборки могут пересечься)
            pick.add(rng.choice(pool_all))
        cache, bad, checked = {}, [], 0
        max_rel = 0.0
        for i in sorted(pick):
            wname, s, j, sig_abs = rows[i]
            sym, ms, raw, c = tabs[(wname, s)]
            root = os.path.join(u.wdir(wname), "aggtrades")
            py = recount(root, sym[j], sig_abs, cache)
            checked += 1
            for k in WIN_MIN:
                n_py, v_py, m_py = py[k]
                got = (int(c["n%d" % k][j]), float(c["v%d" % k][j]), bool(c["m%d" % k][j]))
                if got[0] != n_py or got[2] != m_py or not rel_close(got[1], v_py):
                    bad.append(dict(window=wname, side=s, sym=sym[j], sig_ms=int(ms[j]), W=k, rust=got, py=(n_py, v_py, m_py)))
                max_rel = max(max_rel, abs(got[1] - v_py) / max(1.0, abs(v_py)))
            if len(cache) > 400:
                cache.clear()
        reg_ok = reg["bad_keys"] == 0 and reg["bad_n60"] == 0 and reg["bad_n1440"] == 0 and reg["bad_m60"] == 0 and reg["bad_m1440"] == 0
        ok = not bad and reg_ok
        ok_all &= ok
        r1, r24 = np.concatenate(ctx["rel1h"]), np.concatenate(ctx["rel24h"])
        res["unis"][un] = dict(checked=checked, edge_pool=len(pool_edge), mismatches=len(bad), first_bad=bad[:5], max_rel_v=max_rel, ok_gn39=not bad, reg=reg, ok_reg=reg_ok,
                               rows=total, missing_m=miss, n_zero=undefined, coverage_by_month=cov,
                               ctx_turn=dict(v60_vs_turn1h=dict(compared=int(len(r1)), share_within_1pct=round(float((r1 <= 0.01).mean()), 4), median_rel=float(np.median(r1)), max_rel=float(r1.max())),
                                             v1440_vs_turn24h=dict(compared=int(len(r24)), share_within_1pct=round(float((r24 <= 0.01).mean()), 4), median_rel=float(np.median(r24)), max_rel=float(r24.max()))),
                               ok=ok)
        print("G-n39 %s: сверено %d (из них у границы суток ≤ %d), расхождений %d, макс. отн. расхождение оборота %.2e; G-reg: строк %d, n60 ≠ %d, n1440 ≠ %d, m ≠ %d / %d, ключей ≠ %d; m=1 по окнам %s, n=0 %s; "
              "контекст v60 / turn1h: ≤ 1 %% у %.1f %%, max %.3g; v1440 / turn24h: ≤ 1 %% у %.1f %%, max %.3g" % (
                  un, checked, a.edge, len(bad), max_rel, reg["rows"], reg["bad_n60"], reg["bad_n1440"], reg["bad_m60"], reg["bad_m1440"], reg["bad_keys"], miss, undefined,
                  100 * float((r1 <= 0.01).mean()), float(r1.max()), 100 * float((r24 <= 0.01).mean()), float(r24.max())), flush=True)
    res["ok"] = ok_all
    os.makedirs(os.path.join(ROOT, "res"), exist_ok=True)
    json.dump(res, open(os.path.join(ROOT, "res", "gate-n39.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("G-n39 / G-reg итог:", "PASS" if ok_all else "СТОП (расхождение)")
    sys.exit(0 if ok_all else 3)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--uni", required=True, choices=UNIS)
    b.add_argument("--window", required=True, choices=("wa", "wb", "wc"))
    b.add_argument("--side", required=True, choices=SIDES)
    b.add_argument("--procs", type=int, default=4)
    g = sp.add_parser("gate")
    g.add_argument("--k", type=int, default=150)
    g.add_argument("--edge", type=int, default=40)
    g.add_argument("--seed", type=int, default=639)
    g.add_argument("--min", type=int, default=200, help="не меньше стольких различных кандидатов на вселенную (протокол п. 7)")
    a = ap.parse_args()
    {"build": cmd_build, "gate": cmd_gate}[a.cmd](a)


if __name__ == "__main__":
    main()
