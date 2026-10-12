#!/usr/bin/env bash
# TK-207 проба (4): подсунуть старую голову / битый снимок — calc-build.sh обязан отказать (rc≠0) БЕЗ сборки. На calc, ~сек.
#   bash calc-build-probe.sh <W с готовым снимком от push-head.sh>
set -u; W=${1:?}; B=$(dirname "$0")/calc-build.sh; P=$W.probe; rm -rf "$P"; mkdir -p "$P"; cp "$W"/{src.tgz,files.lst,HEAD} "$P"/; ok=0
CALC_EXPECT=0000000000000000000000000000000000000000 bash "$B" "$P" >/dev/null 2>&1; [ $? = 71 ] && echo "старая голова: отказ rc=71 ок" || { echo "ПРОВАЛ: старая голова принята"; ok=1; }
echo "vendor/zz-extra" >> "$P/files.lst"; bash "$B" "$P" >/dev/null 2>&1; [ $? = 73 ] && echo "список файлов: отказ rc=73 ок" || { echo "ПРОВАЛ: расхождение списка принято"; ok=1; }
rm -rf "$P"; exit $ok
