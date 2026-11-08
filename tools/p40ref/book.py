"""Эталон П-40 (TK-501): книга одной ноги по часам -> таблица тиков (событие = группа строк с одним final_update_id).
Не смотрит в код ядра TK-500. Цены в целых тиках (1e-6), количества в единицах базового актива (OKX: контракты * ctVal).
Запуск: python book.py <каталог raw/<биржа>> <символ> <ctVal> <выход.npz>   (24 часа 00..23)"""
import sys, numpy as np, pyarrow.parquet as pq, pyarrow.compute as pc
from numba import njit

SC = 1_000_000          # тиков на единицу цены
NMAX = 20_000_000       # цена до 20


def load_hour(path, ctval):
    t = pq.read_table(path, columns=["event_time", "event_type", "final_update_id", "side", "price", "quantity"])
    et = t["event_time"].to_numpy()
    fid = t["final_update_id"].to_numpy()
    snap = (pc.equal(t["event_type"], "snapshot")).to_numpy(zero_copy_only=False)
    bid = pc.equal(t["side"], "bid").to_numpy(zero_copy_only=False)
    px = np.rint(pc.cast(t["price"], "float64").to_numpy() * SC).astype(np.int64)
    q = pc.cast(t["quantity"], "float64").to_numpy() * ctval
    return et, fid, snap, bid, px, q, set(t["event_type"].unique().to_pylist())


@njit(cache=True)
def _sweep(qarr, start, step, notional, scale):
    """VWAP-цена набора notional USDT от лучшего уровня start в сторону step; -1 если не хватает книги (до 400 уровней глубже)."""
    need = notional
    spent_q = 0.0
    spent_v = 0.0
    p = start
    for _ in range(4000):
        if p < 0 or p >= qarr.shape[0]:
            break
        qq = qarr[p]
        if qq > 0:
            price = p / scale
            take = min(qq, need / price)
            spent_q += take
            spent_v += take * price
            need -= take * price
            if need <= 1e-9:
                return spent_v / spent_q
        p += step
    return -1.0


@njit(cache=True)
def _top5(qarr, start, step, scale):
    """Номинал (USDT) пяти лучших непустых уровней."""
    n = 0
    s = 0.0
    p = start
    for _ in range(4000):
        if p < 0 or p >= qarr.shape[0] or n >= 5:
            break
        if qarr[p] > 0:
            s += qarr[p] * p / scale
            n += 1
        p += step
    return s


@njit(cache=True)
def run(et, fid, snap, bid, px, q, notional, scale, nmax):
    n = et.shape[0]
    bq = np.zeros(nmax)
    aq = np.zeros(nmax)
    bb = -1          # лучший бид (тик)
    ba = nmax        # лучший аск
    out_t = np.empty(n, np.int64)
    out = np.empty((n, 8))   # bid, ask, bid_q1, ask_q1, bid_vwapN, ask_vwapN, bid_top5, ask_top5
    k = 0
    i = 0
    ncross = 0
    nrow = 0
    while i < n:
        j = i
        if snap[i]:
            # снимок: очистить книгу, применить все строки снимка
            bq[:] = 0.0
            aq[:] = 0.0
            bb = -1
            ba = nmax
            while j < n and snap[j]:
                p = px[j]
                if bid[j]:
                    bq[p] = q[j]
                    if q[j] > 0 and p > bb:
                        bb = p
                else:
                    aq[p] = q[j]
                    if q[j] > 0 and p < ba:
                        ba = p
                j += 1
        else:
            f = fid[i]
            while j < n and (not snap[j]) and fid[j] == f and et[j] == et[i]:
                p = px[j]
                if bid[j]:
                    bq[p] = q[j]
                    if q[j] > 0:
                        if p > bb:
                            bb = p
                    elif p == bb:
                        bb -= 1
                        while bb >= 0 and bq[bb] <= 0:
                            bb -= 1
                else:
                    aq[p] = q[j]
                    if q[j] > 0:
                        if p < ba:
                            ba = p
                    elif p == ba:
                        ba += 1
                        while ba < nmax and aq[ba] <= 0:
                            ba += 1
                j += 1
        # после применения группы — и лучшие цены могут быть устаревшими, если q<=0 на лучшем: пересчёт выше
        # сверка: лучший должен иметь q>0 (иначе пересчитать)
        if bb >= 0 and bq[bb] <= 0:
            while bb >= 0 and bq[bb] <= 0:
                bb -= 1
        if ba < nmax and aq[ba] <= 0:
            while ba < nmax and aq[ba] <= 0:
                ba += 1
        if bb >= 0 and ba < nmax:
            if bb >= ba:
                ncross += 1
            out_t[k] = et[i]
            out[k, 0] = bb
            out[k, 1] = ba
            out[k, 2] = bq[bb]
            out[k, 3] = aq[ba]
            out[k, 4] = _sweep(bq, bb, -1, notional, scale)
            out[k, 5] = _sweep(aq, ba, 1, notional, scale)
            out[k, 6] = _top5(bq, bb, -1, scale)
            out[k, 7] = _top5(aq, ba, 1, scale)
            k += 1
        i = j
        nrow += 1
    return out_t[:k], out[:k], ncross


def main(root, sym, ctval, outp, hours=range(24)):
    parts_t, parts_o = [], []
    for h in hours:
        p = f"{root}/{h:02d}/{sym}_orderbook.parquet"
        et, fid, snap, bid, px, q, types = load_hour(p, ctval)
        ot, oo, ncross = run(et, fid, snap, bid, px, q, 1000.0, float(SC), NMAX)
        mono = bool(np.all(np.diff(ot) >= 0))
        print(f"h{h:02d} rows={len(et)} events={len(ot)} types={sorted(types)} crossed={ncross} t_monotone={mono} "
              f"t0={ot[0]} t1={ot[-1]}", flush=True)
        parts_t.append(ot)
        parts_o.append(oo)
    np.savez_compressed(outp, t=np.concatenate(parts_t), o=np.vstack(parts_o))


if __name__ == "__main__":
    root, sym, ctval, outp = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
    main(root, sym, ctval, outp)
