#!/usr/bin/env bash
# Копия каталога Steam Deck на Hetzner Storage Box со сверкой sha256 на обеих сторонах (T-06, владелец
# 27.09 оформил BX11; место на Steam Deck). Только копирует — с Steam Deck ничего не удаляет: удаление
# единственных копий (`root/`, `deep/`, В-106) — только по «да» владельца и после строки «OK» ниже.
#
#   bash ~/alpha/bin/sb-push.sh <каталог относительно ~/alpha> [...]   # напр. epochs/e-crash deep root
#
# Запуск на Steam Deck фоном: systemd-run --user --unit=alpha-sb-push --collect bash ~/alpha/bin/sb-push.sh deep
# Итог по каталогу — ~/alpha/sync/sb-push-<каталог>.log, последняя строка «VERDICT OK|MISMATCH …».
# Манифест `<каталог>.sha256` (пути относительно каталога) кладётся и на Storage Box рядом с каталогом:
# потом сверка без Steam Deck — `ssh … sha256sum alpha/<каталог>/<файл>` против манифеста.
# Сумма на Storage Box считается его собственным `sha256sum` (ограниченная оболочка Hetzner, без труб) —
# файл прочитан с его диска, а не из кэша передачи.
set -euo pipefail

SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i $HOME/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
BATCH=150   # файлов на один вызов удалённого sha256sum (длина командной строки)

cd "$HOME/alpha"
for arg in "$@"; do
  REL=${arg%/}
  [[ -d $REL ]] || { echo "нет каталога $REL" >&2; exit 2; }
  TAG=${REL//\//_}
  LOG=$HOME/alpha/sync/sb-push-$TAG.log
  MAN=$HOME/alpha/sync/sb-$TAG.sha256
  {
    echo "== $(date -Is) push $REL ($(du -s --apparent-size -B1 "$REL" | cut -f1) байт)"
    $SSHC $SBH mkdir -p "alpha/$REL"
    nice -n 19 ionice -c3 rsync -a --partial --stats -e "$SSHC" "$REL/" "$SBH:alpha/$REL/" | tail -4

    echo "== $(date -Is) sha256 local"
    (cd "$REL" && find . -type f -printf '%P\0' | sort -z | nice -n 19 ionice -c3 xargs -0 sha256sum) > "$MAN"
    n=$(wc -l < "$MAN")

    echo "== $(date -Is) sha256 remote ($n файлов)"
    : > "$MAN.remote"
    mapfile -t files < <(cut -c67- "$MAN")
    files=("${files[@]/#/alpha/$REL/}")
    for ((i = 0; i < n; i += BATCH)); do
      # ошибка вызова попадает в файл строкой без суммы — и считается расхождением ниже
      $SSHC $SBH sha256sum "${files[@]:i:BATCH}" 2>&1 | while read -r h f; do
        echo "$h  ${f#alpha/$REL/}"
      done >> "$MAN.remote" || true
    done
    rsync -a -e "$SSHC" "$MAN" "$SBH:alpha/$REL.sha256"

    bad=$(diff <(sort -k2 "$MAN") <(sort -k2 "$MAN.remote") | grep -c '^[<>]' || true)
    if [[ $bad -eq 0 ]]; then
      echo "VERDICT OK $REL: $n файлов, суммы совпали ($(date -Is))"
    else
      echo "VERDICT MISMATCH $REL: $bad строк расходятся из $n — см. diff $MAN $MAN.remote ($(date -Is))"
    fi
  } >> "$LOG" 2>&1
done
