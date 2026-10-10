#!/usr/bin/env bash
# TK-164 (КТ-3b): гейт всех семей клеток на проб-сутках — diff 0 (cmp всех выходов) нового бинарника против опорного семьи.
# Опись опорных бинарей — docs/findings/gate-families-2026-10-10.md. Запуск на calc ТОЛЬКО через alsched:
#   python3 /data/sched/alsched.py submit --cls measure --name gate-fam --max-runtime 3600 --cores 4 --mem 8 --why "КТ-3b гейт семей" \
#     -- bash /data/tk164/gate-families.sh <новый бинарник> [base,tk115,dl]      (по умолчанию все три семьи)
# Итог: /data/tk164/gate/<семья>.txt (DIFF-строки + "family=… files=N diff=M rc=R"), общий /data/tk164/gate/summary.txt, маркер gate.done.
# Каждая семья — отдельно (непрогнанная семья = нет гейта). Опорный выход считается тем же скриптом на тех же сутках в той же сессии.
set -uo pipefail
NEW=${1:?новый бинарник (путь)}; FAMS=${2:-base,tk115,dl}
G=/data/tk164/gate; mkdir -p "$G"; rm -f "$G/gate.done" "$G/summary.txt"
BINDIR=/opt/alpha-compute/bin
REF_BASE=$BINDIR/alpha-b14flag                  # база; сутки 2026-01-01 + 2026-01-15, 241 клетка (tk148 cells-241)
REF_TK115=/data/tk0115/bin/alpha-tk115f         # md5 40b14da2…, формы tk115, сутки 2026-03-07
REF_DL=$BINDIR/alpha-tk084-dl                   # сборка коммита 38a7da19 (дедлайны 6/8 ч), сутки 2026-03-07 (R2)
CELLS241=/data/tk164/cells-241.txt              # копия /tmp/tk148/cells-241.txt (TK-148)
cmpdirs() {  # $1 семья $2 каталог A $3 каталог B → печатает DIFF, возвращает число расхождений
  local n=0 bad=0 f
  while IFS= read -r f; do n=$((n+1)); cmp -s "$2/$f" "$3/$f" || { bad=$((bad+1)); echo "DIFF $f"; }; done < <(cd "$2" && find . -type f ! -name '.cellstmp*' ! -name '*.log' | sort)
  [ "$(cd "$2" && find . -type f ! -name '.cellstmp*' ! -name '*.log' | wc -l)" = "$(cd "$3" && find . -type f ! -name '.cellstmp*' ! -name '*.log' | wc -l)" ] || { bad=$((bad+1)); echo "COUNT"; }
  echo "files=$n diff=$bad"; return $((bad>0))
}
fam_base() {  # строка 3 jall-jan-<d>.sh (как ряд TK-148), --events wide, b14, ALPHA_SKIP_SAME=1; опорный и новый на той же клетке-пачке
  local H=/data/tk046/jan/home E=/data/tk046/jan/home/alpha/epochs/e-jan C=/data/tk046/jan/home/alpha/tmp-p07/cells-by-day d tag b rc=0
  for d in 2026-01-01 2026-01-15; do for tag in ref new; do
    b=$REF_BASE; [ $tag = new ] && b=$NEW
    local S=/data/tk164/w-base-$tag-$d x bb line
    rm -rf "$S"; mkdir -p "$S/b5" "$S/bin"
    for x in $E/* $E/.[!.]*; do bb=$(basename "$x"); case $bb in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$x" "$S/$bb"; done
    ln -s "$(readlink -f "$b")" "$S/bin/alpha-tk044k1-new"; cp "$CELLS241" "$S/cells.txt"
    line=$(sed -n 3p "$C/jall-jan-$d.sh" | sed "s# bounce-grid # bounce-grid --events wide #; s# --cells [^ ]*# --cells $S/cells.txt#; s# --extra-runs [^ ]*##")
    ( export HOME=$H ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 ALPHA_SKIP_SAME=1; cd "$S" && bash -c "$line" > "$S/grid.log" 2>&1 ) || echo "RUNFAIL base $tag $d"
  done; cmpdirs base /data/tk164/w-base-ref-$d/b5 /data/tk164/w-base-new-$d/b5 || rc=1; done; return $rc
}
fam_tk115() {  # tk115-delta-day.sh (BIN=…): signals/e106/walls сутки 2026-03-07, опорный alpha-tk115f против нового
  local d=2026-03-07 rc=0 tag b
  for tag in ref new; do b=$REF_TK115; [ $tag = new ] && b=$NEW
    rm -rf /data/tk0115/delta/g164-$tag; BIN=$b TP=4 bash /data/tk0115/tk115-delta-day.sh "g164-$tag" mar "$d" || echo "RUNFAIL tk115 $tag"
  done
  for sub in signals e106 walls; do cmpdirs tk115 /data/tk0115/delta/g164-ref/$sub /data/tk0115/delta/g164-new/$sub || rc=1; done; return $rc
}
fam_dl() {  # /data/tk084/gate.sh: R2-сутки 2026-03-07, сверка с боевым счётом /data/tk065/w-<сутки> (счёт опорной сборки 38a7da19 / R2)
  bash /data/tk084/gate.sh "$(basename "$NEW")" 2026-03-07 >/dev/null 2>&1; grep -v '^DIFF ./.cellstmp' /data/tk084/gate.txt | grep -E '^DIFF|^gate'
  grep -q 'diff 0\b' /data/tk084/gate.txt || [ "$(grep -c '^DIFF' /data/tk084/gate.txt)" = 0 ]
}
for f in ${FAMS//,/ }; do
  out=$G/$f.txt; "fam_$f" > "$out" 2>&1; rc=$?
  echo "family=$f rc=$rc $(tail -1 "$out")" | tee -a "$G/summary.txt"
done
touch "$G/gate.done"
