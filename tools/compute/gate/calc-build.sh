#!/usr/bin/env bash
# TK-207: ЕДИНАЯ сборка головы на calc для гейтов (все тикеты — через неё, копии сборки в своих скриптах не держать).
#   под alsched: … -- bash calc-build.sh <W> [r2]      W содержит src.tgz, files.lst, HEAD (их кладёт push-head.sh)
# Защиты: (1) распаковка в чистый каталог, список файлов == files.lst (vendor/ внутри); (2) любой сбой сборки = rc≠0, прежний
# бинарник не берётся (старый удаляется до сборки); (3) ALPHA_COMMIT=<хеш> вшит в бинарник, `alpha --version` сверяется с HEAD;
# CALC_EXPECT=<хеш> — ожидаемая голова от вызывающего: не равна W/HEAD → отказ. Выход: $W/alpha-new[-r2], $W/BUILT-<хеш>.
set -uo pipefail
W=${1:?каталог}; F=${2:-}; SRC=$W/src; SFX=; FEAT=; [ "$F" = r2 ] && { SFX=-r2; FEAT="--features r2"; export RUSTFLAGS="-C target-cpu=x86-64-v3"; }
HEAD_SHA=$(cat "$W/HEAD") || exit 70
[ -n "${CALC_EXPECT:-}" ] && [ "$CALC_EXPECT" != "$HEAD_SHA" ] && { echo "ОТКАЗ: ждали голову $CALC_EXPECT, снимок $HEAD_SHA"; exit 71; }
rm -rf "$SRC" "$W/alpha-new$SFX"; mkdir -p "$SRC"
tar -xzf "$W/src.tgz" -C "$SRC" || { echo "ОТКАЗ: распаковка"; exit 72; }
( cd "$SRC" && find . -type f | sed 's|^\./||' | LC_ALL=C sort ) > "$W/files.have"
LC_ALL=C sort "$W/files.lst" > "$W/files.want"
if ! diff -q "$W/files.want" "$W/files.have" >/dev/null; then echo "ОТКАЗ: список файлов снимка != головы:"; diff "$W/files.want" "$W/files.have" | head; exit 73; fi
export RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo PATH=/opt/rbuild/cargo/bin:$PATH ALPHA_COMMIT=$HEAD_SHA
cd "$SRC" || exit 70
nice -n 5 cargo build --release -j "${JOBS:-8}" $FEAT --bin alpha --target-dir "$W/tgt$SFX" 2>&1 | tail -5
[ "${PIPESTATUS[0]}" = 0 ] || { echo "ОТКАЗ: сборка упала (прежний бинарник не используется)"; exit 74; }
cp "$W/tgt$SFX/release/alpha" "$W/alpha-new$SFX" || exit 75
GOT=$("$W/alpha-new$SFX" --version | awk '{print $2}')
[ "$GOT" = "$HEAD_SHA" ] || { echo "ОТКАЗ: хеш в бинарнике $GOT != голова $HEAD_SHA"; rm -f "$W/alpha-new$SFX"; exit 76; }
echo "OK бинарник $W/alpha-new$SFX голова $GOT md5 $(md5sum "$W/alpha-new$SFX" | cut -c1-32)"; touch "$W/BUILT-$GOT$SFX"
