#!/usr/bin/env bash
# Демон очереди счёта Steam Deck `alpha-gridq` (CEO 27.09, владелец: «загрузка стимдек на 90 %», предохранители —
# скриптами, не памятью ролей). Раз в GRIDQ_TICK с берёт задания из queue/pending (кладёт `q-add.sh`) и запускает
# их юнитами `systemd-run --user` в срезе alpha-q.slice, пока одновременно:
#   (а) занято < GRIDQ_CORES ядер (8 из 8 — владелец 27.09: «повышай до 95 %») — считаются и чужие `lob bounce-grid` вне очереди, по их --threads;
#   (б) MemAvailable − резерв GRIDQ_RESERVE_MB − недобор идущих заданий до их оценки ≥ оценка задания; своп не
#       растёт (упал SwapFree — запуски стоят GRIDQ_SWAP_HOLD с), у задания MemorySwapMax=0;
#   (в) ночь не идёт (alpha-grid-nightly active — после своего ожидания), метка study/.grid-slots/night свободна
#       (её берут ночь и гейты), нет queue/HOLD. Идущие задания доживают — уступают только новые запуски.
# Голова очереди, которой не хватает памяти, дольше GRIDQ_RESERVE_AFTER с не обгоняется мелкими (резерв).
# Итог задания — done/ или failed/ (JOB_RC, JOB_PEAK_MB, JOB_OOM); OOM — один повтор с оценкой ×2; пик пишется в
# queue/peaks.tsv (по каталогу --root) — следующая оценка тех же суток не меньше его.
# Сторож: очередь не пуста, а load1 < GRIDQ_UNDERLOAD дольше 15 мин → queue/ALERT-underload (строка причины);
# очередь пуста, ничего не идёт, load1 < 2 дольше 30 мин → queue/ALERT-idle-deck; метки снимаются сами.
# Повторная работа (подроль «производственная эффективность», В-132): каждый замеченный на машине `lob bounce-grid`
# (свой и чужой) — строка в queue/seen.tsv (время, pid, сутки --root, серия --out-dir); одни сутки, разобранные
# ≥ 2 разными процессами за 24 ч, → queue/ALERT-rework (строка: сколько раз и какие сутки), снимается сама.
# Состояние — queue/STATUS (переписывается каждый такт), журнал — queue/gridq.log.
set -uo pipefail
Q="${GRIDQ_DIR:-$HOME/alpha/queue}"
A="${ALPHA_HOME:-$HOME/alpha}"
BIN_DIR="${GRIDQ_BIN:-$A/bin}"
CORES="${GRIDQ_CORES:-8}"
RESERVE="${GRIDQ_RESERVE_MB:-1536}"
TICK="${GRIDQ_TICK:-10}"
SWAP_HOLD="${GRIDQ_SWAP_HOLD:-300}"
RESERVE_AFTER="${GRIDQ_RESERVE_AFTER:-1200}"
UNDERLOAD="${GRIDQ_UNDERLOAD:-6}"
UNDER_SECS="${GRIDQ_UNDER_SECS:-900}"
IDLE_SECS="${GRIDQ_IDLE_SECS:-1800}"
NIGHT_UNIT="${GRIDQ_NIGHT_UNIT:-alpha-grid-nightly.service}"
NIGHT_LOCK="${GRIDQ_NIGHT_LOCK:-$A/study/.grid-slots/night}"
SLICE="${GRIDQ_SLICE:-alpha-q.slice}"
if [ "$(id -u)" = 0 ]; then SCTL=(systemctl); SRUN=(systemd-run); else SCTL=(systemctl --user); SRUN=(systemd-run --user); fi
mkdir -p "$Q"/{pending,running,done,failed,logs}
LOG="$Q/gridq.log"
say() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

threads_of() { local prev="" a n=1; for a in "$@"; do [ "$prev" = --threads ] && n="$a"; prev="$a"; done; echo "$n"; }
root_of() { local prev="" a; for a in "$@"; do [ "$prev" = --root ] && echo "$a"; prev="$a"; done; }
kv() { awk -F= -v k="$1" '$1 == k {v = substr($0, length(k) + 2)} END {print v}' "$2"; }
unit_of() { echo "gridq-$(basename "$1" .job)"; }
mem_now_mb() {  # текущая память юнита задания, МБ
  local cg; cg=$("${SCTL[@]}" show -p ControlGroup --value "$(unit_of "$1")" 2>/dev/null)
  [ -n "$cg" ] && [ -f "/sys/fs/cgroup$cg/memory.current" ] && echo $(( $(cat "/sys/fs/cgroup$cg/memory.current") / 1048576 )) || echo 0
}

reap() {
  local j rc peak oom st now start
  for j in "$Q"/running/*.job; do
    [ -e "$j" ] || continue
    if [ -f "${j%.job}.rc" ]; then
      read -r rc peak oom < "${j%.job}.rc"; rm -f "${j%.job}.rc"
      peak=$(( ${peak:-0} / 1048576 ))
      # shellcheck disable=SC1090
      ( source "$j"; r=$(root_of "${JOB_CMD[@]}")
        [ -n "$r" ] && [ "$peak" -gt 0 ] && { case "$r" in /*) ;; *) r="$JOB_HOME/$r";; esac
          printf '%s\t%s\t%s\n' "$(readlink -f "$r")" "$peak" "$JOB_TAG" >> "$Q/peaks.tsv"; } )
      printf 'JOB_RC=%s\nJOB_PEAK_MB=%s\nJOB_OOM=%s\nJOB_END=%s\n' "$rc" "$peak" "$oom" "$(date -u +%FT%TZ)" >> "$j"
      if [ "$rc" = 0 ]; then st=done
      elif [ "${oom:-0}" -gt 0 ] && [ "$(kv JOB_RETRY "$j")" = 0 ]; then
        local m; m=$(kv JOB_MEM_MB "$j"); m=$(( m * 2 > peak * 3 / 2 ? m * 2 : peak * 3 / 2 ))
        sed -i -e "s/^JOB_MEM_MB=.*/JOB_MEM_MB=$m/" -e 's/^JOB_RETRY=0/JOB_RETRY=1/' \
          -e '/^JOB_RC=/d' -e '/^JOB_PEAK_MB=/d' -e '/^JOB_OOM=/d' -e '/^JOB_END=/d' -e '/^JOB_START=/d' "$j"
        mv "$j" "$Q/pending/"; say "OOM $(basename "$j" .job): пик $peak МБ — повтор с оценкой $m МБ"; continue
      else st=failed; fi
      mv "$j" "$Q/$st/"; say "$st $(basename "$j" .job): rc $rc, пик $peak МБ, oom $oom"
    elif ! "${SCTL[@]}" is-active -q "$(unit_of "$j")"; then
      start=$(date -u -d "$(kv JOB_START "$j")" +%s 2>/dev/null || echo 0); now=$(date -u +%s)
      if [ $(( now - start )) -gt 30 ]; then
        printf 'JOB_RC=255\nJOB_PEAK_MB=0\nJOB_OOM=0\nJOB_END=%s\nJOB_NOTE=юнит пропал без итога\n' "$(date -u +%FT%TZ)" >> "$j"
        mv "$j" "$Q/failed/"; say "failed $(basename "$j" .job): юнит пропал без итога"
      fi
    fi
  done
}

night_busy() {
  [ -f "$Q/HOLD" ] && { echo "HOLD"; return 0; }
  [ "$("${SCTL[@]}" show -p ActiveState --value "$NIGHT_UNIT" 2>/dev/null)" = active ] && { echo "ночь идёт"; return 0; }
  if [ -e "$NIGHT_LOCK" ] && ! flock -n "$NIGHT_LOCK" true 2>/dev/null; then echo "метка ночи/гейта"; return 0; fi
  return 1
}

cores_used() {  # свои задания по --threads + чужие bounce-grid
  local n=0 j p
  for j in "$Q"/running/*.job; do
    [ -e "$j" ] || continue
    # shellcheck disable=SC1090
    n=$(( n + $(source "$j"; threads_of "${JOB_CMD[@]}") ))
  done
  for p in $(pgrep -f "lob bounce-grid" 2>/dev/null); do
    grep -q "gridq-" "/proc/$p/cgroup" 2>/dev/null && continue
    mapfile -d '' -t args < "/proc/$p/cmdline" 2>/dev/null || continue
    # сам бинарник (`<alpha…> lob bounce-grid …`), а не обёртки, в чьей строке эти слова (q-add, bash -c, …)
    [ "${args[1]:-}" = lob ] && [ "${args[2]:-}" = bounce-grid ] || continue
    n=$(( n + $(threads_of "${args[@]}") ))
  done
  echo "$n"
}

mem_free_mb() {  # MemAvailable − резерв − недобор идущих до их оценки
  local avail j est cur gap=0
  avail=$(( $(awk '/^MemAvailable:/ {print $2}' /proc/meminfo) / 1024 ))
  for j in "$Q"/running/*.job; do
    [ -e "$j" ] || continue
    est=$(kv JOB_MEM_MB "$j"); cur=$(mem_now_mb "$j")
    [ "$est" -gt "$cur" ] && gap=$(( gap + est - cur ))
  done
  echo $(( avail - RESERVE - gap ))
}

track_grids() {  # новые процессы bounce-grid → seen.tsv: время, pid:старт, сутки (путь --root), --out-dir
  local p st args root out cwd
  for p in $(pgrep -f "lob bounce-grid" 2>/dev/null); do
    mapfile -d '' -t args < "/proc/$p/cmdline" 2>/dev/null || continue
    [ "${args[1]:-}" = lob ] && [ "${args[2]:-}" = bounce-grid ] || continue
    st=$(awk '{print $22}' "/proc/$p/stat" 2>/dev/null) || continue
    grep -q -- "	$p:$st	" "$Q/seen.tsv" 2>/dev/null && continue
    cwd=$(readlink "/proc/$p/cwd" 2>/dev/null); root=$(root_of "${args[@]}")
    case "$root" in /*) ;; *) root="$cwd/$root";; esac
    out=""; local prev="" a; for a in "${args[@]}"; do [ "$prev" = --out-dir ] && out="$a"; prev="$a"; done
    printf '%s\t%s:%s\t%s\t%s\n' "$(date -u +%s)" "$p" "$st" "$(readlink -f "$root" 2>/dev/null || echo "$root")" "$out" >> "$Q/seen.tsv"
  done
}

rework_check() {  # сутки, разобранные ≥ 2 процессами за 24 ч → ALERT-rework
  # Пробы и гейты (CEO 27.09) — не повторная работа: --out-dir в рабочем каталоге `tmp-*` или со словом gate/probe.
  local since line
  since=$(( $(date -u +%s) - 86400 ))
  line=$(awk -F'\t' -v s="$since" '$1 >= s && $4 !~ /(^|\/)tmp-|gate|probe/ {n[$3]++} END {
      for (r in n) if (n[r] >= 2) {k++; t += n[r]; if (n[r] > m) {m = n[r]; w = r}}
      if (k) printf "%d суток разобраны повторно за 24 ч (всего разборов %d); больше всех — %s: %d раз", k, t, w, m }' \
    "$Q/seen.tsv" 2>/dev/null)
  if [ -n "$line" ]; then
    [ -f "$Q/ALERT-rework" ] || say "ТРЕВОГА повторная работа: $line"
    echo "$(date -u +%FT%TZ) $line" > "$Q/ALERT-rework"
  elif [ -f "$Q/ALERT-rework" ]; then rm -f "$Q/ALERT-rework"; say "повторная работа снята"; fi
}

launch() {
  local j="$1" id unit est max total
  id=$(basename "$j" .job); unit="gridq-$id"; est=$(kv JOB_MEM_MB "$j")
  total=$(( $(awk '/^MemTotal:/ {print $2}' /proc/meminfo) / 1024 ))
  max=$(( est * 2 > est + 2048 ? est * 2 : est + 2048 )); [ "$max" -gt $(( total - 2048 )) ] && max=$(( total - 2048 ))
  mv "$j" "$Q/running/$id.job" || return 1
  echo "JOB_START=$(date -u +%FT%TZ)" >> "$Q/running/$id.job"
  if "${SRUN[@]}" --quiet --unit="$unit" --collect --slice="$SLICE" -p MemorySwapMax=0 -p MemoryMax="${max}M" \
      -p OOMPolicy=continue -p Nice=10 /bin/bash "$BIN_DIR/gridq-run.sh" "$Q/running/$id.job"; then
    say "start $id: оценка $est МБ, потолок $max МБ, ядер $(source "$Q/running/$id.job"; threads_of "${JOB_CMD[@]}")"
  else
    printf 'JOB_RC=254\nJOB_END=%s\nJOB_NOTE=systemd-run отказал\n' "$(date -u +%FT%TZ)" >> "$Q/running/$id.job"
    mv "$Q/running/$id.job" "$Q/failed/"; say "failed $id: systemd-run отказал"
  fi
}

reason="—"; swap_prev=""; swap_hold_until=0; under_since=""; idle_since=""; rework_at=0
say "демон стартовал: ядер $CORES, резерв $RESERVE МБ, такт $TICK с"
while :; do
  reap
  now=$(date -u +%s)
  swap_free=$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)
  if [ -n "$swap_prev" ] && [ $(( swap_prev - swap_free )) -gt 65536 ]; then
    swap_hold_until=$(( now + SWAP_HOLD )); say "своп растёт ($(( (swap_prev - swap_free) / 1024 )) МБ за такт) — запуски стоят ${SWAP_HOLD} с"
  fi
  swap_prev=$swap_free
  mapfile -t pend < <(ls "$Q/pending" 2>/dev/null | grep '\.job$' | sort)
  if [ ${#pend[@]} -eq 0 ]; then reason="очередь пуста"
  elif nb=$(night_busy); then reason="уступаю: $nb"
  elif [ "$now" -lt "$swap_hold_until" ]; then reason="своп рос — пауза до $(date -u -d "@$swap_hold_until" +%T)"
  else
    reason="—"; first=1
    for name in "${pend[@]}"; do
      j="$Q/pending/$name"; [ -f "$j" ] || continue
      used=$(cores_used); [ "$used" -ge "$CORES" ] && { reason="ядра заняты ($used из $CORES)"; break; }
      # shellcheck disable=SC1090
      need=$(source "$j"; threads_of "${JOB_CMD[@]}"); est=$(kv JOB_MEM_MB "$j"); free=$(mem_free_mb)
      if [ $(( used + need )) -le "$CORES" ] && [ "$est" -le "$free" ]; then launch "$j"; first=0; continue; fi
      if [ "$est" -gt "$free" ]; then reason="память: голове нужно $est МБ, свободно под задания $free МБ"
      else reason="ядра: нужно $need, занято $used из $CORES"; fi
      if [ "$first" = 1 ]; then
        added=$(date -u -d "$(kv JOB_ADDED "$j")" +%s 2>/dev/null || echo "$now")
        [ $(( now - added )) -ge "$RESERVE_AFTER" ] && { reason="$reason (резерв голове — мелкие не обгоняют)"; break; }
      fi
      first=0
    done
  fi
  # повторная работа: процессы — каждый такт, проверка — раз в 5 мин
  track_grids
  [ "$now" -ge "$rework_at" ] && { rework_check; rework_at=$(( now + 300 )); }
  # сторож
  load1=$(cut -d' ' -f1 /proc/loadavg)
  np=$(ls "$Q/pending" | grep -c '\.job$'); nr=$(ls "$Q/running" | grep -c '\.job$')
  # недогруз — только если ядра не заняты: сразу после запусков load1 ещё догоняет
  if [ "$np" -gt 0 ] && [ "$(cores_used)" -lt "$CORES" ] && awk -v l="$load1" -v u="$UNDERLOAD" 'BEGIN {exit !(l < u)}'; then
    under_since=${under_since:-$now}
    if [ $(( now - under_since )) -ge "$UNDER_SECS" ] && [ ! -f "$Q/ALERT-underload" ]; then
      echo "$(date -u +%FT%TZ) очередь $np, идёт $nr, load1 $load1 < $UNDERLOAD дольше 15 мин; причина: $reason" > "$Q/ALERT-underload"
      say "ТРЕВОГА недогруз: $(cat "$Q/ALERT-underload")"
    fi
  else under_since=""; [ -f "$Q/ALERT-underload" ] && { rm -f "$Q/ALERT-underload"; say "недогруз снят"; }; fi
  if [ "$np" -eq 0 ] && [ "$nr" -eq 0 ] && awk -v l="$load1" 'BEGIN {exit !(l < 2)}'; then
    idle_since=${idle_since:-$now}
    if [ $(( now - idle_since )) -ge "$IDLE_SECS" ] && [ ! -f "$Q/ALERT-idle-deck" ]; then
      echo "$(date -u +%FT%TZ) очередь пуста, заданий нет, load1 $load1 — простой дольше 30 мин" > "$Q/ALERT-idle-deck"
      say "ТРЕВОГА простой: $(cat "$Q/ALERT-idle-deck")"
    fi
  else idle_since=""; [ -f "$Q/ALERT-idle-deck" ] && { rm -f "$Q/ALERT-idle-deck"; say "простой снят"; }; fi
  # состояние
  {
    echo "обновлено $(date -u +%FT%TZ) · load1 $load1 · ядер занято $(cores_used) из $CORES · под задания свободно $(mem_free_mb) МБ · своп свободен $(( swap_free / 1024 )) МБ"
    echo "в очереди $np · идёт $nr · готово $(ls "$Q/done" | grep -c '\.job$') · упало $(ls "$Q/failed" | grep -c '\.job$') · не запускаю: $reason"
    for d in pending running done failed; do
      ls "$Q/$d" | grep '\.job$' | sed -E 's/^[0-9]-[0-9]+-(.*)-[0-9]+\.job$/\1/' | sort | uniq -c | awk -v d="$d" '{printf "  %s %s: %d\n", d, $2, $1}'
    done
    for j in "$Q"/running/*.job; do
      [ -e "$j" ] || continue
      echo "  идёт $(basename "$j" .job): с $(kv JOB_START "$j"), память $(mem_now_mb "$j") МБ из оценки $(kv JOB_MEM_MB "$j")"
    done
    for a in "$Q"/ALERT-*; do [ -e "$a" ] && echo "  $(basename "$a"): $(cat "$a")"; done
    true
  } > "$Q/STATUS.tmp"; mv "$Q/STATUS.tmp" "$Q/STATUS"
  sleep "$TICK"
done
