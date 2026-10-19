#!/usr/bin/env bash
# TK-274 Г-03: B1 × 5 seed на двойниках, все B1-сутки янв–сен (251). Сначала одни лёгкие сутки (дым): fail/cmp DIFF → стоп rc 6.
#   tk274-g03.sh   env: BIN NX=3 TP=4 GT=2   результат /data/tk0274/g03/out/<метка>/ ; rc → /data/tk0274/g03.rc
#   Пересчёт двойников без отмены по гибели стены (Судья 17.10): MODES=nowall FROM=/data/tk0274/g03/out OUTD=/data/tk0274/g03nw/out R=/data/tk0274/g03
#   (план и сутки — из R, выход — в OUTD, реплея нет). Гейт: MODES="paired nowall" ONLY_SMOKE=1 SMOKE='jan 2026-01-05' — после дыма paired* байт в байт с FROM, rc 7 при расхождении.
set -uo pipefail
R=/data/tk0274/g03; S=$(cd "$(dirname "$0")" && pwd)
export BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk274-pgo} TP=${TP:-4} GT=${GT:-2} P=$R/plan O=${OUTD:-$R/out}
mkdir -p "$O"; D=$R/days.txt
smoke=${SMOKE:-$(tail -n1 "$D")}; set -- $smoke   # дым — сутки с мелким тиком (иначе двойников нет, SMOKE='feb 2026-02-01')
bash "$S/tk274-g03-day.sh" "run-$1-$2" "$1" "$2"
f=$O/run-$1-$2; [ -e "$f/done" ] && [ ! -s "$f/fail.txt" ] || { echo "дым $1 $2: fail.txt/нет done" >&2; exit 6; }
grep -q DIFF "$f/cmp.txt" && { echo "дым $1 $2: approaches не совпали с кэшем TK-115" >&2; exit 6; }
m1=${MODES:-random paired}; m1=${m1%% *}
built=$(cat "$f"/out/"$2"/"$m1"1/level-control-*.csv 2>/dev/null | awk -F, '$1!="symbol"{n+=$5} END{print n+0}')
[ "$built" -gt 0 ] || { echo "дым $1 $2: двойников построено 0 (сутки без монет с тиком < 1 bps?)" >&2; exit 6; }
if [ -n "${ONLY_SMOKE:-}" ]; then   # гейт: paired* этих суток байт в байт с прежним проходом FROM
  [ -n "${FROM:-}" ] || { echo "ONLY_SMOKE: нужен FROM" >&2; exit 7; }
  sums() { (cd "$1/run-$2-$3/out/$3" && find paired* -type f | LC_ALL=C sort | xargs md5sum); }
  sums "$FROM" "$1" "$2" > "$f/paired-old.md5"; sums "$O" "$1" "$2" > "$f/paired-new.md5"
  cmp -s "$f/paired-old.md5" "$f/paired-new.md5" && [ -s "$f/paired-new.md5" ] && { echo "GATE paired: identical ($(wc -l < "$f/paired-new.md5") файлов)"; exit 0; }
  echo "GATE paired: DIFF" >&2; exit 7
fi
xargs -P "${NX:-3}" -L1 bash -c 'bash "$0/tk274-g03-day.sh" "run-$1-$2" "$1" "$2"' "$S" < "$D"
rc=$?
n=$(cat "$O"/*/fail.txt 2>/dev/null | wc -l); [ "$n" = 0 ] || { echo "fail.txt: $n строк" >&2; rc=3; }
exit $rc
