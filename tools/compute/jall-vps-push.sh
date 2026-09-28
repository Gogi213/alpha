#!/usr/bin/env bash
# TK-018: дом июля e-jul для VPS — дека выкладывает на ящик alpha/derived/jall/ (прямо на VPS нельзя: ключ деки там
# rrsync -ro). Сначала малое (e-jul-home.tar: ссылки root/, study/root-2026-07-*, regime, sigma240, bin), потом
# D20 по суткам с конца июля (VPS берёт сутки с 31-го). Маркер суток — <сутки>/.box-done последним.
set -uo pipefail
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i $HOME/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
cd "$HOME/alpha/epochs/e-jul" || exit 1
say() { echo "== $(date -u +%FT%TZ) $*"; }
T=$HOME/alpha/tmp-p07/jall-home.tar
tar cf "$T" bin root study/root-2026-07-* study/regime study/sigma240 || { say "tar упал"; exit 1; }
rsync -a --mkpath -e "$SSHC" "$T" "$SBH:alpha/derived/jall/e-jul-home.tar" && : > "$T.ok" \
  && rsync -a -e "$SSHC" "$T.ok" "$SBH:alpha/derived/jall/e-jul-home.tar.box-done" || { say "дом не ушёл"; exit 1; }
say "дом: $(du -h "$T" | cut -f1)"
for dd in $(seq 31 -1 1); do
  day=2026-07-$(printf %02d "$dd"); d=study/approaches/D20/$day
  [ -d "$d" ] || { say "$day: нет D20"; continue; }
  if rsync -a --mkpath -e "$SSHC" "$d/" "$SBH:alpha/derived/jall/D20/$day/" \
     && : > "$HOME/alpha/tmp-p07/jall-$day.box-done" \
     && rsync -a -e "$SSHC" "$HOME/alpha/tmp-p07/jall-$day.box-done" "$SBH:alpha/derived/jall/D20/$day/.box-done"; then
    say "$day: $(ls "$d" | wc -l) файлов"
  else
    say "$day: заливка упала"
  fi
done
say конец
