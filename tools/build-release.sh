#!/usr/bin/env bash
# КТ-1 (TK-145): единственный путь release-сборки — только из чистого `master`, строка в манифест (md5 — ключ).
#   tools/build-release.sh [--features f1,f2]              # запускать на VPS в чистом дереве master
# Доставка на счёт: DELIVER_TO=root@host (+ SSH_KEY, KNOWN_HOSTS) — бинарник и строка манифеста уходят в REMOTE_BIN (по умолч. /data/bin),
# строка пишется с удалённым путём; без DELIVER_TO скрипт печатает, что сделать руками (alsched читает манифест на calc).
# Манифест: $MANIFEST (по умолчанию /data/bin/MANIFEST.tsv; колонки — tools/compute/manifest.py). BUILD_DRY=1 — проверки без сборки.
set -euo pipefail
FEAT=""; PROFILE=release      # PGO-сборки здесь нет: обычный release (профиль в манифесте = то, что собрано)
while [ $# -gt 0 ]; do case "$1" in --features) FEAT="$2"; shift 2;; *) echo "неизвестно: $1"; exit 2;; esac; done
BR="$(git rev-parse --abbrev-ref HEAD)"
[ "$BR" = "master" ] || { echo "отказ: сборка только из master (сейчас $BR)"; exit 3; }
[ -z "$(git status --porcelain)" ] || { echo "отказ: дерево не чистое"; exit 3; }
COMMIT="$(git rev-parse --short=8 HEAD)"
MANIFEST="${MANIFEST:-/data/bin/MANIFEST.tsv}"
OUT="${RELEASE_DIR:-/opt/alpha-compute/release}"
[ "${BUILD_DRY:-0}" = 1 ] && { echo "ок: $COMMIT $PROFILE [$FEAT] -> $MANIFEST"; exit 0; }
cargo b${FEAT:+ --features "$FEAT"} --release
mkdir -p "$OUT"
BIN="$OUT/alpha-$COMMIT-$PROFILE${FEAT:+-${FEAT//,/+}}"
cp target/release/lob "$BIN"
MD5="$(md5sum "$BIN" | cut -d' ' -f1)"
HDR='#md5	commit	profile	features	status	built	path	used_by
'
row() { printf '%s	%s	%s	%s	active	%s	%s	
' "$MD5" "$COMMIT" "$PROFILE" "$FEAT" "$(date +%FT%T%z)" "$1"; }
mkdir -p "$(dirname "$MANIFEST")"; [ -f "$MANIFEST" ] || printf "$HDR" > "$MANIFEST"
grep -q "^$MD5	" "$MANIFEST" || row "$BIN" >> "$MANIFEST"
if [ -n "${DELIVER_TO:-}" ]; then
  RB="${REMOTE_BIN:-/data/bin}"; NAME="$(basename "$BIN")"
  SSH=(ssh ${SSH_KEY:+-i "$SSH_KEY"} ${KNOWN_HOSTS:+-o UserKnownHostsFile="$KNOWN_HOSTS"})
  "${SSH[@]}" "$DELIVER_TO" "mkdir -p $RB && cat > $RB/$NAME.part && chmod +x $RB/$NAME.part && mv $RB/$NAME.part $RB/$NAME" < "$BIN"
  [ "$("${SSH[@]}" "$DELIVER_TO" "md5sum $RB/$NAME" | cut -d' ' -f1)" = "$MD5" ] || { echo "отказ: md5 на $DELIVER_TO не совпал"; exit 4; }
  "${SSH[@]}" "$DELIVER_TO" "[ -f $RB/MANIFEST.tsv ] || printf '$HDR' > $RB/MANIFEST.tsv; grep -q '^$MD5	' $RB/MANIFEST.tsv || cat >> $RB/MANIFEST.tsv" < <(row "$RB/$NAME")
  echo "доставлено: $DELIVER_TO:$RB/$NAME + строка манифеста"
else
  echo "не доставлено на счёт: DELIVER_TO не задан — скопировать $BIN в /data/bin на calc и добавить строку манифеста (md5 $MD5), иначе alsched (режим 1) откажет"
fi
echo "$MD5  $BIN"
