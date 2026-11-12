#!/usr/bin/env bash
# TK-402 шаг 1: Bybit по ВСЕМ общим с Gate фьючам (universe.csv TK-379, on_bybit=1; множитель 1000/1000000/10000 в имени снимается): монето-сутки 15–22.10,
#   (а) событийный BBO (`lob seconds --bbo-events`, tk399-prod.sh one -> /data/tk0399/bbo, готовые пропускаются);
#   (б) посекундные середины со флагом gap (`lob seconds`, как TK-369/359, но --allow-unverified: маркер verify=fail у большинства монет, стакан цел) -> /data/tk0402/mid/<cls>/<сутки>/.
#   alsched submit --cls prod --kind work --name tk402-stage1 --cores 15 --mem 24 --est <с> --max-runtime <с> --vypiska <файл> -- bash /data/tk0402/tk402-stage1.sh
# Выход: /data/tk0402/stage1.tsv (охват: coin,bybit_fut,bybit_spot,reason), метка /data/tk0402/stage1.done (или stage1.failed). Единица: tk402-stage1.sh one <корень> <SYM> <сутки> <fut|spot>
set -uo pipefail
W=/data/tk0402; BIN=${BIN:-/data/tk0399/new/alpha-new}; MID=$W/mid
if [ "${1:-}" = one ]; then
  root=$2 sym=$3 day=$4 cls=$5
  OUT=/data/tk0399/bbo BIN=$BIN bash /data/tk0399/tk399-prod.sh one "$root" "$sym" "$day" "$cls"
  dst=$MID/$cls/$day; gz=$dst/$sym-$day.csv.gz; [ -s "$gz" ] && exit 0
  mkdir -p "$dst"; tmp=$(mktemp -d "$MID/.tmp-XXXXXX")
  if nice -n 5 "$BIN" lob seconds --allow-unverified --root "$root" --symbol "$sym" --day "$day" --out "$tmp" > "$tmp/log" 2>&1 \
     && [ -s "$tmp/$sym-$day.csv" ] && gzip -1 -c "$tmp/$sym-$day.csv" > "$gz.tmp" && mv "$gz.tmp" "$gz"; then rm -rf "$tmp"
  else echo "$cls $day $sym $(tail -n1 "$tmp/log" | tr '\t\n' '  ' | cut -c1-200)" >> "$MID/skip.log"; rm -rf "$tmp" "$gz.tmp"; fi
  exit 0
fi
mkdir -p "$MID"; rm -f $W/stage1.done $W/stage1.failed
RF=/srv/alpha-sda/tk366w/bin-fut/e-2026-10; RS=/srv/alpha-sda/tk366w/bin-spot/e-2026-10
python3 - "$RF" "$RS" > $W/stage1-units.txt <<'PY'
import csv, os, sys
RF, RS = sys.argv[1:3]
gf = sorted(r['symbol'].replace('_USDT', '') for r in csv.DictReader(open('/data/tk0379/universe.csv')) if r['market'] == 'фьюч' and r['on_bybit'] == '1')
fl = set(os.listdir(RF)); sl = set(os.listdir(RS)); rows = []
for c in gf:
    got = {}
    for cls, R, L in (('fut', RF, fl), ('spot', RS, sl)):
        for p in ('', '1000', '1000000', '10000'):
            s = f'{p}{c}USDT'; ds = [d for d in range(15, 23) if f'{s}-2026-10-{d}.binlog' in L ]
            if ds:
                got[cls] = s
                for d in ds: print(R, s, f'2026-10-{d}', cls)
                break
    rows.append((c, got.get('fut', ''), got.get('spot', ''), '' if 'fut' in got else 'нет бинлога Bybit-фьюча (монета <50k оборота или не в TK-366)'))
w = csv.writer(open('/data/tk0402/stage1.tsv', 'w', newline=''), delimiter='\t'); w.writerow(['coin', 'bybit_fut', 'bybit_spot', 'reason']); w.writerows(rows)
PY
total=$(wc -l < $W/stage1-units.txt); echo "единиц $total старт $(date +%T)" > $W/stage1.log
export BIN
xargs -a $W/stage1-units.txt -P "${P:-15}" -L 1 bash $W/tk402-stage1.sh one; rc=$?
echo "xargs rc=$rc конец $(date +%T); mid-рядов $(find $MID -name '*.csv.gz' | wc -l); skip $(wc -l < $MID/skip.log 2>/dev/null || echo 0)" >> $W/stage1.log
if [ "$rc" = 0 ]; then echo '{"done":1}' > $W/stage1.done; else touch $W/stage1.failed; fi
