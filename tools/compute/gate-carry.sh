#!/usr/bin/env bash
# Гейт переноса через полночь (T-22 Р10, 26.09): главный вариант В-104 с `--carry-root root` (как ночная дозапись
# `b5/titrc-u500r`: 3 набора, 3 тейка, `--order-usd 500`, кэш подходов D20) — старый и новый бинарник ПО ОЧЕРЕДИ
# (память), все выходы сравниваются целиком (`diff -r`, шапки включены), у каждого прогона — пик памяти (VmHWM) и
# время. Спецификация `<дом>:<сутки>` (дом — каталог эпохи с `study/root-<сутки>` и `root/`).
#   OLD=bin/alpha-<старый> NEW=bin/alpha-<новый> gate-carry.sh <дом>:<сутки> ...
# MEM_ONLY="<дом>:<сутки>:<SYM>" — только новый бинарник на одной монете под потолком MEM_CAP (умолчание 13G,
# свой scope): замер пика памяти тяжёлых суток, на которых старый не влезает.
set -uo pipefail
A="${ALPHA_BASE:-$HOME/alpha}"
OLD="${OLD:?старый бинарник}"; NEW="${NEW:?новый бинарник}"
OUT="${OUT:-$(mktemp -d)}"
MEM_CAP="${MEM_CAP:-13G}"
mkdir -p "$OUT" || exit 1
RTT="--median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000"
FORM="--signal approach --queue-model prob:3 $RTT --regime-from study/regime --order-usd 500 --carry-root root \
  --h3-mode notional --h3-usd 10000 --entry-form ladder3x2..20w2 --entry-ttl-secs 1800 --band-exit-bps 20 \
  --stop-form pct2 --take-form tr1x1 --take-form tr0.5x0.25 --take-form tk1.75 --deadline-secs 14400 --exit-form none \
  --set t-bid-age-45:age=2700,side=bid --set t-bid-btc1h-q1:age=2700,side=bid,btc1h_max=-21.17 \
  --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55"
fail=0

run() {  # $1 дом, $2 сутки, $3 бинарник (абсолютный), $4 каталог выхода, $5.. доп. (напр. --symbol)
  local home=$1 day=$2 bin=$3 out=$4; shift 4
  local t0 hwm=0 v pid rc
  t0=$(date +%s)
  # shellcheck disable=SC2086
  (cd "$home" && exec nice -n 5 "$bin" lob bounce-grid --root "study/root-$day" --touches-from study/approaches/D20 \
     $FORM --threads 2 "$@" --out-dir "$out" > "$out.log" 2>&1) &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    for p in $(pgrep -P "$pid") "$pid"; do
      v=$(awk '/VmHWM/ {print $2}' "/proc/$p/status" 2>/dev/null); [ -n "$v" ] && [ "$v" -gt "$hwm" ] && hwm=$v
    done
    sleep 1
  done
  wait "$pid"; rc=$?
  echo "$rc $(( $(date +%s) - t0 )) $(( hwm / 1024 ))"
}

for spec in "$@"; do
  home=${spec%%:*}; day=${spec##*:}
  case "$home" in /*) ;; *) home="$A/$home" ;; esac
  if [ ! -d "$home/study/root-$day" ]; then echo "FAIL $spec: нет $home/study/root-$day"; fail=1; continue; fi
  tag="$(basename "$home")-$day"
  read -r rc_o t_o m_o <<< "$(run "$home" "$day" "$A/$OLD" "$OUT/$tag-old")"
  read -r rc_n t_n m_n <<< "$(run "$home" "$day" "$A/$NEW" "$OUT/$tag-new")"
  if [ "$rc_o" != 0 ] || [ "$rc_n" != 0 ]; then
    echo "FAIL $tag: код старого $rc_o, нового $rc_n — $(tail -1 "$OUT/$tag-new.log" | cut -c1-150)"; fail=1; continue
  fi
  n_files=$(find "$OUT/$tag-old" -type f | wc -l)
  n_carry=$(grep -ac "довесок" "$OUT/$tag-new.log")
  if diff -rq "$OUT/$tag-old" "$OUT/$tag-new" > /dev/null; then
    echo "OK   $tag: $n_files файлов побайтно (шапки включены), довесков $n_carry · старый ${t_o} с / ${m_o} МБ · новый ${t_n} с / ${m_n} МБ"
    rm -rf "$OUT/$tag-old" "$OUT/$tag-new"
  else
    echo "DIFF $tag: $(diff -rq "$OUT/$tag-old" "$OUT/$tag-new" | head -3 | tr '\n' ' ')"; fail=1
  fi
done

if [ -n "${MEM_ONLY:-}" ]; then
  IFS=: read -r home day sym <<< "$MEM_ONLY"
  case "$home" in /*) ;; *) home="$A/$home" ;; esac
  SCOPE=(systemd-run --scope --quiet -p MemoryMax="$MEM_CAP" -p MemorySwapMax=0)
  [ "$(id -u)" = 0 ] || SCOPE=(systemd-run --user --scope --quiet -p MemoryMax="$MEM_CAP" -p MemorySwapMax=0)
  t0=$(date +%s)
  # shellcheck disable=SC2086
  (cd "$home" && "${SCOPE[@]}" nice -n 5 "$A/$NEW" lob bounce-grid --root "study/root-$day" \
     --touches-from study/approaches/D20 $FORM --symbol "$sym" --threads 1 --out-dir "$OUT/mem-$sym-$day" \
     > "$OUT/mem-$sym-$day.log" 2>&1) &
  pid=$!; hwm=0
  while kill -0 "$pid" 2>/dev/null; do
    for p in $(pgrep -f "lob bounce-grid --root study/root-$day" ); do
      v=$(awk '/VmHWM/ {print $2}' "/proc/$p/status" 2>/dev/null); [ -n "$v" ] && [ "$v" -gt "$hwm" ] && hwm=$v
    done
    sleep 1
  done
  wait "$pid"; rc=$?
  echo "MEM  $sym $day: код $rc, $(( $(date +%s) - t0 )) с, пик $(( hwm / 1024 )) МБ (потолок $MEM_CAP) · $(grep -a "события:\|довесок" "$OUT/mem-$sym-$day.log" | tr -s ' ' | cut -c1-160 | tr '\n' ' ')"
fi
[ "$fail" = 0 ] && echo "ГЕЙТ ПЕРЕНОСА: всё совпало" || echo "ГЕЙТ ПЕРЕНОСА: ЕСТЬ РАСХОЖДЕНИЯ"
exit "$fail"
