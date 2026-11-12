#!/usr/bin/env python3
"""Тесты census_tails.py (TK-493, T1). Запуск: census_tails.py test  (numpy, секунды; на Deck под nice).
Синтетика с известным ответом: эпизоды, протухшая нога, исходы, единицы «актив x сутки», чтение .sap, сквозная проверка пары. Плюс сверка с конв-рядом подсказок на Deck (gate-hints)."""
import csv
import os
import struct
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import census_tails as C   # noqa: E402


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if extra else ""))
    return bool(cond)


def naive_runs(dev, theta):
    out = []
    i, n = 0, len(dev)
    while i < n:
        if np.isfinite(dev[i]) and abs(dev[i]) >= theta:
            sg = np.sign(dev[i])
            j = i
            while j + 1 < n and np.isfinite(dev[j + 1]) and abs(dev[j + 1]) >= theta and np.sign(dev[j + 1]) == sg:
                j += 1
            out.append((i, j - i + 1))
            i = j + 1
        else:
            i += 1
    return out


def t_runs():
    rng = np.random.default_rng(7)
    ok = True
    for _ in range(20):
        dev = rng.normal(0, 150, 4000)
        dev[rng.random(4000) < 0.1] = np.nan
        for th in (160, 320):
            st, du = C.runs(dev, th)
            ok &= list(zip(st.tolist(), du.tolist())) == naive_runs(dev, th)
    return check("runs = наивный цикл (20 случайных рядов x 2 порога)", ok)


def t_episodes():
    N = 3000
    dev = np.zeros(N)
    ok = np.ones(N, bool)
    vA = np.ones(N, bool)
    vB = np.ones(N, bool)
    dev[1000:1005] = 500.0                  # эпизод 1: 5 минут, живой, к 1005 возвращается
    dev[1005] = 10.0
    vA[1998] = vA[1999] = False             # эпизод 2: единственная минута, ноге A две минуты не торговалась до начала
    ok[1998] = ok[1999] = False
    dev[2000] = -300.0
    ok[2001] = False                        # следующая минута без сделки
    dev[2001] = np.nan
    dev[2900:] = 700.0                      # эпизод 3: до конца данных
    dev = np.where(ok, dev, np.nan)
    E = C.episodes_for(160, dev, np.isfinite(dev), vA, vB, 7)
    r = True
    r &= check("три эпизода", len(E) == 3, len(E))
    r &= check("эпизод 1: t0, D, знак, max", (E["t0"][0], E["D"][0], E["sign"][0], float(E["mx"][0])) == (1000, 5, 1, 500.0))
    r &= check("эпизод 1: живой (gA = gB = 0), исход «ниже порога»", E["gA"][0] == 0 and E["gB"][0] == 0 and E["end"][0] == 1)
    r &= check("эпизод 1: d1 = 500, d5 = 10, d30 = 0", E["d1"][0] == 500.0 and E["d5"][0] == 10.0 and E["d30"][0] == 0.0, (E["d1"][0], E["d5"][0], E["d30"][0]))
    r &= check("эпизод 1: первое |dev| <= 40 на +5", E["fp"][0] == 5, E["fp"][0])
    r &= check("эпизод 2: протух: gA = 2, gB = 0; знак -1; D = 1", (E["gA"][1], E["gB"][1], E["sign"][1], E["D"][1]) == (2, 0, -1, 1))
    r &= check("эпизод 2: исход «нет сделки на ноге», d1 = NaN, первое наблюдение <= 40 на +2", E["end"][1] == 3 and np.isnan(E["d1"][1]) and E["fp"][1] == 2, (E["end"][1], E["d1"][1], E["fp"][1]))
    r &= check("эпизод 3: до конца данных, D = 100, d120 = NaN, fp = -1", (E["t0"][2], E["D"][2], E["end"][2], E["fp"][2]) == (2900, 100, 0, -1) and np.isnan(E["d120"][2]))
    return r


def t_summary_units():
    # два актива, пары 0, 1 -> актив 0; пара 2 -> актив 1; эпизоды покрывают сутки (бит 0 = D0)
    asset = np.array([0, 0, 1], dtype=np.int64)
    expair = np.array([0, 1, 0], dtype=np.int64)
    E = np.zeros(4, dtype=C.EP_DTYPE)
    E["pair"] = [0, 1, 2, 2]
    E["t0"] = [100, 1500, 1439 + 1440 * 20, 3000]
    E["D"] = [3, 2, 5, 1]
    E["mx"] = [1100, 1200, 1500, 2100]
    E["gA"] = [0, 4, 0, 3]
    E["gB"] = [0, 0, 0, 0]
    E["d1"] = E["d5"] = E["d30"] = E["d120"] = np.nan
    E["fp"] = -1
    ua, am, al, ast, al2 = C.asset_masks(E, asset)
    r = True
    r &= check("маски по активам: актив 0 - сутки 0 и 1 (бит 0, 1); актив 1 - сутки 2, 20, 21", list(am) == [0b11, (1 << 2) | (1 << 20) | (1 << 21)], list(am))
    r &= check("живые: актив 0 - сутки 0; актив 1 - сутки 20, 21 (эпизод через полночь)", list(al) == [0b1, (1 << 20) | (1 << 21)], list(al))
    r &= check("только протухшие: сутки 1 актива 0 и сутки 2 актива 1", list(ast) == [0b10, 1 << 2], list(ast))
    r &= check("живые длиной >= 2: актив 0 - сутки 0 (D = 3), актив 1 - сутки 20, 21 (D = 5)", list(al2) == [0b1, (1 << 20) | (1 << 21)], list(al2))
    r &= check("серии суток (спеллы): 0b11 -> 1; {2}, {20, 21} -> 2; всего 3", C.spells(am) == 3, C.spells(am))
    s = C.summarize(E, 1000, asset, expair, C.ALL_MASK)
    r &= check("summarize: единицы = 5, живых = 3, только протухших = 2, активов 2", (s["units"], s["units_live"], s["units_stale_only"], s["assets"]) == (5, 3, 2, 2), (s["units"], s["units_live"], s["units_stale_only"]))
    r &= check("summarize: D_le1 = 1, D_le2 = 2, D_le5 = 4; stale_any = 2", (s["D_le1"], s["D_le2"], s["D_le5"], s["stale_any"]) == (1, 2, 4, 2))
    sf = C.summarize(E, 1000, asset, expair, C.FORM_MASK)
    r &= check("summarize: период ограничивает единицы масками (формирование = биты 0..13): 3 единицы (сутки 0, 1, 2)", sf["units"] == 3, sf["units"])
    return r


def write_sap(path, first, o, c):
    with open(path, "wb") as f:
        f.write(b"SAP1" + struct.pack("<I", 0) + struct.pack("<qQ", first, len(c)))
        f.write(np.asarray(o, dtype="<f8").tobytes())
        f.write(np.asarray(c, dtype="<f8").tobytes())


def t_load_close():
    d = tempfile.mkdtemp(prefix="census_t_")
    base = C.D0 * C.MIN_DAY
    n = 5000
    c = np.arange(1, n + 1, dtype=float)
    c[100] = np.nan
    p = os.path.join(d, "a.sap")
    write_sap(p, base + 300, c * 2, c)           # спан начинается на 300-й минуте окна
    x = C.load_close(p)
    r = True
    r &= check("load_close: длина 50 400, NaN до начала спана и после его конца", len(x) == C.NM and np.isnan(x[:300]).all() and np.isnan(x[300 + n:]).all())
    r &= check("load_close: close, а не open; дыра сохранена", x[300] == 1.0 and x[300 + 4999] == 5000.0 and np.isnan(x[400]))
    p2 = os.path.join(d, "b.sap")
    write_sap(p2, base - 100, np.arange(1, 201, dtype=float), np.arange(1001, 1201, dtype=float))   # начинается за 100 минут до окна
    y = C.load_close(p2)
    r &= check("load_close: спан, начавшийся до окна, обрезается слева (x[0] = close 101-й строки)", y[0] == 1101.0 and y[99] == 1200.0 and np.isnan(y[100]))
    return r


def t_pair_end_to_end():
    # две ноги, постоянное отношение; нога A: +5 % на 3 минуты (минуты 5000..5002), ещё один выброс после пустой минуты на ноге B
    N = C.NM
    cA = np.full(N, 100.0)
    cB = np.full(N, 50.0)
    cA[5000:5003] *= 1.05
    cB[8000] = np.nan                       # B не торговалась на минуте 8000
    cA[8001] *= 1.10
    s, dev, ok, vA, vB = C.pair_series(cA, cB)
    r = True
    th = 1e4 * np.log(1.05)
    r &= check("база постоянного спреда = медиана, dev = 0 вне выбросов", abs(dev[4000]) < 1e-9 and abs(dev[9000]) < 1e-9)
    E = C.episodes_for(320, dev, ok, vA, vB, 0)
    r &= check("два эпизода при theta 320", len(E) == 2, len(E))
    r &= check("эпизод 1: D = 3, max = 1e4 ln 1,05, живой, исход «ниже порога», d5 = 0", E["D"][0] == 3 and abs(E["mx"][0] - th) < 1e-3 and E["gB"][0] == 0 and E["end"][0] == 1 and abs(E["d5"][0]) < 1e-6, (E["D"][0], E["mx"][0], E["d5"][0]))
    r &= check("эпизод 2: на минуте 8001, нога B молчала 1 минуту до начала -> gB = 1, max = 1e4 ln 1,10", E["t0"][1] == 8001 and E["gB"][1] == 1 and E["gA"][1] == 0 and abs(E["mx"][1] - 1e4 * np.log(1.10)) < 1e-3, (E["t0"][1], E["gB"][1], E["mx"][1]))
    return r


def t_events():
    meta = {"asset": np.array([0, 0, 0, 1]), "ex1": ["binance", "binance", "bybit", "okx"], "ex2": ["bybit", "okx", "okx", "bingx"],
            "key": ["X", "X", "X", "Y"], "sym1": ["", "", "", ""], "sym2": ["", "", "", ""], "path1": [""] * 4, "path2": [""] * 4}
    E = np.zeros(5, dtype=C.EP_DTYPE)
    E["pair"] = [0, 1, 2, 3, 0]
    E["t0"] = [100, 100, 101, 100, 5000]
    E["D"] = [5, 3, 2, 4, 2]
    E["mx"] = [900, 800, 100, 950, 400]
    ev = C.top_events(E, meta, 10)
    r = True
    r &= check("события: Y (950) первым, X (900) вторым, затем X в минуту 5000", [meta["key"][int(e["rep"]["pair"])] for e in ev] == ["Y", "X", "X"], [meta["key"][int(e["rep"]["pair"])] for e in ev])
    x = ev[1]
    r &= check("событие X: 3 эпизода в 3 парах; общей биржи у пар (binance+bybit), (binance+okx), (bybit+okx) нет", x["n_ep"] == 3 and x["n_pairs"] == 3 and x["common"] == [], (x["n_ep"], x["common"]))
    return r


def main():
    res = [t_runs(), t_episodes(), t_summary_units(), t_load_close(), t_pair_end_to_end(), t_events()]
    print("тестов групп: %d, провалено: %d" % (len(res), sum(1 for x in res if not x)))
    return 0 if all(res) else 1


def gate_hints(root, limit):
    """G-T1-1: те же ряды, что в отчёте подсказок (theta_all.csv, var = rel, theta 40, 80, 160): число эпизодов и деление по длине на формировании. Ряд обрезан по 2478, как у подсказок."""
    ref = os.path.join(root, "audit3", "hints", "crypto")
    pairs = list(csv.DictReader(open(os.path.join(ref, "pairs_all.csv"), encoding="utf-8")))
    ths = (40, 80, 160)
    theta = {}
    for rr in csv.DictReader(open(os.path.join(ref, "theta_all.csv"), encoding="utf-8")):
        if rr["var"] == "rel" and int(float(rr["theta"])) in ths:
            theta[(rr["ex1"], rr["sym1"], rr["ex2"], rr["sym2"], int(float(rr["theta"])))] = rr
    if limit and len(pairs) > limit:
        step = len(pairs) / float(limit)
        pairs = [pairs[int(i * step)] for i in range(limit)]
    bad, n = [], 0
    tot = {th: [0, 0, 0, 0] for th in ths}
    for p in pairs:
        fa = os.path.join(root, "panels", p["ex1"], p["sym1"] + ".sap")
        fb = os.path.join(root, "panels", p["ex2"], p["sym2"] + ".sap")
        if not (os.path.isfile(fa) and os.path.isfile(fb)):
            continue
        cA, cB = C.load_close(fa)[:C.SPLIT], C.load_close(fb)[:C.SPLIT]
        s, dev, ok, vA, vB = C.pair_series(cA, cB)
        n += 1
        for th in ths:
            k = (p["ex1"], p["sym1"], p["ex2"], p["sym2"], th)
            rr = theta.get(k)
            if rr is None:
                continue
            E = C.episodes_for(th, dev, ok, vA, vB, 0)
            mine = (len(E), int((E["D"] == 1).sum()), int((E["D"] == 2).sum()), int((E["D"] >= 3).sum()))
            ref_v = tuple(int(float(rr[c])) for c in ("n_ep", "n_d1", "n_d2", "n_d3p"))
            for i in range(4):
                tot[th][i] += mine[i]
            if mine != ref_v:
                bad.append((k, ref_v, mine))
    print("G-T1-1: пар %d; эпизодов (всего, D=1, D=2, D>=3) по theta: %s; расхождений %d" % (n, tot, len(bad)))
    for b in bad[:10]:
        print("  ", b)
    return 0 if not bad and n > 0 else 1


def naive_base(s):
    """База без conv_lib: медиана по конечным s за [t-1440, t), пересчёт на t кратных 5, нужно >= 240 наблюдений; между пересчётами держится."""
    N = len(s)
    b = np.full(N, np.nan)
    for t in range(0, N, 5):
        w = s[max(t - 1440, 0):t]
        w = w[np.isfinite(w)]
        v = float(np.median(w)) if len(w) >= 240 else np.nan
        b[t:t + 5] = v
    return b


def naive_episode_table(dev, vA, vB, theta):
    """Эпизоды и все признаки наивными циклами: (t0, D, знак, max, gA, gB, end, d1, d5, d30, d120, fp)."""
    N = len(dev)
    out = []
    for (t0, D) in naive_runs(dev, theta):
        sg = int(np.sign(dev[t0]))
        mx = max(abs(dev[t0 + k]) for k in range(D))
        gs = []
        for v in (vA, vB):
            g, j = 0, t0 - 1
            while j >= 0 and not v[j]:
                g += 1
                j -= 1
            gs.append(32000 if j < 0 else g)
        e = t0 + D
        if e >= N:
            end = 0
        elif not np.isfinite(dev[e]):
            end = 3
        elif abs(dev[e]) < theta:
            end = 1
        else:
            end = 2
        dh = [dev[t0 + h] if t0 + h < N else np.nan for h in C.CONV_H]
        fp = -1
        for j in range(t0 + 1, N):
            if np.isfinite(dev[j]) and abs(dev[j]) <= theta / 4.0:
                fp = j - t0
                break
        out.append((t0, D, sg, mx, gs[0], gs[1], end, dh, fp))
    return out


def indep(root, out, n_rand):
    """G-T1-2: независимая реализация (наивный цикл для базы и всех признаков) против census_tails на 35 сутках (в т.ч. на окне): случайные пары и пары с крупнейшими эпизодами."""
    npz = np.load(os.path.join(out, "episodes.npz"))
    pairs = list(csv.DictReader(__import__("gzip").open(os.path.join(out, "pairs.csv.gz"), "rt", encoding="utf-8")))
    ids = set()
    E = npz["th160"]
    for p in np.unique(E["pair"][np.argsort(-E["mx"], kind="stable")[:400]]).tolist()[:25]:
        ids.add(int(p))
    rng = np.random.default_rng(37)
    for p in rng.choice(len(pairs), size=n_rand, replace=False).tolist():
        ids.add(int(p))
    bad, n_ep, n_pairs = [], 0, 0
    for pid in sorted(ids):
        pr = pairs[pid]
        cA = C.load_close(os.path.join(root, "panels", pr["ex1"], pr["sym1"] + ".sap"))
        cB = C.load_close(os.path.join(root, "panels", pr["ex2"], pr["sym2"] + ".sap"))
        s, dev, ok, vA, vB = C.pair_series(cA, cB)
        valid = np.isfinite(cA) & np.isfinite(cB) & (cA > 0) & (cB > 0)
        s2 = np.full(len(cA), np.nan)
        s2[valid] = 1e4 * (np.log(cA[valid]) - np.log(cB[valid]))
        b2 = naive_base(s2)
        dev2 = s2 - b2
        same = np.array_equal(np.isfinite(dev), np.isfinite(dev2)) and (np.nanmax(np.abs(dev - dev2)) < 1e-6 if np.isfinite(dev).any() else True)
        if not same:
            bad.append((pid, "dev"))
            continue
        n_pairs += 1
        for th in C.THETAS:
            mine = C.episodes_for(th, dev, ok, vA, vB, pid)
            ref = naive_episode_table(dev2, vA, vB, th)
            n_ep += len(ref)
            if len(mine) != len(ref):
                bad.append((pid, th, "число", len(mine), len(ref)))
                continue
            for e, r in zip(mine, ref):
                got = (int(e["t0"]), int(e["D"]), int(e["sign"]), float(e["mx"]), int(e["gA"]), int(e["gB"]), int(e["end"]), int(e["fp"]))
                want = (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[8])
                if got[:3] != want[:3] or abs(got[3] - want[3]) > 1e-3 * max(1.0, want[3]) or got[4:] != want[4:]:
                    bad.append((pid, th, got, want))
                    continue
                for h, dv in zip(C.CONV_H, r[7]):
                    x = float(e["d%d" % h])
                    if not ((np.isnan(x) and np.isnan(dv)) or abs(x - dv) < 1e-3 * max(1.0, abs(dv))):
                        bad.append((pid, th, "d%d" % h, x, dv))
    print("G-T1-2: пар %d, эпизодов (все пороги) %d, расхождений %d" % (n_pairs, n_ep, len(bad)))
    for b in bad[:10]:
        print("  ", b)
    return 0 if not bad and n_pairs > 0 else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "gate-hints":
        sys.exit(gate_hints(os.path.expanduser("~/xdl9"), int(sys.argv[2]) if len(sys.argv) > 2 else 0))
    if len(sys.argv) > 1 and sys.argv[1] == "indep":
        sys.exit(indep(os.path.expanduser("~/xdl9"), sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 15))
    sys.exit(main())
