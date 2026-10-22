#!/usr/bin/env bash
# TK-359: боевой прогон ряда `lob seconds` — калибровка режима (В-178, calibrate.sh в режиме --done: сделанное остаётся) →
# остаток единиц на лучшем P. Подача на calc (alsched, 15 ядер, один проход по суткам):
#   python3 /data/sched/alsched.py submit --cls prod --kind work --name tk359-series --cores 15 --mem 24 --est 45m --max-runtime 90m \
#     --vypiska /data/tk0359/vypiska.txt -- bash /data/tk0359/tk359-calibrate-run.sh
# Файлы в /data/tk0359: units.txt (tk359-units.py), tk359-one.sh, calibrate.sh (копия tools/compute), new/alpha-new (бинарь головы).
# Итог: /data/tk0359/series/summary.txt (рядов/сутки по cls, пропуски K1, rc), метка series.done, при сбое — series.failed.
set -uo pipefail
W=/data/tk0359; OUT=$W/series; BIN=${BIN:-$W/new/alpha-new}; S=$OUT/summary.txt
mkdir -p "$OUT"; rm -f "$OUT/series.done" "$OUT/series.failed"
export BIN OUT
echo "bin $("$BIN" --version) md5 $(md5sum "$BIN" | cut -c1-32) старт $(date +%F\ %T)" > "$S"
[ -s "$W/units.txt" ] || python3 "$W/tk359-units.py" "$W/units.txt" >> "$S" 2>&1
total=$(wc -l < "$W/units.txt"); echo "единиц всего $total" >> "$S"
bash "$W/calibrate.sh" --units "$W/units.txt" --cmd "bash $W/tk359-one.sh \"\$1\" \"\$2\" \"\$3\" \"\$4\"" \
  --done "test -s $OUT/\$4/\$3/\$2-\$3.csv.gz" --out "$OUT/calib" --P 8,15 --ra cur,16384 --quick --secs 120 \
  --job tk359 --ticket TK-359 > "$OUT/calib.log" 2>&1
best=$(grep -h '^лучший:' "$OUT/calib.log" | tail -n1 | sed -E 's/.*P=([0-9]+).*/\1/'); P=${best:-15}; [ "$P" -ge 1 ] 2>/dev/null || P=15
echo "калибровка: $(grep -h '^лучший:' "$OUT/calib.log" | tail -n1); P=$P" >> "$S"
# остаток — только не готовые единицы, чтобы xargs не гонял пустые вызовы
rem=$OUT/remaining.txt
while read -r r s d c; do [ -s "$OUT/$c/$d/$s-$d.csv.gz" ] || echo "$r $s $d $c"; done < "$W/units.txt" > "$rem"
echo "остаток после калибровки $(wc -l < "$rem")" >> "$S"
xargs -a "$rem" -P "$P" -L 1 bash "$W/tk359-one.sh"; rc=$?
cnt() { for d in "$OUT/$1"/*/; do ls -1 "$d"; done 2>/dev/null | grep -c 'csv\.gz$'; }   # без глоба на 40 тыс. файлов (ARG_MAX)
nf=$(cnt fut); ns=$(cnt spot); sk=$(wc -l < "$OUT/skip.log" 2>/dev/null || echo 0)
echo "fut: рядов $nf; spot: рядов $ns; пропусков (skip.log) $sk; xargs rc=$rc; конец $(date +%F\ %T)" >> "$S"
if [ "$rc" = 0 ] && [ $((nf + ns + sk)) -ge "$total" ]; then touch "$OUT/series.done"; else touch "$OUT/series.failed"; fi
