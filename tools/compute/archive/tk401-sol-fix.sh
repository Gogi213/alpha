#!/usr/bin/env bash
# TK-401: Solana 15–22.10 — скан сегментов на NUL/неполные строки (Судья 25.10: 21-17, 21-18, 22-21 битые при .done), снять .done у битых,
# перекачать (до 3 проходов, tk387-sqd-sol.py сам проверяет перед .done), разбор свопов суток, где сегмент перекачан или своп-файла нет.
# Метка: SOL2122.done (только если все 192 сегмента целы и разбор без ошибок), иначе SOL2122.fail.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd7; cd "$S" || exit 1
rm -f "$R/SOL2122.done" "$R/SOL2122.fail" "$R/sol/sw/fail.txt"
python3 - "$R/sol/seg" > "$R/sol/scan.txt" <<'PY'
import sys, os, glob
exec('def segbad' + open('/data/tk0393/tk387-sqd-sol.py').read().split('def segbad')[1].split('def seg(')[0])
for f in sorted(glob.glob(sys.argv[1] + '/*.csv')):
    b = segbad(f)
    print(os.path.basename(f), b or 'ok', flush=True)
PY
grep -v ' ok$' "$R/sol/scan.txt" | while read n r; do rm -f "$R/sol/seg/$n.done" "$R/sol/seg/$n"; echo "снят $n ($r)"; done > "$R/sol/rescan.txt"
cat "$R/sol/rescan.txt"
rm -f "$R/sol/sqd.rc"
for i in 1 2 3; do
  SEG_FROM=20261015-00 python3 -u tk387-sqd-sol.py mints-tk393-sol.csv "$R/sol" 1792022400 1792713600 24 8 >> "$R/sol.log" 2>&1
  [ -f "$R/sol/sqd.rc" ] && break
done
[ -f "$R/sol/sqd.rc" ] || { echo "fail: сегменты не перекачаны" > "$R/SOL2122.fail"; exit 1; }
DAYS=$( (cut -d- -f1 "$R/sol/rescan.txt" | sed 's/снят //'; for d in $(ls "$R/sol/seg" | grep '\.csv$' | cut -d- -f1 | sort -u); do [ -s "$R/sol/sw/$d-swaps.csv" ] || echo $d; done) | sort -u)
echo "разбор суток: $DAYS"
echo $DAYS | tr ' ' '\n' | grep . | xargs -r -P 4 -I{} bash -c 'SRC=gate30k python3 tk378-sol-swaps2.py mints-tk393-sol.csv '"$R"'/sol/sw/{}-swaps.csv '"$R"'/sol/sw/{}-cov.csv '"$R"'/sol/seg/{}-*.csv > '"$R"'/sol/sw/{}.log 2>&1 || echo FAIL {} >> '"$R"'/sol/sw/fail.txt'
if [ -s "$R/sol/sw/fail.txt" ]; then echo "fail rc: $(cat $R/sol/sw/fail.txt)" > "$R/SOL2122.fail"; exit 1; fi
echo done > "$R/SOL2122.done"
