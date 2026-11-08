"""Эталон П-40 (TK-501), шаг 4: вспомогательные ряды ноги за сутки — mark/funding/OI/ликвидации -> aux_<name>.npz.
Использование: series.py <root> <sym> <ctval> <out.npz>. Ставка фандинга: из mark_price.funding_rate (Binance — каждая секунда;
OKX — редкие записи, 61 за час), next_funding_time в мс."""
import sys
import numpy as np
import pyarrow.parquet as pq
import pyarrow.compute as pc


def col(t, name):
    return pc.cast(t[name], "float64").to_numpy(zero_copy_only=False)


def main(root, sym, ctval, outp):
    fr_t, fr_v, fr_n, oi_t, oi_v, lq_t = [], [], [], [], [], []
    for h in range(24):
        d = f"{root}/{h:02d}/{sym}"
        m = pq.read_table(f"{d}_mark_price.parquet", columns=["event_time", "funding_rate", "next_funding_time"])
        fv = col(m, "funding_rate")
        k = ~np.isnan(fv)
        fr_t.append(m["event_time"].to_numpy()[k]); fr_v.append(fv[k]); fr_n.append(col(m, "next_funding_time")[k])
        o = pq.read_table(f"{d}_open_interest.parquet", columns=["timestamp", "sum_open_interest_value"])
        oi_t.append(o["timestamp"].to_numpy()); oi_v.append(col(o, "sum_open_interest_value"))
        try:  # часы без ликвидаций файла не имеют
            lq_t.append(pq.read_table(f"{d}_liquidations.parquet", columns=["event_time"])["event_time"].to_numpy())
        except FileNotFoundError:
            lq_t.append(np.zeros(0, dtype=np.int64))
    cat = np.concatenate
    ft, fv, fn = cat(fr_t), cat(fr_v), cat(fr_n)
    o = np.argsort(ft, kind="stable")
    ot, ov = cat(oi_t), cat(oi_v)
    oo = np.argsort(ot, kind="stable")
    np.savez_compressed(outp, fr_t=ft[o], fr_v=fv[o], fr_next=fn[o], oi_t=ot[oo], oi_v=ov[oo], liq_t=np.sort(cat(lq_t)))
    print(sym, "funding rows", len(ft), "OI", len(ot), "liq", sum(len(x) for x in lq_t))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4])
