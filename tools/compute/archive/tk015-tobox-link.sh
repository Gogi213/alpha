#!/usr/bin/env bash
# TK-015 (CEO 08:32: деку заморозил сторож диска, держать ≥ 15 ГБ; e-mar без копии на ящике): D20 месяца, лежащий на
# деке файлами, уезжает на ящик alpha/derived/tk015/<эп>/D20/<сутки>/ (rsync сверяет; то, что
# на ящике уже лежит тем же — сутки VPS, — не переливается), а на деке встаёт пофайловыми ссылками на ящик (.linked) —
# сетка и чтение идут по sshfs, как у суток, забранных без места (tk015-deck-pull.sh). Маркеры .done/.linked — на деке.
#   tk015-tobox-link.sh <эпоха…>
set -uo pipefail
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i $HOME/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
say() { echo "== $(date -u +%FT%TZ) $*"; }
for E in "$@"; do
  for d in "$HOME/alpha/epochs/$E"/study/approaches/D20/*/; do
    day=$(basename "$d"); d="${d%/}"
    [[ "$day" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}$ ]] || continue
    mkdir -p "$HOME/alpha/epochs/$E/study/approaches/tobox-tmp"
    [ -f "$d/.done" ] && [ ! -f "$d/.linked" ] || continue
    box="$HOME/sb/derived/tk015/$E/D20/$day"
    n=$(wc -l < "$HOME/alpha/epochs/$E/study/touches/$day/symbols.txt")
    # Без --remove-source-files: файлы деки живы, пока не готов каталог ссылок (сетка p07-h1 может читать сутки прямо
    # сейчас — Исследователь 08:37); подмена каталога — двумя mv, открытые файлы старого читаются до закрытия.
    if ! rsync -a --mkpath --exclude='.*' -e "$SSHC" "$d/" "$SBH:alpha/derived/tk015/$E/D20/$day/"; then
      say "$E $day: заливка упала"; continue
    fi
    if [ ! -f "$box/.box-done" ]; then
      touch "$HOME/alpha/epochs/$E/study/approaches/tobox-tmp/$day.box-done" \
        && rsync -a --remove-source-files -e "$SSHC" "$HOME/alpha/epochs/$E/study/approaches/tobox-tmp/$day.box-done" "$SBH:alpha/derived/tk015/$E/D20/$day/.box-done"         || { say "$E $day: маркер .box-done не ушёл"; continue; }
    fi
    for i in 1 2 3 4 5 6; do [ -f "$box/.box-done" ] && break; sleep 10; done  # кэш атрибутов sshfs
    m=$(ls "$box"/approaches-*.csv 2>/dev/null | wc -l)
    [ "$m" -ge "$n" ] || { say "$E $day: на ящике подходов $m < $n монет суток — ссылки не ставлю"; continue; }
    T="$HOME/alpha/epochs/$E/study/approaches/tobox-tmp"  # вне D20/: циклы сетки перебирают D20/*/
    new="$T/$day.links"; old="$T/$day.files"
    rm -rf "$HOME/alpha/epochs/$E/study/approaches/tobox-tmp/$day.links"; mkdir -p "$new"
    for f in "$box"/*; do
      [ "$(basename "$f")" = symbols.txt ] || ln -s "$f" "$new/$(basename "$f")"
    done
    cp -p "$d"/.done "$new/" && touch "$new/.linked" "$new/.offloaded"
    mv "$d" "$old" && mv "$new" "$d" || { say "$E $day: подмена каталога не вышла"; continue; }
    rm -rf "$HOME/alpha/epochs/$E/study/approaches/tobox-tmp/$day.files"
    say "$E $day: на ящике, ссылок $(find "$d" -maxdepth 1 -type l | wc -l), подходов $m"
  done
  say "$E готов, свободно $(df -h --output=avail "$HOME/alpha" | tail -1)"
done
