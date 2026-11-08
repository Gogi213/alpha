"""TK-500: parquet orderbook (CHFT) -> плоский бинарник для Rust-ядра: запись 33 Б LE = event_time i64, final_update_id i64 (null=-1), price f64, qty f64, flags u8 (бит0 snapshot, бит1 bid).
Запуск: to_bin.py <in.parquet> <out.bin>"""
import sys, numpy as np, pyarrow.parquet as pq, pyarrow.compute as pc
tb = pq.read_table(sys.argv[1], columns=["event_time", "event_type", "final_update_id", "side", "price", "quantity"])
r = np.empty(tb.num_rows, dtype=np.dtype([("et", "<i8"), ("fu", "<i8"), ("px", "<f8"), ("qt", "<f8"), ("fl", "u1")]))
r["et"] = tb["event_time"].to_numpy(); r["fu"] = pc.fill_null(tb["final_update_id"], -1).to_numpy()
r["px"] = pc.cast(tb["price"], "float64").to_numpy(); r["qt"] = pc.cast(tb["quantity"], "float64").to_numpy()
r["fl"] = pc.equal(tb["event_type"], "snapshot").to_numpy(zero_copy_only=False).astype("u1") | (pc.equal(tb["side"], "bid").to_numpy(zero_copy_only=False).astype("u1") << 1)
open(sys.argv[2], "wb").write(r.tobytes())
