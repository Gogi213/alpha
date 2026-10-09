#!/usr/bin/env bash
# calibrate.sh (В-178, продолжение В-170): подбор режима исполнения ДО боевого прогона на сервере (> ~15 мин).
# Гоняет варианты «параллельность P × readahead диска» по несколько минут каждый на СВОИХ ещё не сделанных единицах,
# меряет единиц/мин, МБ/с диска (/proc/diskstats), ЦП и iowait (/proc/stat), выбирает лучший по единиц/мин
# (варианты в пределах 5 % от максимума равны — берётся меньший P). readahead после калибровки возвращается.
#
# Единица = строка файла --units; её поля идут команде как $1 $2 … (bash -c; без «$» в --cmd поля дописываются сами).
#   --done 'тест'  → команда докачиваемая: тест по полям единицы ($1 …) даёт 0, если единица уже сделана; сделанные
#                    пропускаются, результаты калибровки остаются настоящими (запись — атомарно: .tmp → mv).
#   без --done     → результаты временные: команда стартует в $CAL_TMP (каталог под --out), после варианта он удаляется.
# Недоделанная к концу окна единица доживает до --grace секунд, потом убивается (в режиме --done — .tmp остаётся).
#
#   bash calibrate.sh --units units.txt --cmd 'one "$1" "$2"' --done 'test -s out/$2.csv' --out /data/job/calib
#   Параметры: --P 1,2,4,8,16  --ra cur,16384 (КБ; суффикс K/M; cur = текущий)  --secs 150  --grace <secs>
#              --quota 1500 (CPUQuota %, ≤ 1500)  --path <каталог данных,…> (по умолчанию — из полей единиц)
#              --crit units|mbps  --quick (сначала P при первом ra, потом ra при лучшем P)  --seed 1  --idle 5
#              --job <имя|off> --ticket TK-XX (ход для экрана владельца: /data/progress через alpha-progress)
# Выход: <out>/calibrate.tsv, таблица в stdout, последняя строка «лучший: P=…, readahead=… КБ».
set -eu -o pipefail
trap 'echo "calibrate: сбой (код $?) в строке $LINENO: $BASH_COMMAND" >&2' ERR   # С-51: ошибка шага останавливает, не молчит
export LC_NUMERIC=C
SELF=$(readlink -f "$0"); ARGS=("$@")
die() { echo "calibrate: $*" >&2; exit 2; }
usage() { sed -n '2,/^# Выход/s/^# \{0,1\}//p' "$SELF"; }

UNITS= CMD= DONE= PLIST=1,2,4,8,16 RALIST=cur,16384 SECS=150 GRACE= QUOTA= PATHS= OUT= CRIT=units QUICK=0 SEED=1
IDLE=5 JOB= TICKET=calibrate NOSCOPE=0
norm=(); for a in "$@"; do case $a in --*=*) norm+=("${a%%=*}" "${a#*=}");; *) norm+=("$a");; esac; done
set -- "${norm[@]+"${norm[@]}"}"
while [ $# -gt 0 ]; do
  case $1 in
    -h|--help) usage; exit 0;;
    --quick) QUICK=1; shift; continue;;
    --no-scope) NOSCOPE=1; shift; continue;;
    --units|--cmd|--done|--P|--ra|--secs|--grace|--quota|--path|--out|--crit|--seed|--job|--ticket|--idle)
      [ $# -ge 2 ] || die "$1: нужно значение"
      case $1 in
        --units) UNITS=$2;; --cmd) CMD=$2;; --done) DONE=$2;; --P) PLIST=$2;; --ra) RALIST=$2;; --secs) SECS=$2;;
        --grace) GRACE=$2;; --quota) QUOTA=$2;; --path) PATHS=$2;; --out) OUT=$2;; --crit) CRIT=$2;; --seed) SEED=$2;;
        --job) JOB=$2;; --ticket) TICKET=$2;; --idle) IDLE=$2;;
      esac; shift 2;;
    *) die "неизвестный аргумент $1 (--help)";;
  esac
done
[ -n "$UNITS" ] && [ -s "$UNITS" ] || die "--units: нужен непустой файл единиц"
[ -n "$CMD" ] || die "--cmd: нужна команда на единицу"
[[ $SECS =~ ^[0-9]+$ ]] && [ "$SECS" -ge 5 ] || die "--secs: целое ≥ 5"
[ -n "$GRACE" ] || GRACE=$SECS
[[ $GRACE =~ ^[0-9]+$ ]] || die "--grace: целое"
[[ $IDLE =~ ^[1-9][0-9]*$ ]] || die "--idle: целое ≥ 1"
[[ $SEED =~ ^[0-9]+$ ]] || die "--seed: целое"
case $CRIT in units|mbps) ;; *) die "--crit: units | mbps";; esac
IFS=, read -ra PS <<<"$PLIST"; [ ${#PS[@]} -gt 0 ] || die "--P: пусто"
for p in "${PS[@]}"; do [[ $p =~ ^[1-9][0-9]*$ ]] || die "--P: натуральные числа через запятую"; done
NPROC=$(nproc); [ -n "$QUOTA" ] || { QUOTA=$((NPROC * 95)); [ "$QUOTA" -gt 1500 ] && QUOTA=1500; }
[[ $QUOTA =~ ^[0-9]+$ ]] && [ "$QUOTA" -ge 100 ] && [ "$QUOTA" -le 1500 ] || die "--quota: 100…1500 (сумма заданий на сервере ≤ 1500 %)"
[ -n "$OUT" ] || OUT=./calibrate-$(date +%Y%m%dT%H%M%S)
case $UNITS in /*) ;; *) UNITS=$PWD/$UNITS;; esac
case $OUT in /*) ;; *) OUT=$PWD/$OUT;; esac
# CPUQuota: весь процесс калибровки — в transient-scope systemd (дети делят квоту)
if [ -z "${CAL_IN_SCOPE:-}" ] && [ "$NOSCOPE" = 0 ]; then
  if systemd-run --scope --quiet -p "CPUQuota=${QUOTA}%" -- true >/dev/null 2>&1; then
    CAL_IN_SCOPE=1 exec systemd-run --scope --quiet --unit "alpha-calibrate-$$" -p "CPUQuota=${QUOTA}%" -- bash "$SELF" "${ARGS[@]}"
  fi
  echo "calibrate: ВНИМАНИЕ: systemd-run --scope недоступен — идём без CPUQuota" >&2
fi
[ -n "${EPOCHREALTIME:-}" ] || die "нужен bash ≥ 5 (EPOCHREALTIME)"
{ exec 9>/run/lock/alpha-calibrate.lock; } 2>/dev/null || exec 9>/tmp/alpha-calibrate.lock
flock -n 9 || die "калибровка уже идёт на этой машине (замок alpha-calibrate.lock)"
mkdir -p "$OUT/log" "$OUT/work" || die "не создать $OUT"
set -m   # у каждой единицы — своя группа процессов (убить недоделку целиком)

now_us() { NOW=${EPOCHREALTIME/[.,]/}; }
tsout() { printf '%s\n' "$*" >> "$OUT/calibrate.tsv"; }

# ---------- устройства по пути данных ----------
devname_of() {   # путь → имя блочного устройства, как в /proc/diskstats; пусто, если не блочное (overlay, tmpfs, сеть)
  local src rp n
  src=$(df -P "$1" 2>/dev/null | awk 'NR==2{print $1}'); src=${src%%\[*}
  [ -b "$src" ] || return 1
  rp=$(readlink -f "$src"); n=${rp##*/}
  [ -d "/sys/class/block/$n" ] && echo "$n"
}
DEVS=()
add_dev() { local d=$1 x; [ -n "$d" ] || return 0; for x in "${DEVS[@]+"${DEVS[@]}"}"; do [ "$x" = "$d" ] && return 0; done; DEVS+=("$d"); }
if [ -n "$PATHS" ]; then
  IFS=, read -ra PP <<<"$PATHS"; for p in "${PP[@]}"; do [ -e "$p" ] || die "--path: нет $p"; add_dev "$(devname_of "$p")"; done
else
  while read -r -a f; do for x in "${f[@]}"; do case $x in /*) [ -e "$x" ] && add_dev "$(devname_of "$x")";; esac; done
  done < <(grep -v '^[[:space:]]*\(#\|$\)' "$UNITS" | head -50)
fi
declare -A RA0
RA_OK=1
for d in "${DEVS[@]+"${DEVS[@]}"}"; do
  RA0[$d]=$(blockdev --getra "/dev/$d" 2>/dev/null) || { RA_OK=0; echo "calibrate: blockdev --getra /dev/$d не работает — readahead не меняем" >&2; }
done
[ ${#DEVS[@]} -gt 0 ] || { RA_OK=0; echo "calibrate: устройство по данным не определено (задай --path) — без МБ/с и без readahead" >&2; }

# варианты readahead (КБ); «cur» — как есть
RAS=()
IFS=, read -ra RT <<<"$RALIST"
for t in "${RT[@]}"; do
  if [ "$t" = cur ]; then RAS+=(cur); continue; fi
  [[ $t =~ ^([0-9]+)([KkMm]?)$ ]] || die "--ra: cur или число КБ (суффикс K/M)"
  kb=${BASH_REMATCH[1]}; case ${BASH_REMATCH[2]} in M|m) kb=$((kb * 1024));; esac
  [ "$RA_OK" = 1 ] || continue
  RAS+=("$kb")
done
[ ${#RAS[@]} -gt 0 ] || RAS=(cur)
if [ "$RA_OK" = 1 ]; then   # числовой вариант, равный текущему, — дубль «cur»
  cur_kb=$(( ${RA0[${DEVS[0]}]} / 2 )); have_cur=0; for r in "${RAS[@]}"; do [ "$r" = cur ] && have_cur=1; done
  if [ $have_cur = 1 ]; then n=(); for r in "${RAS[@]}"; do [ "$r" = "$cur_kb" ] || n+=("$r"); done; RAS=("${n[@]}"); fi
fi
set_ra() {   # $1 = cur | КБ → readahead на всех устройствах; в RA_NOW — фактическое значение (КБ) первого
  local d v
  for d in "${DEVS[@]+"${DEVS[@]}"}"; do
    [ "$RA_OK" = 1 ] || break
    if [ "$1" = cur ]; then v=${RA0[$d]}; else v=$(($1 * 2)); fi
    blockdev --setra "$v" "/dev/$d" 2>/dev/null || echo "calibrate: setra /dev/$d $v не удалось" >&2
  done
  RA_NOW=-; [ "$RA_OK" = 1 ] && RA_NOW=$(( $(blockdev --getra "/dev/${DEVS[0]}") / 2 ))
}
restore_ra() {
  local d; [ "$RA_OK" = 1 ] || return 0
  for d in "${DEVS[@]}"; do
    blockdev --setra "${RA0[$d]}" "/dev/$d" 2>/dev/null
    echo "readahead /dev/$d: возвращён $(( $(blockdev --getra "/dev/$d") / 2 )) КБ (было $(( ${RA0[$d]} / 2 )) КБ)"
  done
}

# ---------- замеры ----------
snap() {   # SNAP = «сект.чт сект.зап ЦП.всего ЦП.idle ЦП.iowait» (всё по устройствам / по машине)
  local ds st; ds=0\ 0
  [ ${#DEVS[@]} -gt 0 ] && ds=$(awk -v d=" ${DEVS[*]} " 'index(d," "$3" "){r+=$6;w+=$10}END{print r+0,w+0}' /proc/diskstats)
  st=$(awk '/^cpu /{t=0;for(i=2;i<=9;i++)t+=$i;print t,$5,$6}' /proc/stat)
  SNAP="$ds $st"
}
rates() {  # $1 snap0 $2 snap1 $3 окно мкс → «МБ/с чт, МБ/с зап, ЦП %, iowait %» (ЦП — % всей машины)
  awk -v a="$1" -v b="$2" -v dt="$3" 'BEGIN{split(a,x," ");split(b,y," ");s=dt/1e6;tt=y[3]-x[3];
    printf "%.1f %.1f %.1f %.1f\n",(y[1]-x[1])*512/1e6/s,(y[2]-x[2])*512/1e6/s,tt?100*(tt-(y[4]-x[4])-(y[5]-x[5]))/tt:0,tt?100*(y[5]-x[5])/tt:0}'
}

PROGJOB=
if [ "$JOB" != off ] && [ -d /data/progress ] && command -v alpha-progress >/dev/null 2>&1; then
  PROGJOB=${JOB:-calibrate-$(basename "$OUT")}; PROGJOB=${PROGJOB//[^A-Za-z0-9._-]/_}
fi
progress() {   # $1 = доля готовых вариантов (дробь допустима), $2 = шаг
  [ -n "$PROGJOB" ] || return 0
  alpha-progress "$PROGJOB" "$TICKET" "калибровка: $2" "$1" "$NVAR" вариантов "выбор лучшего режима" >/dev/null 2>&1 || true
}

# ---------- шапка ----------
if [ "$QUICK" = 1 ]; then NVAR=$(( ${#PS[@]} + ${#RAS[@]} - 1 )); else NVAR=$(( ${#PS[@]} * ${#RAS[@]} )); fi
NUNITS=$(grep -vc '^[[:space:]]*\(#\|$\)' "$UNITS")
: > "$OUT/calibrate.tsv"
snap; S0=$SNAP; t0=$(date +%s%N); sleep "$IDLE"; snap; S1=$SNAP; t1=$(date +%s%N)
read -r b_r b_w b_cpu b_iow < <(rates "$S0" "$S1" $(( (t1 - t0) / 1000 )))
read -r l1 l5 l15 _ < /proc/loadavg
FOREIGN=
if [ -d /data/progress ]; then
  FOREIGN+=$(find /data/progress -maxdepth 1 -name '*.json' -mmin -2 ! -name "${PROGJOB:-@}.json" -printf '%f ' 2>/dev/null)
fi
FOREIGN+=$(systemctl list-units --type=service,scope --state=running --no-legend 'tk*' 'alpha-*' 2>/dev/null \
  | awk '$1 !~ /^(alpha-calibrate-|alpha-board.|alpha-bus.|alpha-bus-watcher.)/{printf "%s ", $1}')
{
  echo "# calibrate.sh $(date -Is) $(hostname) units=$UNITS ($NUNITS) cmd=$CMD done=${DONE:-<нет: временные результаты>}"
  echo "# устройства: ${DEVS[*]:-?}; readahead был (КБ): $(for d in "${DEVS[@]+"${DEVS[@]}"}"; do printf '%s=%s ' "$d" $(( ${RA0[$d]:-0} / 2 )); done)"
  echo "# окно ${SECS} с, дожитие ${GRACE} с, CPUQuota ${QUOTA} %, P=${PLIST}, readahead=${RALIST}, критерий=${CRIT}, quick=${QUICK}, seed=${SEED}"
  echo "# загрузка до старта (${IDLE} с): ЦП ${b_cpu} %, iowait ${b_iow} %, диск ${b_r}/${b_w} МБ/с чт/зап, load ${l1} ${l5} ${l15}"
  [ -n "$FOREIGN" ] && echo "# ВНИМАНИЕ: идут чужие задания: $FOREIGN— замер искажён, таблица недостоверна"
} >> "$OUT/calibrate.tsv"
grep '^#' "$OUT/calibrate.tsv" | grep -v ВНИМАНИЕ | sed 's/^# \{0,1\}//'
[ -n "$FOREIGN" ] && echo ">>> ВНИМАНИЕ: на машине идёт чужой прогон ($FOREIGN) — калибровка с ним делит диск и ЦП, замер искажён <<<"
tsout $'P\tra_kb\twin_s\tdone\tcredit\tunits_min\tfailed\tkilled\tMBps_read\tMBps_write\tcpu_pct\tiowait_pct\tnote'
printf '%-4s %-8s %-6s %-6s %-9s %-6s %-6s %-9s %-9s %-6s %-7s %s\n' P ra_kb win_s done units/min fail killed MBps_rd MBps_wr cpu% iow% note

# ---------- пул единиц: случайный порядок (фиксированный seed), общий указатель на все варианты ----------
LC_ALL=C awk -v seed="$SEED" 'BEGIN{srand(seed)} /^[[:space:]]*(#|$)/{next} {printf "%.9f\t%s\n", rand(), $0}' "$UNITS" \
  | LC_ALL=C sort -t$'\t' -k1,1 | cut -f2- > "$OUT/work/pool.txt"
mapfile -t POOL < "$OUT/work/pool.txt"
NXT=0; WRAP=0; U_LINE=
next_unit() {
  local f
  while :; do
    if [ "$NXT" -ge "${#POOL[@]}" ]; then
      [ -n "$DONE" ] && return 1          # настоящий режим: сделанное не повторяем
      NXT=0; WRAP=1                       # временный режим: по кругу (кэш диска может помогать — в заметке)
    fi
    U_LINE=${POOL[NXT]}; NXT=$((NXT + 1))
    if [ -n "$DONE" ]; then read -r -a f <<<"$U_LINE"; bash -c "$DONE" _ "${f[@]}" >/dev/null 2>&1 && continue; fi
    return 0
  done
}
case $CMD in *'$'*) ;; *) CMD="$CMD \"\$@\"";; esac

pids=()
prune() { local p n=(); for p in "${pids[@]+"${pids[@]}"}"; do kill -0 "$p" 2>/dev/null && n+=("$p"); done; pids=("${n[@]+"${n[@]}"}"); }
kill_all() { local p; for p in "${pids[@]+"${pids[@]}"}"; do kill -KILL -- "-$p" 2>/dev/null; done; pids=(); wait 2>/dev/null; }
launch() {  # $1 = номер запуска, $2 = строка единицы
  local f lf=$LOGD/$1.log; read -r -a f <<<"$2"
  (
    export CAL_P=$P CAL_VARIANT=$VNAME CAL_TMP=$TMPD
    [ -n "$TMPD" ] && cd "$TMPD"
    echo "S $1 ${EPOCHREALTIME/[.,]/}" >> "$VLOG"
    bash -c "$CMD" _ "${f[@]}" > "$lf" 2>&1; rc=$?
    echo "E $1 ${EPOCHREALTIME/[.,]/} $rc" >> "$VLOG"
    [ "$rc" -eq 0 ] && rm -f "$lf"
  ) &
  pids+=($!)
}

VARIANT_NO=0; BROKEN=0; NOUNITS=0
run_variant() {  # $1 = P, $2 = cur|КБ → строка в таблицу и в calibrate.tsv
  P=$1; local ra=$2 note=() lines=0 ok bad
  VARIANT_NO=$((VARIANT_NO + 1)); VNAME="P$P-ra$ra"
  LOGD=$OUT/log/$VNAME; VLOG=$OUT/log/$VNAME.units; mkdir -p "$LOGD"; : > "$VLOG"
  TMPD=; [ -z "$DONE" ] && { TMPD=$OUT/tmp-$VNAME; mkdir -p "$TMPD"; }
  set_ra "$ra"; local wrap0=$WRAP
  progress "$((VARIANT_NO - 1))" "вариант $VARIANT_NO/$NVAR: P=$P readahead=${RA_NOW}"
  sleep 1
  snap; local s0=$SNAP; now_us; local u0=$NOW dl=$((NOW + SECS * 1000000)) uend lastp=$NOW exhausted=0
  pids=()
  while :; do
    now_us; [ "$NOW" -ge "$dl" ] && break
    if [ $((NOW - lastp)) -ge 1000000 ]; then   # раз в секунду: не падает ли команда на всех единицах подряд
      lastp=$NOW
      read -r ok bad < <(awk '$1=="E"{if($4==0)o++;else b++}END{print o+0,b+0}' "$VLOG")
      if [ "$ok" -eq 0 ] && [ "$bad" -ge "$P" ]; then BROKEN=1; break; fi
    fi
    prune
    if [ "${#pids[@]}" -lt "$P" ]; then
      if next_unit; then lines=$((lines + 1)); launch "$lines" "$U_LINE"; continue; fi
      exhausted=1; break
    fi
    sleep 0.1
  done
  now_us; uend=$NOW; snap; local s1=$SNAP
  # дожитие: единицы, начатые в окне, доделываются (≤ --grace с), остальное убивается
  local gdl=$((uend + GRACE * 1000000)) lastprog=$uend
  while :; do
    prune; [ "${#pids[@]}" -gt 0 ] || break
    now_us; [ "$NOW" -ge "$gdl" ] && break
    [ $((NOW - lastprog)) -ge 20000000 ] && { lastprog=$NOW; progress "$((VARIANT_NO - 1))" "вариант $VARIANT_NO/$NVAR: дожитие"; }
    sleep 0.2
  done
  kill_all
  [ -n "$TMPD" ] && [[ $TMPD == "$OUT"/tmp-* ]] && rm -rf -- "$TMPD"
  local win=$((uend - u0)) mbr mbw cpu iow done_ credit failed killed upm
  read -r mbr mbw cpu iow < <(rates "$s0" "$s1" "$win")
  read -r done_ credit failed killed < <(awk -v t1="$uend" '
    $1=="S"{s[$2]=$3} $1=="E"{e[$2]=$3; rc[$2]=$4}
    END{for(i in s){ if(!(i in e)){k++; continue} if(rc[i]!=0){f++; continue}
        d=e[i]-s[i]; if(e[i]<=t1){full++; cr+=1} else if(t1>s[i]&&d>0){cr+=(t1-s[i])/d} }
      printf "%d %.3f %d %d\n", full+0, cr+0, f+0, k+0}' "$VLOG")
  upm=$(awk -v c="$credit" -v w="$win" 'BEGIN{printf "%.2f", (w>0)?c/(w/6e7):0}')
  [ "$done_" -lt "$P" ] && [ "$lines" -gt 0 ] && [ "$BROKEN" = 0 ] && [ "$failed" -eq 0 ] && note+=("завершено меньше P: единицы длиннее окна, ед/мин по долям")
  [ "$killed" -gt 0 ] && note+=("убито недоделок: $killed")
  [ "$WRAP" = 1 ] && note+=("единицы по кругу (кэш)")
  [ "$exhausted" = 1 ] && note+=("единицы кончились")
  [ "$BROKEN" = 1 ] && note+=("команда падает на каждой единице: $LOGD")
  [ "$lines" -eq 0 ] && { note+=("нет единиц для запуска"); NOUNITS=1; }
  [ "$failed" -gt 0 ] && [ "$BROKEN" = 0 ] && note+=("упало: $failed, логи $LOGD")
  [ "$failed" -eq 0 ] && { rmdir "$LOGD" 2>/dev/null || true; }
  [ "$failed" -eq 0 ] && [ "$killed" -eq 0 ] && rm -f "$VLOG"
  local nt="${note[*]+"${note[*]}"}"
  tsout "$(printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' "$P" "$RA_NOW" "$((win / 1000000))" "$done_" "$credit" "$upm" "$failed" "$killed" "$mbr" "$mbw" "$cpu" "$iow" "$nt")"
  printf '%-4s %-8s %-6s %-6s %-9s %-6s %-6s %-9s %-9s %-6s %-7s %s\n' "$P" "$RA_NOW" "$((win / 1000000))" "$done_" "$upm" "$failed" "$killed" "$mbr" "$mbw" "$cpu" "$iow" "$nt"
  progress "$VARIANT_NO" "вариант $VARIANT_NO/$NVAR готов: P=$P readahead=${RA_NOW} → ${upm} ед/мин"
}

pick_best() {   # из calibrate.tsv → «P ra_kb значение критерия максимум»; в пределах 5 % от максимума — меньший P, затем больший показатель
  awk -F'\t' -v crit="$CRIT" '
    /^#/||$1=="P"{next}
    {v = (crit=="mbps") ? $9+$10 : $6; P[NR]=$1; R[NR]=$2; V[NR]=v; if(v>m) m=v}
    END{ if(m<=0){print "0 - 0 0"; exit}
         bp=""; for(i in V) if(V[i]>=0.95*m && (bp==""||P[i]+0<bp+0||(P[i]+0==bp+0&&V[i]>bv))){bp=P[i];br=R[i];bv=V[i]}
         print bp, br, bv, m}' "$OUT/calibrate.tsv"
}

cleanup() {
  trap - EXIT INT TERM HUP
  kill_all
  [ -n "${TMPD:-}" ] && [[ $TMPD == "$OUT"/tmp-* ]] && rm -rf -- "$TMPD"
  restore_ra
}
trap cleanup EXIT; trap 'exit 130' INT; trap 'exit 143' TERM; trap 'exit 129' HUP

# ---------- варианты ----------
if [ "$QUICK" = 1 ]; then
  for p in "${PS[@]}"; do run_variant "$p" "${RAS[0]}"; [ "$BROKEN" = 1 ] || [ "$NOUNITS" = 1 ] && break; done
  if [ "$BROKEN" = 0 ] && [ "$NOUNITS" = 0 ] && [ ${#RAS[@]} -gt 1 ]; then
    read -r bp _ < <(pick_best)
    for r in "${RAS[@]:1}"; do run_variant "$bp" "$r"; [ "$BROKEN" = 1 ] || [ "$NOUNITS" = 1 ] && break; done
  fi
else
  for r in "${RAS[@]}"; do
    for p in "${PS[@]}"; do run_variant "$p" "$r"; [ "$BROKEN" = 1 ] || [ "$NOUNITS" = 1 ] && break 2; done
  done
fi

# ---------- итог ----------
if [ "$BROKEN" = 1 ]; then
  echo "лучший: не определён — команда падает на каждой единице (логи в $OUT/log/)"; tsout "# лучший: не определён — команда падает"; exit 3
fi
read -r bp bra bv bmax < <(pick_best)
if [ "$bp" = 0 ] && [ "$CRIT" = units ]; then   # ни одна единица не завершилась — единицы длиннее окна: берём по МБ/с
  CRIT=mbps; echo "ни одной завершённой единицы ни в одном варианте — критерий переключён на МБ/с (--secs/--grace больше или единицы меньше)"
  tsout "# критерий переключён на mbps: ед/мин = 0 во всех вариантах"; read -r bp bra bv bmax < <(pick_best)
fi
if [ "$bp" = 0 ]; then
  echo "лучший: не определён (нет рабочих вариантов)"; tsout "# лучший: не определён"; exit 3
fi
unit=ед/мин; [ "$CRIT" = mbps ] && unit=МБ/с
res="лучший: P=$bp, readahead=$bra КБ (критерий $CRIT: $bv $unit при максимуме $bmax, равенство ±5 % → меньший P)"
echo "$res"; tsout "# $res"
[ "$RA_OK" = 1 ] && echo "для боевого прогона: blockdev --setra $((bra * 2)) /dev/${DEVS[0]} (после — вернуть ${RA0[${DEVS[0]}]}); в записи тикета — таблица $OUT/calibrate.tsv"
[ -n "$DONE" ] && echo "результаты калибровки остались настоящими (--done): повторный запуск их пропустит"
exit 0
