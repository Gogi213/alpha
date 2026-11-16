"""Проверки счёта эпизодов arb-holding двумя независимыми способами.

  raw  — ручная выборка: N эпизодов (стратифицировано по длительности, θ, парам) пересчитываются заново по СЫРЫМ строкам `b` файлов сборщика (tsv.zst), без разобранных npz и без hold_scan:
         свой разбор строки, свои состояния ног, своя прогонка; сравнение начала, длительности и максимума разрыва с таблицей (допуск 1 мс / 0,05 bps) и показ границ (строки до / после).
         Запуск: python3 hold_check.py raw <flag.pkl> <книги (с parsed/)> <N> <seed> <выход.md>
  grid — сверка счёта по сетке 1 с с эпизодами по событиям: строгие (все, в том числе короче 1 с), допускающие (провалы < 1 с не рвут) и серии подряд идущих секунд; точный учёт «кто в кого входит».
         Запуск: python3 hold_check.py grid <книги (с parsed/)> <t0_мс> <t1_мс> монета:биржаA:биржаB[,…]

Связи (доказуемые): эпизод по событиям длительностью ≥ N с содержит ≥ N целых секунд, значит лежит внутри серии сетки ≥ N; серия сетки не пересекает провал ≥ 1 с (в нём есть целая секунда) и потому лежит внутри допускающего эпизода (провалы < 1 с).
Расхождение числа серий сетки и эпизодов — только склейка (несколько эпизодов в одной серии, если провал между ними короче секунды и в него не попала целая секунда) и дробление (допускающий эпизод режется серии, если в провал < 1 с попала секунда).
"""
import bisect
import glob
import random
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from hold_common import ROOTS5, all_meta, norm5  # noqa: E402

DAYS = ["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16"]
TOL_MS = 1000


# ---------- raw: пересчёт по сырым строкам ----------

def files_for(parsed, ex, sym, lo, hi):
    """Файлы сборщика (путь), чьи разобранные куски символа пересекают [lo, hi] по E — только указатель, какие сырые файлы читать."""
    out = set()
    for f in glob.glob(str(Path(parsed, ex, sym + ".*.npz"))):
        m = re.match(r"^(.+)\.(\d{4}-\d{2}-\d{2})-(book\d+-\d+)\.(\d+)\.npz$", Path(f).name)
        if not m:
            continue
        z = np.load(f)
        e = z["e"]
        if len(e) and e[0] <= hi and e[-1] >= lo:
            out.add(f"{ROOTS5[ex]}/{m.group(2)}/{m.group(3)}.tsv.zst")
    return sorted(out)


def read_rows(path, wants):
    """wants: {sym(bytes): [(lo, hi), …]} -> {sym: [(E, rx, bid, ask), …]} по сырым строкам `b`: E = накопленная сумма dns по ВСЕМ строкам // 1e6 − lag."""
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    cum, res = 0, {s: [] for s in wants}
    for line in p.stdout:
        x = line.split(b"\t")
        cum += int(x[1])
        if x[0] != b"b":
            continue
        w = wants.get(x[4])
        if w is None:
            continue
        rx = cum // 1_000_000
        e = rx - int(x[2])
        if any(lo <= e <= hi for lo, hi in w):
            res[x[4]].append((e, rx, float(x[6]), float(x[8])))
    p.wait()
    return res


def derive(rowsA, rowsB, mA, mB, th, side, t_hit):
    """Независимая прогонка: состояния ног по (E, rx), событие = любое E; эпизод = подряд идущие события с разрывом ≥ θ, содержащий t_hit.
    -> dict(t, dur, edgemax, dips (список провалов < 1 с внутри допускающего эпизода), tol_dur, nbefore, nafter, rows) или None."""
    rowsA = sorted(r for r in rowsA if 0 < r[2] <= r[3])
    rowsB = sorted(r for r in rowsB if 0 < r[2] <= r[3])
    if not rowsA or not rowsB:
        return None
    eA, eB = [r[0] for r in rowsA], [r[0] for r in rowsB]
    T = sorted(set(eA) | set(eB))
    states = []                                  # (t, edge)
    for t in T:
        ia, ib = bisect.bisect_right(eA, t) - 1, bisect.bisect_right(eB, t) - 1
        if ia < 0 or ib < 0:
            continue
        bidA, askA, bidB, askB = rowsA[ia][2] / mA, rowsA[ia][3] / mA, rowsB[ib][2] / mB, rowsB[ib][3] / mB
        e = (bidA / askB - 1) * 1e4 if side == 1 else -(askA / bidB - 1) * 1e4
        states.append((t, e))
    k = bisect.bisect_right([s[0] for s in states], t_hit) - 1
    if k < 0 or states[k][1] < th:
        return {"found": False, "edge_at_hit": states[k][1] if k >= 0 else None}
    a = k
    while a > 0 and states[a - 1][1] >= th:
        a -= 1
    b = k
    while b + 1 < len(states) and states[b + 1][1] >= th:
        b += 1
    t_end = states[b + 1][0] if b + 1 < len(states) else None
    # допускающий вариант: расширяем через провалы < 1 с
    a2, b2, dips = a, b, []
    while True:
        j = b2 + 1                               # первое событие после конца строгого участка (оно ниже порога)
        if j >= len(states):
            break
        j2 = j
        while j2 + 1 < len(states) and states[j2 + 1][1] < th:
            j2 += 1
        nxt = states[j2 + 1][0] if j2 + 1 < len(states) else None
        if nxt is not None and nxt - states[j][0] < TOL_MS and j2 + 1 < len(states) and states[j2 + 1][1] >= th:
            dips.append(nxt - states[j][0])
            b2 = j2 + 1
            while b2 + 1 < len(states) and states[b2 + 1][1] >= th:
                b2 += 1
        else:
            break
    t_end2 = states[b2 + 1][0] if b2 + 1 < len(states) else None
    return {"found": True, "t": states[a][0], "dur": None if t_end is None else t_end - states[a][0], "edgemax": max(s[1] for s in states[a:b + 1]),
            "tol_dur": None if t_end2 is None else t_end2 - states[a][0], "dips": dips,
            "before": states[a - 1] if a > 0 else None, "after": states[b + 1] if b + 1 < len(states) else None, "nev": b - a + 1}


def pick(df, n, seed):
    """Стратифицированная выборка эпизодов вида «от нуля», без BingX, разные пары, 5 корзин длительности."""
    rng = random.Random(seed)
    edges = [(1, 2), (2, 5), (5, 30), (30, 300), (300, 10 ** 9)]
    sub = df[(df["view"] == "z") & ~df["f_bingx"]]
    out, used = [], set()
    per = max(n // len(edges), 1)
    for lo, hi in edges:
        s = sub[(sub["dur"] >= lo * 1000) & (sub["dur"] < hi * 1000) & (sub["open"] == 0)]
        idx = list(s.index)
        rng.shuffle(idx)
        got = 0
        for i in idx:
            r = s.loc[i]
            key = (r["coin"], r["exA"], r["exB"])
            if key in used:
                continue
            used.add(key)
            out.append(i)
            got += 1
            if got >= per:
                break
    return out


def main_raw(flagp, bwork, n, seed, outp):
    import pickle
    tab = pickle.load(open(flagp, "rb"))
    df = tab["df"]
    meta = {ex: all_meta(ex, DAYS) for ex in ROOTS5}
    parsed = str(Path(bwork, "parsed"))
    ids = pick(df, n, seed)
    jobs = {}
    for i in ids:
        r = df.loc[i]
        lo, hi = int(r["t"]) - 3_600_000, int(r["t"] + r["dur"]) + 60_000
        for ex, sym in ((r["exA"], r["symA"]), (r["exB"], r["symB"])):
            for f in files_for(parsed, ex, sym, lo, hi):
                jobs.setdefault((ex, f), {}).setdefault(sym.encode(), []).append((lo, hi))
    rows = {}
    for (ex, f), w in jobs.items():
        for s, v in read_rows(f, w).items():
            rows.setdefault((ex, s.decode()), []).extend(v)
        print("read", f, flush=True)
    L = ["| № | монета | A | B | θ bps | сторона | таблица: начало (UTC) | таблица: длит., мс | пересчёт: длит., мс | Δ начала, мс | Δ длит., мс | макс. разрыв, bps: таблица / пересчёт | допускающий: длит., мс (провалов) | итог |", "|" + "---|" * 14]
    ok = bad = 0
    detail = []
    for k, i in enumerate(ids, 1):
        r = df.loc[i]
        mA, mB = norm5(r["exA"], r["symA"], meta[r["exA"]])[1], norm5(r["exB"], r["symB"], meta[r["exB"]])[1]
        d = derive(rows.get((r["exA"], r["symA"]), []), rows.get((r["exB"], r["symB"]), []), mA, mB, r["th"], int(r["side"]), int(r["t"]))
        ts = np.datetime_as_string(np.datetime64(int(r["t"]), "ms"), unit="ms").replace("T", " ")
        if not d or not d["found"]:
            L.append(f"| {k} | {r['coin']} | {r['exA']} | {r['exB']} | {r['th']:.0f} | {int(r['side']):+d} | {ts} | {r['dur']:.0f} | — | — | — | — | — | НЕ НАЙДЕН |")
            bad += 1
            continue
        dt, dd = d["t"] - int(r["t"]), (d["dur"] - r["dur"]) if d["dur"] is not None else None
        good = dt == 0 and dd is not None and abs(dd) <= 1 and abs(d["edgemax"] - r["edgemax"]) < 0.05
        ok += good
        bad += (not good)
        L.append(f"| {k} | {r['coin']} | {r['exA']} | {r['exB']} | {r['th']:.0f} | {int(r['side']):+d} | {ts} | {r['dur']:.0f} | {d['dur']} | {dt} | {dd} | {r['edgemax']:.1f} / {d['edgemax']:.1f} | {d['tol_dur']} ({len(d['dips'])}) | {'совпало' if good else 'РАЗЛИЧИЕ'} |")
        detail.append((k, r, d))
    txt = [f"Совпало {ok} из {ok + bad} (допуск начала 0 мс, длительности ±1 мс, максимума разрыва 0,05 bps).", ""] + L
    txt += ["", "Границы (строка ряда до начала и после конца эпизода: время мс UTC, разрыв bps):", ""]
    for k, r, d in detail:
        txt.append(f"- №{k} {r['coin']} {r['exA']}–{r['exB']}: до {d['before']}, после {d['after']}, событий внутри {d['nev']}, провалы допускающего {['%d мс' % x for x in d['dips']]}")
    Path(outp).write_text("\n".join(txt), encoding="utf-8")
    print("\n".join(txt[:2]))


# ---------- grid: сверка с сеткой 1 с ----------

def runs_of(mask):
    d = np.diff(np.r_[False, mask, False].astype(np.int8))
    return np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1


def bridged(cond, T, Tn, tol):
    """cond с заполненными провалами < tol между участками «верно» (как hold_scan.bridge_dips)."""
    out = cond.copy()
    if len(cond) < 3:
        return out
    st, en = runs_of(~cond)
    ok = (st > 0) & (en < len(cond) - 1) & ((Tn[en] - T[st]) < tol)
    for a, b in zip(st[ok], en[ok]):
        out[a:b + 1] = True
    return out


def grid_report(A, B, t0, t1, th, side, ns=(5, 10, 30, 60)):
    """-> строки сверки для одной стороны: {N: {...}} и все интервалы."""
    import core
    T, S, Bu, ia, ib = core.pair_series(A, B, t1)
    Tn = np.r_[T[1:], t1]
    e = S if side == 1 else -Bu
    cond = (T >= t0) & (e >= th)
    s_st, s_en = runs_of(cond)
    strict = np.c_[T[s_st], Tn[s_en]]                         # [начало, конец) каждого эпизода по событиям (в том числе короче 1 с)
    tcond = bridged(cond, T, Tn, TOL_MS)
    t_st, t_en = runs_of(tcond)
    tol = np.c_[T[t_st], Tn[t_en]]
    g = np.arange((t0 // 1000 + 1) * 1000, t1, 1000)
    ja, jb = np.searchsorted(A["e"], g, "right") - 1, np.searchsorted(B["e"], g, "right") - 1
    ok = (ja >= 0) & (jb >= 0)
    ja, jb = np.maximum(ja, 0), np.maximum(jb, 0)
    eg = (A["bid"][ja] / B["ask"][jb] - 1) * 1e4 if side == 1 else -(A["ask"][ja] / B["bid"][jb] - 1) * 1e4
    g_st, g_en = runs_of(ok & (eg >= th))
    grid = np.c_[g[g_st], g[g_en]]                            # первая и последняя целая секунда серии; длина серии = число секунд
    glen = g_en - g_st + 1
    res = {}
    gok = glen
    tdur = tol[:, 1] - tol[:, 0]
    for n in ns:
        E = strict[(strict[:, 1] - strict[:, 0]) >= n * 1000]
        Gn = grid[gok >= n]
        tmask = tdur >= n * 1000
        # строгий ≥ n содержит ≥ n целых секунд: первая из них (g0) лежит в серии сетки — какой именно
        g0 = -(-E[:, 0] // 1000) * 1000
        gi = np.searchsorted(Gn[:, 0], g0, "right") - 1 if len(Gn) else np.full(len(E), -1)
        inside = (gi >= 0) & (g0 <= Gn[np.maximum(gi, 0), 1]) if len(Gn) else np.zeros(len(E), bool)
        per_g = np.bincount(gi[inside], minlength=len(Gn)) if len(Gn) else np.zeros(0, int)
        pieces = np.zeros(len(Gn), int)
        maxdip = 0.0
        for q, (a, b) in enumerate(Gn):
            lo, hi = np.searchsorted(strict[:, 1], a, "right"), np.searchsorted(strict[:, 0], b, "right")
            seg = strict[lo:hi]
            pieces[q] = len(seg)
            if len(seg) > 1:
                maxdip = max(maxdip, float((seg[1:, 0] - seg[:-1, 1]).max()))
        tg = np.searchsorted(tol[:, 0], Gn[:, 0], "right") - 1 if len(Gn) else np.zeros(0, int)
        tin = (tg >= 0) & (Gn[:, 1] < tol[np.maximum(tg, 0), 1]) if len(Gn) else np.zeros(0, bool)
        cnt = np.bincount(tg[tin], minlength=len(tol))
        res[n] = {"strict": len(E), "tol": int(tmask.sum()), "grid": len(Gn),
                  "strict_not_in_grid": int((~inside).sum()),
                  "grid_k0": int((per_g == 0).sum()), "grid_k1": int((per_g == 1).sum()), "grid_k2p": int((per_g >= 2).sum()),
                  "grid_pieces2p": int((pieces >= 2).sum()), "grid_maxdip_ms": maxdip,
                  "tol_g0": int((cnt[tmask] == 0).sum()), "tol_g1": int((cnt[tmask] == 1).sum()), "tol_g2p": int((cnt[tmask] >= 2).sum()),
                  "grid_not_in_tol": int((~tin).sum()), "grid_in_short_tol": int((tin & (tdur[np.maximum(tg, 0)] < n * 1000)).sum())}
    return res


def main_grid(bwork, t0, t1, spec):
    import maker_run
    from hold_common import build_coins, unit_of
    coins, _ = build_coins(str(Path(bwork, "parsed")), DAYS)
    for item in spec.split(","):
        coin, a, b = item.split(":")
        books = {}
        for ex in (a, b):
            sym, mult, meta = coins[coin][ex]
            books[ex] = maker_run.load_book(str(Path(bwork, "parsed")), ex, sym, mult, unit_of(ex, meta))
        for th in (50.0, 100.0):
            agg = {}
            for side in (1, -1):
                for n, r in grid_report(books[a], books[b], t0, t1, th, side).items():
                    x = agg.setdefault(n, {})
                    for k, v in r.items():
                        x[k] = max(x.get(k, 0), v) if k == "grid_maxdip_ms" else x.get(k, 0) + v
            for n, r in agg.items():
                print(coin, a, b, th, n, r, flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "raw":
        main_raw(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6])
    else:
        main_grid(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
