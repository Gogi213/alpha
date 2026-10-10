#!/usr/bin/env bash
# TK-018/TK-019: дека в трэше (своп 8,4/8,4) — воркеры пула p07-all-jul-read.py (mp.Pool, живут до конца) придерживаются
# SIGSTOP-ом (убить нельзя: imap потеряет задачу и повиснет). Страницы стоящих уходят в своп по LRU, идущие получают RAM.
# Отпуск (SIGCONT по одному): MemAvailable > 2,5 ГБ или все идущие простаивают (пул кончил их задачи, ждёт стоящих).
# Новый стоп (не больше 3 стоящих): MemAvailable < 600 МБ и своп свободен < 300 МБ при ≥ 4 идущих. Конец — нет воркеров.
set -u
say() { echo "== $(date -u +%FT%TZ) $*"; }
mem() { awk -v k="$1" '$1==k":"{print $2}' /proc/meminfo; }
cpu() { awk '{print $14+$15}' "/proc/$1/stat" 2>/dev/null || echo 0; }
declare -A last
while :; do
  par=$(pgrep -of 'p07-all-jul-read.py') || { say "чтения нет — конец"; exit 0; }
  ws=$(pgrep -P "$par" -f 'p07-all-jul-read.py'); [ -n "$ws" ] || { sleep 20; continue; }
  stopped=(); running=(); idle=1
  for p in $ws; do
    st=$(awk '{print $3}' "/proc/$p/stat" 2>/dev/null) || continue
    if [ "$st" = T ]; then stopped+=("$p"); else
      running+=("$p"); c=$(cpu "$p"); [ "${last[$p]:-x}" != "$c" ] && idle=0; last[$p]=$c; fi
  done
  av=$(mem MemAvailable); sf=$(mem SwapFree)
  if [ ${#stopped[@]} -gt 0 ] && { [ "$av" -gt 2621440 ] || [ "$idle" = 1 ]; }; then
    kill -CONT "${stopped[0]}" && say "SIGCONT ${stopped[0]} (MemAvailable ${av} кБ, простой идущих ${idle})"
  elif [ ${#stopped[@]} -lt 3 ] && [ ${#running[@]} -ge 4 ] && [ "$av" -lt 614400 ] && [ "$sf" -lt 307200 ]; then
    big=$(for p in "${running[@]}"; do echo "$(awk '/^VmRSS/{print $2}' /proc/$p/status 2>/dev/null || echo 0) $p"; done | sort -rn | head -1 | cut -d' ' -f2)
    kill -STOP "$big" && say "SIGSTOP $big (MemAvailable ${av} кБ, своп свободен ${sf} кБ, стоят $(( ${#stopped[@]} + 1 )))"
  fi
  sleep 20
done
