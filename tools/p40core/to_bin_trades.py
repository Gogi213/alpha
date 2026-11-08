"""TK-500: parquet сделок (CHFT) -> плоский бинарник для Rust-ядра: запись 25 Б LE = event_time i64 (мс; как в эталоне TK-501, не trade_time), price f64, qty f64, flags u8 (бит0 is_buyer_maker).
Запуск: to_bin_trades.py <in.parquet> <out.bin>"""
import sys, numpy as np, pyarrow.parquet as pq, pyarrow.compute as pc
tb = pq.read_table(sys.argv[1], columns=["event_time", "price", "quantity", "is_buyer_maker"])
r = np.empty(tb.num_rows, dtype=np.dtype([("t", "<i8"), ("px", "<f8"), ("qt", "<f8"), ("fl", "u1")]))
r["t"] = tb["event_time"].to_numpy()
r["px"] = pc.cast(tb["price"], "float64").to_numpy(); r["qt"] = pc.cast(tb["quantity"], "float64").to_numpy()
r["fl"] = tb["is_buyer_maker"].to_numpy(zero_copy_only=False).astype("u1")
r = r[np.argsort(r["t"], kind="stable")]
open(sys.argv[2], "wb").write(r.tobytes())
