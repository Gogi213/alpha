#!/usr/bin/env python3
"""TK-369 / G-016 / P-25: движок банка гипотез v2 (цена — СЕРЕДИНА СТАКАНА Bybit из lob seconds, не последняя сделка; A008 без заглядывания;
+F025/26/28/29, G031/33/35/37, K046/47; накопители для p_rand и контроля 3 — знаковый/перестановочный тест по собственным событиям). Было v1 (чистый python, без numpy — на calc его нет).
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

MID = os.environ.get("MIDDIR", "/data/tk0369/mid")
NSEC = 8 * DAY

def load_mid(sym, mk):
    """середина стакана (mid2, тики x2) по секундам: arr[t_s - T15] = состояние на конец секунды t_s (= момент t_s+1). None, если нет файлов."""
    import gzip
    arr = [0] * NSEC; have = 0
    for d in range(15, 22):
        f = f"{MID}/{mk}/2026-10-{d}/{sym}-2026-10-{d}.csv.gz"
        if not os.path.exists(f): continue
        with gzip.open(f, "rt") as fh:
            next(fh)
            for ln in fh:
                a = ln.split(",", 2)
                i = int(a[0]) - T15
                if 0 <= i < NSEC: arr[i] = int(a[1])
        have += 1
    return arr if have else None

def pm(arr, t):
    """цена (середина) на момент t (с): состояние на t*1000 мс = строка t-1; None если нет."""
    i = t - 1 - T15
    if i < 0 or i >= NSEC: return None
    v = arr[i]
    return v if v > 0 else None

def markout(ser, ts, s):
    """-> [v0,v1] по H: (M, C); C — среднее сдвигов +-3600 (тот же индекс). Цена — середина."""
    out = []
    for v in (0, 1):
        t0 = ts + v
        p0 = pm(ser, t0)
        if p0 is None: out.append(None); continue
        row = []
        for h in H:
            p1 = pm(ser, t0 + h)
            m = s * (p1 - p0) / p0 * 1e4 if p1 else None
            cs = []
            for sh in (-SH, SH):
                q0 = pm(ser, t0 + sh); q1 = pm(ser, t0 + sh + h)
                if q0 and q1 and ts + sh >= T15 - DAY: cs.append(s * (q1 - q0) / q0 * 1e4)
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
            if amt >= Q999 > 0:                       # A008: нет свопов +-60 с; событие известно в ts+60 (t0 = ts+60), без заглядывания
                near = (i > 0 and ts - sw[i - 1][0] <= 60) or (i + 1 < n and sw[i + 1][0] - ts <= 60)
                if not near: ev.append(("A008", ts + 60, s, amt / Q999))
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

def events_move(ser, secs_range, wlist, ids):
    """ход середины за w с >= q99 |хода| (порог — по истории до начала часа, причинно) -> (hid, t, s, strength); t = момент, когда ход завершён
    (состояние ser[i] — на момент T15+i+1). secs_range — [a, b)."""
    ev = []
    if ser is None: return ev
    a, b = secs_range
    lo = next((i for i, v in enumerate(ser) if v > 0), NSEC)
    for w, hid in zip(wlist, ids):
        hist = []; pend = []; thr = None; hour = -1; last_fire = -10**9
        for i in range(lo + w, NSEC):
            p1 = ser[i]; p0 = ser[i - w]
            if p0 <= 0 or p1 <= 0: continue
            tt = T15 + i + 1
            hh = tt // 3600
            if hh != hour:
                hour = hh
                if pend:
                    pend.sort(); hist = sorted(hist + pend); pend = []
                thr = qthr(hist, .99) if len(hist) >= 1000 else None
            r = (p1 - p0) / p0 * 1e4
            ar = abs(r)
            pend.append(ar)
            if thr and thr > 0 and ar >= thr and a <= tt < b and tt - last_fire >= w:
                last_fire = tt
                ev.append((hid, tt, 1 if r > 0 else -1, ar / thr))
    return ev

def big_times(sw):
    """времена свопов >= q95 размера пула (причинно) -> [(ts, s)]"""
    out = []; sizes = []
    for ts, s, amt, pu in sw:
        if len(sizes) >= MIN_PRIOR:
            Q = qthr(sizes, .95)
            if amt >= Q > 0: out.append((ts, s))
        bisect.insort(sizes, amt)
    return out

def lead_network(pools, coin_dirs):
    """K047: ведущая сеть по прогреву (сутки 15.10, вне счёта): сумма произведений 1-с доходностей цены сети X(t) и Y(t+k), k=1..10 минус обратная.
    -> имя сети или None (если сетей < 2 или нет сигнала)."""
    byc = {}
    for (chain, pool), sw in pools.items():
        byc.setdefault(chain, []).extend((t, pu) for t, s, a, pu in sw if T15 <= t < T15 + DAY and pu > 0)
    byc = {c: sorted(v) for c, v in byc.items() if len(v) >= 50}
    if len(byc) < 2: return None
    grid = {}
    for c, v in byc.items():
        g = [None] * DAY; j = 0; cur = None
        for sec in range(DAY):
            while j < len(v) and v[j][0] <= T15 + sec: cur = v[j][1]; j += 1
            g[sec] = cur
        r = [0.0] * DAY
        for sec in range(1, DAY):
            if g[sec] and g[sec - 1]: r[sec] = (g[sec] - g[sec - 1]) / g[sec - 1] * 1e4
        grid[c] = r
    cs = sorted(grid); best = None
    score = {c: 0.0 for c in cs}
    for X in cs:
        for Y in cs:
            if X == Y: continue
            # X ведёт Y: corr(rX(t), rY(t+k))
            sc = 0.0
            for k in range(1, 11):
                rx, ry = grid[X], grid[Y]
                sc += sum(rx[t] * ry[t + k] for t in range(1, DAY - k) if rx[t] and ry[t + k])
            score[X] += sc; score[Y] -= sc
    best = max(cs, key=lambda c: score[c])
    return best if score[best] > 0 else None

def work(job):
    key, coin_dirs, sym, tick = job
    import math
    t_start = time.time()
    fut = load_mid(sym, "fut")
    if fut is None: return key, {"err": "no_fut_mid"}
    spot = load_mid(sym, "spot")
    pools = read_swaps(coin_dirs)
    c_px = tick / 2.0                      # mid2 (тики x2) -> цена
    ev = []   # (hid, ts, s, strength, target)
    for pk, sw in pools.items():
        for e in events_pool(sw): ev.append(e + ("fut",))
        for e in events_imb(sw): ev.append(e + ("fut",))
    allsw = sorted((x[0], x[1], x[3]) for sw in pools.values() for x in sw if x[3] > 0)
    pt = [x[0] for x in allsw]; pp = [x[2] for x in allsw]
    bigs = sorted(t for sw in pools.values() for t in big_times(sw))          # (ts, s), все пулы монеты
    bt = [t for t, _ in bigs]
    # F: расхождение цены пула и СЕРЕДИНЫ Bybit (знак: к пулу); F025/28 — был крупный своп за 60 с; F026/29 — держится >= 10 с
    if allsw:
        first = []
        for ts, s_, pu in allsw[:200]:
            m_ = pm(fut, ts)
            if m_: first.append(pu / (m_ * c_px))
        if first:
            first.sort(); med = first[len(first) // 2]
            f = 10 ** round(math.log10(med)) if med > 0 else 1
            hist = []; last_fire = {}
            for ts, s_, pu in allsw:
                m_ = pm(fut, ts)
                if not m_: continue
                d = (pu / f / (m_ * c_px) - 1) * 1e4
                a_ = abs(d)
                if len(hist) >= MIN_PRIOR and a_ < 2000:
                    sg = 1 if d > 0 else -1
                    for hid, q, hb, hp in (("F024", .90, "F025", "F026"), ("F027", .99, "F028", "F029")):
                        t = qthr(hist, q)
                        if a_ >= t > 0:
                            st = a_ / t
                            ev.append((hid, ts, sg, st, "fut"))
                            j = bisect.bisect_left(bt, ts - 60)
                            if j < len(bt) and bt[j] <= ts: ev.append((hb, ts, sg, st, "fut"))
                            if ts - last_fire.get(hp, -10**9) >= 10:
                                ok = True
                                for k in range(1, 10):
                                    i1 = bisect.bisect_right(pt, ts - k) - 1
                                    mk_ = pm(fut, ts - k)
                                    if i1 < 0 or not mk_: ok = False; break
                                    dk = (pp[i1] / f / (mk_ * c_px) - 1) * 1e4
                                    if dk * sg <= 0 or abs(dk) < t: ok = False; break
                                if ok:
                                    last_fire[hp] = ts; ev.append((hp, ts, sg, st, "fut"))
                bisect.insort(hist, a_)
    flat = {"G030": ("G031", 1), "G032": ("G033", 5), "G034": ("G035", 30), "G036": ("G037", 300)}
    for hid, tt, sg, st in events_move(fut, (SCORE0, SCORE1), [1, 5, 30, 300], ["G030", "G032", "G034", "G036"]):
        ev.append((hid, tt, sg, st, "fut"))
        if pt:
            i1 = bisect.bisect_right(pt, tt) - 1; i0 = bisect.bisect_right(pt, tt - flat[hid][1]) - 1
            if i0 >= 0 and pp[i1] == pp[i0]: ev.append((flat[hid][0], tt, sg, st, "fut"))
    if spot is not None:
        for hid, w in (("J042", 1), ("J044", 5)):
            for e in events_move(spot, (SCORE0, SCORE1), [w], [hid]): ev.append(e + ("fut",))
        for hid, w in (("J043", 1), ("J045", 5)):
            for e in events_move(fut, (SCORE0, SCORE1), [w], [hid]): ev.append(e + ("spot",))
    # K046: первый крупный своп (>= q95) среди пулов/сетей монеты за 60 с (монеты с >= 2 пулами)
    if len(pools) >= 2:
        lastb = -10**9
        for ts, s_ in bigs:
            if ts - lastb > 60: ev.append(("K046", ts, s_, 1.0, "fut"))
            lastb = ts
    # K047: крупные свопы ведущей сети (по прогреву 15.10, вне счёта)
    lead = lead_network(pools, coin_dirs)
    if lead:
        for (chain, pk), sw in pools.items():
            if chain == lead:
                for ts, s_ in big_times(sw): ev.append(("K047", ts, s_, 1.0, "fut"))
    cache = {}
    acc = {}; bins = {}; drops = {}
    for hid, ts, s_, st, tgt in ev:
        if not (SCORE0 <= ts < SCORE1): continue
        ser = fut if tgt == "fut" else spot
        ck = (ts, s_, tgt)
        r = cache.get(ck)
        if r is None:
            r = markout(ser, ts, s_); cache[ck] = r
        day = (ts - T15) // DAY + 15
        for v in (0, 1):
            if r[v] is None:
                drops[(hid, v)] = drops.get((hid, v), 0) + 1; continue
            for hi, (m, c) in enumerate(r[v]):
                if m is None: continue
                sn = m - c if c is not None else None
                a = acc.setdefault((hid, v, hi, day), [0, 0.0, 0, 0.0, 0.0, 0, 0.0])
                a[0] += 1; a[1] += m
                if sn is not None:
                    a[2] += 1; a[3] += sn; a[4] += sn * sn; a[5] += s_; a[6] += s_ * sn
                    b = bisect.bisect_right(BINS, st) - 1
                    bb = bins.setdefault((hid, v, hi, max(b, 0)), [0, 0.0]); bb[0] += 1; bb[1] += sn
    res = {"acc": [list(k) + v for k, v in acc.items()], "bins": [list(k) + v for k, v in bins.items()],
           "drops": [list(k) + [v] for k, v in drops.items()], "lead": lead,
           "n_ev": len(ev), "sec": round(time.time() - t_start, 1), "spot": spot is not None, "pools": len(pools)}
    os.makedirs(f"{OUT}/coin", exist_ok=True)
    json.dump(res, open(f"{OUT}/coin/{key}.json", "w"))
    return key, {"ok": 1, "sec": res["sec"], "n_ev": len(ev)}

def main():
    global TICK
    TICK = {r["symbol"]: float(r["tick_size"]) for r in csv.DictReader(open(os.environ.get("TICKCSV", "/srv/alpha-sda/tk366w/instruments.csv"), encoding="utf-8"))}
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
            if not os.path.exists(f"{MID}/fut/2026-10-16/{sym}-2026-10-16.csv.gz") or sym not in TICK: continue
            if row.get("cg_wrapped") == "1": continue
            if suf and not row["token"].lower().endswith(suf.lower()): continue   # копия токена с другим контрактом
            coins.setdefault(base, {"sym": sym, "dirs": []})["dirs"].append((chain, d))
    jobs = [(b, v["dirs"], v["sym"], TICK[v["sym"]]) for b, v in sorted(coins.items()) if not ONLY or b in ONLY]
    print(f"coins {len(jobs)}", flush=True)
    done = 0
    with Pool(P) as pool:
        for key, st in pool.imap_unordered(work, jobs):
            done += 1
            print(done, key, st, flush=True)
    open(f"{OUT}/DONE.json", "w").write(json.dumps({"coins": len(jobs)}))

if __name__ == "__main__":
    main()
