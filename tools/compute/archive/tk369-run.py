#!/usr/bin/env python3
"""TK-369 / G-016 / P-25: движок банка гипотез v1 (чистый python, без numpy — на calc его нет).
usage: tk369-run.py <usd=/data/tk0369/usd> <sec=/data/tk0369/sec> <universe.csv> <outdir> [P=14] [--only DIR,...]
Один процесс на монету (каталог <chain>/<SYM> слоя USD): события -> маркаут по перпу Bybit на горизонтах H для двух опорных моментов
(t0=ts, t0=ts+1), контроль «сдвиг ±3600 с» (Snet = M - среднее сдвигов), агрегаты по монето-суткам, дозовые бины, базовый второй момент.
Выход: <outdir>/coin/<ключ>.json  (агрегация, бутстрап, BH, вердикт — tk369-agg.py на этой машине с numpy).
Реализовано v1 (32 из 39 «считаются сразу»): A001-A010, B011-B014, L048-L055, J042-J045, G030/032/034/036, F024/F027.
НЕ в v1: F025,F026,F028,F029, G031/033/035/037 (poolflat), K046/K047 — см. отчёт."""
import sys, os, json, bisect, random, csv, glob, time
from multiprocessing import Pool

USD, SEC, UNIV, OUT = sys.argv[1:5]
P = int(sys.argv[5]) if len(sys.argv) > 5 and not sys.argv[5].startswith("--") else 14
ONLY = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
T15 = 1792022400
DAY = 86400
SCORE0, SCORE1 = T15 + DAY, T15 + 7 * DAY      # 16.10 00:00 .. 22.10 00:00 UTC (15.10 — прогрев)
H = [1, 5, 30, 60, 300, 900, 3600]
SH = 3600
MIN_PRIOR = 200
BINS = [1.0, 1.25, 1.5, 2.0, 3.0, 5.0]         # нижние границы силы события (в долях своего порога)

def load_series(sym, mk):
    secs, last = [], []
    for d in range(15, 23):
        f = f"{SEC}/{mk}/{sym}-2026-10-{d}.tsv"
        if not os.path.exists(f): continue
        for ln in open(f):
            a = ln.split("\t")
            secs.append(int(a[0])); last.append(float(a[1]))
    return secs, last

def px(ser, t):
    secs, last = ser
    i = bisect.bisect_right(secs, t) - 1
    if i < 0: return None, 10**9
    return last[i], t - secs[i]

def markout(ser, ts, s):
    """-> (M[14], C[14]) или None; M по (v,h), C — среднее сдвигов ±3600 того же индекса (None если сдвигов нет)."""
    out = []
    for v in (0, 1):
        t0 = ts + v
        p0, age = px(ser, t0)
        if p0 is None or age > 5: out.append(None); continue
        row = []
        for h in H:
            p1, _ = px(ser, t0 + h)
            m = s * (p1 - p0) / p0 * 1e4 if p1 else None
            cs = []
            for sh in (-SH, SH):
                q0, a0 = px(ser, t0 + sh)
                q1, _ = px(ser, t0 + sh + h)
                if q0 and q1 and a0 <= 60 and ts + sh >= T15 - DAY: cs.append(s * (q1 - q0) / q0 * 1e4)
            row.append((m, (sum(cs) / len(cs)) if cs else None))
        out.append(row)
    return out

def qthr(sorted_list, q):
    n = len(sorted_list)
    return sorted_list[min(n - 1, int(q * n))]

def read_swaps(coin_dirs):
    pools = {}
    for chain, d in coin_dirs:
        for f in sorted(glob.glob(f"{USD}/{chain}/{d}/2026-10-*.csv")):
            for ln in open(f).read().split("\n")[1:]:
                if not ln: continue
                a = ln.split(",")
                try:
                    pools.setdefault((chain, a[4]), []).append((int(a[0]), 1 if a[6] == "buy" else -1, float(a[7]), float(a[10]) if a[10] else 0.0))
                except ValueError:
                    continue
    for k in pools: pools[k].sort()
    return pools

def events_pool(sw):
    """sw — свопы одного пула по времени -> список (hid, ts, s, strength). Квантили нарастающим итогом (causal)."""
    ev = []
    sizes = []            # sorted прошлых размеров
    n = len(sw)
    last_fire = {}
    for i, (ts, s, amt, pu) in enumerate(sw):
        if len(sizes) >= MIN_PRIOR:
            for hid, q, side in (("A001", .95, 0), ("A002", .99, 0), ("A003", .999, 0), ("A004", .95, 1), ("A005", .95, -1), ("A006", .99, 1), ("A007", .99, -1)):
                if side and s != side: continue
                Q = qthr(sizes, q)
                if amt >= Q > 0: ev.append((hid, ts, s, amt / Q))
            Q999, Q99 = qthr(sizes, .999), qthr(sizes, .99)
            if amt >= Q999 > 0:                       # A008: нет других свопов в пуле за +-60 с
                near = (i > 0 and ts - sw[i - 1][0] <= 60) or (i + 1 < n and sw[i + 1][0] - ts <= 60)
                if not near: ev.append(("A008", ts, s, amt / Q999))
            if amt >= Q99 > 0:
                if i > 0 and ts - sw[i - 1][0] >= 300: ev.append(("A009", ts, s, amt / Q99))
                if i >= 3 and all(sw[i - k][1] == s for k in (1, 2, 3)): ev.append(("A010", ts, s, amt / Q99))
            # B: серии
            for hid, cnt, win, sz in (("B011", 3, 10, 0), ("B012", 3, 10, 1), ("B013", 5, 60, 0), ("B014", 5, 60, 1)):
                if i + 1 < cnt: continue
                j = i - cnt + 1
                if all(sw[k][1] == s for k in range(j, i + 1)) and ts - sw[j][0] <= win:
                    if sz:
                        tot = sum(sw[k][2] for k in range(j, i + 1))
                        Q = qthr(sizes, .95)
                        if not tot >= Q > 0: continue
                    key = (hid, s)
                    if ts - last_fire.get(key, -10**9) >= win:
                        last_fire[key] = ts
                        ev.append((hid, ts, s, cnt / cnt))
        bisect.insort(sizes, amt)
    return ev

def events_imb(sw):
    ev = []
    for hid0, W in (("L048", 10), ("L050", 30), ("L052", 60), ("L054", 300)):
        hist = []         # sorted |imb| прошлых оценок
        lo = 0
        last_fire = -10**9
        buy = sell = 0.0; cnt = 0
        win = []
        for i, (ts, s, amt, pu) in enumerate(sw):
            win.append((ts, s, amt))
            if s > 0: buy += amt
            else: sell += amt
            cnt += 1
            while win and win[0][0] < ts - W:
                t_, s_, a_ = win.pop(0)
                if s_ > 0: buy -= a_
                else: sell -= a_
                cnt -= 1
            tot = buy + sell
            if tot <= 0: continue
            imb = (buy - sell) / tot
            a = abs(imb)
            if len(hist) >= MIN_PRIOR:
                thr = qthr(hist, .95)
                if a >= thr > 0 and ts - last_fire >= W:
                    last_fire = ts
                    sg = 1 if imb > 0 else -1
                    ev.append((hid0, ts, sg, a / thr))
                    if cnt >= 3: ev.append((hid0[:1] + str(int(hid0[1:]) + 1).zfill(3), ts, sg, a / thr))
            bisect.insort(hist, a)
    return ev

def events_move(src, secs_range, wlist, ids):
    """ход цены src за w c >= q99 (порог — по всей истории до начала часа) -> (hid, t, s, strength). secs_range — [a, b)."""
    secs, last = src
    ev = []
    a, b = secs_range
    for w, hid in zip(wlist, ids):
        rets = []; t = a - 14 * 3600 if False else secs[0] if secs else 0
        # доходности на каждой секунде-сделке с лагом w
        buf = []
        thr = None; hour = -1; hist = []
        last_fire = -10**9
        for i, tt in enumerate(secs):
            j = bisect.bisect_left(secs, tt - w, 0, i + 1)
            # цена на момент tt-w: последняя сделка <= tt-w
            if secs[j] > tt - w:
                if j == 0: continue
                j -= 1
            r = (last[i] - last[j]) / last[j] * 1e4
            hh = tt // 3600
            if hh != hour:
                hour = hh
                hist_sorted = sorted(hist) if len(hist) < 400000 else sorted(hist[-400000:])
                thr = qthr(hist_sorted, .99) if len(hist_sorted) >= 1000 else None
            hist.append(abs(r))
            if thr and thr > 0 and abs(r) >= thr and a <= tt < b and tt - last_fire >= w:
                last_fire = tt
                ev.append((hid, tt, 1 if r > 0 else -1, abs(r) / thr))
    return ev

def work(job):
    key, coin_dirs, sym, factor_ok = job
    t_start = time.time()
    fut = load_series(sym, "fut")
    if len(fut[0]) < 1000: return key, {"err": "no_fut"}
    spot = load_series(sym, "spot")
    pools = read_swaps(coin_dirs)
    ev = []   # (hid, ts, s, strength, src_target)
    for pk, sw in pools.items():
        for e in events_pool(sw): ev.append(e + ("fut",))
        for e in events_imb(sw): ev.append(e + ("fut",))
    # F: расхождение цены пула и Bybit (знак: к пулу)
    allsw = sorted((x[0], x[1], x[3]) for sw in pools.values() for x in sw if x[3] > 0)
    if allsw and factor_ok:
        first = [pu / px(fut, ts)[0] for ts, s, pu in allsw[:200] if px(fut, ts)[0]]
        if first:
            first.sort(); med = first[len(first) // 2]
            import math
            f = 10 ** round(math.log10(med)) if med > 0 else 1
            hist = []; thr = {}
            for ts, s, pu in allsw:
                p, age = px(fut, ts)
                if not p or age > 5: continue
                d = (pu / f / p - 1) * 1e4
                a = abs(d)
                if len(hist) >= MIN_PRIOR:
                    for hid, q in (("F024", .90), ("F027", .99)):
                        t = qthr(hist, q)
                        if a >= t > 0 and abs(d) < 2000: ev.append((hid, ts, 1 if d > 0 else -1, a / t, "fut"))
                bisect.insort(hist, a)
    for e in events_move(fut, (SCORE0, SCORE1), [1, 5, 30, 300], ["G030", "G032", "G034", "G036"]): ev.append(e + ("fut",))
    if len(spot[0]) >= 1000:
        for hid, w in (("J042", 1), ("J044", 5)):
            for e in events_move(spot, (SCORE0, SCORE1), [w], [hid]): ev.append(e + ("fut",))
        for hid, w in (("J043", 1), ("J045", 5)):
            for e in events_move(fut, (SCORE0, SCORE1), [w], [hid]): ev.append(e + ("spot",))
    cache = {}
    acc = {}; bins = {}; drops = {}
    for hid, ts, s, st, tgt in ev:
        if not (SCORE0 <= ts < SCORE1): continue
        ser = fut if tgt == "fut" else spot
        ck = (ts, s, tgt)
        r = cache.get(ck)
        if r is None:
            r = markout(ser, ts, s); cache[ck] = r
        day = (ts - T15) // DAY + 15
        for v in (0, 1):
            if r[v] is None:
                drops[(hid, v)] = drops.get((hid, v), 0) + 1; continue
            for hi, (m, c) in enumerate(r[v]):
                if m is None: continue
                sn = m - c if c is not None else None
                a = acc.setdefault((hid, v, hi, day), [0, 0.0, 0, 0.0, 0.0])
                a[0] += 1; a[1] += m
                if sn is not None:
                    a[2] += 1; a[3] += sn; a[4] += sn * sn
                    b = bisect.bisect_right(BINS, st) - 1
                    bb = bins.setdefault((hid, v, hi, max(b, 0)), [0, 0.0]); bb[0] += 1; bb[1] += sn
    # базовый второй момент M: 300 случайных секунд на сутки, t0=ts+1, тот же перп
    rnd = random.Random(369)
    base = {}
    for d in range(16, 22):
        for _ in range(300):
            ts = T15 + (d - 15) * DAY + rnd.randrange(DAY)
            p0, age = px(fut, ts + 1)
            if not p0 or age > 5: continue
            for hi, h in enumerate(H):
                p1, _ = px(fut, ts + 1 + h)
                if p1:
                    b = base.setdefault((d, hi), [0, 0.0]); b[0] += 1; b[1] += ((p1 - p0) / p0 * 1e4) ** 2
    res = {"acc": [list(k) + v for k, v in acc.items()], "bins": [list(k) + v for k, v in bins.items()],
           "base": [list(k) + v for k, v in base.items()], "drops": [list(k) + [v] for k, v in drops.items()],
           "n_ev": len(ev), "sec": round(time.time() - t_start, 1), "fut_n": len(fut[0]), "spot_n": len(spot[0]), "pools": len(pools)}
    os.makedirs(f"{OUT}/coin", exist_ok=True)
    json.dump(res, open(f"{OUT}/coin/{key}.json", "w"))
    return key, {"ok": 1, "sec": res["sec"], "n_ev": len(ev)}

def main():
    uni = {}
    for r in csv.DictReader(open(UNIV, encoding="utf-8")):
        uni.setdefault(r["base"], []).append(r)
    coins = {}
    for chain in ("ethereum", "bsc", "base"):
        for d in sorted(os.listdir(f"{USD}/{chain}")):
            base, _, suf = d.partition("-")
            rows = uni.get(base)
            if not rows: continue
            row = rows[0]
            sym = row["symbol"]
            if not os.path.exists(f"{SEC}/fut/{sym}-2026-10-16.tsv"): continue
            if row.get("cg_wrapped") == "1": continue
            if suf and not row["token"].lower().endswith(suf.lower()): continue   # копия токена с другим контрактом
            coins.setdefault(base, {"sym": sym, "dirs": []})["dirs"].append((chain, d))
    jobs = [(b, v["dirs"], v["sym"], True) for b, v in sorted(coins.items()) if not ONLY or b in ONLY]
    print(f"coins {len(jobs)}", flush=True)
    done = 0
    with Pool(P) as pool:
        for key, st in pool.imap_unordered(work, jobs):
            done += 1
            print(done, key, st, flush=True)
    open(f"{OUT}/DONE.json", "w").write(json.dumps({"coins": len(jobs)}))

if __name__ == "__main__":
    main()
