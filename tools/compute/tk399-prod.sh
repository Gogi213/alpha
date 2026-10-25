#!/usr/bin/env bash
# TK-399: боевой проход `lob seconds --bbo-events` — BBO Bybit по событиям, 104 монеты (f104.tsv TK-394) × 15–22.10, фьюч и спот.
#   python3 /data/sched/alsched.py submit --cls prod --kind work --name tk399-bbo --cores 15 --mem 24 --est <с> --max-runtime <с> \
#     --vypiska <файл> -- bash /data/tk0399/tk399-prod.sh
#   единица: bash tk399-prod.sh one <корень> <SYM> <сутки> <fut|spot>   (env BIN OUT); готовая (.gz непустой) пропускается.
# Выход: $OUT/<cls>/<сутки>/<SYM>-<сутки>.csv.gz (t_ms,bid,ask,bq,aq), сводка $OUT/summary.txt, метка DONE.json (или bbo.failed).
set -uo pipefail
W=/data/tk0399; BIN=${BIN:-$W/new/alpha-new}; OUT=${OUT:-$W/bbo}
if [ "${1:-}" = one ]; then
  root=$2 sym=$3 day=$4 cls=$5; dst=$OUT/$cls/$day; gz=$dst/$sym-$day.csv.gz
  [ -s "$gz" ] && exit 0
  mkdir -p "$dst"; tmp=$(mktemp -d "$OUT/.tmp-XXXXXX")
  if nice -n 5 "$BIN" lob seconds --bbo-events --root "$root" --symbol "$sym" --day "$day" --out "$tmp" > "$tmp/log" 2>&1 \
     && [ -s "$tmp/$sym-$day.csv" ] && gzip -1 -c "$tmp/$sym-$day.csv" > "$gz.tmp" && mv "$gz.tmp" "$gz"; then
    rm -rf "$tmp"
  else
    echo "$cls $day $sym $(tail -n1 "$tmp/log" | tr '\t\n' '  ' | cut -c1-200)" >> "$OUT/skip.log"; rm -rf "$tmp" "$gz.tmp"
  fi
  exit 0
fi
RF=/srv/alpha-sda/tk366w/bin-fut/e-2026-10; RS=/srv/alpha-sda/tk366w/bin-spot/e-2026-10
mkdir -p "$OUT"; S=$OUT/summary.txt; rm -f "$OUT/DONE.json" "$OUT/bbo.failed"; export BIN OUT
echo "bin $("$BIN" --version) md5 $(md5sum "$BIN" | cut -c1-32) старт $(date +%F\ %T)" > "$S"
units=$OUT/units.txt
while IFS=$'\t' read -r cls sym; do
  R=$RF; [ "$cls" = spot ] && R=$RS
  for d in 15 16 17 18 19 20 21 22; do
    if ls "$R/$sym-2026-10-$d.binlog"* > /dev/null 2>&1; then echo "$R $sym 2026-10-$d $cls"; fi
  done
done < /srv/alpha-sda/tk366w/f104.tsv > "$units"
total=$(wc -l < "$units"); echo "единиц $total" >> "$S"
xargs -a "$units" -P "${P:-15}" -L 1 bash "$W/tk399-prod.sh" one; rc=$?
cnt() { local n=0 d; for d in "$OUT/$1"/*/; do n=$((n + $(ls -1 "$d" 2> /dev/null | grep -c 'csv\.gz$'))); done; echo "$n"; }
nf=$(cnt fut); ns=$(cnt spot); sk=$(wc -l < "$OUT/skip.log" 2> /dev/null || echo 0)
echo "fut: рядов $nf; spot: рядов $ns; пропусков (skip.log) $sk; xargs rc=$rc; конец $(date +%F\ %T)" >> "$S"
if [ "$rc" = 0 ] && [ $((nf + ns + sk)) -ge "$total" ]; then echo '{"done":1}' > "$OUT/DONE.json"; else touch "$OUT/bbo.failed"; fi
