"""TK-500 П-40: замер узких мест на 1 файле CHFT Binance (час×контракт): чтение parquet, цены строка→число, построение книги.
Запуск: bench_parse.py <файл.parquet> [naive]. Варианты книги: naive (dict + max/min на каждую строку — эталон) и heap (dict + ленивые кучи).
Сверка: последовательность изменений лучшей пары (индекс строки, bid, ask) — sha256; обязана совпасть у вариантов."""
import sys, time, hashlib, heapq
import pyarrow.parquet as pq, pyarrow.compute as pc

def load(path):
    t = time.time(); tb = pq.read_table(path); t_read = time.time() - t
    t = time.time()
    p = pc.cast(tb["price"], "float64").to_numpy(); q = pc.cast(tb["quantity"], "float64").to_numpy()
    t_cast = time.time() - t
    snap = (tb["event_type"].to_numpy(zero_copy_only=False) == "snapshot")
    isbid = (tb["side"].to_numpy(zero_copy_only=False) == "bid")
    return tb.num_rows, t_read, t_cast, p.tolist(), q.tolist(), snap.tolist(), isbid.tolist()

def book_naive(n, p, q, snap, isbid):
    bids = {}; asks = {}; out = []; last = None
    for i in range(n):
        if snap[i] and (i == 0 or not snap[i - 1]): bids.clear(); asks.clear()
        d = bids if isbid[i] else asks
        if q[i] == 0.0: d.pop(p[i], None)
        else: d[p[i]] = q[i]
        if bids and asks:
            cur = (max(bids), min(asks))
            if cur != last: out.append((i,) + cur); last = cur
    return out

def book_heap(n, p, q, snap, isbid):
    bids = {}; asks = {}; hb = []; ha = []; out = []; last = None
    for i in range(n):
        if snap[i] and (i == 0 or not snap[i - 1]): bids.clear(); asks.clear(); hb.clear(); ha.clear()
        px = p[i]
        if isbid[i]:
            if q[i] == 0.0: bids.pop(px, None)
            else:
                if px not in bids: heapq.heappush(hb, -px)
                bids[px] = q[i]
        else:
            if q[i] == 0.0: asks.pop(px, None)
            else:
                if px not in asks: heapq.heappush(ha, px)
                asks[px] = q[i]
        while hb and (-hb[0]) not in bids: heapq.heappop(hb)
        while ha and ha[0] not in asks: heapq.heappop(ha)
        if hb and ha:
            cur = (-hb[0], ha[0])
            if cur != last: out.append((i,) + cur); last = cur
    return out

def digest(out): return hashlib.sha256(repr(out).encode()).hexdigest()[:16]

if __name__ == "__main__":
    n, t_read, t_cast, p, q, snap, isbid = load(sys.argv[1])
    print(f"rows {n}  read {t_read:.3f}s ({n/t_read/1e6:.2f} Mрядов/с)  cast price+qty {t_cast:.3f}s ({n/t_cast/1e6:.2f} Mрядов/с)")
    res = {}
    for name, fn in (("heap", book_heap),) + ((("naive", book_naive),) if "naive" in sys.argv[2:] else ()):
        t = time.time(); out = fn(n, p, q, snap, isbid); dt = time.time() - t
        res[name] = digest(out)
        print(f"{name}: {dt:.2f}s ({n/dt/1e3:.0f} kрядов/с)  смен лучшей пары {len(out)}  sha {res[name]}")
    if len(res) > 1: print("совпадение", len(set(res.values())) == 1)
