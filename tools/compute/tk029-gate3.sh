#!/usr/bin/env bash
# TK-029 шаг (1): гейт «слияние проходов» на VPS. tk029-gate3.sh <мес> <сутки> <бинарник-новый>
# old: e74f200-v3, три отдельных прогона (отрезки 1,2,3 из jall-<мес>-<сутки>.sh) подряд; new: один прогон отрезка 1 с
# --extra-runs (строки отрезков 2 и 3, свои --out-dir), затем те же awk-вырезки. Сравнение: sha файлов b5 без '#'-строк.
# Итог: $A/tk029/gate/<мес>-<сутки>-merge/{RESULT.txt,DONE,old.time,new.time} (ЦП и стена — /usr/bin/time -v).
set -uo pipefail
M="${1:?мес}"; D="${2:?сутки}"; NEW="${3:?бинарник}"
A=/home/deck/alpha; E="$A/epochs/e-$M"; SC="$A/tmp-p07/cells-by-day/jall-$M-$D.sh"
O="$A/tk029/gate/$M-$D-merge"; rm -rf "$O"; mkdir -p "$O"
awk -v v="$O" 'BEGIN { k = 0 } /^set -e$/ { k++ } { print > (v "/seg" k ".sh") }' "$SC"
OLDB=/opt/alpha-compute/bin/alpha-e74f200-v3
for w in old new; do
  H="$O/$w"; mkdir -p "$H/b5" "$H/bin"
  for x in root study; do ln -s "$E/$x" "$H/$x"; done
  [ -e "$E/regime" ] && ln -s "$E/regime" "$H/regime"
  cp "$A/tmp-p07/cells-by-day/"*"$M-$D.txt" "$H/" 2>/dev/null
  if [ "$w" = old ]; then cp "$OLDB" "$H/bin/alpha-e74f200-v3"; else cp "$NEW" "$H/bin/alpha-e74f200-v3"; fi
done
R="s#/home/deck/alpha/tmp-p07/cells-by-day/#$O/old/#g"
# old: исходные три отрезка подряд
for n in 1 2 3; do sed "$R" "$O/seg$n.sh" | grep -v '^set -e$'; done > "$O/old/all.body"
{ echo 'set -e'; cat "$O/old/all.body"; } > "$O/old/seg.sh"
# new: отрезок 1 + --extra-runs из команд отрезков 2 и 3
RN="s#/home/deck/alpha/tmp-p07/cells-by-day/#$O/new/#g"
cmd() { sed -n '3p' "$O/seg$1.sh" | sed -e 's#^bin/alpha-e74f200-v3 lob bounce-grid ##' -e 's# > b5/[^ ]* 2>&1$##' -e "s#--out-dir b5/[^ ]*#--out-dir b5/.m$1tmp#" -e "$RN"; }
{ cmd 2; cmd 3; } > "$O/new/extra.txt"
{
  echo 'set -e'
  sed "$RN" "$O/seg1.sh" | grep -v '^set -e$' | awk -v x="$O/new/extra.txt" 'NR==2 { sub(/ > b5\//, " --extra-runs " x " > b5/") } { print }'
  sed "$RN" "$O/seg2.sh" | grep -v '^set -e$' | sed -n '3,$p' | grep -v '^cp ' | sed "s#\.t9tmp-touch-$D#.m2tmp#g"
  sed "$RN" "$O/seg3.sh" | grep -v '^set -e$' | sed -n '3,$p' | grep -v '^cp ' | sed "s#\.cellstmp-$D#.m3tmp#g"
} > "$O/new/seg.sh"
for w in old new; do
  H="$O/$w"
  systemd-run --collect --quiet -u "tk029-$M-$D-merge-$w" -p WorkingDirectory="$H" \
    -p StandardOutput=file:"$O/$w.out" -p StandardError=file:"$O/$w.err" /usr/bin/time -v -o "$O/$w.time" bash "$H/seg.sh"
done
while systemctl is-active --quiet "tk029-$M-$D-merge-old" || systemctl is-active --quiet "tk029-$M-$D-merge-new"; do sleep 5; done
for w in old new; do
  ( cd "$O/$w" && find b5 -type f ! -name "*.log" | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(grep -v '^#' "$f" | sha256sum | cut -d' ' -f1)" "$f"; done ) > "$O/$w.body.sha"
done
{ for w in old new; do echo "== $w"; grep -E "User time|System time|Elapsed|Maximum resident|Exit status" "$O/$w.time"; done
  wc -l "$O/old.body.sha" "$O/new.body.sha"; cmp "$O/old.body.sha" "$O/new.body.sha" && echo GATE=OK || echo GATE=DIFF; } > "$O/RESULT.txt"
date -Is > "$O/DONE"
