"""П-40 G0, часть 1: читаемость parquet Binance (все файлы — подвал) + глубокая проверка выборки.
Запуск на 5.231.25.224: python3 g0_binance.py <out.json>. Исходов не касается."""
import os, sys, json, glob, random
from multiprocessing import Pool
import pyarrow.parquet as pq
import numpy as np

ROOT = "/data/stakan/chft/binance_futures"

def footer(p):
    try:
        m = pq.ParquetFile(p).metadata
        return (p, m.num_rows, None)
    except Exception as e:
        return (p, -1, repr(e)[:120])

def deep(p):
    r = {"file": p}
    try:
        t = pq.read_table(p, columns=["received_time", "event_time", "event_type", "first_update_id",
                                      "final_update_id", "prev_final_update_id", "side", "price", "quantity"])
    except Exception as e:
        r["error"] = repr(e)[:120]; return r
    n = t.num_rows; r["rows"] = n
    et = t["event_type"].to_numpy(zero_copy_only=False)
    r["types"] = {k: int(v) for k, v in zip(*np.unique(et, return_counts=True))}
    rt = t["received_time"].to_numpy() / 1e6  # мс
    ev = t["event_time"].to_numpy().astype(float)
    lag = (rt - ev)
    r["lag_ms_q"] = [float(x) for x in np.percentile(lag, [1, 50, 99])]
    r["event_time_nonmono"] = int((np.diff(ev) < 0).sum())
    # непрерывность update_id по дифам
    m = et != "snapshot"
    fu = t["first_update_id"].to_numpy(zero_copy_only=False)[m].astype(float)
    fi = t["final_update_id"].to_numpy(zero_copy_only=False)[m].astype(float)
    pv = t["prev_final_update_id"].to_numpy(zero_copy_only=False)[m].astype(float)
    if len(fi):
        keep = np.r_[True, fi[1:] != fi[:-1]]  # одно сообщение = несколько строк
        fi2, pv2 = fi[keep], pv[keep]
        r["msgs"] = int(len(fi2))
        r["gaps"] = int((pv2[1:] != fi2[:-1]).sum())
        r["final_id_nonmono"] = int((np.diff(fi2) < 0).sum())
    # кроссированная книга: проход по сообщениям
    side = t["side"].to_numpy(zero_copy_only=False)
    price = np.array(t["price"].to_pylist(), dtype=float)
    qty = np.array(t["quantity"].to_pylist(), dtype=float)
    bids, asks = {}, {}
    crossed = checked = 0
    fid = t["final_update_id"].to_numpy(zero_copy_only=False)
    ages = []
    last_chg = ev[0]
    prev_id = None
    bb = ba = None
    for i in range(n):
        book = bids if side[i] == "bid" else asks
        if qty[i] == 0:
            book.pop(price[i], None)
        else:
            book[price[i]] = qty[i]
        if i + 1 == n or fid[i + 1] != fid[i]:
            if bids and asks:
                nb, na = max(bids), min(asks)
                checked += 1
                if nb >= na: crossed += 1
                if nb != bb or na != ba:
                    ages.append(ev[i] - last_chg); last_chg = ev[i]; bb, ba = nb, na
    r["checked"] = checked; r["crossed"] = crossed
    if ages:
        r["best_age_ms_q"] = [float(x) for x in np.percentile(ages, [50, 99, 99.9])]
    r["bidask_n_final"] = [len(bids), len(asks)]
    return r

if __name__ == "__main__":
    out = sys.argv[1]
    files = sorted(glob.glob(f"{ROOT}/*/*/*.parquet"))
    print("files", len(files), flush=True)
    with Pool(8) as pool:
        fr = pool.map(footer, files, chunksize=200)
    bad = [x for x in fr if x[2]]
    empty = [x[0] for x in fr if x[1] == 0]
    print("footer bad", len(bad), "empty", len(empty), flush=True)
    random.seed(40)
    days = sorted({f.split("/")[-3] for f in files})
    sample = []
    for d in days:
        for h in ("03", "15"):
            cand = [f for f in files if f"/{d}/{h}/" in f]
            pick = [f for f in cand if f.endswith(("/BTCUSDT_orderbook.parquet", "/AAVEUSDT_orderbook.parquet"))]
            rest = [f for f in cand if f not in pick]
            if rest: pick.append(random.choice(rest))
            sample += pick
    print("deep sample", len(sample), flush=True)
    with Pool(8) as pool:
        dr = pool.map(deep, sample, chunksize=1)
    json.dump({"n_files": len(files), "footer_bad": bad[:50], "n_footer_bad": len(bad), "n_empty": len(empty),
               "empty_examples": empty[:20], "deep": dr}, open(out, "w"))
    print("done", flush=True)
