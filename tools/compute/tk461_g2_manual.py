#!/usr/bin/env python3
"""TK-461: ручная сверка сделок bp g2.* D1 (Судья 04:14 п.4). Эталон Python (tk463_d2b_ref.one) по минутам против строк Rust d2b-out.
python tk461_g2_manual.py <SYM> <ev> <dir> <число сделок>   (на calc; PYTHONPATH=/data/tk0455:/data/tk0461:/data/tk0463)
Печатает на сделку: вход (open минуты e), стоп, причина, выход, gross/fee/fund, net; минутный путь до выхода; b из d2b-out той же даты."""
import sys
import numpy as np, pandas as pd
import tk463_d2b_ref as R
from sancho_l3_trades import Coin

sym, ev, dr, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
SRC = "/srv/alpha-sda/candles-1m/binance-um"
E = pd.read_csv(f"/data/tk0461/d2b-ev-bp3/{sym}-1440.csv.gz")
E = E[(E.ev == ev) & (E["dir"] == dr)].head(n)
cn = Coin(f"{SRC}/{sym}.csv.gz", 1440, 2, "/data/tk0455/fund")
T = {d: R.tables(cn, d) for d in (1, -1)}
cell = ("SF1", 1.0, "TN", 0.0, "", "OT1", 0.2)
O = pd.read_csv(f"/data/tk0461/d2b-out-bp3/{sym}-1440.csv.gz", dtype=str)
O = O[(O.ev == ev) & (O["dir"] == dr) & (O.stop == "SF1.0") & (O.take == "TN") & (O.ot == "OT1") & (O.otp == "0.2") & O.be_r.isna()]
for s in E.to_dict("records"):
    r = R.one(cn, T, s, int(s["side"]), cell, None)
    day = s["tt0"] // 86400000
    rb = O[O.day == str(day)]
    e = int(np.searchsorted(cn.tm, s["tt0"] + 1440 * 60000))
    d = int(s["side"])
    o, h, l, c = cn.px[d]
    print("---", sym, ev, dr, "tt0", pd.to_datetime(s["tt0"], unit="ms"), "side", d, "atr_bps %.1f ot1_bps %.1f" % (s["atr_bps"], s["ot1_bps"]))
    print("rust b=%s" % (rb.b.astype(float).tolist()))
    if r is None:
        print("python: пропуск"); continue
    print("python:", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items() if k in ("reason", "gross", "fee_mk", "fee_tk", "fund", "bars", "mfe_bps", "risk_bps")})
    print("entry open[e]=%.6g at %s; short-orient stop=%.6g" % (o[e], pd.to_datetime(cn.tm[e], unit="ms"), o[e] - r["risk_bps"] / 1e4 * abs(o[e])))
    mx = e + int(round(r["bars"] * 1440)) - 1
    print("exit minute", pd.to_datetime(cn.tm[mx], unit="ms"), "o,h,l,c(orient)=", o[mx], h[mx], l[mx], c[mx], "min low over trade=%.6g" % l[e:mx + 1].min(), "max high=%.6g" % h[e:mx + 1].max())
