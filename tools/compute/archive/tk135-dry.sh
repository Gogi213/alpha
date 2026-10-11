#!/bin/bash
# TK-135/С-64: сухой гейт слияния .sh: старые копии (из git, $OLD) vs p12-psim.sh/p12-expo.sh <набор>. python3 и cp — заглушки (пишут командную строку в трассу), O — песочница;
# сравниваются трасса команд, дерево песочницы (имена/ссылки), fail.txt; каждый набор в двух режимах: заглушки ок и заглушки busy-replay/portfolio-sim = rc 1.
# Запуск: tk135-dry.sh <рабочий каталог> <старый коммит> (из корня репо; читает только /data/tk065/days). Отчёт: <каталог>/dry-report.tsv.
W=$(realpath -m "$1"); OLD=${2:-aac4bffd^}; HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
rm -rf "$W"; mkdir -p "$W/stub" "$W/old"
cat > "$W/stub/python3" <<'STUB'
#!/bin/bash
echo "python3 $* | EXPO_DROP=${EXPO_DROP:-} EXPO_OUT=${EXPO_OUT:-}" | sed "s#$SB#/data#g" >> "$TRACE"
case "$1" in
  *p12-r[23]-expo.py) mkdir -p vn-b/2026-01 vn-b/2026-02 vtn-b/2026-01; exit 0 ;;
  *busy-replay.py|*portfolio-sim.py) [ -n "${STUBFAIL:-}" ] && exit 1 ;;
esac
exit 0
STUB
cat > "$W/stub/cp" <<'STUB'
#!/bin/bash
echo "cp $*" | sed "s#$SB#/data#g" >> "$TRACE"
STUB
chmod +x "$W/stub/python3" "$W/stub/cp"
for f in p12-r2-psim p12-r2a-psim p12-r3a-psim p12-r2-expo p12-r2a-expo p12-r3a-expo; do   # OLDDIR — готовые старые копии (на calc нет git)
  if [ -n "${OLDDIR:-}" ]; then cp "$OLDDIR/$f.sh" "$W/old/$f.sh"; else git -C "$REPO" show "$OLD:tools/compute/$f.sh" > "$W/old/$f.sh" || exit 3; fi
done
drive() {  # drive <имя прогона> <скрипт> <набор|""> <STUBFAIL> -> $W/<имя>.{trace,tree,fail}
  n=$1; scr=$2; set_=$3; export SB=$W/sb-$n TRACE=$W/$n.trace STUBFAIL=$4
  rm -rf "$SB"; mkdir -p "$SB"; : > "$TRACE"
  sed -e "s#O=/data/p12#O=$SB/p12#" "$scr" > "$W/$n.sh"
  mkdir -p "$SB"/p12r2 "$SB"/p12r2a "$SB"/p12r3a
  ( PATH="$W/stub:$PATH" bash "$W/$n.sh" $set_ >/dev/null 2>&1; echo "rc=$?" >> "$TRACE" )
  ( cd "$SB" && find . -printf '%p %y %l\n' | sort > "$W/$n.tree"; cat p12r*/fail.txt > "$W/$n.fail" 2>/dev/null )
}
echo -e "набор\tскрипт\tзаглушки\tтрасса строк\tдерево строк\tfail строк\tравно" > "$W/dry-report.tsv"
for sf in "" 1; do
  for pair in "psim r2 p12-r2-psim" "psim r2a p12-r2a-psim" "psim r3a p12-r3a-psim" "expo r2 p12-r2-expo" "expo r2a p12-r2a-expo" "expo r3a p12-r3a-expo"; do
    set -- $pair; k=$1; s=$2; o=$3; m=${sf:+fail}; m=${m:-ok}
    drive "o-$o-$m" "$W/old/$o.sh" "" "$sf"
    drive "n-$o-$m" "$HERE/p12-$k.sh" "$s" "$sf"
    # песочница старых скриптов называется иначе (sb-o-… vs sb-n-…) — в дереве нормализуем
    sed -i "s#sb-o-$o-$m#sb#g" "$W/o-$o-$m".{trace,tree,fail}; sed -i "s#sb-n-$o-$m#sb#g" "$W/n-$o-$m".{trace,tree,fail}
    # старый p12-r3-expo.py слит в p12-r2-expo.py --g95 (С-64, дифф — только флаг): в трассе старого приводим к новой форме
    sed -i -E 's#p12-r3-expo\.py (\S+) \|#p12-r2-expo.py \1 --g95 |#' "$W/o-$o-$m.trace"
    ok=да; for e in trace tree fail; do cmp -s "$W/o-$o-$m.$e" "$W/n-$o-$m.$e" || ok=НЕТ; done
    echo -e "$s\t$k\t$m\t$(wc -l < $W/n-$o-$m.trace)\t$(wc -l < $W/n-$o-$m.tree)\t$(wc -l < $W/n-$o-$m.fail)\t$ok" >> "$W/dry-report.tsv"
  done
done
cat "$W/dry-report.tsv"
