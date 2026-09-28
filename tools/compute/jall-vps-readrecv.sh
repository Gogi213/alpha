#!/usr/bin/env bash
# TK-018 (дека): готовые строки чтения jall с VPS (rrsync -ro /opt/alpha-compute → jall/jall-read/) → tmp-p07/jall-read/.
# Строка ставится, только если у деки её ps-closes.json ещё нет; ps-closes.json кладётся последним (по нему --resume
# читателя деки пропускает строку). Конец — когда цепочка чтения деки кончилась.
set -uo pipefail
T=/home/deck/alpha/tmp-p07; V=$T/jall-read-vps
say() { echo "== $(date -u +%FT%TZ) $*"; }
mkdir -p $V
while :; do
  rsync -a root@13.140.29.171:jall/jall-read/ $V/ 2>/dev/null
  for d in $V/*/; do
    n=$(basename "$d"); [ -f "$d/ps-closes.json" ] || continue
    python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$d/ps-closes.json" 2>/dev/null || continue
    [ -f "$T/jall-read/$n/ps-closes.json" ] && continue
    rsync -a --exclude=ps-closes.json "$d" "$T/jall-read/$n/" && cp -a "$d/ps-closes.json" "$T/jall-read/$n/ps-closes.json.new" \
      && mv "$T/jall-read/$n/ps-closes.json.new" "$T/jall-read/$n/ps-closes.json" && say "$n: с VPS"
  done
  systemctl --user is-active -q alpha-jall-readchain3 || { say "цепочка деки кончилась — конец"; exit 0; }
  sleep 60
done
