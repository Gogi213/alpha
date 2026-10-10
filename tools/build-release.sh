#!/usr/bin/env bash
# КТ-1 (TK-145): единственный путь release-сборки — только из чистого `master`, строка в манифест (md5 — ключ).
#   tools/build-release.sh [--features f1,f2] [--profile release|pgo]   # запускать на VPS в чистом дереве master
# Манифест: $MANIFEST (по умолчанию /data/bin/MANIFEST.tsv; колонки — tools/compute/manifest.py). BUILD_DRY=1 — проверки без сборки.
set -euo pipefail
FEAT=""; PROFILE=release
while [ $# -gt 0 ]; do case "$1" in --features) FEAT="$2"; shift 2;; --profile) PROFILE="$2"; shift 2;; *) echo "неизвестно: $1"; exit 2;; esac; done
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
mkdir -p "$(dirname "$MANIFEST")"; [ -f "$MANIFEST" ] || printf '#md5\tcommit\tprofile\tfeatures\tstatus\tbuilt\tpath\tused_by\n' > "$MANIFEST"
grep -q "^$MD5	" "$MANIFEST" || printf '%s\t%s\t%s\t%s\tactive\t%s\t%s\t\n' "$MD5" "$COMMIT" "$PROFILE" "$FEAT" "$(date +%FT%T%z)" "$BIN" >> "$MANIFEST"
echo "$MD5  $BIN"
