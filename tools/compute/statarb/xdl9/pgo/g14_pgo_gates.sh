#!/usr/bin/env bash
# TK-493 pgo, G14: гейт «байт в байт» для PGO-бинаря. Запуск на Steam Deck. Скрипты — gates/g6a_bench_digest.sh, g6b_run_vs_tk486.sh, g11_w3_flags.sh
# без правок, с BIN_N = проверяемый бинарь; выходы сравниваются с эталоном не-PGO 4ef45cc8:
#   G6а: sha256-cmp.txt нового = закоммиченный g6a_sha256_cmp.txt (прогон 4ef45cc8);
#   G6б: sha256-new.txt нового = закоммиченный g6b_sha256_new.txt (прогон 4ef45cc8), sha256-tk486.txt = выводам tk486;
#   G11: тот же скрипт ещё раз с BIN_N = эталон 4ef45cc8, затем побайтное сравнение ВСЕХ csv обоих каталогов (daily, trades, ext, jd; --rearm и без).
# Окно — формирование (скрипты ограничены заданиями 2465..2478); исключение — G6а: bench читает панели целиком (у него нет ограничения суток),
# выход bench --digest — хеш и счётчики, не P&L; прогон повторяет закоммиченный G6а.
#   g14_pgo_gates.sh <проверяемый бинарь> <метка> <каталог эталонных списков (g6a_sha256_cmp.txt, g6b_sha256_new.txt)> [потоки=4] [шаги=g6a,g6b,g11]
set -u
BIN=${1:?бинарь}; TAG=${2:?метка}; REF=${3:?каталог эталонов}; TH=${4:-4}; STEPS=${5:-g6a,g6b,g11}
BASE=${BASE:-$HOME/xdl9/bin/statarb-xdl9-4ef45cc8}
S=${GATES_SRC:-$HOME/xdl9/pgo/gates_src}   # копии скриптов gates/ из репозитория
H=$HOME/xdl9/pgo/gates_$TAG; mkdir -p "$H"
rc=0
has() { case ",$STEPS," in *",$1,"*) return 0;; esac; return 1; }
echo "G14 $TAG: бинарь $BIN ($(sha256sum "$BIN" | cut -c1-16)), эталон не-PGO $BASE ($(sha256sum "$BASE" | cut -c1-16)), потоков $TH, нагрузка $(cat /proc/loadavg)"

if has g6a; then
  BIN_N=$BIN G6A_DIR=$H/g6 bash "$S/g6a_bench_digest.sh" "$TH" > "$H/g6a.log" 2>&1; r=$?
  tail -3 "$H/g6a.log"
  if cmp -s "$H/g6/g6a/sha256-cmp.txt" "$REF/g6a_sha256_cmp.txt"; then echo "РАВНО G6а: sha256-cmp ($(wc -l < "$REF/g6a_sha256_cmp.txt") файлов) = эталон 4ef45cc8, скрипт rc=$r"; else echo "РАЗНО G6а: sha256-cmp != эталон"; diff "$H/g6/g6a/sha256-cmp.txt" "$REF/g6a_sha256_cmp.txt" | head; rc=1; fi
  [ "$r" = 0 ] || rc=1
fi
if has g6b; then
  BIN_N=$BIN G6B_DIR=$H/g6b bash "$S/g6b_run_vs_tk486.sh" "$TH" > "$H/g6b.log" 2>&1; r=$?
  tail -2 "$H/g6b.log"
  if cmp -s "$H/g6b/sha256-new.txt" "$REF/g6b_sha256_new.txt"; then echo "РАВНО G6б: sha256-new ($(wc -l < "$REF/g6b_sha256_new.txt") хешей) = эталон 4ef45cc8, скрипт rc=$r"; else echo "РАЗНО G6б: sha256-new != эталон"; diff "$H/g6b/sha256-new.txt" "$REF/g6b_sha256_new.txt" | head; rc=1; fi
  [ "$r" = 0 ] || rc=1
fi
if has g11; then
  BIN_N=$BIN G11_DIR=$H/g11 bash "$S/g11_w3_flags.sh" "$TH" > "$H/g11.log" 2>&1; r=$?
  tail -1 "$H/g11.log"; [ "$r" = 0 ] || rc=1
  if [ ! -d "$H/../gates_base4ef/g11" ] || [ -n "${REBASE:-}" ]; then
    mkdir -p "$H/../gates_base4ef"
    BIN_N=$BASE G11_DIR=$H/../gates_base4ef/g11 bash "$S/g11_w3_flags.sh" "$TH" > "$H/../gates_base4ef/g11.log" 2>&1; echo "G11 эталон 4ef45cc8: $(tail -1 "$H/../gates_base4ef/g11.log")"
  fi
  B=$H/../gates_base4ef/g11
  ( cd "$H/g11" && find . -name '*.csv' | sort | xargs sha256sum ) > "$H/g11.sha256"
  ( cd "$B" && find . -name '*.csv' | sort | xargs sha256sum ) > "$H/g11.base.sha256"
  nf=$(wc -l < "$H/g11.sha256")
  if cmp -s "$H/g11.sha256" "$H/g11.base.sha256" && [ "$nf" -gt 0 ]; then echo "РАВНО G11: все $nf csv (daily, trades, ext, job-day; с --rearm и без) побайтно = выходам не-PGO 4ef45cc8"; else echo "РАЗНО G11: csv != не-PGO"; diff "$H/g11.sha256" "$H/g11.base.sha256" | head; rc=1; fi
  # (1) и (4) сводки из логов: строки РАВНО/РАЗНО и (4) без времени одинаковы
  if diff <(grep -E '^(РАВНО|РАЗНО|  \(4\))' "$H/g11.log") <(grep -E '^(РАВНО|РАЗНО|  \(4\))' "$H/../gates_base4ef/g11.log") > /dev/null; then echo "РАВНО G11: строки итогов (1)-(4) лога совпадают с логом не-PGO"; else echo "РАЗНО G11: итоги лога"; rc=1; fi
fi
echo "ИТОГ G14 $TAG: код $rc (0 — расхождений нет)"
exit $rc
