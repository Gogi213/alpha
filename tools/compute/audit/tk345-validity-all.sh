#!/usr/bin/env bash
# TK-345 проход валидности 1–14.02: 14 суток × SH шардов задач на NX ядрах (xargs -P), затем повтор шарда 0 суток 01 для детерминизма.
#   tk345-validity-all.sh <выход-каталог>        env: NX (выданные ядра), SH (по умолчанию 6)
set -uo pipefail
O=$1; SH=${SH:-6}; NX=${NX:-15}; export SH
P=/data/tk0345/tk345-validity-pass.sh
mkdir -p "$O"
for d in 01 02 03 04 05 06 07 08 09 10 11 12 13 14; do for ((s = 0; s < SH; s++)); do echo "2026-02-$d $s"; done; done \
  | xargs -P "$NX" -L1 bash -c 'bash '"$P"' $0 $1 '"$O"
echo "pass rc=$? $(date +%s)" >> "$O/units.txt"
# детерминизм: тот же шард, другой каталог, md5 всех rounds/forms
bash "$P" 2026-02-01 0 "$O-B"
bad=0; n=0
for u in "$O"/2026-02-01-s0-g*-F*; do
  b="$O-B/$(basename "$u")"
  for f in "$u"/out/*/rounds.csv "$u"/out/*/forms.csv; do
    r=${f#"$u"/}; n=$((n+1))
    [ "$(md5sum < "$f")" = "$(md5sum < "$b/$r" 2>/dev/null)" ] || { bad=$((bad+1)); echo "РАЗЛИЧИЕ $u/$r" >> "$O/determinism.txt"; }
  done
done
echo "детерминизм: файлов $n, различий $bad" | tee -a "$O/determinism.txt"
touch "$O/done"
