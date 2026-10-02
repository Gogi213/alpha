#!/usr/bin/env bash
# TK-029: гейт «байт в байт» нового бинарника против alpha-e74f200-v3 на VPS и замер ЦП. Запуск от root на 13.140.29.171.
# tk029-gate.sh <мес> <сутки> <бинарник-новый> [отрезок=1]   — старый (bin/alpha-e74f200-v3) и новый идут параллельно
# двумя юнитами (CPUAccounting); дом каждого — свой каталог $G/{old,new} с ссылками на root/study эпохи; отрезок n из
# jall-<мес>-<сутки>.sh (n=1 основной, 2 t9, 3 Г-86). Итог: $G/<мес>-<сутки>-s<n>/{old,new}.{props,body.sha}, RESULT.txt, DONE.
set -uo pipefail
M="${1:?мес}"; D="${2:?сутки}"; NEW="${3:?бинарник}"; N="${4:-1}"
A=/home/deck/alpha; E="$A/epochs/e-$M"; SC="$A/tmp-p07/cells-by-day/jall-$M-$D.sh"
O="$A/tk029/gate/$M-$D-s$N"; rm -rf "$O"; mkdir -p "$O"
awk -v v="$O" 'BEGIN { k = 0 } /^set -e$/ { k++ } { print > (v "/seg" k ".sh") }' "$SC"
OLDB=/opt/alpha-compute/bin/alpha-e74f200-v3
for w in old new; do
  H="$O/$w"; mkdir -p "$H/b5" "$H/bin"
  for x in root study; do ln -s "$E/$x" "$H/$x"; done
  [ -e "$E/regime" ] && ln -s "$E/regime" "$H/regime"
  cp "$A/tmp-p07/cells-by-day/jall-$M-$D.txt" "$H/"
  sed "s#/home/deck/alpha/tmp-p07/cells-by-day/#$H/#g" "$O/seg$N.sh" > "$H/seg.sh"   # логи сетки — в свой дом, не поверх общих
  if [ "$w" = old ]; then cp "$OLDB" "$H/bin/alpha-e74f200-v3"; else cp "$NEW" "$H/bin/alpha-e74f200-v3"; fi
done
for w in old new; do
  H="$O/$w"
  systemd-run --collect --quiet -u "tk029-$M-$D-s$N-$w" -p CPUAccounting=yes -p MemoryAccounting=yes -p WorkingDirectory="$H" \
    -p StandardOutput=file:"$O/$w.out" -p StandardError=file:"$O/$w.err" bash "$O/$w/seg.sh"
done
while systemctl is-active --quiet "tk029-$M-$D-s$N-old" || systemctl is-active --quiet "tk029-$M-$D-s$N-new"; do sleep 5; done
for w in old new; do
  systemctl show "tk029-$M-$D-s$N-$w" -p CPUUsageNSec -p ExecMainStatus > "$O/$w.props" 2>&1
  ( cd "$O/$w" && find b5 -type f ! -name "*.log" | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(grep -v '^#' "$f" | sha256sum | cut -d' ' -f1)" "$f"; done ) > "$O/$w.body.sha"
done
{ cat "$O/old.props" "$O/new.props"; wc -l "$O/old.body.sha" "$O/new.body.sha"; cmp "$O/old.body.sha" "$O/new.body.sha" && echo GATE=OK || echo GATE=DIFF; } > "$O/RESULT.txt"
date -Is > "$O/DONE"
