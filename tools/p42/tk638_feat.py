#!/usr/bin/env python3
"""П-42 §13.36 (TK-638): число сделок монеты n60 / n24 на кандидатах баз h775 / o566 (Rust `alpha-feat tcount`) и ворота G-n, G-turn. P&L НЕ читает.
  python3 tk638_feat.py build --uni h775|o566 --window wa|wb|wc --side L|S [--procs 4]  — /data/p42-tk638/feat/<вс>/<окно>-<сторона>.csv (+ .info.json)
  python3 tk638_feat.py gate [--k 120] [--edge 30] [--seed 638]                          — G-n (независимый пересчёт по zip, точное равенство; контекст tpm60·60; покрытие n24; m60 / m24) и G-turn → res/gate-n.json
Причинность: окна — минуты строго раньше минуты сигнала t = sig_ms // 60000: [(t−60)·60 000, t·60 000) и [(t−1 440)·60 000, t·60 000) (как пул `tick::candidates`)."""
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

ROOT = "/data/p42-tk638"
FEAT34 = "/data/p42-tk634/feat"
BIN = os.environ.get("TK638_BIN", "/data/p42-tk638/bin/alpha-feat")
STORE = "/data/p42-tk599/tick-store"
MIN_MS = 60_000
DAY_MS = 86_400_000
SIDES = ("L", "S")
UNIS = ("h775", "o566")
TURN1H_MIN = 0.5e6      # ключ базы t1h = 0,5 млн $ (обе вселенные)
TURN24H_MIN = 12e6      # ключ базы t24 = 12 млн $ (только h775; у o566 ключа нет)


def feat_path(root, uni, wname, side):
    return os.path.join(root, uni, "%s-%s.csv" % (wname, side))


def read_counts(path):
    """Выход tcount: ({столбец: массив}, список монет); n60 / n24 — int64, m60 / m24 — bool."""
    sym, ms, n60, n24, m60, m24 = [], [], [], [], [], []
    with open(path, encoding="utf-8") as f:
        head = f.readline().strip().split(",")
        if head != ["sym", "sig_ms", "n60", "n24", "m60", "m24"]:
            raise ValueError("%s: заголовок %r" % (path, head))
        for line in f:
            p = line.rstrip("\n").split(",")
            sym.append(p[0])
            ms.append(int(p[1]))
            n60.append(int(p[2]))
            n24.append(int(p[3]))
            m60.append(int(p[4]))
            m24.append(int(p[5]))
    return sym, dict(sig_ms=np.array(ms, dtype=np.int64), n60=np.array(n60, dtype=np.int64), n24=np.array(n24, dtype=np.int64),
                     m60=np.array(m60, dtype=np.int64).astype(bool), m24=np.array(m24, dtype=np.int64).astype(bool))


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
    cmd = [BIN, "tcount", "--eps", src, "--out", out, "--base-date", w.t0, "--trades", trades, "--store", STORE, "--threads", str(a.procs)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit("tcount rc=%d: %s" % (p.returncode, p.stderr[-2000:]))
    sym, c = read_counts(out)
    with open(src, encoding="utf-8") as f:
        n_src = sum(1 for _ in f) - 1
    if len(sym) != n_src:
        sys.exit("строк в выходе %d ≠ кандидатов %d" % (len(sym), n_src))
    info = dict(uni=u.name, window=w.name, side=a.side, rows=len(sym), src=src, base_date=w.t0, trades=trades, bin_md5=md5, m60_rows=int(c["m60"].sum()), m24_rows=int(c["m24"].sum()),
                summary=p.stdout.strip()[-1500:], sha256=hashlib.sha256(open(out, "rb").read()).hexdigest())
    json.dump(info, open(out + ".info.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in info.items() if k != "summary"}, ensure_ascii=False))


# ---- ворота -----------------------------------------------------------------------------------------------------------------

def date_str(abs_day):
    return (datetime.date(1970, 1, 1) + datetime.timedelta(days=int(abs_day))).isoformat()


def day_times(root, sym, abs_day, cache):
    """Времена сделок (transact_time, мс) суток из zip независимо от Rust: `unzip -p | cut -f6`; None — файла нет."""
    key = (root, sym, abs_day)
    if key in cache:
        return cache[key]
    name = "%s-aggTrades-%s" % (sym, date_str(abs_day))
    path = os.path.join(root, sym, name + ".zip")
    if not os.path.exists(path):
        cache[key] = None
        return None
    p = subprocess.run("unzip -p '%s' '%s.csv' | tail -n +2 | cut -d, -f6" % (path, name), shell=True, capture_output=True, check=True)
    cache[key] = np.fromstring(p.stdout.decode("ascii"), dtype=np.int64, sep="\n") if p.stdout.strip() else np.zeros(0, dtype=np.int64)
    return cache[key]


def recount(root, sym, sig_abs, cache):
    """(n60, n24, m60, m24) по определению п. 4: минуты строго раньше минуты сигнала; файла суток, задетых окном, нет → m = 1."""
    end = sig_abs // MIN_MS * MIN_MS
    out = []
    for span in (60 * MIN_MS, DAY_MS):
        lo = end - span
        n, miss = 0, False
        for d in range(lo // DAY_MS, (end - 1) // DAY_MS + 1):
            t = day_times(root, sym, d, cache)
            if t is None:
                miss = True
                continue
            n += int(((t >= lo) & (t < end)).sum())
        out.append((n, miss))
    return out[0][0], out[1][0], out[0][1], out[1][1]


def cmd_gate(a):
    import tk604_stats as st
    import tk625_run as r5
    rng = random.Random(a.seed)
    res = dict(seed=a.seed, k=a.k, edge=a.edge, unis={})
    ok_all = True
    for un in UNIS:
        u = r5.Uni(un)
        rows = []          # (окно, сторона, строка таблицы, sig_ms абсолютный)
        tabs = {}
        ctx = dict(rows=0, tpm_equal=0, tpm_absdiff_max=0.0, tpm_absdiff_sum=0.0, tpm_n=0)
        miss = dict(m60=0, m24=0, rows=0)
        cov = {}
        turn = dict(rows=0, viol_1h=0, viol_24h=0, nan_1h=0, nan_24h=0)
        for w in st.TRADE_WINDOWS:
            for s in SIDES:
                import retitr_ftab as rfx
                sym, ms, raw = rfx.read_ftab(feat_path(FEAT34, un, w.name, s))
                csym, c = read_counts(feat_path(os.path.join(ROOT, "feat"), un, w.name, s))
                if list(sym) != list(csym) or not np.array_equal(ms, c["sig_ms"]):
                    sys.exit("%s %s-%s: ключи tcount ≠ таблица признаков f12" % (un, w.name, s))
                tabs[(w.name, s)] = (sym, ms, raw, c)
                n = len(sym)
                ctx["rows"] += n
                tp = raw["tpm60"] * 60.0
                fin = np.isfinite(tp) & ~c["m60"]
                ctx["tpm_n"] += int(fin.sum())
                diff = np.abs(np.rint(tp[fin]) - c["n60"][fin])
                ctx["tpm_equal"] += int((diff == 0).sum())
                ctx["tpm_absdiff_sum"] += float(diff.sum())
                if len(diff):
                    ctx["tpm_absdiff_max"] = max(ctx["tpm_absdiff_max"], float(diff.max()))
                miss["rows"] += n
                miss["m60"] += int(c["m60"].sum())
                miss["m24"] += int(c["m24"].sum())
                absms = ms + w.t0_ms
                mon = np.array([datetime.datetime.fromtimestamp(x / 1000, datetime.timezone.utc).strftime("%Y-%m") for x in absms])
                for m in sorted(set(mon)):
                    k = cov.setdefault(m, dict(rows=0, m24=0, m60=0))
                    sel = mon == m
                    k["rows"] += int(sel.sum())
                    k["m24"] += int((c["m24"] & sel).sum())
                    k["m60"] += int((c["m60"] & sel).sum())
                t1, t24 = raw["turn1h"], raw["turn24h"]
                turn["rows"] += n
                turn["nan_1h"] += int((~np.isfinite(t1)).sum())
                turn["nan_24h"] += int((~np.isfinite(t24)).sum())
                turn["viol_1h"] += int((np.isfinite(t1) & (t1 < TURN1H_MIN)).sum())
                if un == "h775":
                    turn["viol_24h"] += int((np.isfinite(t24) & (t24 < TURN24H_MIN)).sum())
                for i in range(n):
                    rows.append((w.name, s, i, int(absms[i])))
        # выбор: k случайных + edge у начала суток (первые 61 мин UTC — окно 60 мин переходит через границу суток)
        pool_all = list(range(len(rows)))
        pool_edge = [i for i in pool_all if rows[i][3] % DAY_MS < 61 * MIN_MS]
        pick = set(rng.sample(pool_all, min(a.k, len(pool_all))))
        pick |= set(rng.sample(pool_edge, min(a.edge, len(pool_edge))))
        cache, bad, checked = {}, [], 0
        for i in sorted(pick):
            wname, s, j, sig_abs = rows[i]
            sym, ms, raw, c = tabs[(wname, s)]
            root = os.path.join(u.wdir(wname), "aggtrades")
            n60, n24, m60, m24 = recount(root, sym[j], sig_abs, cache)
            got = (int(c["n60"][j]), int(c["n24"][j]), bool(c["m60"][j]), bool(c["m24"][j]))
            checked += 1
            if got != (n60, n24, m60, m24):
                bad.append(dict(window=wname, side=s, sym=sym[j], sig_ms=int(ms[j]), rust=got, py=(n60, n24, m60, m24)))
            if len(cache) > 400:
                cache.clear()
        ok = not bad
        ok_all &= ok
        res["unis"][un] = dict(checked=checked, edge_pool=len(pool_edge), mismatches=len(bad), first_bad=bad[:5], ok=ok, ctx_tpm60=dict(
            rows=ctx["rows"], compared=ctx["tpm_n"], equal=ctx["tpm_equal"], share_equal=round(ctx["tpm_equal"] / max(1, ctx["tpm_n"]), 4),
            absdiff_max=ctx["tpm_absdiff_max"], absdiff_mean=round(ctx["tpm_absdiff_sum"] / max(1, ctx["tpm_n"]), 3)), missing=miss, coverage_by_month=cov, turn=turn)
        print("G-n %s: сверено %d (из них у границы суток ≤ %d), расхождений %d; tpm60·60 = n60 у %.1f %% (контекст), max |Δ| %g; m60 %d, m24 %d из %d; G-turn: нарушений turn1h %d, turn24h %d (NaN %d / %d)" % (
            un, checked, a.edge, len(bad), 100 * res["unis"][un]["ctx_tpm60"]["share_equal"], ctx["tpm_absdiff_max"], miss["m60"], miss["m24"], miss["rows"],
            turn["viol_1h"], turn["viol_24h"], turn["nan_1h"], turn["nan_24h"]), flush=True)
    res["ok"] = ok_all
    os.makedirs(os.path.join(ROOT, "res"), exist_ok=True)
    json.dump(res, open(os.path.join(ROOT, "res", "gate-n.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("G-n итог:", "PASS" if ok_all else "СТОП (расхождение)")
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
    g.add_argument("--k", type=int, default=120)
    g.add_argument("--edge", type=int, default=30)
    g.add_argument("--seed", type=int, default=638)
    a = ap.parse_args()
    {"build": cmd_build, "gate": cmd_gate}[a.cmd](a)


if __name__ == "__main__":
    main()
