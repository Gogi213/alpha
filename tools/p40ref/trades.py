"""Эталон П-40 (TK-501): сделки CHFT за 24 часа -> trades_<имя>.npz (t мс, p тик 1e-6, q база, sell = тейкер продаёт = is_buyer_maker).
Запуск: python trades.py <каталог raw/<биржа>> <символ> <ctVal> <выход.npz>"""
import sys, numpy as np, pyarrow.parquet as pq, pyarrow.compute as pc

root, sym, ctval, outp = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
T, P, Q, S = [], [], [], []
for h in range(24):
    t = pq.read_table(f"{root}/{h:02d}/{sym}_trades.parquet", columns=["event_time", "price", "quantity", "is_buyer_maker"])
    T.append(t["event_time"].to_numpy())
    P.append(np.rint(pc.cast(t["price"], "float64").to_numpy() * 1e6).astype(np.int64))
    Q.append(pc.cast(t["quantity"], "float64").to_numpy() * ctval)
    S.append(t["is_buyer_maker"].to_numpy(zero_copy_only=False).astype(bool))
T, P, Q, S = map(np.concatenate, (T, P, Q, S))
o = np.argsort(T, kind="stable")
np.savez_compressed(outp, t=T[o], p=P[o], q=Q[o], sell=S[o])
print(sym, len(T), "monotone_before_sort", bool(np.all(np.diff(T) >= 0)))
