#!/usr/bin/env bash
# Замер полосы подхода D (F1 этапа F, dev-plan-2026-09-20.md §3):
# `lob touches --approach-bps D` по суткам ночного H3 — те же корни-сутки
# (`study/root-<day>`), тот же список сверенных монет (`study/touches/<day>/
# symbols.txt`), тот же порог (notional $10k), что у касаний ночи.
#   bin/approach-scan.sh <D> [сутки...]
# Артефакты: study/approaches/D<D>/<сутки>/{touches,approaches}-<SYM>.csv + логи;
# сводку читает bin/approach-signal.py.
set -euo pipefail
cd /opt/alpha-compute
D="${1:?полоса подхода D в bps}"
shift || true
DAYS=("$@")
[ ${#DAYS[@]} -gt 0 ] || DAYS=(2026-09-16 2026-09-17 2026-09-18 2026-09-19)
BIN=bin/alpha
JOBS="${JOBS:-3}"
OUT_BASE="${OUT_BASE:-study/approaches}"
echo "== $(date -u +%FT%TZ) scan D=$D сутки: ${DAYS[*]}"
for day in "${DAYS[@]}"; do
  out="$OUT_BASE/D$D/$day"
  src="study/root-$day"
  syms="study/touches/$day/symbols.txt"
  if [ ! -d "$src" ] || [ ! -f "$syms" ]; then
    echo "D=$D $day: нет $src или $syms — сутки пропущены" >&2
    continue
  fi
  mkdir -p "$out"
  xargs -r -P "$JOBS" -I{} nice -n 15 bash -c \
    "$BIN lob touches --root '$src' --symbol {} --h3-mode notional --h3-usd 10000 \
       --approach-bps $D --out '$out/touches-{}.csv' >'$out/{}.log' 2>&1 \
       || echo {} >> '$out/failed.txt'" < "$syms"
  files=$(ls "$out"/approaches-*.csv 2>/dev/null | wc -l)
  failed=$([ -f "$out/failed.txt" ] && wc -l < "$out/failed.txt" || echo 0)
  echo "== $(date -u +%FT%TZ) D=$D $day: файлов $files, ошибок $failed"
done
echo "== $(date -u +%FT%TZ) scan D=$D готов"
