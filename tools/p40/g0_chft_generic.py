"""П-40 G0: годность книг OKX/Bitget/Aster/Hyperliquid (CHFT parquet, общая схема). Запуск: g0_chft_generic.py <каталог с <биржа>/*.parquet> <out.json>.
Проверки на файл: типы событий, received−event (мс), event_time назад, final_id назад, разрывы prev_final (если поле есть),
кроссы, возраст лучших цен, интервал между снимками. Исходов не касается."""
import sys, json, glob, os
from multiprocessing import Pool
import pyarrow.parquet as pq
import numpy as np

def one(p):
    r = {"file": os.path.basename(p), "ex": p.split("/")[-2]}
    try:
        t = pq.read_table(p)
    except Exception as e:
        r["error"] = repr(e)[:120]; return r
    n = t.num_rows; r["rows"] = n
    if n == 0: return r
    et = t["event_type"].to_numpy(zero_copy_only=False)
    r["types"] = {k: int(v) for k, v in zip(*np.unique(et, return_counts=True))}
    rt = t["received_time"].to_numpy() / 1e6; ev = t["event_time"].to_numpy().astype(float)
    r["lag_ms_q"] = [float(x) for x in np.percentile(rt - ev, [1, 50, 99])]
    r["event_time_nonmono"] = int((np.diff(ev) < 0).sum())
    fid = t["final_update_id"].to_numpy(zero_copy_only=False).astype(float)
    r["final_id_nonmono"] = int((np.diff(fid) < 0).sum())
    pv = t["prev_final_update_id"].to_numpy(zero_copy_only=False).astype(float)
    m = (et != "snapshot") & ~np.isnan(pv)
    if m.sum() > 1:
        keep = np.r_[True, fid[m][1:] != fid[m][:-1]]
        a, b = fid[m][keep], pv[m][keep]
        r["prev_gaps"] = int((b[1:] != a[:-1]).sum()); r["prev_msgs"] = int(len(a))
    else:
        r["prev_gaps"] = None  # поля prev_final нет — разрывы не проверяемы
    side = t["side"].to_numpy(zero_copy_only=False)
    price = np.array(t["price"].to_pylist(), dtype=float); qty = np.array(t["quantity"].to_pylist(), dtype=float)
    bids, asks = {}, {}; crossed = checked = 0; ages = []; last = ev[0]; bb = ba = None
    snap_t = []; prev_snap = False
    for i in range(n):
        is_snap = et[i] == "snapshot"
        if is_snap and (not prev_snap or fid[i] != fid[i - 1]):
            bids.clear(); asks.clear(); snap_t.append(ev[i])
        prev_snap = is_snap
        book = bids if side[i] == "bid" else asks
        if qty[i] == 0: book.pop(price[i], None)
        else: book[price[i]] = qty[i]
        if i + 1 == n or fid[i + 1] != fid[i] or et[i + 1] != et[i]:
            if bids and asks:
                nb, na = max(bids), min(asks); checked += 1
                if nb >= na: crossed += 1
                if nb != bb or na != ba:
                    ages.append(ev[i] - last); last = ev[i]; bb, ba = nb, na
    r["checked"] = checked; r["crossed"] = crossed
    if ages: r["best_age_ms_q"] = [float(x) for x in np.percentile(ages, [50, 99, 99.9])]
    r["snapshots"] = len(snap_t)
    if len(snap_t) > 1: r["snap_interval_s_q"] = [float(x) for x in np.percentile(np.diff(snap_t) / 1000, [50, 99])]
    return r

if __name__ == "__main__":
    files = sorted(glob.glob(sys.argv[1] + "/*/*.parquet"))
    with Pool(8) as pool: res = pool.map(one, files, chunksize=1)
    json.dump(res, open(sys.argv[2], "w"))
    for ex in sorted({r["ex"] for r in res}):
        rs = [r for r in res if r["ex"] == ex and "error" not in r]
        ch = sum(r.get("checked", 0) for r in rs); cr = sum(r.get("crossed", 0) for r in rs)
        pg = [r["prev_gaps"] for r in rs if r.get("prev_gaps") is not None]
        print(ex, "files", len(rs), "err", sum("error" in r for r in res if r["ex"] == ex), "rows", sum(r["rows"] for r in rs),
              "crossed", cr, "/", ch, "prev_gaps", (sum(pg) if pg else "n/a"),
              "evt_back", sum(r.get("event_time_nonmono", 0) for r in rs), "fid_back", sum(r.get("final_id_nonmono", 0) for r in rs),
              "lag_p50_med", float(np.median([r["lag_ms_q"][1] for r in rs if "lag_ms_q" in r])),
              "age_p99_med", float(np.median([r["best_age_ms_q"][1] for r in rs if "best_age_ms_q" in r])),
              "snap_int_med", float(np.median([r["snap_interval_s_q"][0] for r in rs if "snap_interval_s_q" in r] or [-1])))
