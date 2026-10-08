#!/bin/sh
# Сопоставление «задание alsched → тикет» (TK-092): ОДИН источник для сторожа жизни плагина и табло (RPV_JOB_OWNERS_CMD).
# Строка на задание, поля через TAB: id, тикет (TK-NNN из имени заявки tkNNN-…, иначе пусто), юнит systemd, состояние
# running|queued|done|failed. Берутся не завершённые и завершённые за последний час. Только чтение.
d=${SCHED_DIR:-/data/sched}; now=$(date +%s)
for f in $(ls -t "$d"/jobs/*.json 2>/dev/null | head -80); do
  id=$(basename "$f" .json)
  g() { grep -o "\"$1\": \"[^\"]*\"" "$f" | head -1 | cut -d'"' -f4; }
  st=$(g state); name=$(g name); cls=$(g cls)
  rc=$(grep -o '"rc": -\{0,1\}[0-9]*' "$f" | head -1 | grep -o -- '-\{0,1\}[0-9]*$')
  te=$(grep -o '"t_end": [0-9]*' "$f" | head -1 | grep -o '[0-9]*$')
  case "$st" in
    queued|running) ;;
    done) [ -n "$te" ] && [ $((now - te)) -gt 3600 ] && continue
          [ "${rc:-0}" = 0 ] || st=failed ;;
    *) continue ;;
  esac
  n=$(echo "$name" | sed -n 's/^tk0*\([0-9][0-9]*\).*/\1/p')
  tk=; [ -n "$n" ] && tk=$(printf 'TK-%03d' "$n")
  pre=tk0s-; [ "$cls" = measure ] && pre=alpha-sm-
  printf '%s\t%s\t%s%s-%s\t%s\n' "$id" "$tk" "$pre" "$name" "$id" "$st"
done
