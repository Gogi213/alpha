#!/usr/bin/env bash
# TK-018: хвост чтения jall на VPS (CEO 15:38). Дом — путь деки (/home/deck/alpha, HOME=/home/deck). Пакет с деки
# (tmp-p07 без jall-read, klines, b5 июля, скрипты bin) — через ящик (bin/jall-readpack.sh). Скрипты деки ложатся в
# /opt/alpha-compute/jall/deckbin (ссылки на все бинарники VPS сохраняются), /home/deck/alpha/bin → туда.
# jall-read → /opt/alpha-compute/jall/jall-read: дека забирает готовые строки rrsync-ом (jall-vps-readrecv.sh).
# Ворота: 2 готовые на деке строки (h9-a-cap3, h9-b-h9r-k1) — ps-closes.json, keep.csv, ps.json без generated_utc побайтно.
#   jall-vps-read.sh <файл со списком строк хвоста>
set -uo pipefail
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i /root/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
W=/opt/alpha-compute/jall; A=/home/deck/alpha; T=$A/tmp-p07
say() { echo "== $(date -u +%FT%TZ) $*"; }
LIST=$(readlink -f "${1:?список строк}")
until [ -f /mnt/sb/alpha/derived/jall/readpack.tar.box-done ]; do sleep 20; done
if [ ! -f $W/.readpack-done ]; then
  rsync -a -e "$SSHC" "$SBH:alpha/derived/jall/readpack.tar" $W/readpack.tar || { say "забор пакета упал"; exit 1; }
  mkdir -p $W/deckbin $W/jall-read $T
  for f in /opt/alpha-compute/bin/*; do [ -e "$W/deckbin/$(basename "$f")" ] || ln -s "$f" "$W/deckbin/"; done
  ln -sfn $W/deckbin $A/bin.new && mv -T $A/bin.new $A/bin
  [ -L $T/jall-read ] || ln -s $W/jall-read $T/jall-read
  tar xf $W/readpack.tar -C /home/deck || { say "распаковка упала"; exit 1; }
  touch $W/.readpack-done; say "пакет распакован"
fi
cd $T || exit 1
export HOME=/home/deck
if [ ! -f $W/.gate-ok ]; then
  python3 p07-all-jul-read.py --out $W/vps-gate.json --jobs 2 --only h9-a-cap3,h9-b-h9r-k1 > $W/vps-gate.log 2>&1 || { say "ворота: читатель упал"; exit 2; }
  g=$(cd jall-read && { md5sum h9-a-cap3/ps-closes.json h9-b-h9r-k1/ps-closes.json h9-b-h9r-k1/keep.csv | cut -d' ' -f1;
      for f in h9-a-cap3/ps.json h9-b-h9r-k1/ps.json; do sed 's/"generated_utc":"[^"]*"//' $f | md5sum | cut -d' ' -f1; done; } | tr '\n' ' ')
  want="aab9770aa2de1957df79edb3840e13e0 5c9b07aeeeb58313910715011eeaf2f1 e4900d2d7e56a57d50686e7289dd39d4 5a6ba34aa334108c58a751dab808529c 59797e5c40cdf15cae3ba3456a5f7e65 "
  [ "$g" = "$want" ] || { say "ВОРОТА: РАЗНИЦА ($g)"; echo "РАЗНИЦА" > $W/vps-gate.status; exit 3; }
  echo "ВОРОТА VPS чтение: ok" > $W/vps-gate.status; touch $W/.gate-ok; say "ворота ok"
fi
python3 p07-all-jul-read.py --out $W/vps-read.json --jobs ${JOBS:-3} --resume --only "$(paste -sd, "$LIST")" > $W/vps-read.log 2>&1
say "хвост: rc=$? строк с ps-closes $(ls jall-read/*/ps-closes.json 2>/dev/null | wc -l)"
