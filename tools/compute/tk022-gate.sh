#!/usr/bin/env bash
# TK-022 ворота счёта: клетки p07m-main/p07b-base/p07a-h2-fr1 на сутках, где их считала дека, — на VPS побайтно (sha256 b5/<клетка>/<сутки>/**).
# Запуск с рабочей машины: tk022-gate.sh [мес ...]  (по умолчанию jan mar apr). Сверяются только сутки с vps-status/<сутки>.rc на VPS.
set -u
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15 -o BatchMode=yes)
DECK=deck@192.168.1.49
VPS=root@13.140.29.171
MONTHS=("$@"); [ ${#MONTHS[@]} -gt 0 ] || MONTHS=(jan mar apr)
ok=0; bad=0; skip=0
for m in "${MONTHS[@]}"; do
  done_days=$(ssh "${KEY[@]}" $VPS "ls /home/deck/alpha/epochs/e-$m/vps-status/ 2>/dev/null | sed -n 's/\.rc$//p'")
  for d in $done_days; do
    sums() { ssh "${KEY[@]}" "$1" "cd $2/epochs/e-$m/b5 2>/dev/null && for c in p07m-main p07b-base p07a-h2-fr1; do [ -d \$c/$d ] && find \$c/$d -type f | sort | xargs sha256sum; done"; }
    a=$(sums $DECK /home/deck/alpha); b=$(sums $VPS /home/deck/alpha)
    if [ -z "$a" ]; then skip=$((skip+1)); echo "$m $d: на деке нет — пропуск"; continue; fi
    if [ "$a" == "$b" ]; then ok=$((ok+1)); echo "$m $d: ok ($(echo "$a" | wc -l) файлов)"; else bad=$((bad+1)); echo "$m $d: РАСХОЖДЕНИЕ"; diff <(echo "$a") <(echo "$b") | head -5; fi
  done
done
echo "ворота: ok $ok, расхождений $bad, без образца на деке $skip"
[ $bad -eq 0 ]
