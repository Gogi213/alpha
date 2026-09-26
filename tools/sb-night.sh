#!/usr/bin/env bash
# Ночной перенос записи Steam Deck → Storage Box (T-06, CEO 27.09): после забора с коллектора новые файлы `root/` и
# `deep/` уходят на ящик, sha256 сверяется на обеих сторонах; `deep/` (поток `.200`, вычисления его не читают)
# с деки удаляется — только файл, чья сумма на ящике совпала с суммой на деке, перепроверенной перед удалением.
# `root/` на деке остаётся (рабочие сутки). Сверенное копится в ~/alpha/sync/sb-verified-<каталог>.sha256
# (строка — только если суммы совпали); файл оттуда повторно не переносится. Лог — ~/alpha/sync/sb-night-<сутки>.log.
# Служба: tools/alpha-sb-night.{service,timer} (после `alpha-pull`, ждёт его конца).
set -uo pipefail
export LC_ALL=C   # comm и sort — один порядок
A="$HOME/alpha"
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i $HOME/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
LOG="$A/sync/sb-night-$(date -u +%F).log"
exec >> "$LOG" 2>&1
say() { echo "== $(date -u +%FT%TZ) $*"; }
while systemctl --user is-active -q alpha-pull.service; do sleep 60; done   # забор ещё пишет
TODAY=$(date -u +%F)
cd "$A" || exit 1
for REL in root deep; do
  VER="$A/sync/sb-verified-$REL.sha256"; touch "$VER"
  # новые файлы: закрытые сутки (имя с датой раньше сегодняшней UTC), ещё не сверенные
  new=$(cd "$REL" && find . -maxdepth 1 -type f -name '*-20??-??-??*' -printf '%P\n' | sort \
    | awk -v t="$TODAY" '{ if (match($0, /20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]/) && substr($0, RSTART, 10) < t) print }' \
    | comm -23 - <(cut -c67- "$VER" | sort))
  n=$(printf '%s' "$new" | grep -c . || true)
  if [ "$n" -gt 0 ]; then
    say "$REL: новых файлов $n"
    $SSHC $SBH mkdir -p "alpha/$REL"
    printf '%s\n' "$new" | nice -n 19 ionice -c3 rsync -a --partial --files-from=- -e "$SSHC" "$REL/" "$SBH:alpha/$REL/" \
      || { say "$REL: rsync упал — сверка и удаление пропущены"; continue; }
    loc=$(cd "$REL" && printf '%s\n' "$new" | nice -n 19 xargs sha256sum | sort -k2)
    rem=$(printf '%s\n' "$new" | sed "s#^#alpha/$REL/#" | xargs -n 150 $SSHC $SBH sha256sum 2>/dev/null \
      | sed "s#  alpha/$REL/#  #" | sort -k2)
    ok=$(comm -12 <(echo "$loc") <(echo "$rem"))
    printf '%s\n' "$ok" | grep . >> "$VER"
    say "$REL: сверено $(printf '%s' "$ok" | grep -c . || true) из $n"
  fi
  sort -u -k2 "$VER" -o "$VER"
  rsync -a -e "$SSHC" "$VER" "$SBH:alpha/$REL.verified.sha256" || true
  [ "$REL" = deep ] || continue
  # удаление `deep/`: файл есть в сверенных и его сумма на деке сейчас та же
  del=0; bytes=0
  while read -r sha f; do
    [ -f "deep/$f" ] || continue
    [ "$(sha256sum "deep/$f" | cut -c1-64)" = "$sha" ] || { say "deep/$f: сумма на деке изменилась — не удаляю"; continue; }
    bytes=$((bytes + $(stat -c %s "deep/$f"))); rm -f "deep/$f"; del=$((del + 1))
  done < "$VER"
  say "deep: удалено с деки $del файлов, $((bytes / 1000000)) МБ; свободно $(df -h "$A" | awk 'NR == 2 {print $4}')"
done
say "готово"
