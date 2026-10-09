#!/bin/bash
# approaches при флаге вкл.: b17r1 (g2) против b17r1e (g3) целиком побайтно, SUI и AAVE 20.09 -> /data/tk064/cmpon.out
O=/data/tk064/cmpon.out
for s in SUIUSDT AAVEUSDT; do
  cmp /data/tk064/g2/$s-2026-09-20/on/approaches-$s.csv /data/tk064/g3/$s-2026-09-20/on/approaches-$s.csv >> $O 2>&1 && echo "OK $s on approaches r1 == r1e побайтно" >> $O
done; echo done >> $O
