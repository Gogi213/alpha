#!/usr/bin/env bash
# TK-289 П-18 ось 6в: распределение силы В-67 (S = size_at_arm / flow_1h_lots × 100) у сигналов B1 по месяцам, q10/25/50/75/90/95 -> /data/tk0289/s67/ (правило «шире/гуще» Судьи 19.10).
# Три процесса по группам месяцев (однопроцессный счёт ~27 мин при лимите 15); склейка json/csv/логов. Выходы перезаписываются, каталог чистый.
D=/data/tk0289/s67; mkdir -p $D
run() { for m in "$@"; do python3 /data/tk0289/tk289-thr6b.py /data/tk0289/f1/out $D/m-$m.json $D/m-$m.csv --s67 --month 2026-$m > $D/m-$m.log 2>&1 || return 1; done; }
run 03 02 09 & run 07 01 05 & run 08 04 06 &
wait
ls $D/m-*.json | wc -l | grep -qx 9 || exit 1
python3 - <<EOF || exit 1
import glob, json
res = {}
for f in sorted(glob.glob("$D/m-*.json")):
    res.update(json.load(open(f, encoding="utf-8")))
json.dump(res, open("$D/s67.json", "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1, sort_keys=True)
rows = []
for i, f in enumerate(sorted(glob.glob("$D/m-*.csv"))):
    rows += open(f, encoding="utf-8").read().splitlines()[0 if i == 0 else 1:]
open("$D/P-18-s67-dist.csv", "w", encoding="utf-8", newline="\n").write("\n".join(rows) + "\n")
EOF
cat $D/m-*.log | sort > $D/run.log
echo ok > $D/done
