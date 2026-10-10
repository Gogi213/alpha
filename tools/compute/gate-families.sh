#!/usr/bin/env bash
# TK-164 (КТ-3b): гейт семей клеток на проб-сутках — diff 0 (cmp всех выходов) нового бинарника против опорного семьи.
# Опись опорных бинарей — docs/findings/gate-families-2026-10-10.md. Запуск на calc ТОЛЬКО через alsched:
#   python3 /data/sched/alsched.py submit --cls measure --name gate-fam --max-runtime 3600 --cores 4 --mem 8 --why "КТ-3b гейт семей" \
#     -- bash /data/tk164/gate-families.sh <новый бинарник, путь> [base,r2,dl,tk115,tk115j,tk115w]
#   проверка самого скрипта без нового бинаря (опорный против себя, все семьи): … -- bash /data/tk164/gate-families.sh --selftest [семьи]
# Семьи (опорный = тем, кем посчитаны числа, Летопись): base b14flag | r2 b15pyr4pgoflag | dl tk084-dl | tk115 (r3a) b26tk115r2 |
# tk115j (signals/e106/walls, tk115-delta-day) tk115f | tk115w (chase<W>, tape/cxl, пороги v5 марта, --tape-log 30) tk115f.
# Семьи идут параллельно (каждая — своё дерево /data/tk164/*), summary.txt — строка на семью.
# Итог: /data/tk164/gate/<семья>.txt, summary.txt (строка на семью: rc), маркер gate.done. Зелёный = rc 0 И files>0 в каждой
# подпапке выхода И rc каждого прогона 0 (RUNFAIL/files=0/пустой выход = красный, не зелёный).
# Каждая семья — отдельно: опорный и новый считают те же сутки в той же сессии. Новый бинарь запускается с тем же env, что опорный
# (обёртка по образцу опорной: b14flag, b15pyr4pgoflag); у семей с сырым опорным (tk084-dl, tk115f) — без env.
set -uo pipefail
ALLF=base,r2,dl,tk115,tk115j,tk115w
if [ "${1:-}" = --selftest ]; then SELF=1; NEW=""; FAMS=${2:-$ALLF}; else SELF=0; NEW=${1:?новый бинарник (путь)}; FAMS=${2:-$ALLF}; fi
T=/data/tk164; G=$T/gate; BIN=/opt/alpha-compute/bin; mkdir -p "$G" "$T/wrap"; rm -f "$G/gate.done" "$G/summary.txt"
# семья: опорный (в $BIN или путь) | месяц | сутки | скрипт суток | клетки (для счёта got/exp, пусто — только files>0)
fam_conf() {
  case $1 in
    base)  REF=$BIN/alpha-b14flag;            MON=jan; DAY=2026-01-01,2026-01-15; SCR=jall; CELLS=$T/cells-241.txt
           [ $SELF = 1 ] && DAY=2026-01-01;;  # самопроверка: путь jall один раз, не 2×2 прогона по 241 клетке
    r2)    REF=$BIN/alpha-b15pyr4pgoflag;     MON=mar; DAY=2026-03-07; SCR=/data/tk065/days/r2-2026-03-07.sh;  CELLS=/data/tk065/days/r2-2026-03-07.cells;;
    dl)    REF=$BIN/alpha-tk084-dl;           MON=mar; DAY=2026-03-07; SCR=/data/tk084/days/p12-2026-03-07.sh; CELLS=/data/tk084/days/p12-2026-03-07.cells;;
    tk115) REF=$BIN/alpha-b26tk115r2;         MON=mar; DAY=2026-03-07; SCR=/data/tk065/days/r3a-2026-03-07.sh; CELLS=/data/tk065/days/r3a-2026-03-07.cells;;
    tk115j|tk115w) REF=/data/tk0115/bin/alpha-tk115f; MON=mar; DAY=2026-03-07;;
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
  exp=$(grep -c . "$6"); got=$(find "$S/b5" -name 'forms.csv' 2>/dev/null | xargs -r cat | awk -F, -v d="$4" '$2==d{print $3}' | sort -u | grep -c .)
  local nr; nr=$(find "$S/b5" -name 'rounds*.csv' 2>/dev/null | wc -l)
  echo "run $1 rc=$rc cells_got=$got cells_exp=$exp rounds_files=$nr"
  [ "$rc" = 0 ] && [ "$nr" -gt 0 ] && { [ "$got" = "$exp" ] || { [ "$5" = jall ] && [ "$got" = 0 ]; }; } || { echo "RUNFAIL $1 (rc=$rc rounds=$nr got=$got/$exp)"; return 1; }
}
cmpdirs() {  # $1 A $2 B: cmp всех файлов (без логов), files>0, одинаковый список; красный при files=0
  local n=0 bad=0 f la lb
  la=$(cd "$1" && find . -type f ! -name '.cellstmp*' ! -name '*.log' | sort); lb=$(cd "$2" && find . -type f ! -name '.cellstmp*' ! -name '*.log' | sort)
  [ "$la" = "$lb" ] || { bad=$((bad+1)); echo "LISTDIFF"; }
  for f in $la; do n=$((n+1)); cmp -s "$1/$f" "$2/$f" || { bad=$((bad+1)); echo "DIFF $f"; }; done
  echo "files=$n diff=$bad"; [ "$n" -gt 0 ] && [ "$bad" = 0 ]
}
# пороги v5 марта (docs/findings/p12-r2b-thresholds-2026-10-10.json): e112 tape30q, e116 cxl30q, e133 chase W=12080 мс ×0,5/1/2
WFORMS="chase86400000 chase6040 chase12080 chase24160 tape30q0.014653226867454029 tape30q0.21387408589803297 cxl30q1.5778084895559001 cxl30q15.839990627236537"
tk115j_run() {  # $1 метка ref|new $2 бинарь: tk115-delta-day (signals/e106/walls), красный при fail.txt/нет done/пустых подпапках
  local O=/data/tk0115/delta/g164-$1 rc=0 s
  rm -rf "$O"; BIN=$2 TP=4 GT=2 bash /data/tk0115/tk115-delta-day.sh "g164-$1" mar 2026-03-07 > "$T/tk115j-$1.out" 2>&1 || rc=1
  [ -e "$O/done" ] && [ ! -s "$O/fail.txt" ] || { echo "RUNFAIL tk115j $1 (done/fail.txt: $(cat "$O/fail.txt" 2>/dev/null | head -3))"; return 1; }
  for s in signals e106 walls; do [ -n "$(find "$O/$s" -type f 2>/dev/null | head -1)" ] || { echo "RUNFAIL tk115j $1: пусто $s"; rc=1; }; done
  return $rc
}
tk115w_run() {  # $1 метка $2 бинарь: сетка B1 суток (логика tk115-w-day.sh, КОПИЕЙ — боевой /data/tk0115/delta/w не трогаем) всеми формами WFORMS
  local MON=mar d=2026-03-07 BN=$2 H=/data/tk046/mar/home W=$T/xw-$1 x b rc fl=() f got
  local E=$H/alpha/epochs/e-mar V=/data/tk044/final3/verdict.csv
  rm -rf "$W"; mkdir -p "$W/b5" "$W/bin"; ln -s "$BN" "$W/bin/alpha-tk044k1-new"
  for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
  awk -F, -v m="$MON" -v d="$d" 'NR>1 && $1==m && $2==d{print $3}' /data/tk0115/delta/b1-symdays.csv | sort -u > "$W/syms.txt"
  awk -F, -v d="$d" 'NR==FNR{s[$1]=1;next} FNR==1||($2==d&&($1 in s))' "$W/syms.txt" "$V" > "$W/verdict-b1.csv"
  for f in $WFORMS; do fl+=(--exit-form "$f"); done
  ( export HOME=$H; cd "$W" && nice -n 5 "$BN" lob bounce-grid --verdict-csv "$W/verdict-b1.csv" --root "study/root-$d" --touches-from "$E/study/approaches/D20"     --signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000     --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --entry-ttl-secs 1800 --band-exit-bps 20     --busy-skip off --threads 2 --hold-step skip --exit-group on --events wide --entry-form ladder3x0..0.0409sw2 --sigma-from study/sigma240     --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 "${fl[@]}"     --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55 --tape-log 30 --out-dir "b5/out-$d" > "$W/grid.log" 2>&1 ); rc=$?
  mkdir -p "$W/out"; cp -r "$W/b5/out-$d"/. "$W/out"/ 2>/dev/null
  got=$(find "$W/out" -name 'forms.csv' | xargs -r cat | awk -F, -v d="$d" '$2==d{print $3}' | sort -u | grep -c .)
  echo "run tk115w $1 rc=$rc форм_got=$got форм_exp=$(echo $WFORMS | wc -w) rounds_files=$(find "$W/out" -name 'rounds*.csv' | wc -l)"
  [ "$rc" = 0 ] && [ "$(find "$W/out" -name 'rounds*.csv' | wc -l)" -gt 0 ] && [ "$got" = "$(echo $WFORMS | wc -w)" ] || { echo "RUNFAIL tk115w $1 (rc=$rc got=$got)"; return 1; }
}
fam_ext() {  # $1 tk115j|tk115w: ref и new в одной сессии, сверка
  local f=$1 rc=0 nb s; fam_conf "$f"; nb=$NEW; [ $SELF = 1 ] && nb=$REF
  nb=$(readlink -f "$nb")
  if [ "$f" = tk115j ]; then
    tk115j_run ref "$REF" || rc=1; tk115j_run new "$nb" || rc=1
    for s in signals e106 walls; do cmpdirs /data/tk0115/delta/g164-ref/$s /data/tk0115/delta/g164-new/$s || rc=1; done
  else
    tk115w_run ref "$REF" || rc=1; tk115w_run new "$nb" || rc=1
    cmpdirs "$T/xw-ref/out" "$T/xw-new/out" || rc=1
  fi
  echo "family=$f rc=$rc"; return $rc
}
fam_one() {  # $1 семья
  local f=$1 d rc=0 nb w
  case $f in tk115j|tk115w) fam_ext "$f"; return $?;; esac
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
  ( out=$G/$f.txt; fam_one "$f" > "$out" 2>&1; rc=$?
    echo "family=$f rc=$rc $(grep -E '^files=' "$out" | tail -1 | tr '
' ' ')" >> "$G/summary.txt" ) &
done
wait
touch "$G/gate.done"
