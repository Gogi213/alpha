#!/usr/bin/env bash
# TK-170 (В-223): раннер сборки на сервере счёта. Ставится как /opt/rbuild/run.sh; зовёт его tools/vps-check.sh под
# `alsched submit` (тяжёлое — только в юнитах планировщика).
#   run.sh <архив исходников> <файл команды> <ядра>
# Слот: свободный из N (flock -n /opt/rbuild/slot<k>.lock) — постоянный тёплый target-slot<k> и каталог src-slot<k>
# (путь стабилен — свои крейты cargo пересобирает по содержимому/mtime, зависимости из реестра не трогает).
# Команда исполняется с SLOT_TGT и JOBS в окружении; при красном — все строки ^error/^warning.
set -u
ARC="$1"; CMDF="$2"; JOBS="${3:-4}"
B=/opt/rbuild; NSLOT="${RBUILD_SLOTS:-3}"
export RUSTUP_HOME=$B/rustup CARGO_HOME=$B/cargo PATH=$B/cargo/bin:$PATH
K=""
while [ -z "$K" ]; do
  for k in $(seq 1 "$NSLOT"); do
    exec 9>"$B/slot$k.lock"
    if flock -n 9; then K=$k; break; fi
    exec 9>&-
  done
  [ -z "$K" ] && sleep 2
done
SRC=$B/src-slot$K; export SLOT_TGT=$B/target-slot$K JOBS
FREE=$(df --output=avail -BG "$B" | tail -1 | tr -dc 0-9)
[ "$FREE" -lt 40 ] && { echo "rbuild: свободно ${FREE}G < 40G — target слота сброшен"; rm -rf "$SLOT_TGT"; }
rm -rf "$SRC" && mkdir -p "$SRC" && tar -xzf "$ARC" -C "$SRC" && cd "$SRC" || exit 70
echo "rbuild: слот $K, ядер $JOBS, target $(du -sm "$SLOT_TGT" 2>/dev/null | cut -f1) МБ"
OUT=$B/out-slot$K.log
bash "$CMDF" >"$OUT" 2>&1; RC=$?
if [ $RC -eq 0 ]; then tail -60 "$OUT"; else grep -E '^(error|warning)' "$OUT" | sort -u | head -150; echo "--- хвост"; tail -25 "$OUT"; fi
rm -f "$ARC" "$CMDF"
exit $RC
