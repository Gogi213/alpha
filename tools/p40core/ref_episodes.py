"""TK-500 П-40 §3.1–3.2: эталон-Python (медленный, прозрачный) — эпизоды схождения одного актива на двух биржах за сутки.
Запуск: ref_episodes.py <dirA> <fileA> <dirB> <fileB> <день> <выход.csv> [часы=0-23] — dir<X>/<ЧЧ>/<fileX> (parquet CHFT, orderbook).
Определения (сверяются с Rust-ядром построчно, sha256 выхода печатается):
 * книга: dict уровней + ленивые кучи; сброс на начале серии snapshot; состояние публикуется на границе пакета
   (ключ пакета = (event_type, final_update_id|-1, event_time)); внутри серии snapshot — не публикуется; bid>=ask — состояние не публикуется (счётчик).
 * время as-of: мс event_time (если время пакета меньше предыдущего — берётся предыдущее: ряд времён неубывающий); состояние в t = после всех пакетов с event_time <= t (последний побеждает); момент берётся из объединения времён.
 * x(t)=ln(midA/midB), mid=(bid+ask)/2. Уровень: сетка 1 с (as-of x в целую секунду k), level(k)=медиана x по k' в [k-L, k-60] (L=6 ч),
   чётное число — среднее двух средних; окно должно быть полным (все L-60 секунд определены) — иначе уровня нет, эпизодов нет (нет данных до диапазона).
   d(t) = 1e4*(x(t) - level(floor(t/1000))) бп — это d^(2) протокола (нет index у ног CHFT); пересчёт только на событиях.
 * взвод/эпизод: |d|>=θ при предыдущем состоянии |d|<θ/2 и непрерывном прогоне |d|<θ/2 длиной >= W=60 с до t (по состояниям до t). Конец: dir*d<=0.
   Цензура: конец данных. Нет стопов, нет таймаутов. Дыры (S_max) — после G0, здесь не обрезаются."""
import sys, time, hashlib, heapq, bisect, math
import numpy as np, pyarrow.parquet as pq, pyarrow.compute as pc

THETAS = (5.04, 12.6, 25.2, 50.0, 100.0)
L_S, GAP_S, W_MS = 21600, 60, 60000

def leg_series(paths):
    """-> (t_ms[int64], bid[f8], ask[f8], счётчики). Публикуются только состояния на границах пакетов с bid<ask."""
    bids = {}; asks = {}; hb = []; ha = []; T = []; B = []; A = []; crossed = 0; nonmono = 0; last_t = -1
    for path in paths:
        tb = pq.read_table(path, columns=["event_time", "event_type", "final_update_id", "side", "price", "quantity"])
        n = tb.num_rows
        px = pc.cast(tb["price"], "float64").to_numpy().tolist(); qt = pc.cast(tb["quantity"], "float64").to_numpy().tolist()
        et = tb["event_time"].to_numpy().tolist()
        fu = pc.fill_null(tb["final_update_id"], -1).to_numpy().tolist()
        snap = (tb["event_type"].to_numpy(zero_copy_only=False) == "snapshot").tolist()
        isbid = (tb["side"].to_numpy(zero_copy_only=False) == "bid").tolist()
        for i in range(n):
            if snap[i] and (i == 0 or not snap[i - 1]):
                bids.clear(); asks.clear(); hb.clear(); ha.clear()
            p = px[i]
            if isbid[i]:
                if qt[i] == 0.0: bids.pop(p, None)
                else:
                    if p not in bids: heapq.heappush(hb, -p)
                    bids[p] = qt[i]
            else:
                if qt[i] == 0.0: asks.pop(p, None)
                else:
                    if p not in asks: heapq.heappush(ha, p)
                    asks[p] = qt[i]
            last = i == n - 1
            if not last and (snap[i] == snap[i + 1] and fu[i] == fu[i + 1] and et[i] == et[i + 1]): continue
            if not last and snap[i] and snap[i + 1]: continue
            while hb and (-hb[0]) not in bids: heapq.heappop(hb)
            while ha and ha[0] not in asks: heapq.heappop(ha)
            if hb and ha:
                bb = -hb[0]; aa = ha[0]
                if bb >= aa: crossed += 1; continue
                te = et[i]
                if te < last_t: nonmono += 1; te = last_t
                last_t = te
                if T and T[-1] == te: B[-1] = bb; A[-1] = aa
                else: T.append(te); B.append(bb); A.append(aa)
    return np.array(T, dtype=np.int64), np.array(B), np.array(A), {"crossed": crossed, "nonmono": nonmono}

def x_series(a, b):
    """объединённые моменты; x=ln(midA/midB) там, где обе книги определены."""
    tu = np.union1d(a[0], b[0])
    ia = np.searchsorted(a[0], tu, side="right") - 1; ib = np.searchsorted(b[0], tu, side="right") - 1
    ok = (ia >= 0) & (ib >= 0); tu, ia, ib = tu[ok], ia[ok], ib[ok]
    ma = (a[1][ia] + a[2][ia]) / 2.0; mb = (b[1][ib] + b[2][ib]) / 2.0
    return tu, np.log(ma / mb)

def level_grid(tu, x, t0_s, t1_s):
    """level[k-t0_s] для целых секунд k в [t0_s, t1_s]; NaN, если окно неполно."""
    secs = np.arange(t0_s, t1_s + 1)
    ig = np.searchsorted(tu, secs * 1000, side="right") - 1
    g = np.where(ig >= 0, x[np.maximum(ig, 0)], np.nan)
    lev = np.full(len(secs), np.nan); win = []; nnan = 0
    # окно сетки [k-L, k-60]: добавляем g[k-60], удаляем g[k-L-1]
    for j in range(len(secs)):
        k = j
        add = k - GAP_S; rem = k - L_S - 1
        if add >= 0:
            v = g[add]
            if v == v: bisect.insort(win, v)
            else: nnan += 1
        if rem >= 0:
            v = g[rem]
            if v == v: win.pop(bisect.bisect_left(win, v))
            else: nnan -= 1
        if k - L_S >= 0 and nnan == 0 and len(win) == L_S - GAP_S + 1:
            m = len(win); lev[j] = win[m // 2] if m % 2 else (win[m // 2 - 1] + win[m // 2]) / 2.0
    return secs, lev

def episodes(tu, x, secs, lev, theta):
    s0 = secs[0]
    js = tu // 1000 - s0
    ok = (js >= 0) & (js < len(lev))
    lv = np.full(len(tu), np.nan); lv[ok] = lev[js[ok]]
    d = 1e4 * (x - lv); n = len(tu); out = []
    calm_since = None; prev_ad = None; open_ep = None
    th2 = theta / 2.0
    for i in range(n):
        di = d[i]
        if di != di:
            calm_since = None; prev_ad = None
            continue
        ad = abs(di); t = int(tu[i])
        if open_ep is not None:
            e = open_ep; s = e["dir"] * di
            if s > e["peak"]: e["peak"] = s; e["t_peak"] = t
            if e["t_half"] < 0 and s <= 0.5 * e["d0"]: e["t_half"] = t
            if e["t_q"] < 0 and s <= 0.25 * e["d0"]: e["t_q"] = t
            if s <= 0.0: e["t_end"] = t; out.append(e); open_ep = None
        else:
            if prev_ad is not None and prev_ad < th2 and ad >= theta and calm_since is not None and t - calm_since >= W_MS:
                dr = 1 if di > 0 else -1
                open_ep = {"t0": t, "theta": theta, "dir": dr, "d0": ad, "peak": ad, "t_peak": t, "t_half": -1, "t_q": -1, "t_end": -1}
        if open_ep is None:
            if ad < th2:
                if calm_since is None or prev_ad is None or prev_ad >= th2: calm_since = t
            else: calm_since = None
        prev_ad = ad
    if open_ep is not None: out.append(open_ep)
    return out

def fmt(e):
    mae = e["peak"] - e["d0"]
    return (f"{e['theta']},{e['t0']},{e['dir']},{e['d0']:.4f},{e['peak']:.4f},{e['t_peak'] - e['t0']},{mae:.4f},"
            f"{e['t_half'] - e['t0'] if e['t_half'] >= 0 else -1},{e['t_q'] - e['t0'] if e['t_q'] >= 0 else -1},"
            f"{e['t_end'] - e['t0'] if e['t_end'] >= 0 else -1},{0 if e['t_end'] >= 0 else 1}")

HDR = "theta,t0_ms,dir,d0_bps,peak_bps,t_peak_ms,mae_mid_bps,t_half_ms,t_quarter_ms,t_conv_ms,censored"

if __name__ == "__main__":
    dA, fA, dB, fB, day, outp = sys.argv[1:7]
    h0, h1 = (0, 23) if len(sys.argv) < 8 else (int(v) for v in sys.argv[7].split("-")); hrs = range(h0, h1 + 1)
    t = time.time()
    pa = [f"{dA}/{h:02d}/{fA}" for h in hrs]; pb = [f"{dB}/{h:02d}/{fB}" for h in hrs]
    A = leg_series(pa); print(f"A {len(A[0])} состояний {A[3]} {time.time()-t:.1f}s", flush=True)
    B = leg_series(pb); print(f"B {len(B[0])} состояний {B[3]} {time.time()-t:.1f}s", flush=True)
    tu, x = x_series(A, B); print(f"объединённых моментов {len(tu)} {time.time()-t:.1f}s", flush=True)
    t0s = int(tu[0] // 1000); t1s = int(tu[-1] // 1000)
    secs, lev = level_grid(tu, x, t0s, t1s); print(f"уровень: определён на {int(np.isfinite(lev).sum())} из {len(lev)} с {time.time()-t:.1f}s", flush=True)
    lines = [HDR]; counts = {}
    for th in THETAS:
        eps = episodes(tu, x, secs, lev, th); counts[th] = (len(eps), sum(1 for e in eps if e["t_end"] < 0))
        lines += [fmt(e) for e in eps]
    txt = "\n".join(lines) + "\n"
    open(outp, "w").write(txt)
    print("эпизодов (θ: всего/цензур.)", counts)
    print(f"sha256 {hashlib.sha256(txt.encode()).hexdigest()}  время {time.time()-t:.1f}s")
