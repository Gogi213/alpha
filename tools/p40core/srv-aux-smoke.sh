#!/bin/bash
# TK-500: aux_feats для ног без рядов (Bybit/Gate, корень «-»): DOGE 17.09, пары 1 (Binance x OKX), 6 (Binance x Bybit), 7 (OKX x Gate).
# (1) пара 1: колонки 1-8 .aux.csv байт в байт (без CR csv.writer) с прежним out_f.csv.aux.csv (srv-test.sh old); (2) пары 6, 7: srcX, rX = NaN, phi = NaN, oi5m по ноге с рядами; (3) assemble без/с --aux-strict.
cd /root/p40core || exit 1
printf 'asset,pair,exA,symA,multA,lotA,stepA,exB,symB,multB,lotB,stepB,ctA,ctB\n1,1,binance,DOGEUSDT,,,,okx,DOGE-USDT-SWAP,,,,1,1000\n1,6,binance,DOGEUSDT,,,,bybit,DOGEUSDT,,,,1,1\n1,7,okx,DOGE-USDT-SWAP,,,,gate,DOGE_USDT,10.0,,,1000,1\n' > pairs_aux.csv
rm -rf /tmp/orchx
python3 orch.py pairs_aux.csv /tmp/orchx --days 2026-09-17 --jobs 1 2>&1 | tail -3
O=/tmp/orchx/1/2026-09-17
cut -d, -f1-8 /tmp/orchx/1/2026-09-17/1.aux.csv | cmp - <(cut -d, -f1-8 out_f.csv.aux.csv | tr -d '\r') && echo "pair1 aux 1-8: байт в байт"
for p in 1 6 7; do echo "pair $p: $(head -1 $O/$p.aux.csv)"; awk -F, 'NR>1{n++; if($3!="nan")oi++; if($5!="nan")ph++; l+=$4; if($9==0)a0++; if($10==0)b0++}END{print "  строк",n,"oi5m конечных",oi+0,"phi конечных",ph+0,"liq=1",l+0,"srcA=0",a0+0,"srcB=0",b0+0}' $O/$p.aux.csv; done
cat /tmp/orchx/specs.txt
for m in "" --aux-strict; do python3 assemble.py /tmp/orchx/t$m.npz /tmp/orchx/b$m.json $m $(cat /tmp/orchx/specs.txt | tr '\n' ' ') | tail -1
python3 - "$m" <<'PY'
import numpy as np, sys
t = np.load("/tmp/orchx/t%s.npz" % sys.argv[1])
for p in (1, 6, 7):
    k = t["pair"] == p
    print(" ", sys.argv[1] or "умолч.", "pair", p, "n", int(k.sum()), "f_oi5m -1:", int((t["f_oi5m"][k] == -1).sum()), "f_liq -1:", int((t["f_liq"][k] == -1).sum()), "f_phi_sign -1:", int((t["f_phi_sign"][k] == -1).sum()))
PY
done
