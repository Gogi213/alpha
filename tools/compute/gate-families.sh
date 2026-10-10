#!/usr/bin/env bash
# TK-164 (КТ-3b): гейт семей клеток на проб-сутках — diff 0 (cmp всех выходов) нового бинарника против опорного семьи.
# Опись опорных бинарей — docs/findings/gate-families-2026-10-10.md. Запуск на calc ТОЛЬКО через alsched:
#   python3 /data/sched/alsched.py submit --cls measure --name gate-fam --max-runtime 3600 --cores 4 --mem 8 --why "КТ-3b гейт семей" \
#     -- bash /data/tk164/gate-families.sh <новый бинарник, путь> [base,r2,dl,tk115]
#   проверка самого скрипта без нового бинаря (опорный против себя): … -- bash /data/tk164/gate-families.sh --selftest [семьи]
# Итог: /data/tk164/gate/<семья>.txt, summary.txt (строка на семью: rc), маркер gate.done. Зелёный = rc 0 И files>0 в каждой
# подпапке выхода И rc каждого прогона 0 (RUNFAIL/files=0/пустой выход = красный, не зелёный).
# Каждая семья — отдельно: опорный и новый считают те же сутки в той же сессии. Новый бинарь запускается с тем же env, что опорный
# (обёртка по образцу опорной: b14flag, b15pyr4pgoflag); у семей с сырым опорным (tk084-dl, tk115f) — без env.
set -uo pipefail
if [ "${1:-}" = --selftest ]; then SELF=1; NEW=""; FAMS=${2:-dl}; else SELF=0; NEW=${1:?новый бинарник (путь)}; FAMS=${2:-base,r2,dl,tk115}; fi
T=/data/tk164; G=$T/gate; BIN=/opt/alpha-compute/bin; mkdir -p "$G" "$T/wrap"; rm -f "$G/gate.done" "$G/summary.txt"
# семья: опорный (в $BIN или путь) | месяц | сутки | скрипт суток | клетки (для счёта got/exp, пусто — только files>0)
fam_conf() {
  case $1 in
    base)  REF=$BIN/alpha-b14flag;            MON=jan; DAY=2026-01-01,2026-01-15; SCR=jall; CELLS=$T/cells-241.txt;;
    r2)    REF=$BIN/alpha-b15pyr4pgoflag;     MON=mar; DAY=2026-03-07; SCR=/data/tk065/days/r2-2026-03-07.sh;  CELLS=/data/tk065/days/r2-2026-03-07.cells;;
    dl)    REF=$BIN/alpha-tk084-dl;           MON=mar; DAY=2026-03-07; SCR=/data/tk084/days/p12-2026-03-07.sh; CELLS=/data/tk084/days/p12-2026-03-07.cells;;
    tk115) REF=/data/tk0115/bin/alpha-tk115f; MON=mar; DAY=2026-03-07; SCR=/data/tk065/days/r3a-2026-03-07.sh; CELLS=/data/tk065/days/r3a-2026-03-07.cells;;
  esac
}
mkwrap() {  # $1 опорный $2 новый → путь, который кладётся в bin/alpha-tk044k1-new (обёртка с env опорной, если опорная — скрипт)
  local w=$T/wrap/$(basename "$1")-$(basename "$2")
  if [ "$(head -c2 "$1")" = '#!' ]; then sed "s#^exec .*#exec $(readlink -f "$2") \"\$@\"#" "$1" > "$w"; chmod +x "$w"; echo "$w"; else readlink -f "$2"; fi
}
run_day() {  # $1 метка $2 бинарь/обёртка $3 мес $4 сутки $5 скрипт суток $6 клетки → каталог $T/w-<метка>; rc 0 только при rc=0 и got==exp
  local S=$T/w-$1 H=/data/tk046/$3/home x b rc exp got
  local E=$H/alpha/epochs/e-$3
  rm -rf "$S"; mkdir -p "$S/b5" "$S/bin"; ln -s "$2" "$S/bin/alpha-tk044k1-new"
  for x in $E/* $E/.[!.]*; do b=$(basename "$x"); case $b in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$x" "$S/$b"; done
  cp "$6" "$S/cells.txt"
  if [ "$5" = jall ]; then  # строка 3 jall-скрипта суток (как ряд TK-148), --events wide, свои клетки
    sed -n 3p "$H/alpha/tmp-p07/cells-by-day/jall-jan-$4.sh" | sed "s# bounce-grid # bounce-grid --events wide #; s# --cells [^ ]*# --cells $S/cells.txt#; s# --extra-runs [^ ]*##; s#b5/.cellstmp-$4.log#$S/grid.log#" > "$S/day.sh"
  else  # готовый скрипт суток: клетки/каталоги внутри него — как в боевом счёте; пишет в b5/ рабочего каталога $S
    cp "$5" "$S/day.sh"; sed -i "s#--cells [^ ]*#--cells $S/cells.txt#; s#> b5/\.cellstmp-\([^ ]*\)\.log#> b5/.cellstmp-\1.log#; s#^cp b5/\.cellstmp[^ ]*\.log /data/[^ ]*#:#" "$S/day.sh"
  fi
  ( export HOME=$H ALPHA_SKIP_SAME=1 ALPHA_APPROACH_BIN_DIR=${ALPHA_APPROACH_BIN_DIR:-/data/tk048/abin-t46}; cd "$S" && /usr/bin/time -f "%e %U %S %M" -o "$S/t.txt" bash "$S/day.sh" > "$S/run.out" 2> "$S/run.err" ); rc=$?
  exp=$(grep -c . "$6"); got=$(find "$S/b5" -name 'forms.csv' 2>/dev/null | xargs -r cat | awk -F, '{print $3}' | sort -u | grep -c .)
  local nr; nr=$(find "$S/b5" -name 'rounds*.csv' 2>/dev/null | wc -l)
  echo "run $1 rc=$rc cells_got=$got cells_exp=$exp rounds_files=$nr"
  [ "$rc" = 0 ] && [ "$nr" -gt 0 ] && { [ "$got" = "$exp" ] || [ "$got" = 0 ]; } || { echo "RUNFAIL $1 (rc=$rc rounds=$nr got=$got/$exp)"; return 1; }
}
cmpdirs() {  # $1 A $2 B: cmp всех файлов (без логов), files>0, одинаковый список; красный при files=0
  local n=0 bad=0 f la lb
  la=$(cd "$1" && find . -type f ! -name '.cellstmp*' ! -name '*.log' | sort); lb=$(cd "$2" && find . -type f ! -name '.cellstmp*' ! -name '*.log' | sort)
  [ "$la" = "$lb" ] || { bad=$((bad+1)); echo "LISTDIFF"; }
  for f in $la; do n=$((n+1)); cmp -s "$1/$f" "$2/$f" || { bad=$((bad+1)); echo "DIFF $f"; }; done
  echo "files=$n diff=$bad"; [ "$n" -gt 0 ] && [ "$bad" = 0 ]
}
fam_one() {  # $1 семья
  local f=$1 d rc=0 nb w
  fam_conf "$f"; nb=$NEW; [ $SELF = 1 ] && nb=$REF
  w=$(mkwrap "$REF" "$nb")
  for d in ${DAY//,/ }; do
    run_day "$f-ref-$d" "$(mkwrap "$REF" "$REF")" "$MON" "$d" "$SCR" "$CELLS" || rc=1
    run_day "$f-new-$d" "$w" "$MON" "$d" "$SCR" "$CELLS" || rc=1
    cmpdirs "$T/w-$f-ref-$d/b5" "$T/w-$f-new-$d/b5" || rc=1
  done
  echo "family=$f rc=$rc"; return $rc
}
for f in ${FAMS//,/ }; do
  out=$G/$f.txt; fam_one "$f" > "$out" 2>&1; rc=$?
  echo "family=$f rc=$rc $(grep -E '^files=' "$out" | tail -1)" | tee -a "$G/summary.txt"
done
touch "$G/gate.done"
