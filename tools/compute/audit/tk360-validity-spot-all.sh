#!/usr/bin/env bash
# TK-360 проход валидности СПОТА 1–14 числа месяца YM (по умолчанию 2026-02; CEO 23.10: июль → YM=2026-07): планировщик по памяти (tk360-memq.py, задача = сутки × символ × F1 F2 F3), затем повтор трёх
# символов суток 01 (детерминизм: md5 rounds/forms) и проверка инвариантов (tk360-validity-spot-check.py).
#   tk360-validity-spot-all.sh <выход-каталог>        env: YM (2026-02) NX (15) BUDGET (48; юнит --mem 56; KILL 4 — в memq.cfg)
set -uo pipefail
O=$1; D=/data/tk0360; export NX=${NX:-15} BUDGET=${BUDGET:-48} YM=${YM:-2026-02}
mkdir -p "$O"
python3 $D/tk360-memq.py "$O" $(for i in 01 02 03 04 05 06 07 08 09 10 11 12 13 14; do printf "%s-%s," "$YM" "$i"; done | sed "s/,\$//")
rc=$?
echo "memq rc=$rc $(date +%s)" >> "$O/units.txt"
syms=$(ls -d "$O"/$YM-01-*-F3 2>/dev/null | sed "s#.*/$YM-01-\(.*\)-F3#\1#" | sort | sed -n '1p;20p;40p')
bad=0; n=0
for s in $syms; do
  TAG=$s GT=1 bash $D/tk360-validity-spot.sh $YM-01 "$O-B" "$s"
  for u in "$O"/$YM-01-$s-F*; do
    b="$O-B/$(basename "$u")"
    for f in "$u"/out/*/rounds.csv "$u"/out/*/forms.csv; do
      r=${f#"$u"/}; n=$((n+1))
      [ "$(md5sum < "$f")" = "$(md5sum < "$b/$r" 2>/dev/null)" ] || { bad=$((bad+1)); echo "РАЗЛИЧИЕ $u/$r" >> "$O/determinism.txt"; }
    done
  done
done
echo "детерминизм ($syms): файлов $n, различий $bad" | tee -a "$O/determinism.txt"
NP=${NX} python3 $D/tk360-validity-spot-check.py "$O" "$O/check"
echo "check rc=$?" >> "$O/units.txt"
touch "$O/done"
exit $rc
