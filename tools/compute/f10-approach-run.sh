#!/usr/bin/env bash
# F10-fix (аудит этапа F 21.09 §8, план исправления п.1): прогон входа на подходе.
# Отличия от невалидного E15: наборы — **по семьям флоров раздельно** (age | flow, не вместе;
# floors-balance §1: не пересекаются) и с именами ночного набора; две полосы D ∈ {20, 50}.
# Предрегистрация — docs/plan/runs.csv (2026-09-21T18:30Z, шесть строк `prereg`); числа В-80.
# Запуск (счётная, root): systemd-run --unit=alpha-grid-f10fix … -- /opt/alpha-compute/bin/f10-approach-run.sh
set -uo pipefail
cd /opt/alpha-compute || exit 1
BIN=bin/alpha-8cdeb38
RTT="--median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000"
# Корень — ровно сутки кэша подходов (иначе cached_approaches пропускает все символы).
COMMON="--root study/root-f10-1619 --signal approach --queue-model prob:3 $RTT --regime-from study/regime"
COMMON="$COMMON --order-qty-from-pool --threads 3 --h3-mode notional --h3-usd 10000"
COMMON="$COMMON --entry-form ladder3x2..10 --entry-form ladder3x2..20 --entry-form ladder3x2..20w2"
COMMON="$COMMON --entry-ttl-secs wall --entry-ttl-secs 300 --entry-ttl-secs 1800 --band-exit-bps 20"
COMMON="$COMMON --stop-form pct1 --stop-form pct2 --take-form 1to1"
COMMON="$COMMON --deadline-secs 3600 --deadline-secs 7200"
COMMON="$COMMON --exit-form none --exit-form eat20 --exit-form eat50 --exit-form gone50"
# Наборы: имя — как в ночи, ключи — как в предрегистрации (семьи раздельно).
# `a45-bid-b4h-neg` — главная гипотеза H1 (лонг после просадки BTC за 4 ч): режим читается
# по минуте взвода из --regime-from (F10b, 21.09), контекст `ret*` у подходов по-прежнему нет.
SETS="a45-bid:age=2700,side=bid s100-bid:flow=100,side=bid a15-bid:age=900,side=bid a45-bid-b4h-neg:age=2700,side=bid,btc4h_max=0"
# Страж предрегистрации (В-81, TypeSafe): обе семьи флоров в одном наборе или имя ночного
# набора с другими ключами — отказ до запуска (аудит §8 Ф1). Без ключа в окружении страж
# выходит 2 и печатает причину — тогда прогон идёт, а суждение снимается там, где ключ есть.
# shellcheck disable=SC2086
python3 bin/prereg-guard.py --sets $SETS --nightly bin/nightly-grid.sh; rc=$?
if [ "$rc" -eq 1 ]; then echo "prereg-guard: ОТКАЗ — прогон не запущен" | tee -a b5/f10fix.log; exit 1; fi
for D in D20 D50; do
  for S in $SETS; do
    NAME="${S%%:*}"
    LOG="b5/f10fix-$D-$NAME.err"
    echo "== $(date -u +%FT%TZ) $D $NAME start" | tee -a b5/f10fix.log
    # shellcheck disable=SC2086
    $BIN lob bounce-grid $COMMON --touches-from "study/approaches/$D" --set "$S" \
      --out-dir "b5/f10fix-$D" 2>>"$LOG" | tail -1 | tee -a b5/f10fix.log
    echo "== $(date -u +%FT%TZ) $D $NAME done rc=${PIPESTATUS[0]}" | tee -a b5/f10fix.log
  done
done
echo "== $(date -u +%FT%TZ) ALL DONE" | tee -a b5/f10fix.log
