#!/usr/bin/env bash
# Гейт G10 «ускорение без потерь» (владелец 23.09: «даблчек, что ничего не поломается и всё охватится»).
#   OLD=bin/alpha-<старый> NEW=bin/alpha-<новый> gate-g10.sh
# На каждом дне и случае — три прогона: старый бинарник, новый с памятью кругов (`--round-memo on`,
# умолчание), новый без памяти (`off`); тела `rounds.csv` и `forms.csv` каждого набора (строки `#`
# отброшены — в шапке аргументы) обязаны совпасть побайтово. Случаи:
#   touch — ночная сетка на касаниях: 8 стопов × 1:1 × 4 дедлайна, наборы лонг/шорт возраст/сила + режим;
#   exit  — сетка выхода на подходе (F10): стоп × тейк/трейл × 3 дедлайна × {none, eat20, gone20, gone90tr0.5},
#           наборы база лонга, две просадки и база шорта;
#   e7    — дробный выход (half1to1, eat50x80, двойной лот).
# Дни: история 05.09 и 13.09, запись 19.09 и 22.09. Итог — строка на (случай, день, набор, файл) и время.
set -uo pipefail
A="${ALPHA_BASE:-$HOME/alpha}"
OLD="${OLD:?старый бинарник}"; NEW="${NEW:?новый бинарник}"
OUT="${OUT:-$(mktemp -d)}"
mkdir -p "$OUT" || { echo "каталог $OUT не создаётся"; exit 1; }
RTT="--median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000"
USD="--h3-mode notional --h3-usd 10000 --order-qty-from-pool --threads 2"

touch_args="--touches-from study/touches --regime-from study/regime --touches-cache-only --signal touch \
  --entry-form single@fr --entry-ttl-secs wall --band-exit-bps 20 --queue-model risk-adverse $RTT $USD \
  --stop-form before --stop-form at --stop-form behind --stop-form midfr --stop-form stack2 \
  --stop-form pct0.5 --stop-form pct1 --stop-form pct2 --take-form 1to1 --exit-form none \
  --set a45-bid:age=2700,side=bid --set a45-ask:age=2700,side=ask --set s100-bid:flow=100,side=bid \
  --set s100-ask:flow=100,side=ask --set a45-bid-b4h-neg:age=2700,side=bid,btc4h_max=0"
exit_args="--touches-from study/approaches/D20 --regime-from study/regime --signal approach --queue-model prob:3 $RTT $USD \
  --entry-form ladder3x2..20w2 --entry-ttl-secs 1800 --band-exit-bps 20 \
  --stop-form pct0.5 --stop-form pct2 --take-form tk0.5 --take-form tk2 --take-form tr1x1 \
  --deadline-secs 3600 --deadline-secs 7200 --deadline-secs 14400 \
  --exit-form none --exit-form eat20 --exit-form gone20 --exit-form gone90tr0.5 \
  --set t-bid-age-45:age=2700,side=bid --set t-bid-btc1h-q1:age=2700,side=bid,btc1h_max=-21.17 \
  --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55 --set t-ask-age-45:age=2700,side=ask"
e7_args="--touches-from study/touches --regime-from study/regime --touches-cache-only --signal touch \
  --entry-form single@fr --entry-ttl-secs wall --band-exit-bps 20 --queue-model risk-adverse $RTT $USD \
  --stop-form pct0.5 --stop-form pct1 --stop-form pct2 --take-form half1to1 --take-form eat50x80 --order-qty-mult 2 \
  --set a15-s10-any:age=900,flow=10 --set a45-bid:age=2700,side=bid --set a45-ask:age=2700,side=ask"

run() {  # $1 дом, $2 день, $3 случай, $4 бинарник-метка, $5 бинарник, $6.. доп. флаги
  local home=$1 day=$2 kind=$3 tag=$4 bin=$5; shift 5
  local args_var="${kind}_args"
  local dir="$OUT/$kind-$day-$tag"
  local t0; t0=$(date +%s)
  (cd "$home" && nice -n 5 "$A/$bin" lob bounce-grid --root "study/root-$day" ${!args_var} "$@" \
      --out-dir "$dir" > "$dir.log" 2>&1)
  echo "$? $(( $(date +%s) - t0 ))" > "$dir.rc"
}

days="$A/epochs/e-archive:2026-09-05 $A/epochs/e-archive:2026-09-13 $A:2026-09-19 $A:2026-09-22"
for kind in touch exit e7; do
  for spec in $days; do
    home=${spec%%:*}; day=${spec##*:}
    [ "$kind" = e7 ] && [ "$day" != 2026-09-05 ] && [ "$day" != 2026-09-19 ] && continue
    run "$home" "$day" "$kind" old "$OLD" &
    run "$home" "$day" "$kind" on "$NEW" &
    run "$home" "$day" "$kind" off "$NEW" --round-memo off &
    wait
    # Прогон, упавший до записи, — провал гейта, а не «пусто»: пустые каталоги не дают ложного OK.
    for tag in old on off; do
      rc=$(cut -d' ' -f1 "$OUT/$kind-$day-$tag.rc" 2>/dev/null)
      [ "$rc" = 0 ] || echo "$kind $day $tag ПРОГОН УПАЛ rc=${rc:-нет} $(tail -1 "$OUT/$kind-$day-$tag.log" 2>/dev/null | cut -c1-200)"
    done
    for d in "$OUT/$kind-$day-old"/*/; do
      set_name=$(basename "$d")
      [ -f "$d/forms.csv" ] || continue
      for f in rounds.csv forms.csv; do
        o=$(grep -av '^#' "$OUT/$kind-$day-old/$set_name/$f" | md5sum | cut -c1-12)
        n=$(grep -av '^#' "$OUT/$kind-$day-on/$set_name/$f" 2>/dev/null | md5sum | cut -c1-12)
        x=$(grep -av '^#' "$OUT/$kind-$day-off/$set_name/$f" 2>/dev/null | md5sum | cut -c1-12)
        rows=$(grep -avc '^#' "$OUT/$kind-$day-old/$set_name/$f")
        verdict=OK; { [ "$o" = "$n" ] && [ "$o" = "$x" ]; } || verdict=РАЗНЫЕ
        echo "$kind $day $set_name $f строк=$rows $verdict"
      done
    done
    echo "$kind $day время, с: старый $(cut -d' ' -f2 "$OUT/$kind-$day-old.rc") · память $(cut -d' ' -f2 "$OUT/$kind-$day-on.rc") · без памяти $(cut -d' ' -f2 "$OUT/$kind-$day-off.rc") · rc $(cut -d' ' -f1 "$OUT"/$kind-$day-*.rc | tr '\n' ' ') · $(grep -ah 'память кругов' "$OUT/$kind-$day-on.log" | awk '{h+=$5; m+=$9} END {print "из памяти " h ", движком " m}')"
  done
done
echo "каталог: $OUT"
