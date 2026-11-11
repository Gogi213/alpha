#!/bin/bash
# TK-522: smoke assemble --funding на реальных выходах v2 (актив 97, 6 пар, 17.09+14) и таблице фандинга D1 v3 (srv). Запуск на srv: bash srv-fund-smoke.sh
set -e
cd /root/p40core-tk522f
B=/tmp/smk
mkdir -p /tmp/smk; S=/root/p40core/out_v2_gate/97/2026-09-17+14; for p in 1286 1288 1289 1291 1292 1295; do cp $S/$p $S/$p.exits.csv $S/$p.features.csv $S/$p.fills.csv $S/$p.path.csv /tmp/smk/; [ -f $S/$p.aux.csv ] && cp $S/$p.aux.csv /tmp/smk/; done
cp /tmp/auxchk/1295.aux.csv /tmp/smk/   # Bybit×Gate: aux_feats.py <base> - SYM - SYM (как orch после правки)
SP=""
for p in 1286 1288 1289 1291 1292 1295; do SP="$SP 97:0:$p:0:$B/$p"; done
python3 assemble.py /tmp/smk_nof.npz /tmp/smk_b.json --day0 2026-09-17 --cens-s 600 $SP
python3 assemble.py /tmp/smk_f.npz /tmp/smk_b2.json --day0 2026-09-17 --cens-s 600 \
  --funding fund_d1_v3.csv --pairs /root/p40core/pairs_d1_v3_nobitget.csv --funding-window 2026-09-17:2026-10-22 $SP
python3 - <<'EOF'
import numpy as np
a = np.load("/tmp/smk_nof.npz"); b = np.load("/tmp/smk_f.npz")
print("keys f:", sorted(b.files))
print("n", len(a["net_bps"]), len(b["net_bps"]))
d = b["net_bps"] - a["net_bps"]
print("fund_bps: mean %.4f min %.4f max %.4f nonzero %d" % (b["fund_bps"].mean(), b["fund_bps"].min(), b["fund_bps"].max(), (b["fund_bps"] != 0).sum()))
print("net-diff == fund_bps (on executed):", np.allclose(d, b["fund_bps"], atol=1e-9))
print("pnl-only columns equal:", all(np.array_equal(a[k], b[k]) for k in a.files if k not in ("net_bps",)))
EOF
