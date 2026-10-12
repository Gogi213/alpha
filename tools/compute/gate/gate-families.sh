#!/usr/bin/env bash
# TK-164 (КТ-3b): гейт семей клеток на проб-сутках — diff 0 (cmp всех выходов) нового бинарника против опорного семьи.
# Опись опорных бинарей — docs/findings/gate-families-2026-10-10.md. Запуск на calc ТОЛЬКО через alsched:
#   python3 /data/sched/alsched.py submit --cls prod --kind gate --name gate-fam --max-runtime 3600 --cores 4 --mem 8 --why "КТ-3b гейт семей" \
#     -- bash /data/tk164/gate-families.sh <новый бинарник, путь> [base,r2,dl,tk115,tk115j,tk115w,tk115e]
#   семьи r2, tk115*, tk115j/w/e считают сборкой с feature r2: NEW_R2=<путь> (иначе берётся <новый бинарник>)
#   проверка самого скрипта без нового бинаря (опорный против себя, все семьи): … -- bash /data/tk164/gate-families.sh --selftest [семьи]
# Семьи (опорный = тем, кем посчитаны числа, Летопись): base b14flag | r2 b15pyr4pgoflag | dl tk084-dl | tk115 (r3a) b26tk115r2 |
# tk115j (signals/e106/walls, tk115-delta-day) tk115f | tk115w (chase<W>, tape/cxl, пороги v5 марта, --tape-log 30) tk115f |
# tk115e (e65: wall2/wall2x, --driver setups, журнал стен touches --wall-log) tk115f.
# КТ-5 (TK-150): ABIN_SPLIT=1 — у каждого прогона свой пустой кэш $GW/abin-<метка> (сверка .abin: cmp -r abin-base-ref-* abin-base-new-*); REF_OVERRIDE=<опорный> — подмена опорного base.
# Семьи идут параллельно (каждая — своё дерево /data/tk164/*), summary.txt — строка на семью.
# Итог: /data/tk164/gate/<семья>.txt, summary.txt (строка на семью: rc), маркер gate.done. Зелёный = rc 0 И files>0 в каждой
# подпапке выхода И rc каждого прогона 0 (RUNFAIL/files=0/пустой выход = красный, не зелёный).
# Каждая семья — отдельно: опорный и новый считают те же сутки в той же сессии. Новый бинарь запускается с тем же env, что опорный
# (обёртка по образцу опорной: b14flag, b15pyr4pgoflag); у семей с сырым опорным (tk084-dl, tk115f) — без env.
# TK-172 (В-213): выходы ОПОРНОГО считаются один раз и кладутся в кэш $RC/<ключ> (ключ = md5 опорного бинаря (у скрипта-обёртки ещё
# и бинаря, на который он exec) + сутки + md5 клеток и скрипта суток + env-флаги); следующие гейты сверяют new с кэшем
# (прогон опорного пропускается, в выходе «ref кэш: попадание»). Пересчитать опорный заново — флаг --refresh-ref (в любом месте
# аргументов) или REFRESH_REF=1. ABIN_SPLIT=1 кэш отключает (КТ-5 сверяет .abin опорного). Менялся входной корень данных или код
# запуска опорного в этом скрипте — --refresh-ref. Изоляция: при GATE_TAG корни по умолчанию свои ($T/w-<тег>, $T/gate-<тег>).
set -uo pipefail
REFRESH=${REFRESH_REF:-}; ARGS=(); for a in "$@"; do if [ "$a" = --refresh-ref ]; then REFRESH=1; else ARGS+=("$a"); fi; done
set -- ${ARGS[@]+"${ARGS[@]}"}
ALLF=base,r2,dl,tk115,tk115j,tk115w,tk115e
if [ "${1:-}" = --selftest ]; then SELF=1; NEW=""; FAMS=${2:-$ALLF}; else SELF=0; NEW=${1:?новый бинарник (путь)}; FAMS=${2:-$ALLF}; fi
T=/data/tk164; TAG=${GATE_TAG:-164}; if [ -n "${GATE_TAG:-}" ]; then GW=${GATE_W:-$T/w-$TAG}; G=${GATE_DIR:-$T/gate-$TAG}; else GW=${GATE_W:-$T}; G=${GATE_DIR:-$T/gate}; fi
BIN=/opt/alpha-compute/bin; RC=${REF_CACHE:-$T/refcache}; mkdir -p "$G" "$T/wrap" "$GW" "$RC"; rm -f "$G/gate.done" "$G/summary.txt"
rc_on() { [ -z "$REFRESH" ] && [ -z "${ABIN_SPLIT:-}" ]; }
rc_md5() { local f; f=$(readlink -f "$1"); md5sum "$f" | cut -c1-32; if [ "$(head -c2 "$f")" = '#!' ]; then md5sum "$(readlink -f "$(grep -E '^exec ' "$f" | tail -1 | awk '{print $2}')")" 2>/dev/null | cut -c1-32; fi; }
rc_key() { { for x in "$@"; do if [ -f "$x" ]; then md5sum < "$x"; else echo "$x"; fi; done; echo "${ALPHA_APPROACH_BIN_DIR:-}"; } | md5sum | cut -c1-32; }  # файлы — по содержимому, остальное — как строка
rc_hit() { rc_on && [ -e "$RC/$1/.ok" ]; }  # $1 ключ
rc_put() {  # $1 ключ; остальные: пары «каталог-в-кэше|путь» — каталог УХОДИТ в кэш (mv), на его месте ссылка; при гонке остаётся первый
  local k=$1 t="$RC/.tmp-$1-$$" p; shift; [ -z "${ABIN_SPLIT:-}" ] || return 0; rm -rf "$t"; mkdir -p "$t"
  for p in "$@"; do mv "${p#*|}" "$t/${p%%|*}"; done
  if [ -e "$RC/$k/.ok" ]; then rm -rf "$RC/$k"; fi; touch "$t/.ok"; mv "$t" "$RC/$k"
  for p in "$@"; do ln -s "$RC/$k/${p%%|*}" "${p#*|}"; done
}
# семья: опорный (в $BIN или путь) | месяц | сутки | скрипт суток | клетки (для счёта got/exp, пусто — только files>0)
fam_conf() {
  case $1 in
    base)  REF=${REF_OVERRIDE:-$BIN/alpha-b14flag};            MON=jan; DAY=2026-01-01,2026-01-15; SCR=jall; CELLS=/data/tk164/cells-241.txt
           [ $SELF = 1 ] && DAY=2026-01-01;;  # самопроверка: путь jall один раз, не 2×2 прогона по 241 клетке
    r2)    REF=$BIN/alpha-b15pyr4pgoflag;     MON=mar; DAY=2026-03-07; SCR=/data/tk065/days/r2-2026-03-07.sh;  CELLS=/data/tk065/days/r2-2026-03-07.cells;;
    dl)    REF=$BIN/alpha-tk084-dl;           MON=mar; DAY=2026-03-07; SCR=/data/tk084/days/p12-2026-03-07.sh; CELLS=/data/tk084/days/p12-2026-03-07.cells;;
    tk115) REF=$BIN/alpha-b26tk115r2;         MON=mar; DAY=2026-03-07; SCR=/data/tk065/days/r3a-2026-03-07.sh; CELLS=/data/tk065/days/r3a-2026-03-07.cells;;
    tk115j|tk115w|tk115e) REF=/data/tk0115/bin/alpha-tk115f; MON=mar; DAY=2026-03-07;;
  esac
  [ -e "$REF" ] || REF=$BIN/_archive/$(basename "$REF")  # TK-176А: опорные ушли в bin/_archive
  if [ "$(head -c2 "$REF")" = '#!' ]; then  # опорный — обёртка с env: её exec тоже мог уйти в _archive → копия с исправленным путём
    local x; x=$(sed -n 's#^exec \([^ ]*\) .*#\1#p' "$REF" | head -1)
    if [ -n "$x" ] && [ ! -e "$x" ] && [ -e "$BIN/_archive/$(basename "$x")" ]; then
      mkdir -p "$T/wrap"; sed "s#^exec $x #exec $BIN/_archive/$(basename "$x") #" "$REF" > "$T/wrap/fix-$(basename "$REF")"; chmod +x "$T/wrap/fix-$(basename "$REF")"; REF=$T/wrap/fix-$(basename "$REF")
    fi
  fi
}
# новый бинарь семьи: r2 и tk115* требуют сборку с feature r2 (NEW_R2), остальные — боевую (NEW)
fam_new() { case $1 in r2|tk115*) echo "${NEW_R2:-$NEW}";; *) echo "$NEW";; esac; }
mkwrap() {  # $1 опорный $2 новый → путь, который кладётся в bin/alpha-tk044k1-new (обёртка с env опорной, если опорная — скрипт)
  local w=$T/wrap/$(basename "$1")-$(basename "$2")
  if [ "$(head -c2 "$1")" = '#!' ]; then sed "s#^exec .*#exec $(readlink -f "$2") \"\$@\"#" "$1" > "$w"; chmod +x "$w"; echo "$w"; else readlink -f "$2"; fi
}
run_day() {  # $1 метка $2 бинарь/обёртка $3 мес $4 сутки $5 скрипт суток $6 клетки → каталог $GW/w-<метка>; rc 0 только при rc=0 и got==exp
  local S=$GW/w-$1 H=/data/tk046/$3/home x b rc exp got
  local E=$H/alpha/epochs/e-$3
  rm -rf "$S"; mkdir -p "$S/b5" "$S/bin"; ln -s "$2" "$S/bin/alpha-tk044k1-new"
  for x in $E/* $E/.[!.]*; do b=$(basename "$x"); case $b in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$x" "$S/$b"; done
  cp "$6" "$S/cells.txt"
  if [ "$5" = jall ]; then  # строка 3 jall-скрипта суток (как ряд TK-148), --events wide, свои клетки
    sed -n 3p "$H/alpha/tmp-p07/cells-by-day/jall-jan-$4.sh" | sed "s# bounce-grid # bounce-grid --events wide #; s# --cells [^ ]*# --cells $S/cells.txt#; s# --extra-runs [^ ]*##; s#b5/.cellstmp-$4.log#$S/grid.log#" > "$S/day.sh"
  else  # готовый скрипт суток: клетки/каталоги внутри него — как в боевом счёте; пишет в b5/ рабочего каталога $S
    cp "$5" "$S/day.sh"; sed -i "s#--cells [^ ]*#--cells $S/cells.txt#; s#> b5/\.cellstmp-\([^ ]*\)\.log#> b5/.cellstmp-\1.log#; s#^cp b5/\.cellstmp[^ ]*\.log /data/[^ ]*#:#" "$S/day.sh"
  fi
  ( export HOME=$H ALPHA_SKIP_SAME=1 ALPHA_APPROACH_BIN_DIR=$([ -n "${ABIN_SPLIT:-}" ] && echo "$GW/abin-$1" || echo "${ALPHA_APPROACH_BIN_DIR:-/data/tk048/abin-t46}"); cd "$S" && /usr/bin/time -f "%e %U %S %M" -o "$S/t.txt" bash "$S/day.sh" > "$S/run.out" 2> "$S/run.err" ); rc=$?
  exp=$(awk 'NF{print $1}' "$6" | sort -u | grep -c .); got=$(find "$S/b5" -name 'forms.csv' 2>/dev/null | xargs -r cat | awk -F, -v d="$4" '$2==d{print $3}' | sort -u | grep -c .)
  local nr; nr=$(find "$S/b5" -name 'rounds*.csv' 2>/dev/null | wc -l)
  echo "run $1 rc=$rc cells_got=$got cells_exp=$exp rounds_files=$nr"
  # base: forms.csv перечисляет только клетки с исходами за сутки (66 из 241), счёт клеток не применим — rc=0 и rounds>0
  [ "$rc" = 0 ] && [ "$nr" -gt 0 ] && { [ "$got" = "$exp" ] || [ "$5" = jall ]; } || { echo "RUNFAIL $1 (rc=$rc rounds=$nr got=$got/$exp)"; return 1; }
}
refvsprod() {  # $1 каталог опорного b5 $2 боевой b5: forms.csv и rounds*.csv опорного побайтно = боевым (опорный = тем, кем посчитаны числа)
  local n=0 bad=0 f
  for f in $(cd "$1" && find . -type f \( -name forms.csv -o -name 'rounds*.csv' \) | sort); do n=$((n+1)); cmp -s "$1/$f" "$2/$f" || { bad=$((bad+1)); echo "PRODDIFF $f"; }; done
  echo "ref_vs_prod files=$n diff=$bad"; [ "$n" -gt 0 ] && [ "$bad" = 0 ]
}
cmpf() {  # $1 A $2 B относительный путь $3: manifest.txt — без снимка ALPHA_* (env_count=, env:*, П2b С-06: строки только добавлены, не регрессия)
  case $3 in
    */manifest.txt|manifest.txt) cmp -s <(grep -Ev '^(env_count=|env:)' "$1/$3") <(grep -Ev '^(env_count=|env:)' "$2/$3");;
    *) cmp -s "$1/$3" "$2/$3";;
  esac
}
afile() {  # $1 новый $2 опорный (b5 r2) $3 боевая волна A (r2m-a): строки форм A (pyre/pynw/pyeat — сдвиг от входа + безубыток Г-92, 6b792282) новые = волне A, остальные = опорному
  local f=$3; [ -e "$1/$f" ] && [ -e "$2/$f" ] || return 1
  cmp -s <(grep -Ev -- '-py(re|nw|eat)' "$1/$f") <(grep -Ev -- '-py(re|nw|eat)' "$2/$f") || return 1
  local a=$AREF/${f#./r2/}  # b5/r2/<сутки>/… ↔ волна A r2a/<сутки>/…
  [ -e "$a" ] && cmp -s <(grep -E -- '-py(re|nw|eat)' "$1/$f" | sort) <(grep -E -- '-py(re|nw|eat)' "$a" | sort)
}
cmpdirs() {  # $1 A $2 B: cmp всех файлов (без логов), files>0, одинаковый список; красный при files=0; AREF=<каталог волны A> — формы A сверяются с ней
  local n=0 bad=0 f la lb
  la=$(cd "$1" && find . -type f ! -path '*/.cellstmp*' ! -name '*.log' | sort); lb=$(cd "$2" && find . -type f ! -path '*/.cellstmp*' ! -name '*.log' | sort)
  [ "$la" = "$lb" ] || { bad=$((bad+1)); echo "LISTDIFF"; }
  for f in $la; do n=$((n+1)); cmpf "$1" "$2" "$f" && continue
    case $f in *.csv) [ -n "${AREF:-}" ] && afile "$2" "$1" "$f" && { echo "A-ok $f"; continue; };; esac
    bad=$((bad+1)); echo "DIFF $f"; done
  echo "files=$n diff=$bad"; [ "$n" -gt 0 ] && [ "$bad" = 0 ]
}
# пороги v5 марта (docs/findings/p12-r2b-thresholds-2026-10-10.json): e112 tape30q, e116 cxl30q, e133 chase W=12080 мс ×0,5/1/2
WFORMS="chase86400000 chase6040 chase12080 chase24160 tape30q0.014653226867454029 tape30q0.21387408589803297 cxl30q1.5778084895559001 cxl30q15.839990627236537"
tk115j_run() {  # $1 метка ref|new $2 бинарь: tk115-delta-day (signals/e106/walls), красный при fail.txt/нет done/пустых подпапках
  local O=/data/tk0115/delta/g${TAG}-$1 rc=0 s
  rm -rf "$O"; BIN=$2 TP=4 GT=2 bash /data/tk0115/tk115-delta-day.sh "g${TAG}-$1" mar 2026-03-07 > "$GW/tk115j-$1.out" 2>&1 || rc=1
  [ -e "$O/done" ] && [ ! -s "$O/fail.txt" ] || { echo "RUNFAIL tk115j $1 (done/fail.txt: $(cat "$O/fail.txt" 2>/dev/null | head -3))"; return 1; }
  for s in signals e106 walls; do [ -n "$(find "$O/$s" -type f 2>/dev/null | head -1)" ] || { echo "RUNFAIL tk115j $1: пусто $s"; rc=1; }; done
  return $rc
}
tk115w_run() {  # $1 метка $2 бинарь: сетка B1 суток (логика tk115-w-day.sh, КОПИЕЙ — боевой /data/tk0115/delta/w не трогаем) всеми формами WFORMS
  local MON=mar d=2026-03-07 BN=$2 H=/data/tk046/mar/home W=$GW/xw-$1 x b rc fl=() f got
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
tk115e_run() {  # $1 метка $2 бинарь: e65 (wall2/wall2x) — touches --r1-cols --wall-log по символам B1 суток + сетка --driver setups (отказ без журнала стен)
  local MON=mar d=2026-03-07 BN=$2 H=/data/tk046/mar/home W=$GW/xe-$1 x b rc=0 s got nr
  local E=$H/alpha/epochs/e-mar V=/data/tk044/final3/verdict.csv
  rm -rf "$W"; mkdir -p "$W/b5" "$W/bin" "$W/study/approaches" "$W/wt/D20/$d" "$W/log"; ln -s "$BN" "$W/bin/alpha-tk044k1-new"
  for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in study|b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
  for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*|approaches) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
  ln -s "$(readlink -f "$E/study/root-$d")" "$W/study/root-$d"
  awk -F, -v m="$MON" -v d="$d" 'NR>1 && $1==m && $2==d{print $3}' /data/tk0115/delta/b1-symdays.csv | sort -u > "$W/syms.txt"
  awk -F, -v d="$d" 'NR==FNR{s[$1]=1;next} FNR==1||($2==d&&($1 in s))' "$W/syms.txt" "$V" > "$W/verdict-b1.csv"
  export HOME=$H
  xargs -P 4 -I{} bash -c 'nice -n 5 "'"$BN"'" lob touches --root "'"$W"'/study/root-'"$d"'" --symbol {} --h3-mode notional --h3-usd 10000 --approach-bps 20 --r1-cols --wall-log --out "'"$W"'/wt/D20/'"$d"'/touches-{}.csv" > "'"$W"'/log/touches-{}.log" 2>&1 || echo "{} TOUCHES_FAIL" >> "'"$W"'/fail.txt"' < "$W/syms.txt"
  [ ! -s "$W/fail.txt" ] || { echo "RUNFAIL tk115e $1 touches: $(head -3 "$W/fail.txt")"; return 1; }
  ( cd "$W" && nice -n 5 "$BN" lob bounce-grid --verdict-csv "$W/verdict-b1.csv" --root "study/root-$d" --touches-from wt/D20 --signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000     --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --entry-ttl-secs 1800 --band-exit-bps 20     --busy-skip off --threads 2 --hold-step skip --exit-group on --driver setups --events wide --entry-form ladder3x0..0.0409sw2 --sigma-from study/sigma240     --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 --exit-form wall2 --exit-form wall2x     --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55 --r1-cols --out-dir "b5/out-$d" > "$W/grid.log" 2>&1 ); rc=$?
  mkdir -p "$W/out"; cp -r "$W/b5/out-$d"/. "$W/out"/ 2>/dev/null; cp "$W/wt/D20/$d"/walls-*.csv "$W/out"/ 2>/dev/null
  got=$(find "$W/out" -name 'forms.csv' | xargs -r cat | awk -F, -v d="$d" '$2==d{print $3}' | sort -u | grep -c .)
  nr=$(find "$W/out" -name 'rounds*.csv' | wc -l); s=$(find "$W/out" -name 'walls-*.csv' | wc -l)
  echo "run tk115e $1 rc=$rc форм_got=$got (exp 2: wall2,wall2x) rounds_files=$nr walls_files=$s"
  [ "$rc" = 0 ] && [ "$nr" -gt 0 ] && [ "$s" -gt 0 ] && [ "$got" -ge 2 ] || { echo "RUNFAIL tk115e $1 (rc=$rc got=$got rounds=$nr walls=$s)"; return 1; }
}
fam_ext() {  # $1 tk115j|tk115w: ref и new в одной сессии, сверка
  local f=$1 rc=0 nb s; fam_conf "$f"; nb=$(fam_new "$f"); [ $SELF = 1 ] && nb=$REF
  nb=$(readlink -f "$nb")
  if [ "$f" = tk115j ]; then
    local k O=/data/tk0115/delta/g${TAG}-ref; k=$(rc_key "$(rc_md5 "$REF")" tk115j mar 2026-03-07 /data/tk0115/tk115-delta-day.sh)
    if rc_hit "$k"; then echo "ref кэш: попадание tk115j ($k)"; rm -rf "$O"; mkdir -p "$O"; for s in signals e106 walls; do ln -s "$RC/$k/$s" "$O/$s"; done
    else tk115j_run ref "$REF" && rc_put "$k" "signals|$O/signals" "e106|$O/e106" "walls|$O/walls" || rc=1; fi
    tk115j_run new "$nb" || rc=1
    for s in signals e106 walls; do cmpdirs /data/tk0115/delta/g${TAG}-ref/$s /data/tk0115/delta/g${TAG}-new/$s || rc=1; done
  elif [ "$f" = tk115e ]; then
    local k; k=$(rc_key "$(rc_md5 "$REF")" tk115e e65 mar 2026-03-07)
    if rc_hit "$k"; then echo "ref кэш: попадание tk115e ($k)"; rm -rf "$GW/xe-ref"; mkdir -p "$GW/xe-ref"; ln -s "$RC/$k/out" "$GW/xe-ref/out"
    else tk115e_run ref "$REF" && rc_put "$k" "out|$GW/xe-ref/out" || rc=1; fi
    tk115e_run new "$nb" || rc=1
    cmpdirs "$GW/xe-ref/out" "$GW/xe-new/out" || rc=1
  else
    local k; k=$(rc_key "$(rc_md5 "$REF")" tk115w mar 2026-03-07 "$WFORMS")
    if rc_hit "$k"; then echo "ref кэш: попадание tk115w ($k)"; rm -rf "$GW/xw-ref"; mkdir -p "$GW/xw-ref"; ln -s "$RC/$k/out" "$GW/xw-ref/out"
    else tk115w_run ref "$REF" && rc_put "$k" "out|$GW/xw-ref/out" || rc=1; fi
    tk115w_run new "$nb" || rc=1
    cmpdirs "$GW/xw-ref/out" "$GW/xw-new/out" || rc=1
  fi
  echo "family=$f rc=$rc"; return $rc
}
fam_one() {  # $1 семья
  local f=$1 d rc=0 nb w
  case $f in tk115j|tk115w|tk115e) fam_ext "$f"; return $?;; esac
  fam_conf "$f"; nb=$(fam_new "$f"); [ $SELF = 1 ] && nb=$REF
  w=$(mkwrap "$REF" "$nb")
  for d in ${DAY//,/ }; do
    local k S=$GW/w-$f-ref-$d; k=$(rc_key "$(rc_md5 "$REF")" "$f" "$MON" "$d" "$CELLS" "$SCR" "$([ "$SCR" = jall ] && echo "/data/tk046/$MON/home/alpha/tmp-p07/cells-by-day/jall-jan-$d.sh")")
    if rc_hit "$k"; then echo "ref кэш: попадание $f $d ($k)"; rm -rf "$S"; mkdir -p "$S"; ln -s "$RC/$k/b5" "$S/b5"
    else run_day "$f-ref-$d" "$(mkwrap "$REF" "$REF")" "$MON" "$d" "$SCR" "$CELLS" && rc_put "$k" "b5|$S/b5" || rc=1; fi
    run_day "$f-new-$d" "$w" "$MON" "$d" "$SCR" "$CELLS" || rc=1
    AREF=; [ "$f" = r2 ] && AREF=/data/tk065/wa-$d/b5/r2a
    if [ "$f" = base ]; then  # jall: выход суток остаётся в b5/.cellstmp-<сутки> (отделка суток не выполняется) — сверяем его
      cmpdirs "$GW/w-$f-ref-$d/b5/.cellstmp-$d" "$GW/w-$f-new-$d/b5/.cellstmp-$d" || rc=1
    else cmpdirs "$GW/w-$f-ref-$d/b5" "$GW/w-$f-new-$d/b5" || rc=1; fi
    [ "$f" = r2 ] && { refvsprod "$GW/w-$f-ref-$d/b5" "/data/tk065/w-$d/b5" || rc=1; }
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
