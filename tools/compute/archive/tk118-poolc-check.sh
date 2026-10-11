#!/usr/bin/env bash
# TK-118 «поймала бы»: копия шагов TK-115 poolc-c.sh (m-feb, сутки 2026-02-01, монета 0GUSDT) -> guard.py plan.
# Журнал — копия боевого во временном REG_DIR (боевой не трогаем). Ждём: B1 grid СТАРЫЙ бинарник (TK-064) — ГОТОВО (adopt той же командой);
# B1 grid НОВЫЙ — СЧИТАЕМ (в выходе +2 колонки 60s, equiv отказывает); touches — СЧИТАЕМ (кэш TK-064 стёрт); e106 — СЧИТАЕМ (не регистрировался).
set -u
export REG_DIR=/tmp/regtest118; rm -rf "$REG_DIR"; mkdir -p "$REG_DIR"; cp /data/registry/ledger.jsonl "$REG_DIR"/
G="python3 /data/registry/guard.py"
OLD=/data/tk064/bin/alpha-tk064-r1; NEW=/opt/alpha-compute/bin/alpha-b26tk115r2
d=2026-02-01; s=0GUSDT; V=/data/tk044/final3/verdict.csv
E=/data/tk046/feb/home/alpha/epochs/e-feb; W=/data/tk0115/pool/m-feb/w-$d; T=$'\t'
grid() { echo "$1 lob bounce-grid --verdict-csv $V --root $W/study/root-$d --touches-from $W/study/approaches/D20 --signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 --regime-from $W/study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --entry-ttl-secs 1800 --band-exit-bps 20 --busy-skip off --threads 2 --hold-step skip --exit-group on --events wide --entry-form ladder3x0..0.0409sw2 --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 --sigma-from $W/study/sigma240 --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55 --r1-cols --out-dir $W/b5/out-$d"; }
touches() { echo "$1 lob touches --root $W/study/root-$d --symbol $s --h3-mode notional --h3-usd 10000 --approach-bps 20 --r1-cols --out $W/study/approaches/D20/$d/touches-$s.csv"; }
IN=$E/study/root-$d
SIG=/data/tk064/pool/m-feb/signals/$d
# TK-064 выложил свой выход той же командой старым бинарником -> регистрируем факт (--why), не равенство бинарников
$G adopt b1grid-old --out "$SIG" --in "$IN" --why "TK-064 tk064-pool2.sh, alpha-tk064-r1, m-feb $d" -- $(grid $OLD)
# equiv новый=старый на B1 grid: пары (выход старого TK-064 vs выход нового TK-115) — guard сверяет сам
echo "== equiv (ждём ОТКАЗ: +2 колонки 60s)"
$G equiv --new $NEW --old $OLD --scope b1grid-new --pair "$SIG" "/data/tk0115/pool/m-feb/signals/$d" 2>&1 | tail -2
F=/tmp/regtest118/steps.tsv
{
  echo "touches${T}$W/study/approaches/D20/$d/touches-$s.csv${T}$IN${T}$(touches $NEW)"
  echo "b1grid-old(TK-064)${T}$SIG${T}$IN${T}$(grid $OLD)"
  echo "b1grid-new(TK-115)${T}$SIG${T}$IN${T}$(grid $NEW)"
  echo "e106-r3c${T}/data/tk0115/pool/m-feb/r3c/$d${T}$IN${T}bash $W/b5/r3c-$d.sh"
} > "$F"
echo "== guard.py plan"; $G plan --steps "$F"
