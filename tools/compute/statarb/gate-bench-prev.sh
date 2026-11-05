#!/usr/bin/env bash
# TK-482: гейт «байт в байт» нового бинарника против прежнего (тот же код + только `bench --digest`) на панелях calc,
# скорость ядра (строк/с) и дрейф новых режимов (задержка ноги X, вход по ставке) против референса. Окна/пороги — аргументы
# замера, не назначение. Панели из bench.sh/gate.sh (/data/tk0479/panels-*), фандинг /srv/alpha-sda/funding.
#   gate-bench-prev.sh <новый бинарь> <прежний бинарь> <потоки> [<прежний с правкой эпохи ставок>]
# E-010: ставки фандинга читались в другой эпохе минут, чем панели — у прежнего бинарника фандинг на реальных данных ноль.
# Для funding-* байт в байт — против 4-го аргумента (прежний код + только правка загрузчика), для остальных видов — против 2-го.
# rc 0 — все хеши и счётчики совпали, дрейф новых режимов 0 сделок; строки `ИТОГ` в конце.
set -u
NEW="${1:?новый бинарь}"; PREV="${2:?прежний бинарь}"; TH="${3:-8}"; PREVF="${4:-$PREV}"
W=/data/tk0479; F=/srv/alpha-sda/funding; N=50; NP=$((N * (N - 1) / 2)); bad=0
b() { local bin=$1; shift; "$bin" statarb bench --panels "$W/panels-bybit-linear" --symbols "$N" --min-span 1440 --min-obs 240 \
  --entry-z 2 --exit-z 0.5 --max-hold 1440 --threads "$TH" "$@"; }
# заданий/минут/пропусков/сделок и ставок фандинга — без времени и скорости
core() { grep -o 'заданий [0-9]* из [0-9]*, минут [0-9]*, пропусков [0-9]*, сделок [0-9]*'; }
fund() { grep -o 'ставок фандинга [0-9]*'; }
kinds=(
  "pair|$NP|--kind pair"
  "cross-binance|$N|--kind cross --panels-b $W/panels-binance-um"
  "cross-gate|$N|--kind cross --panels-b $W/panels-gate-futures_usdt"
  "basket|$N|--kind basket --basket-k 10"
  "funding-pp|$N|--kind funding-pp --panels-b $W/panels-binance-um --funding-y $F/bybit-linear --funding-x $F/binance-um"
  "funding-ps|$N|--kind funding-ps --panels-b $W/panels-bybit-spot --funding-y $F/bybit-linear"
)
echo "== байт в байт: новый $NEW против прежнего $PREV (kernel и reference, d=0, правило z)"
for kv in "${kinds[@]}"; do
  name=${kv%%|*}; rest=${kv#*|}; jobs=${rest%%|*}; args=${rest#*|}
  P=$PREV; case "$name" in funding-*) P=$PREVF ;; esac
  for w in "240 240" "1440 1440"; do
    set -- $w
    for e in kernel reference; do
      [ "$e" = reference ] && [ "$1" != 240 ] && continue   # референс O(W): только окно 240, как в bench.sh
      J="--max-jobs $jobs"; [ "$e" = reference ] && J="--max-jobs 8"
      a=$(b "$P" $args $J --window "$1" --refresh "$2" --engine "$e" --digest 2>&1)
      n=$(b "$NEW" $args $J --window "$1" --refresh "$2" --engine "$e" --digest 2>&1)
      da=$(echo "$a" | grep '^digest'); dn=$(echo "$n" | grep '^digest')
      ca="$(echo "$a" | core) $(echo "$a" | fund)"; cn="$(echo "$n" | core) $(echo "$n" | fund)"
      if [ -n "$da" ] && [ "$da" = "$dn" ] && [ "$ca" = "$cn" ]; then s=СОВПАЛО; else s=РАЗНО; bad=1; fi
      echo "ИТОГ байт-в-байт $name window=$1 engine=$e: $s ($da | $dn) [$cn]"
    done
  done
done
# E-010: фандинг теперь ненулевой — хеш funding-pp обязан отличаться от кросс-биржи (в хеш входит funding_bps)
pp=$(b "$NEW" --kind funding-pp --panels-b $W/panels-binance-um --funding-y $F/bybit-linear --funding-x $F/binance-um --max-jobs "$N" --window 240 --refresh 240 --engine kernel --digest 2>&1 | grep '^digest')
cr=$(b "$NEW" --kind cross --panels-b $W/panels-binance-um --max-jobs "$N" --window 240 --refresh 240 --engine kernel --digest 2>&1 | grep '^digest')
if [ -n "$pp" ] && [ "$pp" != "$cr" ]; then s=РАЗНЫЕ; else s=ОДИНАКОВЫЕ; bad=1; fi
echo "ИТОГ фандинг начисляется: хеш funding-pp ($pp) и cross ($cr): $s"
echo "== скорость kernel (строк/с), по два прогона на бинарник; без хеша"
for kv in "${kinds[@]}"; do
  name=${kv%%|*}; rest=${kv#*|}; jobs=${rest%%|*}; args=${rest#*|}
  best_p=0; best_n=0
  for i in 1 2; do
    for who in PREV NEW; do
      bin=$PREV; [ $who = NEW ] && bin=$NEW
      r=$(b "$bin" $args --max-jobs "$jobs" --window 240 --refresh 240 --engine kernel 2>&1 | grep -o '[0-9.]*e[+0-9]* строк/с' | cut -d' ' -f1)
      if [ $who = PREV ]; then best_p=$(awk -v a="$best_p" -v b="$r" 'BEGIN{print (b>a)?b:a}'); else best_n=$(awk -v a="$best_n" -v b="$r" 'BEGIN{print (b>a)?b:a}'); fi
    done
  done
  ratio=$(awk -v a="$best_p" -v b="$best_n" 'BEGIN{printf "%.3f", b/a}')
  echo "ИТОГ скорость $name: прежний $best_p, новый $best_n строк/с, новый/прежний $ratio"
done
echo "== новые режимы на реальных данных: дрейф ядро против референса (8 заданий)"
db() { b "$NEW" "$@" --max-jobs 8 --window 240 --refresh 240 --engine drift 2>&1 | grep '^drift'; }
for dx in 1 2 5; do
  for kv in "cross-binance|--kind cross --panels-b $W/panels-binance-um" \
            "funding-pp|--kind funding-pp --panels-b $W/panels-binance-um --funding-y $F/bybit-linear --funding-x $F/binance-um"; do
    name=${kv%%|*}; args=${kv#*|}
    o=$(db $args --delay-x $dx); echo "ИТОГ дрейф delay-x=$dx $name: $o"
    echo "$o" | grep -q 'разошлось сделок 0,' || bad=1
  done
done
for kv in "funding-ps|--kind funding-ps --panels-b $W/panels-bybit-spot --funding-y $F/bybit-linear" \
          "funding-pp|--kind funding-pp --panels-b $W/panels-binance-um --funding-y $F/bybit-linear --funding-x $F/binance-um"; do
  name=${kv%%|*}; args=${kv#*|}
  for kh in "3 6" "9 24"; do
    set -- $kh
    for dx in 0 2; do
      o=$(db $args --funding-k "$1" --hold-payments "$2" --delay-x $dx)
      echo "ИТОГ дрейф D3 k=$1 h=$2 delay-x=$dx $name: $o"
      echo "$o" | grep -q 'разошлось сделок 0,' || bad=1
      n=$(echo "$o" | grep -o 'сделок ядро [0-9]*' | grep -o '[0-9]*$'); [ "${n:-0}" -gt 0 ] && eval "nz_${name//-/_}=1"
    done
  done
done
# E-010: D3 на реальных данных с ненулевыми сделками хотя бы в одной из комбинаций (перп↔перп)
if [ "${nz_funding_pp:-0}" = 1 ]; then echo "ИТОГ D3 перп↔перп: сделки ненулевые"; else echo "ИТОГ D3 перп↔перп: сделок нет ни в одной комбинации"; bad=1; fi
echo "ИТОГ гейт: $([ $bad = 0 ] && echo ЗЕЛЁНЫЙ || echo КРАСНЫЙ)"
exit $bad
