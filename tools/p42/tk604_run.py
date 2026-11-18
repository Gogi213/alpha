#!/usr/bin/env python3
"""П-42 §13.26 (TK-604): драйвер счёта на srv. Подкоманды (порядок — п. 10): gates1, eng <окно>, ep <окно>, g9, seam, g2, g4, g3, g5, step1, cells, g8, select, freeze.
Запускается юнитом (`tk604_go.sh`), не с ПК. Окна — только w0 / wa / wb / wc (`wd` и даты после 13.11 — отказ, `tk604_stats.check_isolation`).
Выходы — `/data/p42-tk604/{eng,base,ep,ftab,cells,gates,res}/…` (малые: счётчики, CSV, JSON). P&L печатают только подкоманды, которым протокол это разрешает.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_feat as tf  # noqa: E402
import tk604_stats as st  # noqa: E402

ROOT = "/data/p42-tk604"
TK = "/data/p42-tk599"
BIN = TK + "/bin/alpha-master"
SYMS = "/data/p42-t566/symbols.txt"
BIN_SHA = "f4a0d5a348757e23ec231f09796e9d2ca414eaaa08a9731ca6f356a3084cebca"
BIN_MD5 = "a03c91df5a70c8eb32cbb7122943b8f9"
SYMS_SHA = "25c9ad8caab30af583b18f0678189f381282cce23b5d42c043471481bf0eaae0"
MANIFESTS_SHA = "e377de791796bcc93bee2ad5ad70c5ee69cf1e45499369637cdf293e07c9bd59"
THREADS = 4

B0P0 = "lat=4 latx=4 natr_min=0 t1h=0"
B0 = "lat=4 latx=4"
BASE = {   # п. 4, дословно
    "L": "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20",
    "S": "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05",
}
VID = {"L": "L_base", "S": "S_base"}
TK599_BASE = {"L": "c53a116125b39", "S": "c10fbba7be522"}
WINS = {w.name: w for w in st.HIST_WINDOWS}
SEAM_LO, SEAM_HI = "2026-07-01", "2026-07-03"       # сутки 01–02.07 (G9, шов w0 / wa)


def win(name):
    if name not in st.ALLOWED_WINDOWS:
        raise st.GuardError("изоляция: окно %r не разрешено" % name)
    return WINS[name]


def wdir(name):
    return os.path.join(ROOT, name) if name == "w0" else os.path.join(TK, name)


def p(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def sha256(path):
    return st.sha256_file(path)


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def run(cmd, env=None, log=None):
    e = dict(os.environ)
    e.update(env or {})
    t = time.time()
    with open(log, "w") if log else open(os.devnull, "w") as f:
        r = subprocess.run(cmd, env=e, stdout=f, stderr=subprocess.STDOUT)
    say("rc=%d %.0f с: %s" % (r.returncode, time.time() - t, " ".join(cmd)[:160]))
    if r.returncode != 0:
        raise st.GuardError("команда вернула %d (журнал %s)" % (r.returncode, log))


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)


# ---- G1 ----------------------------------------------------------------------------------------------------------------------

def manifests_sections():
    out, cur = {}, None
    with open(os.path.join(HERE, "tk599-manifests.txt"), encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("== "):
                cur = line[3:].strip()
                out[cur] = []
            elif cur is not None and line.strip():
                out[cur].append(line)
    return out


def cmd_gates1(a):
    bad = []
    got = {"bin_sha": sha256(BIN), "syms_sha": sha256(SYMS), "manifests_sha": sha256(os.path.join(HERE, "tk599-manifests.txt"))}
    with open(BIN, "rb") as f:
        got["bin_md5"] = hashlib.md5(f.read()).hexdigest()
    for k, want in (("bin_sha", BIN_SHA), ("bin_md5", BIN_MD5), ("syms_sha", SYMS_SHA), ("manifests_sha", MANIFESTS_SHA)):
        if got[k] != want:
            bad.append("%s: %s ≠ %s" % (k, got[k], want))
    sec = manifests_sections()
    for w in ("wa", "wb", "wc"):
        have = open(os.path.join(TK, w, "MANIFEST"), encoding="utf-8").read().splitlines()
        want = sec[w]
        if [x.strip() for x in have if x.strip()] != [x.strip() for x in want]:
            bad.append("MANIFEST %s ≠ секция tk599-manifests.txt" % w)
        for line in want:
            if line.startswith("manifest-"):
                name, _, rest = line.partition(" ")
                want_sha = rest.rsplit("sha256 ", 1)[1].strip()
                f = os.path.join(TK, w, name + ".txt")
                if sha256(f) != want_sha:
                    bad.append("%s/%s.txt sha256 ≠ записанному" % (w, name))
    w0 = open(os.path.join(ROOT, "w0", "MANIFEST"), encoding="utf-8").read()
    got["w0_manifest"] = w0.splitlines()
    write_json(p("gates", "g1.json"), dict(got=got, bad=bad))
    say("G1", "FAIL " + "; ".join(bad) if bad else "ok: бинарь, монеты, манифесты wa/wb/wc = записанным")
    if bad:
        sys.exit(1)


# ---- движок ------------------------------------------------------------------------------------------------------------------

def tick(w, variants, out, env, log, ftab=None):
    d = wdir(w.name)
    vf = out + ".variants.txt"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(vf, "w", encoding="utf-8") as f:
        f.write("".join("%s %s\n" % v for v in variants))
    cmd = [BIN, "breakout", "tick", "--cache", d + "/cache", "--trades", d + "/aggtrades", "--variants", vf,
           "--symbols", open(SYMS, encoding="utf-8").read().strip(), "--out", out, "--threads", str(THREADS)]
    if ftab:
        cmd += ["--ftab", ftab]
    e = {"P42_T0": w.t0}
    e.update(env)
    run(cmd, e, log)


def cmd_eng(a):
    w = win(a.window)
    st.check_dates(w.t0, w.hi)
    days = "14:29" if w.name == "w0" else "14:73"
    out = p("eng", w.name)
    if not os.path.exists(out + "/DONE"):
        tick(w, [("b0p0", B0P0), ("b0", B0)], out, {"P42_SIGSX_ONLY": "1", "P42_SIGSX": days}, p("log", "eng-%s.log" % w.name))
        open(out + "/DONE", "w").close()
    if w.name != "w0":
        out = p("base", w.name)
        if not os.path.exists(out + "/DONE"):
            tick(w, [(VID["L"], BASE["L"]), (VID["S"], BASE["S"])], out, {"P42_SIGSX": days, "P42_DETAIL": "1"}, p("log", "base-%s.log" % w.name))
            open(out + "/DONE", "w").close()
    say("eng %s готово" % w.name)


def cmd_ep(a):
    w = win(a.window)
    hi = "2026-07-02" if w.name == "w0" else w.hi         # w0: до 02.07 для шва G9; в историю идёт только зачёт 17–30.06 (`splice_episodes`)
    out = p("ep", w.name + ".csv")
    d = wdir(w.name)
    env = {"SIGFEAT_BASE_DATE": w.t0, "SIGFEAT_DAYS": "80"}
    run([sys.executable, os.path.join(HERE, "tk604_episodes.py"), "--t0", w.t0, "--eng", p("eng", w.name), "--cache", d + "/cache", "--trades", d + "/aggtrades",
         "--lo", w.lo, "--hi", hi, "--out", out, "--procs", str(THREADS)], env, p("log", "ep-%s.log" % w.name))
    say("ep %s: %s" % (w.name, open(out + ".stats.json", encoding="utf-8").read().replace("\n", " ")))


# ---- G9 и шов ----------------------------------------------------------------------------------------------------------------

def sigsx_set(path, t0_ms, lo_ms, hi_ms):
    out = set()
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            ab = t0_ms + int(r[2])
            if lo_ms <= ab < hi_ms:
                out.add((r[0], ab, int(r[4])))          # монета, абсолютное время сигнала, цена уровня; уровень (r[1]) — внутренний номер окна, не сравниваем
    return out


def day_ms(s):
    import datetime
    d = datetime.date.fromisoformat(s)
    return int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)


def cmd_g9(a):
    lo, hi = day_ms(SEAM_LO), day_ms(SEAM_HI)
    bad, res = [], {}
    for var in ("b0p0", "b0"):
        for side in ("long", "short"):
            A = sigsx_set(p("eng", "w0", var, side + "-sigsx.csv"), st.W0.t0_ms, lo, hi)
            B = sigsx_set(p("eng", "wa", var, side + "-sigsx.csv"), st.WA.t0_ms, lo, hi)
            res["%s/%s" % (var, side)] = dict(w0=len(A), wa=len(B), only_w0=len(A - B), only_wa=len(B - A))
            if A != B:
                bad.append("%s/%s" % (var, side))
    # сигналов раньше 17.06 нет
    early = 0
    for side in ("long", "short"):
        with open(p("eng", "w0", "b0p0", side + "-sigsx.csv"), encoding="utf-8", newline="") as f:
            rd = csv.reader(f)
            next(rd)
            early += sum(1 for r in rd if st.W0.t0_ms + int(r[2]) < day_ms("2026-06-17"))
    res["early_before_17_06"] = early
    if early:
        bad.append("сигналы раньше 17.06: %d" % early)
    write_json(p("gates", "g9.json"), dict(res=res, bad=bad))
    say("G9", json.dumps(res, ensure_ascii=False))
    say("G9", "FAIL " + "; ".join(bad) if bad else "ok: множества сигналов w0 = wa на 01–02.07, раньше 17.06 нет")
    if bad:
        sys.exit(1)


def read_ep_rows(w, lo, hi):
    out = {}
    with open(p("ep", w.name + ".csv"), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            ab = w.t0_ms + int(r["sig_ms"])
            if lo <= ab < hi:
                out[(r["side"], r["sym"], ab, int(r["lp_e8"]))] = int(r["indep"])
    return out


def cmd_seam(a):
    """Отчётная строка критика: число эпизодов 01–02.07, у которых `indep` или первый сигнал различаются между `w0` и `wa` (G9 — по сигналам, здесь — по эпизодам)."""
    lo, hi = day_ms(SEAM_LO), day_ms(SEAM_HI)
    A, B = read_ep_rows(st.W0, lo, hi), read_ep_rows(st.WA, lo, hi)
    both = set(A) & set(B)
    diff_indep = sum(1 for k in both if A[k] != B[k])
    res = dict(w0=len(A), wa=len(B), both=len(both), only_w0=len(A) - len(both), only_wa=len(B) - len(both), indep_differs=diff_indep,
               differs_total=len(A) - len(both) + len(B) - len(both) + diff_indep)
    write_json(p("gates", "seam.json"), res)
    say("шов 01–02.07 (эпизоды w0 vs wa):", json.dumps(res, ensure_ascii=False))


# ---- загрузка данных (общая) --------------------------------------------------------------------------------------------------

_C = {}
SIDE_NAME = tf.SIDE_NAME
ENG = st.ENGINE_SIDE


def coin_list():
    if "coins" not in _C:
        _C["coins"] = sorted(x for x in open(SYMS, encoding="utf-8").read().replace("\n", ",").split(",") if x)
    return _C["coins"]


def episodes_all():
    if "eps" not in _C:
        per = {w.name: tf.window_rows_to_arrays(tf.read_episodes_csv(p("ep", w.name + ".csv")), w) for w in st.HIST_WINDOWS}
        _C["eps"] = tf.splice_episodes(per)
    return _C["eps"]


def cand_arrays(side):
    """Кандидаты стороны (дамп базы `P42_SIGSX`) в зачётных диапазонах wa/wb/wc: монета, абсолютное время сигнала, мс от T0 окна, окно."""
    if ("cand", side) in _C:
        return _C[("cand", side)]
    S, A, R, W = [], [], [], []
    for w in st.TRADE_WINDOWS:
        sym, rel = tf.read_sigsx_keys(p("base", w.name, VID[side], ENG[side] + "-sigsx.csv"))
        ab = rel + w.t0_ms
        m = tf.in_range(ab, w)
        S.append(sym[m]); A.append(ab[m]); R.append(rel[m]); W.append(np.array([w.name] * int(m.sum()), dtype=object))
    out = tuple(np.concatenate(x) for x in (S, A, R, W))
    st.check_abs_ms(out[1], "время кандидата")
    _C[("cand", side)] = out
    return out


def cand_stream(side, wname):
    """Все строки дампа окна (включая разгон) — ключи таблицы `--ftab` (мс от T0 окна)."""
    w = WINS[wname]
    sym, rel = tf.read_sigsx_keys(p("base", wname, VID[side], ENG[side] + "-sigsx.csv"))
    return sym, rel, rel + w.t0_ms


def trade_keys(side):
    """G5: только `sym, sig_ms, entry_ms` сделок базы (gross/fees/net не читаются): сутки входа, монета, абсолютное время сигнала; только зачётные диапазоны."""
    day, sym, key = [], [], []
    for w in st.TRADE_WINDOWS:
        with open(p("base", w.name, VID[side], ENG[side] + "-trades.csv"), encoding="utf-8", newline="") as f:
            rows = [(r["sym"], int(r["sig_ms"]), int(r["entry_ms"])) for r in csv.DictReader(f)]
        e = np.array([r[2] for r in rows], dtype=np.int64) + w.t0_ms
        m = tf.in_range(e, w)
        day.append(tf.abs_to_day(e[m]))
        sym.append(np.array([r[0] for r in rows], dtype=object)[m])
        key.append((np.array([r[1] for r in rows], dtype=np.int64) + w.t0_ms)[m])
    return np.concatenate(day), np.concatenate(sym), np.concatenate(key)


def load_series(side, kind, cid=None):
    """Склейка сделок стороны: kind 'base' (несфильтрованная база) или 'cell' (клетка `cid`); охрана суммы daily = net."""
    tables, daily = {}, {}
    for w in st.TRADE_WINDOWS:
        d = p("base", w.name, VID[side]) if kind == "base" else p("cells", "%s-%s" % (w.name, side), cid)
        tables[w.name] = st.read_trades_csv(os.path.join(d, ENG[side] + "-trades.csv"))
        daily[w.name] = st.read_daily_csv(os.path.join(d, ENG[side] + "-daily.csv"))
    return st.splice(tables, coin_list(), daily=daily)


def feats_at(side, sym, abs_ms):
    return tf.features(tf.episode_history(episodes_all(), SIDE_NAME[side]), sym, abs_ms)


def dups_of(side):
    return json.load(open(p("res", "dups-%s.json" % side), encoding="utf-8"))


# ---- G2, G4, G3 --------------------------------------------------------------------------------------------------------------

def cmd_g2(a):
    bad, res = [], {}
    for w in st.TRADE_WINDOWS:
        for side in st.SIDES:
            mine = st.read_trades_csv(p("base", w.name, VID[side], ENG[side] + "-trades.csv"))
            ref = st.read_trades_csv(os.path.join(TK, "out", w.name, TK599_BASE[side], ENG[side] + "-trades.csv"))
            ok, n, diff = st.tables_equal(mine, ref, w)
            res["%s/%s" % (w.name, side)] = dict(equal=ok, trades=n, diff=diff)
            if not ok:
                bad.append("%s/%s: %d различий из %d" % (w.name, side, diff, n))
    write_json(p("gates", "g2.json"), dict(res=res, bad=bad))
    say("G2", json.dumps(res, ensure_ascii=False))
    say("G2", "FAIL " + "; ".join(bad) if bad else "совпало: базы моего прогона = базам TK-599 на зачётных диапазонах")
    if bad:
        sys.exit(1)


def cmd_g4(a):
    import pandas as pd
    df = pd.read_parquet("/data/tk594/ep_u0.parquet", columns=["side", "sym", "sig_ms", "indep", "ok_t60", "y"])
    t0 = day_ms("2026-09-01")
    lo, hi = day_ms("2026-09-15"), day_ms("2026-11-14")
    df = df[(df["indep"]) & (df["ok_t60"])]
    ref = {(r.side, r.sym, int(r.sig_ms) + t0): int(r.y) for r in df.itertuples(index=False) if lo <= int(r.sig_ms) + t0 < hi}
    mine = {}
    for w in (st.WB, st.WC):
        for r in tf.read_episodes_csv(p("ep", w.name + ".csv")):
            ab = w.t0_ms + int(r["sig_ms"])
            if r["indep"] == "1" and r["ok_t60"] == "1" and r["y"] != "" and lo <= ab < hi and tf.in_range([ab], w)[0]:
                mine[(r["side"], r["sym"], ab)] = int(r["y"])
    common = set(ref) & set(mine)
    diff = [k for k in common if ref[k] != mine[k]]
    res = dict(ref=len(ref), mine=len(mine), common=len(common), share_common=len(common) / max(len(ref), 1), y_differs=len(diff),
               only_ref=len(ref) - len(common), only_mine=len(mine) - len(common), diff_sample=sorted(diff)[:10])
    write_json(p("gates", "g4.json"), res)
    say("G4", json.dumps(res, ensure_ascii=False))
    if diff:
        say("G4 FAIL: метка y различается у %d общих ключей" % len(diff))
        sys.exit(1)
    say("G4 ok: y равна у 100 %% общих ключей (%d); доля общих от ключей ep_u0 %.1f %%" % (len(common), 100 * res["share_common"]))


def cmd_g3(a):
    eps = episodes_all()
    cand_side = np.concatenate([np.array([SIDE_NAME[s]] * len(cand_arrays(s)[1]), dtype=object) for s in st.SIDES])
    cand_abs = np.concatenate([cand_arrays(s)[1] for s in st.SIDES])
    cand_sym = np.concatenate([cand_arrays(s)[0] for s in st.SIDES])
    seams = [day_ms(d) for d in ("2026-07-01", "2026-08-26", "2026-10-03")]
    picks = tf.pick_candidates(cand_side, cand_abs, seams, total=60)
    near = {d: {s: int(sum(1 for i in picks if cand_side[i] == s and abs(int(cand_abs[i]) - sm) <= 3 * st.DAY_MS)) for s in ("long", "short")}
            for d, sm in zip(("01.07", "26.08", "03.10"), seams)}
    bad = tf.causality_check(eps, cand_side, cand_sym, cand_abs, picks)
    res = dict(picks=len(picks), near_seams=near, mismatches=len(bad), sample=bad[:5])
    write_json(p("gates", "g3.json"), res)
    say("G3", json.dumps(res, ensure_ascii=False, default=str))
    if bad:
        say("G3 FAIL")
        sys.exit(1)
    say("G3 ok: признак из полной склейки = из журнала с мусором после t = перебору, у %d кандидатов" % len(picks))


# ---- G5: таблицы признака, дубли, покрытие, MDE ----------------------------------------------------------------------------

MODEL_SIGMA = {"L": 5.7, "S": 4.1}


def cmd_g5(a):
    for side in st.SIDES:
        sym, ab, rel, wn = cand_arrays(side)
        n, k, bk = feats_at(side, sym, ab)
        dups = tf.find_duplicates(tf.exclusion_masks(n, k))
        for w in st.TRADE_WINDOWS:          # таблицы --ftab: все строки дампа окна (в том числе разгон), одна на сторону и окно
            s_, r_, a_ = cand_stream(side, w.name)
            n_, k_, bk_ = feats_at(side, s_, a_)
            rows = tf.write_ftab(p("ftab", "%s-%s.csv" % (w.name, side)), s_, r_, n_, k_, bk_)
            say("ftab %s-%s: %d строк (кандидатов дампа %d)" % (w.name, side, rows, len(s_)))
        dd, tsym, tkey = trade_keys(side)
        tn, tk_, _ = feats_at(side, tsym, tkey)
        cov = tf.coverage(ab, n, dd, tn, tk_)
        ids, jm = tf.jaccard_matrix(tf.exclusion_masks(tn, tk_))
        write_json(p("res", "dups-%s.json" % side), dups)
        write_json(p("res", "g5-%s.json" % side), dict(cov=cov, readable={str(K): v for K, v in cov["readable"].items()}, jaccard_ids=ids,
                                                       jaccard=np.round(jm, 3).tolist(), n_cand=int(len(ab)), n_trades=int(len(dd))))
        sig = MODEL_SIGMA[side]
        say("== %s: кандидатов %d, сделок базы %d; дубли: %s" % (side, len(ab), len(dd), {c: d for c, d in dups.items() if d}))
        for K in tf.KS:
            b = cov["by_K"][K]
            say("  K=%d: доля кандидатов с n≥K по месяцам %s; сделок по месяцам %s; покрытых суток %s; читается %s" % (
                K, [round(x, 3) for x in b["cand_share"]], b["trades"], b["covered_days"], cov["readable"][K]))
        for cid, c in cov["cells"].items():
            d_min, mde = st.mde_cell(sig, c["N_cov"], c["f_cov"], c["f_all"]) if c["N_cov"] else (float("nan"), float("nan"))
            say("  %s%s: N_cov %d, E %d, f_cov %.3f, f_all %.3f, MDE d_min %.2f MDE_Δ %.2f (σ модель %.1f)" % (
                cid, " (дубль %s)" % dups[cid] if dups[cid] else "", c["N_cov"], c["E"], c["f_cov"], c["f_all"], d_min, mde, sig))
        say("  Jaccard 9×9 (сделки базы):")
        for i, cid in enumerate(ids):
            say("   %-8s %s" % (cid, " ".join("%.2f" % x for x in jm[i])))
    say("G5 готово — запись в JOURNAL до чтения клеток")


# ---- ступень 1 ---------------------------------------------------------------------------------------------------------------

def wil(k, n):
    lo, hi = tf.wilson(k, n)
    return [round(lo, 4), round(hi, 4)]


def auc_drop2(x, y, keys):
    """AUC после жадного удаления двух значений ключа (суток / монет), без которых AUC падает сильнее всего."""
    x, y, keys = np.asarray(x, dtype=float), np.asarray(y), np.asarray(keys)
    ok = ~np.isnan(x)
    x, y, keys = x[ok], y[ok], keys[ok]
    keep = np.ones(len(x), dtype=bool)
    for _ in range(2):
        best = None
        for u in np.unique(keys[keep]):
            m = keep & (keys != u)
            v = tf.auc(x[m], y[m])
            if v == v and (best is None or v < best[0]):
                best = (v, u)
        if best is None:
            return float("nan")
        keep &= keys != best[1]
    return tf.auc(x[keep], y[keep])


def cmd_step1(a):
    """Ступень 1 (i): эпизоды, без P&L."""
    eps = episodes_all()
    cid = {c: i for i, c in enumerate(coin_list())}
    res = {}
    for side in st.SIDES:
        name = SIDE_NAME[side]
        other = SIDE_NAME["S" if side == "L" else "L"]
        d_all = tf.abs_to_day(eps.abs_ms)
        E = eps.take((eps.side == name) & (d_all >= 0) & (d_all < st.NDAYS))
        hs, ho = tf.episode_history(eps, name), tf.episode_history(eps, other)
        n, k = hs.counts(E.sym, E.abs_ms)
        k = np.rint(k).astype(np.int64)
        n30, k30 = hs.counts(E.sym, E.abs_ms, within_ms=30 * st.DAY_MS)
        k30 = np.rint(k30).astype(np.int64)
        no, ko = ho.counts(E.sym, E.abs_ms)
        ko = np.rint(ko).astype(np.int64)
        with np.errstate(invalid="ignore", divide="ignore"):
            bk = np.where(n >= 10, k / np.maximum(n, 1), np.nan)
            bk30 = np.where(n30 >= 10, k30 / np.maximum(n30, 1), np.nan)
            bkb = np.where(n + no >= 10, (k + ko) / np.maximum(n + no, 1), np.nan)
        day = tf.abs_to_day(E.abs_ms)
        mon = st.MONTH_OF_DAY[day]
        coin = np.array([cid[s] for s in E.sym], dtype=np.int64)
        y = E.y.astype(int)
        spec = tf.strata_edges(bk, n)
        strata = tf.assign_strata(bk, n, spec)
        r = dict(n_ep=int(len(E)), spec=spec, share_y=float(y.mean()), strata={})
        for g in sorted(set(strata.tolist())):
            row = {}
            for mi in [-1] + list(range(len(st.MONTH_LEN))):
                sel = (strata == g) & ((mon == mi) if mi >= 0 else True)
                row["all" if mi < 0 else st.MONTH_NAMES[mi]] = dict(n=int(sel.sum()), k=int(y[sel].sum()), share=float(y[sel].mean()) if sel.any() else None,
                                                                 wilson=wil(int(y[sel].sum()), int(sel.sum())))
            r["strata"][g] = row
        r["auc_bk"] = tf.auc_ci(bk, y, day, coin)
        r["auc_bk_month"] = {st.MONTH_NAMES[mi]: tf.auc_ci(bk[mon == mi], y[mon == mi], day[mon == mi], coin[mon == mi]) for mi in range(len(st.MONTH_LEN))}
        r["auc_bk_drop2_days"] = auc_drop2(bk, y, day)
        r["auc_bk_drop2_coins"] = auc_drop2(bk, y, coin)
        yn = E.y_n.astype(int)
        has = yn >= 0
        r["auc_bk_yN"] = tf.auc_ci(bk[has], yn[has], day[has], coin[has])
        v = E.natr_e8[E.natr_e8 > 0]                       # терцили NATR сигнала внутри стороны; AUC bk внутри каждого терциля
        qs = np.quantile(v, [1 / 3, 2 / 3]) if len(v) else [0, 0]
        ter = np.where(E.natr_e8 <= 0, -1, (E.natr_e8 > qs[0]).astype(int) + (E.natr_e8 > qs[1]).astype(int))
        r["auc_bk_in_natr_tercile"] = {"T%d" % (t + 1): tf.auc_ci(bk[ter == t], y[ter == t], day[ter == t], coin[ter == t]) for t in range(3)}
        r["auc_n"] = tf.auc_ci(n.astype(float), y, day, coin)
        r["strata_n"] = {lab: dict(n=int(s.sum()), share=float(y[s].mean()) if s.any() else None) for lab, s in
                         (("n<10", n < 10), ("10–19", (n >= 10) & (n < 20)), ("20–39", (n >= 20) & (n < 40)), ("40–59", (n >= 40) & (n < 60)), ("≥60", n >= 60))}
        r["auc_bk30"] = tf.auc_ci(bk30, y, day, coin)
        ip = E.in_pool
        r["auc_bk_in_pool"] = dict(n_ep=int(ip.sum()), **tf.auc_ci(bk[ip], y[ip], day[ip], coin[ip]))
        r["auc_bk_both_sides"] = tf.auc_ci(bkb, y, day, coin)
        res[side] = r
        say("== ступень 1 (i), сторона %s: эпизодов %d, доля y %.4f; страты %s" % (side, len(E), r["share_y"], {g: v_["all"]["n"] for g, v_ in r["strata"].items()}))
        say("  AUC bk vs y: %s" % json.dumps(r["auc_bk"], ensure_ascii=False))
        for mname, v_ in r["auc_bk_month"].items():
            say("   %s: AUC %.4f n %d p %.3f" % (mname, v_["auc"], v_["n"], v_["p"]))
        say("  без 2 лучших суток %.4f, монет %.4f; y_N %.4f; bk30 %.4f; n %.4f; in_pool %.4f; обе стороны %.4f" % (
            r["auc_bk_drop2_days"], r["auc_bk_drop2_coins"], r["auc_bk_yN"]["auc"], r["auc_bk30"]["auc"], r["auc_n"]["auc"], r["auc_bk_in_pool"]["auc"], r["auc_bk_both_sides"]["auc"]))
        for g, row in r["strata"].items():
            say("   страта %-12s: %s" % (g, {mm: (c["n"], None if c["share"] is None else round(c["share"], 4)) for mm, c in row.items()}))
    write_json(p("res", "step1-i.json"), res)
    say("ступень 1 (i) готова")


def cmd_step1ii(a):
    """Ступень 1 (ii): первое чтение P&L баз (только после записи JOURNAL по G5)."""
    spec_all = json.load(open(p("res", "step1-i.json"), encoding="utf-8"))
    res = {}
    for side in st.SIDES:
        ser = load_series(side, "base")
        sym = np.array(ser.sym_name, dtype=object)
        n, k, bk = feats_at(side, sym, ser.key_ms)
        grp = tf.trade_strata(bk[20], spec_all[side]["spec"])
        r = dict(by_bk20=tf.mean_by_group(ser.val, grp))
        hist = tf.History(sym, ser.t_out + tf.TRADE_L_MS, ser.val)
        ntr, str_ = hist.counts(sym, ser.key_ms)
        with np.errstate(invalid="ignore", divide="ignore"):
            mtr = np.where(ntr > 0, str_ / np.maximum(ntr, 1), np.nan)
        g2 = np.where(ntr == 0, "нет сделок", np.where(ntr == 1, "одна сделка", np.where(mtr < 0, "m_tr<0", "m_tr≥0")))
        r["by_mtr"] = tf.mean_by_group(ser.val, g2)
        mm = st.MONTH_OF_DAY[ser.day]
        r["by_month_bk20"] = {st.MONTH_NAMES[mi]: tf.mean_by_group(ser.val[mm == mi], grp[mm == mi]) for mi in range(len(st.MONTH_LEN))}
        r["total"] = dict(n=int(len(ser)), sum=float(ser.val.sum()), mean=float(ser.val.mean()), sigma=float(ser.val.std(ddof=1)))
        res[side] = r
        say("== ступень 1 (ii), сторона %s: сделок %d, Σ %.2f $, среднее %.3f $, σ %.3f" % (side, r["total"]["n"], r["total"]["sum"], r["total"]["mean"], r["total"]["sigma"]))
        for g, v in r["by_bk20"].items():
            say("   bk20 %-14s: n %d, Σ %.2f, среднее %.3f, в плюсе %.3f" % (g, v["n"], v["total"], v["mean"], v["pos"]))
        for g, v in r["by_mtr"].items():
            say("   m_tr %-14s: n %d, Σ %.2f, среднее %.3f, в плюсе %.3f" % (g, v["n"], v["total"], v["mean"], v["pos"]))
        g5 = json.load(open(p("res", "g5-%s.json" % side), encoding="utf-8"))
        for cidn, c in g5["cov"]["cells"].items():
            if c["N_cov"]:
                d_min, mde = st.mde_cell(r["total"]["sigma"], c["N_cov"], c["f_cov"], c["f_all"])
                say("   MDE %s: d_min %.2f MDE_Δ %.2f (σ измерено %.3f)" % (cidn, d_min, mde, r["total"]["sigma"]))
    write_json(p("res", "step1-ii.json"), res)


# ---- клетки ------------------------------------------------------------------------------------------------------------------

def cell_variants(side):
    dups = dups_of(side)
    return [(st.cell_id(K, t), "%s fa=bk%d fo=lo fv=%s" % (BASE[side], K, repr(t))) for K, t in st.GRID if not dups[st.cell_id(K, t)]]


def cmd_cells(a):
    w = win(a.window)
    for side in st.SIDES:
        out = p("cells", "%s-%s" % (w.name, side))
        if os.path.exists(out + "/DONE"):
            continue
        tick(w, cell_variants(side), out, {"P42_DETAIL": "1"}, p("log", "cells-%s-%s.log" % (w.name, side)), ftab=p("ftab", "%s-%s.csv" % (w.name, side)))
        open(out + "/DONE", "w").close()
    say("cells %s готово" % w.name)


def cmd_cellsall(a):
    for w in st.TRADE_WINDOWS:
        a.window = w.name
        cmd_cells(a)


def filter_counters(meta_side):
    for k, v in meta_side.items():
        if k.startswith("filter_") and isinstance(v, list) and len(v) == 4:
            return dict(zip(("cands", "dropped", "no_key", "no_value"), v))
    return None


def cmd_g8(a):
    bad, res = [], {}
    for w in st.TRADE_WINDOWS:
        for side in st.SIDES:
            sym, rel, n, k = tf.read_ftab(p("ftab", "%s-%s.csv" % (w.name, side)))
            for K, t in st.GRID:
                cidn = st.cell_id(K, t)
                if dups_of(side)[cidn]:
                    continue
                m = json.load(open(p("cells", "%s-%s" % (w.name, side), cidn, "meta.json"), encoding="utf-8"))[ENG[side]]
                fc = filter_counters(m)
                exp = int(tf.excluded(n, k, K, t).sum())
                res["%s/%s/%s" % (w.name, side, cidn)] = dict(counters=fc, expected_dropped=exp)
                if fc is None or fc["no_key"] != 0 or fc["dropped"] != exp:
                    bad.append("%s/%s/%s: %s, ожидалось dropped %d" % (w.name, side, cidn, fc, exp))
    write_json(p("gates", "g8.json"), dict(res=res, bad=bad))
    say("G8", "FAIL " + "; ".join(bad[:8]) if bad else "ok: no_key = 0 и dropped = независимому пересчёту во всех %d прогонах" % len(res))
    if bad:
        sys.exit(1)


# ---- выбор, свёртки, заморозка -----------------------------------------------------------------------------------------------

def build_side(side):
    base = load_series(side, "base")
    sym = np.array(base.sym_name, dtype=object)
    n, k, _ = feats_at(side, sym, base.key_ms)
    dups = dups_of(side)
    cells = []
    for K, t in st.GRID:
        cidn = st.cell_id(K, t)
        cells.append(st.CellRun(cidn, K, t, None if dups[cidn] else load_series(side, "cell", cidn), tf.excluded(n, k, K, t), dups[cidn]))
    g5 = json.load(open(p("res", "g5-%s.json" % side), encoding="utf-8"))
    return st.SideData(base, cells, {int(K): v for K, v in g5["readable"].items()}, len(coin_list()))


def fmt_sel(sel):
    out = dict(winner=sel.winner, accepted=bool(sel.accepted), reasons=sel.reasons, rows=sel.rows, detail={k: [bool(v[0]), v[1]] for k, v in sel.detail.items()})
    if sel.stat is not None:
        out["stat"] = {f: float(getattr(sel.stat, f)) for f in ("d", "z", "dsum", "se_sum", "s_c", "p") if hasattr(sel.stat, f)}
    return out


def cmd_select(a):
    sides = {s: build_side(s) for s in st.SIDES}
    res, wf = {}, {}
    allom = np.arange(st.NDAYS)
    for s, sd in sides.items():
        sel = st.select_cell(sd, allom)
        wf[s] = st.walk_forward(sd)
        res[s] = dict(select=fmt_sel(sel), n_base=int(len(sd.base)), sum_base=float(sd.base.val.sum()))
        say("== %s: выбор п. 6 на 136 сут.: победитель %s, принят %s; %s" % (s, sel.winner, sel.accepted, sel.reasons))
        for r in sel.rows:
            say("   %s: Δ %s, n_c %d, n_r %d, исключено %d, а1 %s, а2 %s, читается %s" % (r["cid"], "%.4f" % r["d"] if r["d"] == r["d"] else "nan", r["n_c"], r["n_r"], r["n_ex"], r["a1"], r["a2"], r["readable"]))
        for f in wf[s]["folds"]:
            say("   свёртка обучение %s → тест %d: клетка %s (победитель %s) %s" % (f["train"], f["test"], f["cid"], f["winner"], f["reasons"]))
        w_ = wf[s]
        res[s]["wf"] = dict(p=float(w_["p"]), d=float(w_["stat"].d), none=bool(w_["none"]), n_days=w_["n_days"], active_days=w_["active_days"], robust=bool(w_["robust"]),
                            no_best_days=w_["no_best_days"], no_best_coins=w_["no_best_coins"], neg_robust=bool(w_["neg_robust"]), guard=bool(w_["guard"]),
                            mean_sel=float(w_["mean_sel"]), mean_base=float(w_["mean_base"]), folds=[dict(train=f["train"], test=f["test"], cid=f["cid"]) for f in w_["folds"]])
    for s, (txt, ok) in st.verdicts(wf).items():
        res[s]["verdict"] = txt
        say("ИСХОД %s: %s" % (s, txt))
    try:                                                    # фандинг (справочно): граница B июль–август, поле движка — сентябрь–ноябрь
        rates = st.load_funding_rates("/data/p42-metrics/funding", coin_list())
        for s, sd in sides.items():
            win_ = res[s]["select"]["winner"]
            items = [("база", sd.base)] + ([("клетка " + win_, next(c.series for c in sd.cells if c.cid == win_))] if win_ else [])
            for lab, ser in items:
                jm = st.MONTH_OF_DAY[ser.day] <= 1
                b = st.funding_bound(np.array(ser.sym_name, dtype=object)[jm], ser.t_in[jm], ser.t_out[jm], rates)
                say("   фандинг %s %s: B (июль–август) %.2f $; поле движка сент.–нояб. Σ %.2f $" % (s, lab, float(b.sum()), float(ser.fund[~jm].sum())))
    except Exception as e:                                  # noqa: BLE001 — справочно; решений не несёт
        say("фандинг: не посчитан (%s)" % e)
    write_json(p("res", "select.json"), res)


def cmd_freeze(a):
    res = json.load(open(p("res", "select.json"), encoding="utf-8"))
    fr = dict(protocol="docs/research/P-42-level-breakout.md §13.26", frozen_at=time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()), code_sha=a.code_sha,
              bin_sha256=BIN_SHA, symbols_sha256=SYMS_SHA, grid=dict(K=list(st.GRID_K), tau=list(st.GRID_TAU)), sides={})
    for s in st.SIDES:
        r = res[s]
        win_ = r["select"]["winner"] if r["select"]["accepted"] else None
        K = t = None
        if win_:
            K, t = next((K_, t_) for K_, t_ in st.GRID if st.cell_id(K_, t_) == win_)
        fr["sides"][s] = dict(cell=win_, keys=None if not win_ else "%s fa=bk%d fo=lo fv=%s" % (BASE[s], K, repr(t)), base_keys=BASE[s], verdict_p7=r["verdict"],
                              ftab_sha256={w.name: sha256(p("ftab", "%s-%s.csv" % (w.name, s))) for w in st.TRADE_WINDOWS})
    write_json(p("res", "frozen.json"), fr)
    say("заморожено:", json.dumps({s: v["cell"] for s, v in fr["sides"].items()}, ensure_ascii=False))



def cmd_engall(a):
    for w in st.ALLOWED_WINDOWS:
        a.window = w
        cmd_eng(a)


def cmd_epall(a):
    for w in st.ALLOWED_WINDOWS:
        a.window = w
        cmd_ep(a)


COMMANDS = {"gates1": cmd_gates1, "eng": cmd_eng, "ep": cmd_ep, "engall": cmd_engall, "epall": cmd_epall, "g9": cmd_g9, "seam": cmd_seam, "g2": cmd_g2, "g3": cmd_g3,
            "g4": cmd_g4, "g5": cmd_g5, "step1": cmd_step1, "step1ii": cmd_step1ii, "cells": cmd_cells, "cellsall": cmd_cellsall, "g8": cmd_g8, "select": cmd_select,
            "freeze": cmd_freeze}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=sorted(COMMANDS))
    ap.add_argument("window", nargs="?")
    ap.add_argument("--code-sha", default="")
    a = ap.parse_args()
    try:
        COMMANDS[a.cmd](a)
    except st.GuardError as e:
        say("СТОП:", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
