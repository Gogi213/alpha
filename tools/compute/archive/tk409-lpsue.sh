#!/bin/bash
# TK-409 возврат Судьи 08:58: LAPTOP (base aerodrome) и SUE (bsc pancake v2) — gev2 LAPTOP, середина из Sync (SQD), S(б)(в), Rq. Метка LS/LS.done
B=/data/tk0393/tk409/LS; PY=/data/tk0380/npvenv/bin/python; S=/data/tk0393; export GEV=/data/tk0393/tk401/gev2all BASIS=ev
mkdir -p $B/sqd; cd $B; rm -f LS.done
printf 'coin,chain,pool\nSUE,bsc,0xfFCa411CFbcD3194503eFfA80Fd394b55387915c\nLAPTOP,base,0x99cf3E8bfB02c300312c53Aac5D0B082e3D5975C\n' > pools.csv
for pair in bsc:binance-mainnet base:base-mainnet; do c=${pair%%:*}; ds=${pair##*:}
  PYTHONUTF8=1 python3 -u $S/tk410/tk410-sqd-evm.py $ds pools.csv $c $B/sqd 1792022400 1792713599 >> $B/dl-$c.log 2>&1 &
done
# gev2 LAPTOP (1 ядро)
OB=/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610
fs=$(ls $OB/LAPTOP_USDT-20261015*.csv.gz $OB/LAPTOP_USDT-2026101[6-9]*.csv.gz $OB/LAPTOP_USDT-2026102[0-2]*.csv.gz 2>/dev/null | tr '\n' ' ')
$PY /data/tk0402/tk402-gate-bbo-ev.py $GEV LAPTOP $fs > gev2-LAPTOP.log 2>&1 &
wait
# середина: Sync -> ts,block,logidx,цена (SUE: r1/r0 = WBNB за SUE; LAPTOP: token0=USDC, цена = r0/r1)
sed 's#/data/tk0393/sqd10/#/data/tk0393/tk409/LS/sqd/#' $S/tk410/tk410-extract-all.py > ext.py
$PY ext.py pools.csv raw > ext.log 2>&1
for p in "SUE bsc 0xffca411cfbcd3194503efffa80fd394b55387915c 0" "LAPTOP base 0x99cf3e8bfb02c300312c53aac5d0b082e3d5975c 1"; do set -- $p
  $PY - $1 raw/$2__$3.csv $4 <<'PYEOF'
import sys, csv, gzip
coin, f, inv = sys.argv[1], sys.argv[2], sys.argv[3] == '1'
rows = [r for r in csv.DictReader(open(f)) if r['kind'] == 'v2']
with gzip.open(f'mid-{coin}.csv.gz', 'wt') as o:
    for r in rows:
        p = float(r['mid_raw']); o.write(f"{r['ts']},{r['block']},{r['logidx']},{repr(1 / p if inv else p)}\n")
print(coin, 'n', len(rows))
PYEOF
done > mid.log 2>&1
for c in SUE LAPTOP; do
  ( $PY $S/tk409-ev-conv.py $c $B/mid-$c.csv.gz $B/Eb-$c 1000 b > $B/Eb-$c.log 2>&1; BASIS=ev $PY $S/tk409-ev-conv.py $c $B/mid-$c.csv.gz $B/Ec-$c 1000 c > $B/Ec-$c.log 2>&1
    $PY $S/tk409-ev-rq.py $c $B/mid-$c.csv.gz $B/Rq-$c > $B/Rq-$c.log 2>&1 ) &
done
wait; touch $B/LS.done
