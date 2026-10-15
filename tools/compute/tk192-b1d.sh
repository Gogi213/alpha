#!/bin/bash
# TK-192: closes базы B1 (ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800, t-bid-btc4h-q1) v171c без пересчёта бэктеста (В-213):
# готовое busy-replay дерево /data/p12r2a/vn-b -> portfolio-sim --drop TRUMP/TRX/BCH, cap 0 и 3, янв–сен.
#  (1) closes-b1d-cap<N>-<мес>.json — как есть (гейт: free 2750 сд./+52,3 $, cap3 1503/−0,4 $)
#  (2) closes-b1du-cap<N>-<мес>.json — дерево без символо-суток r2b-failed-symdays.csv (вселенная R2-B)
# Метка /data/p12r3a/b1d.done.
set -u
O=/data/p12r3a; SRC=/data/p12r2a/vn-b; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT
S=t-bid-btc4h-q1; B=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800
cd $O || exit 2; rm -f b1d.done b1d-fail.txt
# дерево (2): ссылки на SRC, файлы с символо-сутками — отфильтрованная копия (сделки других монет не трогаем)
rm -rf b1u-b; mkdir -p b1u-b
for mo in 2026-01 2026-02 2026-03 2026-04 2026-05 2026-06 2026-07 2026-08 2026-09; do cp -rs $SRC/$mo b1u-b/; done
python3 - <<'PY'
import csv, os
O="/data/p12r3a"; SRC="/data/p12r2a/vn-b"
for r in csv.DictReader(open(O+"/r2b-failed-symdays.csv")):
    d, s = r["day"], r["symbol"]; mo = d[:7]
    for st in os.listdir(f"{SRC}/{mo}/{d}"):
        p = f"{SRC}/{mo}/{d}/{st}/rounds.csv"
        if not os.path.exists(p): continue
        lines = open(p, encoding="utf-8").read().split("\n")
        out = [l for l in lines if not (l.startswith(s + ",") and l.split(",")[1] == d)]
        q = f"{O}/b1u-b/{mo}/{d}/{st}"
        os.unlink(q) if os.path.islink(q) else None
        if os.path.islink(f"{O}/b1u-b/{mo}/{d}"): pass
        os.makedirs(q, exist_ok=True)
        # каталог суток — симлинки на файлы; заменяем ссылки реальным каталогом
        open(q + "/rounds.csv", "w", encoding="utf-8").write("\n".join(out))
        print("filtered", d, s, st, len(lines) - len(out))
PY
for mo in 2026-01 2026-02 2026-03 2026-04 2026-05 2026-06 2026-07 2026-08 2026-09; do
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$mo=$SRC:$mo" --variant "$S@$B=$S/$B" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-b1d-cap$cap-$mo.json > psim-b1d-cap$cap-$mo.txt 2> psim-b1d-cap$cap-$mo.log || echo "FAIL b1d $cap $mo" >> b1d-fail.txt
    python3 $T/portfolio-sim.py --epoch "$mo=$O/b1u-b:$mo" --variant "$S@$B=$S/$B" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-b1du-cap$cap-$mo.json > psim-b1du-cap$cap-$mo.txt 2> psim-b1du-cap$cap-$mo.log || echo "FAIL b1du $cap $mo" >> b1d-fail.txt
  done
done
touch b1d.done
