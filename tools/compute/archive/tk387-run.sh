#!/usr/bin/env bash
# TK-387 (G-016): Gate фьюч — сделки (REST) и стакан (download.gatedata.org, почасовые) 01.09–14.10 по монетам кандидатов TK-386.
# usage: tk387-run.sh <корень> ; список контрактов — $R/list-fut.csv (колонка gate), день листинга — $R/listing.tsv (контракт<TAB>YYYY-MM-DD)
# Сделки: tk372-gate-dl.py run (готовые сутки пропускаются). Стакан: 8 потоков, 404 → log/miss.tsv, готовые файлы пропускаются.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
R=${1:?корень}; S=/data/tk0387/tk372-gate-dl.py; cd "$R" || exit 1; mkdir -p log
D0=${D0:-2026-09-01}; D1=${D1:-2026-10-14}
days=$(python3 - "$D0" "$D1" <<'PY'
import sys, datetime as d
a, b = (d.date.fromisoformat(x) for x in sys.argv[1:3]); print(",".join(str(a + d.timedelta(i)) for i in range((b - a).days + 1)))
PY
)
python3 $S run --root "$R" --workers 4 --rps 15 --days "$days" >> log/run.log 2>&1
echo "tk387 trades rc=$? $(date +%T)" >> log/run.log
free=$(df -BG --output=avail "$R" | tail -1 | tr -dc 0-9); [ "$free" -ge 250 ] || { echo "мало места: ${free} ГБ" >> log/run.log; exit 2; }
python3 - "$R" "$D0" "$D1" > log/tasks.txt <<'PY'
import sys, csv, datetime as d
R, a, b = sys.argv[1], d.date.fromisoformat(sys.argv[2]), d.date.fromisoformat(sys.argv[3])
lst = dict(l.rstrip("\n").split("\t") for l in open(R + "/listing.tsv"))
for r in csv.DictReader(open(R + "/list-fut.csv")):
    c = r["gate"]; s = max(a, d.date.fromisoformat(lst[c]))
    for i in range((b - s).days + 1):
        day = s + d.timedelta(i)
        for h in range(24): print(c, day.strftime("%Y%m%d") + "%02d" % h)
PY
dir=$R/futures_usdt/orderbooks; mkdir -p "$dir"
dl() {
  c=$1; h=$2; m=${h:0:6}; mkdir -p "$3/$m"; f=$3/$m/$c-$h.csv.gz
  [ -s "$f" ] && return 0
  code=$(curl -sf -m 600 --retry 6 --retry-delay 10 -o "$f.part" -w '%{http_code}' "https://download.gatedata.org/futures_usdt/orderbooks/$m/$c-$h.csv.gz")
  if [ "$code" = 200 ] && gzip -t "$f.part" 2>/dev/null; then mv "$f.part" "$f"
  else rm -f "$f.part"; printf '%s\t%s\t%s\n' "$c" "$h" "$code" >> "$4/log/miss.tsv"; fi
}
export -f dl
echo "tk387 ob start $(date +%T) tasks=$(wc -l < log/tasks.txt)" >> log/run.log
xargs -a log/tasks.txt -P8 -L1 bash -c 'dl "$0" "$1" '"$dir $R"
echo "tk387 ob end rc=$? $(date +%T) files=$(find "$dir" -name '*.csv.gz' | wc -l) miss=$(wc -l < log/miss.tsv 2>/dev/null)" >> log/run.log
