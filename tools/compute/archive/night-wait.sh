#!/usr/bin/env bash
# Ночная сетка уступает дневному счёту (владелец 27.09, устав `de1a779`: ночь — только наблюдение, В-115;
# правило «к 01:50 UTC свои задания остановить» снято). ExecStartPre юнита `alpha-grid-nightly` на Steam Deck:
# пока на машине идёт чей-то `lob bounce-grid` (ночь ещё не стартовала — значит, чужой), ждать; потолок —
# NIGHT_WAIT_UNTIL (UTC, по умолчанию 08:00 = 12:00 GMT+4), дальше ночь стартует всё равно. Лог —
# ~/alpha/sync/night-wait-<сутки UTC>.log. Код всегда 0: ожидание не должно ронять ночь.
# Когда встанет общая встройка слотов (`grid-slot.sh`, О5) в скрипты — этот шаг заменяется ею.
set -u
UNTIL="${NIGHT_WAIT_UNTIL:-08:00}"
POLL="${NIGHT_WAIT_POLL:-60}"
LOG="${ALPHA_HOME:-$HOME/alpha}/sync/night-wait-$(date -u +%F).log"
deadline=$(date -u -d "today $UNTIL" +%s)
[ "$deadline" -le "$(date -u +%s)" ] && deadline=$(date -u -d "tomorrow $UNTIL" +%s)
said=""
while pgrep -f "lob bounce-grid" >/dev/null; do
  if [ "$(date -u +%s)" -ge "$deadline" ]; then
    echo "$(date -u +%FT%TZ) потолок $UNTIL UTC — ночь стартует при идущем счёте: $(pgrep -fc 'lob bounce-grid') процесс(ов)" >> "$LOG"
    exit 0
  fi
  [ -n "$said" ] || { echo "$(date -u +%FT%TZ) жду: идёт bounce-grid ($(pgrep -fc 'lob bounce-grid') процесс(ов))" >> "$LOG"; said=1; }
  sleep "$POLL"
done
[ -n "$said" ] && echo "$(date -u +%FT%TZ) свободно — ночь стартует" >> "$LOG"
exit 0
