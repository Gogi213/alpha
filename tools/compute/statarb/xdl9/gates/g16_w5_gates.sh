#!/usr/bin/env bash
# TK-493 w5, G16: гейт «байт в байт» нового бинаря ядра против базового. Запуск на Steam Deck. Скрипты — g6a_bench_digest.sh, g6b_run_vs_tk486.sh,
# g11_w3_flags.sh (с переопределениями P_DIR / F_DIR / G6A_FORM / G6B_JOBS_DIR / G11_JOBS / BIN_A / BIN_O), без других правок.
#   g16_w5_gates.sh <проверяемый бинарь> <базовый бинарь> <метка> [потоки=4] [шаги=g6a,g6b,g11]
# Для не-PGO нового исходника базовый — 4ef45cc8 (флаги ядра выключены, выводы побайтно прежние); для PGO-сборки нового исходника базовый —
# не-PGO того же исходника. Базовый и проверяемый гоняются в одном прогоне на одних входах, сверка внутри скриптов и по sha256 всех csv.
# ОКНО — ТОЛЬКО ФОРМИРОВАНИЕ (аудит M1-02): панели и ставки — копии prep_pform.py, обрезанные до суток < 2479 (15.10.2026 00:00 UTC), задания G6б/G11 —
# сутки 2465..2478. Полные панели ~/xdl9/panels и сырьё ~/xdl/out не читаются: торговое окно в процесс не попадает.
#   P_ROOT   корень копий панелей (по умолчанию ~/xdl9/pgo/pform), F_ROOT — копии ставок (~/xdl9/pgo/pfund), JOBS — задания G6б (~/xdl9/gates/g6b)
set -u
BIN=${1:?проверяемый бинарь}; BASE=${2:?базовый бинарь}; TAG=${3:?метка}; TH=${4:-4}; STEPS=${5:-g6a,g6b,g11}
S=${GATES_SRC:-$(cd "$(dirname "$0")" && pwd)}
PF=${P_ROOT:-$HOME/xdl9/pgo/pform}; FF=${F_ROOT:-$HOME/xdl9/pgo/pfund}; JOBS=${JOBS:-$HOME/xdl9/gates/g6b}
H=${W5_DIR:-$HOME/xdl9/w5}/gates_$TAG; mkdir -p "$H"
has() { case ",$STEPS," in *",$1,"*) return 0;; esac; return 1; }
rc=0
echo "G16 $TAG: бинарь $BIN ($(sha256sum "$BIN" | cut -c1-16)), база $BASE ($(sha256sum "$BASE" | cut -c1-16)), потоков $TH, панели $PF, нагрузка $(cat /proc/loadavg)"

if has g6a; then
  BIN_A=$BASE BIN_N=$BIN G6A_FORM=$PF F_DIR=$FF G6A_DIR=$H/g6 bash "$S/g6a_bench_digest.sh" "$TH" > "$H/g6a.log" 2>&1; r=$?
  tail -2 "$H/g6a.log"; [ "$r" = 0 ] || { echo "РАЗНО G6а: скрипт rc=$r"; rc=1; }
  # сводка наполнения: задания и сделки по случаям (пустой случай ничего не доказывает)
  for f in "$H"/g6/g6a/*.N.cmp; do
    b=$(basename "$f" .N.cmp); case "$b" in *.th*) continue;; esac
    printf '  %-16s %s\n' "$b" "$(grep -o 'заданий [0-9]* из [0-9]*, минут [0-9]*, пропусков [0-9]*, сделок [0-9]*' "$f")"
  done
  if grep -q 'заданий 0 из' "$H"/g6/g6a/*.N.cmp; then echo "ВНИМАНИЕ G6а: есть случаи с нулём заданий (панели набора не пересеклись)"; fi
fi
if has g6b; then
  BIN_O=$BASE BIN_N=$BIN P_DIR=$PF G6B_JOBS_DIR=$JOBS G6B_DIR=$H/g6b bash "$S/g6b_run_vs_tk486.sh" "$TH" > "$H/g6b.log" 2>&1; r=$?
  tail -2 "$H/g6b.log"; [ "$r" = 0 ] || { echo "РАЗНО G6б: скрипт rc=$r"; rc=1; }
  grep -c '^РАВНО' "$H/g6b.log" | sed 's/^/  наборов РАВНО: /'
fi
if has g11; then
  BIN_N=$BIN BIN_O=$BASE P_DIR=$PF G11_JOBS=$JOBS G11_DIR=$H/g11 bash "$S/g11_w3_flags.sh" "$TH" > "$H/g11.log" 2>&1; r=$?
  tail -1 "$H/g11.log"; [ "$r" = 0 ] || { echo "РАЗНО G11: скрипт rc=$r"; rc=1; }
  # тот же G11 базовым бинарём в отдельный каталог; затем побайтное сравнение ВСЕХ csv (daily, trades, ext, job-day; --rearm и без)
  BIN_N=$BASE BIN_O=$BASE P_DIR=$PF G11_JOBS=$JOBS G11_DIR=$H/g11base bash "$S/g11_w3_flags.sh" "$TH" > "$H/g11base.log" 2>&1
  ( cd "$H/g11" && find . -name '*.csv' | sort | xargs sha256sum ) > "$H/g11.sha256"
  ( cd "$H/g11base" && find . -name '*.csv' | sort | xargs sha256sum ) > "$H/g11base.sha256"
  nf=$(wc -l < "$H/g11.sha256")
  if cmp -s "$H/g11.sha256" "$H/g11base.sha256" && [ "$nf" -gt 0 ]; then echo "РАВНО G11: все $nf csv побайтно = выходам базового бинаря"; else echo "РАЗНО G11: csv != база"; diff "$H/g11.sha256" "$H/g11base.sha256" | head; rc=1; fi
  if diff <(grep -E '^(РАВНО|РАЗНО|  \(4\))' "$H/g11.log") <(grep -E '^(РАВНО|РАЗНО|  \(4\))' "$H/g11base.log") > /dev/null; then echo "РАВНО G11: строки итогов (1)-(4) лога совпадают с логом базы"; else echo "РАЗНО G11: итоги лога"; rc=1; fi
fi
echo "ИТОГ G16 $TAG: код $rc (0 — расхождений нет)"
exit $rc
