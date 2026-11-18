#!/usr/bin/env bash
# П-42 §13.21 (TK-595): запуск прогона стадии перетитровки — тонкая обёртка над runentry.sh (srv, systemd-run, возврат сразу; ожидание — tools/srv-wait.sh).
# Использование: [P42_DETAIL=1] [P42_FTAB=<csv кандидатов признаков на srv>] bash tools/p42/retitr_run.sh <NAME> <файл вариантов> <бинарь на srv>
#   файл вариантов — из `retitr_cells.py emit … [--sig]` (строки E/T вместе с сигнальными двойниками `<id>_sig` — один прогон);
#   P42_FTAB добавляет в командную строку движка `--ftab <путь>` (для клеток F с ключами fa/fo/fv); P42_DETAIL, P42_SIGS проходят в runentry.sh как есть.
# Обёртка ничего не считает сама: проверяет файл вариантов (id уникальны, стадия F ↔ P42_FTAB, сигнальные двойники парны) и вызывает runentry.sh.
set -euo pipefail
NAME="${1:?имя прогона}"; VF="${2:?файл вариантов}"; BIN="${3:?бинарь на srv}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -s "$VF" ] || { echo "нет файла вариантов или он пуст: $VF" >&2; exit 2; }
ids="$(grep -v '^[[:space:]]*#' "$VF" | awk 'NF{print $1}')"
dup="$(printf '%s\n' "$ids" | sort | uniq -d | head -3)"
[ -z "$dup" ] || { echo "повторяющиеся id вариантов: $dup" >&2; exit 2; }
if grep -qE '(^|[[:space:]])fa=' "$VF" && [ -z "${P42_FTAB:-}" ]; then
  echo "в $VF есть ключи fa=… (клетки F), но P42_FTAB не задан — движку нужна таблица кандидатов (--ftab)" >&2; exit 2
fi
if [ -n "${P42_FTAB:-}" ]; then
  case "$P42_FTAB" in /*) ;; *) echo "P42_FTAB — абсолютный путь на srv: $P42_FTAB" >&2; exit 2;; esac
fi
# сигнальные двойники: каждому `<id>_sig` (кроме <L|S>_base_sig) соответствует портфельная клетка <id>, если файл не «только двойники»
sig="$(printf '%s\n' "$ids" | grep -c '_sig$' || true)"; tot="$(printf '%s\n' "$ids" | wc -l)"
if [ "$sig" -gt 0 ] && [ "$sig" -lt "$tot" ]; then
  while read -r s; do
    c="${s%_sig}"
    printf '%s\n' "$ids" | grep -qx "$c" || { echo "у двойника $s нет портфельной клетки $c в $VF" >&2; exit 2; }
  done < <(printf '%s\n' "$ids" | grep '_sig$')
fi
echo "retitr_run: $NAME — $tot вариантов ($sig сигнальных двойников)${P42_FTAB:+, --ftab $P42_FTAB}"
# v21 (сетка 80 суток) читает кэш `cache80` (старый кэш на 74 суток движок отвергает); P42_CACHE переопределяет
export P42_CACHE="${P42_CACHE:-/data/p42-t566/cache80}"
exec bash "$HERE/runentry.sh" "$NAME" "$VF" "$BIN"
