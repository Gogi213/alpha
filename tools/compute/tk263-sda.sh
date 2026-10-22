#!/usr/bin/env bash
# TK-263 п.2 (В-21): месяц на оба диска. Копия половины суток среза (root-<d> + wt журнала) на sda (/srv/tk0263), остальные сутки читаются с sdb как были.
#   /data/benchrun.sh wave bash tk263-sda.sh      (env D=<каталог ответа>, BS=<bs-launch.sh>, STEPS="метка:NX:режим …", режим sda|sdb)
# Делёж: сутки среза в порядке day-order (тяжёлые первыми), на sda идут позиции NR%4 in {0,1} (схема АББА — байт поровну).
# Подмена — только копия SCRIPTS (day-cells.sh с ALT_ROOT) и JRN-каталог, боевое дерево и симлинки не правятся; исходники на sdb не трогаются.
# Умолчание: B12 (NX12, сутки на обоих дисках), B16 (NX16), A16 (NX16, всё с sdb — повтор для шума).
set -u
D=${D:-/data/tk0263/w2}; W1=${W1:-/data/tk0263/w1}; BS=${BS:-/data/tk0263/bs-launch.sh}; A=/srv/tk0263
E=/data/tk046/mar/home/alpha/epochs/e-mar/study; JS=/data/p12r3a/jrn
mkdir -p "$D" "$A/root" "$A/jrn" "$A/scripts"
[ -s "$D/days.txt" ] || { cp -r "$W1/plan" "$D/plan"; cp "$W1/days.txt" "$W1/ref.md5" "$D/"; }
# --- копия (один раз) ---
if [ ! -e "$A/copy.done" ]; then
  t0=$(date +%s)
  cp /data/tk0231/day-cells.sh /data/tk0231/day-order.sh /data/tk0231/merge-packs.py "$A/scripts/"
  # root-<d> на sda, если есть копия: ALT_ROOT/root-<d>
  sed -i 's|ln -s "$(readlink -f "$E/study/root-$d")" "$W/study/root-$d"|ln -s "$( [ -d "${ALT_ROOT:-/nonexistent}/root-$d" ] \&\& echo "$ALT_ROOT/root-$d" \|\| readlink -f "$E/study/root-$d")" "$W/study/root-$d"|' "$A/scripts/day-cells.sh"
  [ "$(grep -c 'ALT_ROOT' "$A/scripts/day-cells.sh")" -ge 1 ] || { echo "патч day-cells.sh не лёг" > "$A/copy.fail"; exit 3; }
  : > "$A/sda-days.txt"
  awk '{print NR, $2}' "$D/days.txt" | while read -r n d; do
    mkdir -p "$A/jrn/$d"; touch "$A/jrn/$d/done"
    if [ $((n % 4)) -le 1 ]; then
      echo "$d" >> "$A/sda-days.txt"
      cp -a "$(readlink -f "$E/root-$d")" "$A/root/root-$d"
      cp -a "$JS/$d/wt" "$A/jrn/$d/wt"
    else
      ln -sfn "$JS/$d/wt" "$A/jrn/$d/wt"
    fi
  done
  # проверка копии: число файлов и байт у каждой копии = исходнику
  while read -r d; do
    for p in "$(readlink -f "$E/root-$d")|$A/root/root-$d" "$JS/$d/wt|$A/jrn/$d/wt"; do
      s=${p%|*}; c=${p#*|}
      a=$(cd "$s" && find . -type f -printf '%s\n' | awk '{n++;b+=$1}END{print n+0, b+0}')
      b=$(cd "$c" && find . -type f -printf '%s\n' | awk '{n++;b+=$1}END{print n+0, b+0}')
      echo "$d $s $a $b $([ "$a" = "$b" ] && echo ok || echo РАЗНИЦА)"
    done
  done < "$A/sda-days.txt" > "$A/copy-check.txt"
  echo "copy_s $(( $(date +%s) - t0 ))" > "$A/copy.done"
  du -sb "$A/root" "$A/jrn"/*/wt 2>/dev/null | awk '{s+=$1}END{print "copy_bytes", s}' >> "$A/copy.done"
fi
# --- проходы ---
for st in ${STEPS:-B12:12:sda B16:16:sda A16:16:sdb}; do
  lab=${st%%:*}; r=${st#*:}; nx=${r%%:*}; mode=${r#*:}
  if [ "$mode" = sda ]; then
    SCRIPTS=$A/scripts ALT_ROOT=$A/root JRN=$A/jrn NX=$nx bash "$BS" pass "$D" "$lab" > "$D.$lab.out" 2>&1
  else
    NX=$nx bash "$BS" pass "$D" "$lab" > "$D.$lab.out" 2>&1
  fi
done
touch "$D.done"
