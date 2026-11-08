"""П-40 G0: быстрая версия g0_chft_generic.one (numba для цикла по книге); те же поля результата. Проверена на равенство со старой версией (g0_fast_check)."""
import os
import numpy as np, pyarrow as pa, pyarrow.parquet as pq
from numba import njit, types
from numba.typed import Dict

@njit(cache=True)
def _book(is_snap, is_bid, price, qty, fid, ev, tcode):
    n = len(price)
    bids = Dict.empty(types.float64, types.float64); asks = Dict.empty(types.float64, types.float64)
    nb = -1e300; na = 1e300; bb = -2e300; ba = 2e300
    crossed = 0; checked = 0
    ages = np.empty(n); nages = 0; last = ev[0]
    snap_t = np.empty(n); ns = 0; prev_snap = False
    for i in range(n):
        s = is_snap[i]
        if s and ((not prev_snap) or fid[i] != fid[i - 1]):
            bids = Dict.empty(types.float64, types.float64); asks = Dict.empty(types.float64, types.float64)
            nb = -1e300; na = 1e300
            snap_t[ns] = ev[i]; ns += 1
        prev_snap = s
        p = price[i]
        if is_bid[i]:
            if qty[i] == 0:
                if p in bids:
                    del bids[p]
                    if p == nb:
                        nb = -1e300
                        for k in bids: 
                            if k > nb: nb = k
            else:
                bids[p] = qty[i]
                if p > nb: nb = p
        else:
            if qty[i] == 0:
                if p in asks:
                    del asks[p]
                    if p == na:
                        na = 1e300
                        for k in asks:
                            if k < na: na = k
            else:
                asks[p] = qty[i]
                if p < na: na = p
        if i + 1 == n or fid[i + 1] != fid[i] or tcode[i + 1] != tcode[i]:
            if len(bids) > 0 and len(asks) > 0:
                checked += 1
                if nb >= na: crossed += 1
                if nb != bb or na != ba:
                    ages[nages] = ev[i] - last; nages += 1; last = ev[i]; bb = nb; ba = na
    return crossed, checked, ages[:nages], snap_t[:ns]

def one(p):
    r = {"file": os.path.basename(p), "ex": p.split("/")[-2]}
    try:
        t = pq.read_table(p)
    except Exception as e:
        r["error"] = repr(e)[:120]; return r
    n = t.num_rows; r["rows"] = n
    if n == 0: return r
    et_d = t["event_type"].dictionary_encode().combine_chunks()
    tcode = et_d.indices.to_numpy(zero_copy_only=False).astype(np.int64); names = et_d.dictionary.to_pylist()
    r["types"] = {names[k]: int(c) for k, c in zip(*np.unique(tcode, return_counts=True))}
    snap_code = names.index("snapshot") if "snapshot" in names else -1
    is_snap = tcode == snap_code
    rt = t["received_time"].to_numpy() / 1e6; ev = t["event_time"].to_numpy().astype(float)
    r["lag_ms_q"] = [float(x) for x in np.percentile(rt - ev, [1, 50, 99])]
    r["event_time_nonmono"] = int((np.diff(ev) < 0).sum())
    fid = t["final_update_id"].to_numpy(zero_copy_only=False).astype(float)
    r["final_id_nonmono"] = int((np.diff(fid) < 0).sum())
    pv = t["prev_final_update_id"].to_numpy(zero_copy_only=False).astype(float)
    m = (~is_snap) & ~np.isnan(pv)
    if m.sum() > 1:
        keep = np.r_[True, fid[m][1:] != fid[m][:-1]]
        a, b = fid[m][keep], pv[m][keep]
        r["prev_gaps"] = int((b[1:] != a[:-1]).sum()); r["prev_msgs"] = int(len(a))
    else:
        r["prev_gaps"] = None
    sd = t["side"].dictionary_encode().combine_chunks(); sn = sd.dictionary.to_pylist()
    is_bid = sd.indices.to_numpy(zero_copy_only=False) == (sn.index("bid") if "bid" in sn else -1)
    price = t["price"].cast(pa.float64()).to_numpy(zero_copy_only=False); qty = t["quantity"].cast(pa.float64()).to_numpy(zero_copy_only=False)
    cr, ch, ages, snap_t = _book(is_snap, is_bid, price, qty, fid, ev, tcode)
    r["checked"] = int(ch); r["crossed"] = int(cr)
    if len(ages): r["best_age_ms_q"] = [float(x) for x in np.percentile(ages, [50, 99, 99.9])]
    r["snapshots"] = int(len(snap_t))
    if len(snap_t) > 1: r["snap_interval_s_q"] = [float(x) for x in np.percentile(np.diff(snap_t) / 1000, [50, 99])]
    return r
