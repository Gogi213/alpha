#!/usr/bin/env python3
"""П-42 §13.68 (TK-668): ворота без P&L — G-src, G-n, G-lvl, G-off1, G-causal (§13.68 п. 8). Запуск на srv: `python3 tk668_gate.py` → `/data/p42-tk668/gates/g68.json`, код возврата ≠ 0 при провале.
G-causal: признаки монеты и BTC (X24, X7, XP, XV, r_h, σ_h) — подмена минуток ≥ t мусором не меняет значение (точное равенство); R — прогон движка (дамп уровней) на кэше с мусором с минуты отсечки монеты:
значения R и «свой уровень найден» у строк с `t ≤ отсечки` не меняются."""
from __future__ import annotations

import csv
import datetime
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk668_feat as ft  # noqa: E402
import tk668_run as r  # noqa: E402

SEED = 668
MIN_ROWS = 300
KLINES = "/data/klines/binance/BTCUSDT"


def same(a, b):
    return (a != a and b != b) or a == b or (np.isinf(a) and np.isinf(b) and np.sign(a) == np.sign(b))


def write_b42(path, a, tail):
    n = len(a["o"])
    with open(path, "wb") as f:
        f.write(b"B42\x01" + n.to_bytes(4, "little"))
        f.write(np.stack([a["o"], a["h"], a["l"], a["c"], a["qv"]]).astype("<i8").tobytes())
        f.write(tail)


def read_tail(path, n):
    sz = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(8 + 40 * n)
        return f.read(sz - 8 - 40 * n)


def g_src(u):
    out, bad = {}, []
    files = []
    for w in st.TRADE_WINDOWS:
        files.append(r.cache_path(u, w.name, "BTCUSDT"))
        for s in r.SIDES:
            files += [r.sigsx_path(u, w.name, s), r.levels_path(w.name, s), r.feat_path(w.name, s), os.path.join(r.FEAT34, r.UNI, "%s-%s.csv" % (w.name, s)),
                      os.path.join(u.base(w.name, s), r.ENG[s] + "-trades.csv")]
    for f in files:
        if not os.path.exists(f):
            bad.append("нет файла %s" % f)
        else:
            out[f] = r.sha256(f)
    out["bin"] = r.sha256(r4.BIN)
    return out, bad


def g_n(u):
    """G-n: сделки баз 319 / 428 (TK-662); ключи таблицы признаков = ключам дампа сигналов = ключам таблицы признаков TK-634; событий rt (оценка TK-663 188 — разбор в counts)."""
    res, bad = {}, []
    for s in r.SIDES:
        n = len(r.base_trades(u, s))
        res["trades_%s" % s] = n
        if n != r.G_TRADES[s]:
            bad.append("G-n: сделок базы %s %d ≠ %d" % (s, n, r.G_TRADES[s]))
        for w in st.TRADE_WINDOWS:
            rows, dups = r.read_sigsx(r.sigsx_path(u, w.name, s))
            tab = r.load_feat(w.name, s)
            k_sig = [(x["sym"], x["sig_ms"]) for x in rows]
            k_tab = list(zip(tab["sym"], [int(m) for m in tab["sig_ms"]]))
            import pandas as pd
            d34 = pd.read_csv(os.path.join(r.FEAT34, r.UNI, "%s-%s.csv" % (w.name, s)), usecols=["sym", "sig_ms"], dtype={"sym": str})
            k34 = list(zip(d34["sym"], [int(m) for m in d34["sig_ms"]]))
            res["keys_%s-%s" % (w.name, s)] = dict(sigsx=len(k_sig), feat=len(k_tab), tk634=len(k34), dups=dups, equal=bool(k_sig == k_tab and set(k_sig) == set(k34)))
            if not (k_sig == k_tab and set(k_sig) == set(k34)):
                bad.append("G-n: ключи %s-%s: sigsx %d, признаки %d, tk634 %d" % (w.name, s, len(k_sig), len(k_tab), len(k34)))
    res["rt_events"] = len(r.e8_events(u))
    return res, bad


def g_lvl(u):
    """G-lvl: у каждой строки дампа сигналов найден свой уровень, живой на минуте сигнала (born ≤ t < end), с ценой версии = |level_px_e8| — 100 %; шорт: цена > 0, следующий уровень ниже P (лонг — выше)."""
    res, bad = {}, []
    for s in r.SIDES:
        n = n_own = n_pos = n_dir = n_next = 0
        for w in st.TRADE_WINDOWS:
            tab = r.load_feat(w.name, s)
            for i in range(len(tab["sym"])):
                n += 1
                n_own += int(tab["own"][i] == 1)
                n_pos += int(tab["px_e8"][i] > 0)
                if tab["pnext"][i] > 0:
                    n_next += 1
                    n_dir += int((tab["pnext"][i] > tab["px_e8"][i]) if s == "L" else (tab["pnext"][i] < tab["px_e8"][i]))
        res[s] = dict(rows=n, own_found=n_own, price_positive=n_pos, with_next=n_next, next_direction_ok=n_dir)
        if n_own != n or n_pos != n or n_dir != n_next:
            bad.append("G-lvl %s: %s" % (s, res[s]))
    return res, bad


def g_off1(u, k_kl=150):
    """G-off1: (а) формула при h = 5 / 15 = готовым `btc_mv5` / `btc_mv15` f12 на всех строках эпизодов; (б) r_1 отличается от сдвига на минуту (назад и вперёд) на ≥ 99 % строк;
    (в) r_h = независимому пересчёту из klines BTCUSDT на ≥ 120 строках (точное совпадение закрытий — допуск 1e-9)."""
    import pandas as pd
    res, bad = dict(geq={}, shift={}, klines={}), []
    rng = np.random.default_rng(SEED)
    pool = []
    for w in st.TRADE_WINDOWS:
        cf, r1 = r.btc_arrays(u, w)
        for s in r.SIDES:
            eps = pd.read_parquet(os.path.join(r.FEAT34, r.UNI, "%s-%s" % (w.name, s), "eps-%s.parquet" % s), columns=["sym", "sig_ms", "btc_mv5", "btc_mv15"])
            for h in (5, 15):
                mine = np.array([ft.btc_shock(cf, r1, int(m) // ft.MIN_MS, h)[0] * 1e4 * r.SIGN[s] for m in eps["sig_ms"]])
                old = eps["btc_mv%d" % h].to_numpy(np.float64)
                both = np.isfinite(mine) & np.isfinite(old)
                d = dict(rows=int(len(mine)), both=int(both.sum()), only_mine=int((np.isfinite(mine) & ~np.isfinite(old)).sum()), only_old=int((~np.isfinite(mine) & np.isfinite(old)).sum()),
                         max_abs=float(np.abs(mine[both] - old[both]).max()) if both.any() else 0.0)
                res["geq"]["%s-%s/btc_mv%d" % (w.name, s, h)] = d
                if d["max_abs"] > 1e-9 or d["only_old"] or d["both"] == 0:
                    bad.append("G-off1 (а) %s-%s h=%d: %s" % (w.name, s, h, d))
            rows = r.load_feat(w.name, s)
            for i in range(len(rows["sym"])):
                pool.append((w, s, int(rows["sig_ms"][i]), rows["r1"][i], rows["r5"][i], rows["r15"][i], cf))
    # (б) сдвиг
    diff_b = diff_f = n = 0
    for w, s, m, r1_, _, _, cf in pool:
        t = m // ft.MIN_MS
        if t < 4 or t >= len(cf) or not np.isfinite(r1_):
            continue
        back = cf[t - 2] / cf[t - 3] - 1.0
        fwd = cf[t] / cf[t - 1] - 1.0
        n += 1
        diff_b += int(r1_ != back)
        diff_f += int(r1_ != fwd)
    res["shift"] = dict(rows=n, differs_back=diff_b / n, differs_fwd=diff_f / n)
    if diff_b / n < 0.99 or diff_f / n < 0.99:
        bad.append("G-off1 (б): отличие от сдвига назад %.3f, вперёд %.3f (< 0,99)" % (diff_b / n, diff_f / n))
    # (в) klines
    cache = {}

    def kl_close(day, open_ms):
        if day not in cache:
            p = os.path.join(KLINES, day + ".csv")
            cache[day] = {int(x["open_time"]): float(x["close"]) for x in csv.DictReader(open(p))} if os.path.exists(p) else {}
        return cache[day].get(open_ms)
    cand = []
    for w, s, m, r1_, r5_, r15_, cf in pool:
        t = m // ft.MIN_MS
        day = datetime.datetime.fromtimestamp((w.t0_ms + (t - 1) * 60000) / 1000, datetime.timezone.utc).date().isoformat()
        if os.path.exists(os.path.join(KLINES, day + ".csv")):
            cand.append((w, s, m, r1_, r5_, r15_))
    pick = rng.choice(len(cand), size=min(k_kl, len(cand)), replace=False)
    ok = tot = 0
    for j in pick:
        w, s, m, r1_, r5_, r15_ = cand[j]
        t = m // ft.MIN_MS
        for h, mine in ((1, r1_), (5, r5_), (15, r15_)):
            a_ms, b_ms = w.t0_ms + (t - 1) * 60000, w.t0_ms + (t - 1 - h) * 60000
            ca = kl_close(datetime.datetime.fromtimestamp(a_ms / 1000, datetime.timezone.utc).date().isoformat(), a_ms)
            cb = kl_close(datetime.datetime.fromtimestamp(b_ms / 1000, datetime.timezone.utc).date().isoformat(), b_ms)
            if ca is None or cb is None or not np.isfinite(mine):
                continue
            tot += 1
            ok += int(abs((ca / cb - 1.0) - mine) <= 1e-9 + 1e-9 * abs(mine))
    res["klines"] = dict(rows_sampled=int(len(pick)), values_checked=tot, equal=ok)
    if len(pick) < 120 or ok < tot:
        bad.append("G-off1 (в): klines — строк %d (нужно ≥ 120), значений %d, совпало %d" % (len(pick), tot, ok))
    return res, bad


def g_causal_arrays(u, n_per_window=110):
    """G-causal (признаки по минуткам): мусор в минутках ≥ t (монета и BTC) не меняет X24, X7, XP, XV, r_h, σ_h — строки по окнам и сторонам, зерно 668."""
    rng = np.random.default_rng(SEED)
    res, bad, rows_total = {}, [], 0
    for w in st.TRADE_WINDOWS:
        cf0, r10 = r.btc_arrays(u, w)
        a0 = ft.load_b42_full(r.cache_path(u, w.name, "BTCUSDT"))
        allrows = []
        for s in r.SIDES:
            rows, _ = r.read_sigsx(r.sigsx_path(u, w.name, s))
            allrows += [(s, x) for x in rows]
        idx = rng.choice(len(allrows), size=min(n_per_window, len(allrows)), replace=False)
        nbad = 0
        for j in idx:
            s, x = allrows[j]
            t = x["sig_ms"] // ft.MIN_MS
            natr = x["natr_e8"] / 1e8
            sg = r.SIGN[s]
            a = ft.load_b42_full(r.cache_path(u, w.name, x["sym"]))
            ref = ft.place_features(ft.Mins(a), t, sg, natr)
            g = ft.place_features(ft.Mins(ft.garbage_from(a, t, rng)), t, sg, natr)
            refb = [ft.btc_shock(cf0, r10, t, h) for h in ft.HORIZONS]
            cfg = ft.ffill_close(*(lambda b: (b["o"], b["c"]))(ft.garbage_from(a0, t, rng)))
            gb = [ft.btc_shock(cfg, ft.minute_returns(cfg), t, h) for h in ft.HORIZONS]
            if not all(same(p, q) for p, q in zip(ref, g)) or not all(same(p, q) for pp, qq in zip(refb, gb) for p, q in zip(pp, qq)):
                nbad += 1
        res[w.name] = dict(k=int(len(idx)), bad=nbad)
        rows_total += len(idx)
        if nbad:
            bad.append("G-causal (минутки) %s: %d из %d" % (w.name, nbad, len(idx)))
    res["rows_total"] = rows_total
    return res, bad


def g_causal_levels(u, n_rows_window=110):
    """G-causal (R): на окно — набор монет с ≥ 110 строками дампа; кэш монет подменён мусором с минуты `T_c` = максимальное `t` строк монеты; движок (база, обе стороны, `P42_LEVELS=1`) → R и «свой уровень» у всех строк монеты = эталону."""
    rng = np.random.default_rng(SEED + 1)
    res, bad = {}, []
    for w in st.TRADE_WINDOWS:
        by = {}
        for s in r.SIDES:
            rows, _ = r.read_sigsx(r.sigsx_path(u, w.name, s))
            for x in rows:
                by.setdefault(x["sym"], []).append((s, x))
        syms = sorted(by)
        rng.shuffle(syms)
        pick, n = [], 0
        for sy in syms:
            if sy == "BTCUSDT":
                continue
            pick.append(sy)
            n += len(by[sy])
            if n >= n_rows_window:
                break
        base = os.path.join(r.ROOT, "causal", w.name)
        os.makedirs(base + "/cache", exist_ok=True)
        cuts = {}
        for sy in pick:
            a = ft.load_b42_full(r.cache_path(u, w.name, sy))
            tail = read_tail(r.cache_path(u, w.name, sy), len(a["o"]))
            cut = max(x["sig_ms"] // ft.MIN_MS for _, x in by[sy])
            cuts[sy] = cut
            write_b42(os.path.join(base, "cache", sy + ".b42"), ft.garbage_from(a, cut, rng), tail)
        out = os.path.join(base, "out")
        if not os.path.exists(out + "/DONE"):
            vf = out + ".variants.txt"
            os.makedirs(out, exist_ok=True)
            with open(vf, "w", encoding="utf-8") as f:
                f.write("".join("%s %s\n" % (r.VID[s], r.BASE[s] + u.extra) for s in r.SIDES))
            cmd = [r4.BIN, "breakout", "tick", "--cache", base + "/cache", "--trades", u.wdir(w.name) + "/aggtrades", "--variants", vf, "--symbols", ",".join(pick), "--out", out,
                   "--threads", str(r4.THREADS)] + r5.TICK_STORE
            r4.run(cmd, {"P42_T0": w.t0, "P42_LEVELS": "1"}, os.path.join(base, "engine.log"))
            open(out + "/DONE", "w").close()
        nbad = rows_n = changed = 0
        for s in r.SIDES:
            lv_g = ft.parse_levels(os.path.join(out, r.VID[s], r.ENG[s] + "-levels.csv"))
            lv_0 = ft.parse_levels(r.levels_path(w.name, s))
            for sy in pick:
                if sy in lv_g and (len(lv_g[sy].born) != len(lv_0[sy].born) or not np.array_equal(lv_g[sy].b, lv_0[sy].b) or not np.array_equal(lv_g[sy].e, lv_0[sy].e)):
                    changed += 1            # мусор действительно изменил уровни монеты после отсечки (тест способен упасть)
                for ss, x in by[sy]:
                    if ss != s:
                        continue
                    t = x["sig_ms"] // ft.MIN_MS
                    natr = x["natr_e8"] / 1e8
                    ref = ft.room_to_next(lv_0[sy], x["level"], t, abs(x["px_e8"]), 1 if s == "L" else -1, natr)
                    got = ft.room_to_next(lv_g[sy], x["level"], t, abs(x["px_e8"]), 1 if s == "L" else -1, natr) if sy in lv_g and x["level"] < len(lv_g[sy].born) else (float("nan"), None, False)
                    rows_n += 1
                    if not (same(ref[0], got[0]) and ref[2] == got[2]):
                        nbad += 1
        res[w.name] = dict(coins=len(pick), rows=rows_n, bad=nbad, coin_sides_levels_changed_by_garbage=changed)
        if nbad:
            bad.append("G-causal (R) %s: %d из %d" % (w.name, nbad, rows_n))
    res["rows_total"] = sum(v["rows"] for v in res.values() if isinstance(v, dict))
    return res, bad


def main():
    u = r.uni()
    out, bad = {}, []
    for name, fn in (("G-src", g_src), ("G-n", g_n), ("G-lvl", g_lvl), ("G-off1", g_off1), ("G-causal-arrays", g_causal_arrays), ("G-causal-levels", g_causal_levels)):
        res, b = fn(u)
        out[name] = res
        bad += b
        r.say(name, "FAIL " + "; ".join(b) if b else "ok")
    out["bad"] = bad
    r.write_json(r.qq("gates", "g68.json"), out)
    r.say("ворота:", "FAIL " + "; ".join(bad) if bad else "все ok")
    if out["G-causal-arrays"]["rows_total"] < MIN_ROWS or out["G-causal-levels"]["rows_total"] < MIN_ROWS:
        r.say("G-causal: строк меньше %d" % MIN_ROWS)
        sys.exit(1)
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
