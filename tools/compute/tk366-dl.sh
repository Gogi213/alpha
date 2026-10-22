#!/usr/bin/env bash
# TK-366 (G-016), фаза 1 (слово владельца 23.10 02:20: «сначала всё качается, потом конвертируется»): только закачка сырья публичного архива Bybit.
#   tk366-dl.sh [P=48]            — собрать список (todo минус уже в журналах) и качать P потоками, без unzip/импорта
#   tk366-dl.sh --one mk SYM DAY  — одна монето-сутки (воркер)         env: DLP, SDA_RESERVE_GB(умолч. 120) DATA_RESERVE_GB(умолч. 60)
# Сырьё: $D/raw (sda5), пока на / свободно > SDA_RESERVE_GB (запас под бинлоги), иначе /data/tk366-raw (пока > DATA_RESERVE_GB), иначе ждёт места.
# Журнал $D/log/dl.tsv: mk sym day status ob_bytes tr_bytes сек каталог   status = dl|missing-ob|missing-tr|fail:<http>:<rc> (fail и неполное качается заново).
D=${TKD:-/srv/alpha-sda/tk366}; LOG=$D/log/dl.tsv   # TKD — другой рабочий каталог (неделя 15–21.10)
if [ "$1" = --one ]; then
  mk=$2; sym=$3; d=$4; mkdir -p "$D/raw" /data/tk366-raw
  while :; do
    a1=$(df --output=avail -k "$D/raw" | tail -1); a2=$(df --output=avail -k /data/tk366-raw | tail -1)
    if [ "$a1" -gt $(( ${SDA_RESERVE_GB:-120} * 1048576 )) ]; then dir=$D/raw; break
    elif [ "$a2" -gt $(( ${DATA_RESERVE_GB:-60} * 1048576 )) ]; then dir=/data/tk366-raw; break; fi
    sleep 60
  done
  if [ "$mk" = fut ]; then uo="https://quote-saver.bycsi.com/orderbook/linear/$sym/${d}_${sym}_ob200.data.zip"; ut="https://public.bybit.com/trading/$sym/$sym$d.csv.gz"
  else uo="https://quote-saver.bycsi.com/orderbook/spot/$sym/${d}_${sym}_ob200.data.zip"; ut="https://public.bybit.com/spot/$sym/${sym}_$d.csv.gz"; fi
  ob=$dir/$mk-$sym-$d.zip; tr=$dir/$mk-$sym-$d.csv.gz; t0=$(date +%s)
  log() { printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$mk" "$sym" "$d" "$1" "${2:-0}" "${3:-0}" $(( $(date +%s)-t0 )) "$dir" >> "$LOG"; }
  get() { local c rc; c=$(curl -s -f --retry 3 --connect-timeout 20 --speed-limit 100000 --speed-time 120 --max-time 7200 -o "$2.part" -w '%{http_code}' "$1"); rc=$?
          if [ $rc = 0 ]; then mv "$2.part" "$2"; return 0; fi; rm -f "$2.part"; GETERR="$c:$rc"; [ "$c" = 404 ] && return 44; return 1; }
  [ -f "$ob" ] || { get "$uo" "$ob"; r=$?; [ $r = 44 ] && { log missing-ob; exit 0; }; [ $r != 0 ] && { log "fail:ob:$GETERR"; exit 0; }; }
  # флаг $D/skip-tr: public.bybit.com отдаёт 403 (бан по IP calc) — качаем только стакан, сделки дотягивает фаза tk366-tr.sh
  [ -f "$tr" ] || { [ -f "$D/ban" ] && exit 0; [ -f "$D/skip-tr" ] && { log ob-only "$(stat -c%s "$ob")"; exit 0; }; get "$ut" "$tr"; r=$?; [ $r = 44 ] && { rm -f "$ob"; log missing-tr; exit 0; }; [ $r != 0 ] && { case "$GETERR" in 403:*) touch "$D/ban";; esac; log "fail:tr:$GETERR"; exit 0; }; }   # 403 — флаг ban: остальные воркеры сделок не бьют, пока круг не закончится
  log dl "$(stat -c%s "$ob")" "$(stat -c%s "$tr")"; exit 0
fi
P=${1:-${DLP:-48}}; mkdir -p "$D/log" "$D/raw" /data/tk366-raw; touch "$LOG"
# список: todo (спот, затем фьюч) минус всё, что уже есть в журналах импорта (любой статус) и закачки (dl|missing-*)
python3 - "$D" > "$D/log/dl-list.txt" <<'PY'
import sys, glob
D = sys.argv[1]; done = set()
for f in glob.glob(D + '/log/import-*.tsv') + glob.glob(D + '/log/import2.tsv'):
    for l in open(f, errors='replace'):
        p = l.split('\t')
        if len(p) > 3: done.add((p[0], p[1], p[2]))
for l in open(D + '/log/dl.tsv'):
    p = l.split('\t')
    if len(p) > 3 and (p[3] == 'dl' or p[3].startswith('missing')): done.add((p[0], p[1], p[2]))
for mk in ('spot', 'fut'):
    for l in open(f'{D}/{mk}-todo.txt'):
        s = l.split()
        if len(s) == 2 and (mk, s[0], s[1]) not in done: print(mk, s[0], s[1])
PY
wc -l < "$D/log/dl-list.txt" | sed 's/^/к закачке: /'
xargs -a "$D/log/dl-list.txt" -r -P "$P" -n 3 bash "$D/tk366-dl.sh" --one
echo "закачка завершена: $(grep -c . "$LOG") строк журнала"
