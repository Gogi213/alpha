#!/usr/bin/env bash
# TK-018: пакет для хвоста чтения jall на VPS → ящик alpha/derived/jall/readpack.tar
set -uo pipefail
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i $HOME/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
cd /home/deck || exit 1
P=/home/deck/alpha/tmp-p07/readpack.tar
tar cf "$P" --exclude=alpha/tmp-p07/jall-read --exclude=alpha/tmp-p07/readpack.tar --exclude='alpha/tmp-p07/*.tar' \
  alpha/tmp-p07 alpha/study/klines alpha/epochs/e-jul/study/klines alpha/epochs/e-jul/b5 \
  $(cd /home/deck && ls alpha/bin/*.py alpha/bin/*.sh) || { echo "tar упал"; exit 1; }
rsync -a --mkpath -e "$SSHC" "$P" "$SBH:alpha/derived/jall/readpack.tar" && : > "$P.ok" \
  && rsync -a -e "$SSHC" "$P.ok" "$SBH:alpha/derived/jall/readpack.tar.box-done" && echo "== $(date -u +%T) пакет $(du -h $P | cut -f1) на ящике"
