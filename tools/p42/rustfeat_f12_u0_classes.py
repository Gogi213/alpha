"""TK-611: классы расхождений f12 против `ep_u0.parquet` по столбцам, различающимся по построению маршрута (`ep_no`, `n_sig_ep`, `last_sig_ms`).

  python3 rustfeat_f12_u0_classes.py <ep_u0.parquet> <rust.csv> <long|short>
`ep_u0` — маршрут `sigfeat_run.Worker.do_universe` (TK-594): сигналы одного уровня, не уходившие от него, сливаются в один эпизод; признаки считаются на первом
сигнале, `n_sig_ep` — число слитых сигналов, `last_sig_ms` — время последнего, `ep_no` — номер эпизода внутри `(монета, уровень)` по ВСЕМ сигналам.
Порт — маршрут `CandWorker` (TK-599): строка sigsx = один эпизод (`n_sig_ep = 1`, `last_sig_ms = sig_ms`, `ep_no` — номер кандидата в порядке `(level, sig_ms)`
по монете среди поданных строк). Значит: на эпизодах из одного сигнала `n_sig_ep`/`last_sig_ms` совпадают, на слитых — нет; `ep_no` совпадает только случайно.
"""
import sys

import pandas as pd

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from rustfeat_f12_gate import read_rust_csv, ref_frame  # noqa: E402

ref_p, csv_p, side = sys.argv[1:4]
ref = ref_frame(ref_p, side)
new = read_rust_csv(csv_p)
key = ["sym", "level", "sig_ms"]
j = ref.merge(new, on=key, suffixes=("", "__rs"), validate="one_to_one")
single = j["n_sig_ep"] == 1
multi = ~single
ds = j["n_sig_ep"] != j["n_sig_ep__rs"]
dl = j["last_sig_ms"] != j["last_sig_ms__rs"]
dn = j["ep_no"] != j["ep_no__rs"]
out = [
    f"classes {side}: rows={len(j)}; ep_u0 episodes with n_sig_ep==1: {int(single.sum())}, merged (n_sig_ep>1): {int(multi.sum())}",
    f"  n_sig_ep  differs: single={int((ds & single).sum())} merged={int((ds & multi).sum())} (merged rows where it must differ: {int(multi.sum())})",
    f"  last_sig_ms differs: single={int((dl & single).sum())} merged={int((dl & multi).sum())} (merged rows with last_sig_ms != sig_ms: {int((multi & (j['last_sig_ms'] != j['sig_ms'])).sum())})",
    f"  ep_no differs: {int(dn.sum())} of {len(j)} (ref = episode index per (sym, level) over all signals; port = candidate index per coin over supplied rows)",
]
ok = int((ds & single).sum()) == 0 and int((dl & single).sum()) == 0
out.append("  CLASS-OK" if ok else "  CLASS-FAIL: n_sig_ep/last_sig_ms differ on single-signal episodes")
print("\n".join(out))
sys.exit(0 if ok else 1)
