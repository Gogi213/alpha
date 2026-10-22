#!/usr/bin/env bash
# TK-216 гейт 1 (calc): closes раннера filter-cells.py = closes прямого bounce-grid (TK-215 outB), 2 суток × 4 непустых набора F1.
# Пул — outA (набор pool: age=2700,side=bid), прямой — cmp/closes-B-<набор>.json (portfolio-sim по outB). sha256 значений closes.
#   tk216-gate.sh  -> /data/tk216/gate/{summary.tsv,report.txt}
set -uo pipefail
R=/data/tk214; G=/data/tk216/gate; T=/data/tk083/tools; MO=2026-01
F=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800; FF=$F,$F-pyfresh10
mkdir -p $G/tree/$MO; cd $G || exit 2
for d in 2026-01-02 2026-01-03; do mkdir -p tree/$MO/$d/pool; for f in rounds.csv signals.csv; do ln -sf $R/outA/$d/out/$d/pool/$f tree/$MO/$d/pool/$f; done; done
printf '%s\n' \
 "t-bid-btc4h-q1	$FF	age=2700,side=bid,btc4h_max=-44.55" \
 "g87-fresh	$FF	age=2700,agemax=3600,side=bid,btc4h_max=-44.55" \
 "g10n12q90-2026-01	$FF	age=2700,side=bid,btc12h_max=138.35" \
 "g10n4q50-2026-01	$FF	age=2700,side=bid,btc4h_max=-1.57" > $G/cells.tsv

# режим: по суткам в разных каталогах -> одна папка со ссылками
mkdir -p regime; for d in 2026-01-02 2026-01-03; do ln -sf /data/tk0196/regime/jan/$d.csv regime/$d.csv; done
python3 /data/tk216/filter-cells.py --pool-tree $G/tree/$MO --pool-set pool --pool-base age=2700,side=bid \
  --approaches '/data/p12r3a/jrn/{day}/wt/sub/D20' --regime-from $G/regime --cells $G/cells.tsv --out $G/out --tools $T \
  --psim-deposit 2500 --psim-position 500 --psim-max-pos 0 > report.txt 2>&1 || { echo "FAIL runner" >> report.txt; exit 1; }
python3 - >> report.txt <<'PY'
import json,hashlib
def h(p):
    v=next(iter(json.load(open(p)).values()));return hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest()[:16]
R='/data/tk214/cmp'; G='/data/tk216/gate/out'
for n,b in [('t-bid-btc4h-q1','t-bid-btc4h-q1'),('g87-fresh','g87-fresh'),('g10n12q90-2026-01','g10n12q90-2026-01'),('g10n4q50-2026-01','g10n4q50-2026-01')]:
    a=h(f'{G}/{n}/closes.json');d=h(f'{R}/closes-B-{b}.json');print('GATE',n,a,d,'РАВНЫ' if a==d else 'РАЗНЫ')
PY
touch $G/done
