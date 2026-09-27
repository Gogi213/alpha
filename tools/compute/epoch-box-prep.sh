#!/usr/bin/env bash
# Эпоха архива с ящика (TK-005, июль): бинлоги остаются на Storage Box (`~/sb/epochs/<эпоха>/root`, sshfs ro,
# импорт T-30 со сверкой sha256), на деке — только производное (диск: бинлоги месяца ~42 ГБ не влезают).
# root/ эпохи — локальный каталог ссылок на файлы ящика + session.json-заглушка (ящик только для чтения);
# переезд бинлогов на локальный диск = замена ссылок, корни-сутки ходят через root/ и не меняются.
#   epoch-box-prep.sh <эпоха> <первые сутки> <последние сутки> light        — root/, режим BTC/ETH, свечи пула, σ240
#   epoch-box-prep.sh <эпоха> <первые сутки> <последние сутки> day <сутки…> — корень-день (как day_root ночи),
#       список ok-монет, кэш подходов D20 бинарником T-28 (16f0a80, как approaches-t28 e-aug), режим суток из его
#       mids1m (одно чтение бинлогов: mids1m подходов = mids1m касаний, cmp e-aug 01.08).
set -uo pipefail
E="${1:?эпоха}"; FIRST="${2:?первые сутки}"; LAST="${3:?последние сутки}"; MODE="${4:?light|day}"; shift 4
A="$HOME/alpha"; H="$A/epochs/$E"; SB="$HOME/sb/epochs/$E/root"
[ -d "$SB" ] || { echo "нет $SB (ящик не смонтирован?)"; exit 1; }
say() { echo "== $(date -u +%FT%TZ) $E $*"; }

if [ "$MODE" = light ]; then
  mkdir -p "$H/root"
  for f in "$SB"/*.binlog; do ln -sfn "$f" "$H/root/$(basename "$f")"; done
  ln -sfn "$SB/verify-logs" "$H/root/verify-logs"
  cp -n "$SB/instruments.csv" "$H/root/"
  "$A/bin/epoch-bootstrap.sh" "$H" "$A" "$FIRST" "$LAST" | tail -2
  syms=$(ls "$SB"/*.binlog | xargs -n1 basename | sed -E 's/-20[0-9]{2}-[0-9]{2}-[0-9]{2}(-p[0-9]+)?\.binlog$//' | sort -u | paste -sd,)
  since=$(date -u -d "$FIRST -1 day" +%F); until=$(date -u -d "$LAST +1 day" +%F)
  python3 "$A/bin/ref-klines.py" --out-dir "$H/study/klines" --symbols "$syms" --since "$since" --until "$until" | tail -2
  python3 "$A/bin/sigma-table.py" --klines "$H/study/klines" --out-dir "$H/study/sigma240" | tail -2
  say "light готово: ссылок $(ls "$H/root" | grep -c binlog), монет свечей $(ls "$H/study/klines" | wc -l), σ $(ls "$H/study/sigma240" | wc -l)"
  exit 0
fi

[ "$MODE" = day ] || { echo "режим light|day"; exit 1; }
cd "$H" || exit 1
scan="$H/approach-scan-t28.sh"
sed 's#^BIN=bin/alpha$#BIN=bin/alpha-16f0a80#' "$A/bin/approach-scan.sh" > "$scan"
grep -q '^BIN=bin/alpha-16f0a80$' "$scan" || { echo "подмена BIN не удалась"; exit 1; }
cp "$A/bin/_env.sh" "$H/"
for day in "$@"; do
  [ -f "study/approaches/D20/$day/.done" ] && { say "$day уже готов"; continue; }
  dir="study/root-$day"; rm -rf "$dir"; mkdir -p "$dir" "study/touches/$day"
  for f in root/*-"$day".binlog root/*-"$day"-*.binlog root/instruments.csv root/session.json; do
    [ -e "$f" ] || continue
    ln -s "$PWD/$f" "$dir/$(basename "$f")"
  done
  grep -aE '^verify: [A-Z0-9]+ status=(ok|fail)' "root/verify-logs/$day.log" | while read -r _ sym st _; do
    echo "${st#status=}" > "$dir/verify-$sym.status"
  done
  grep -l '^ok$' "$dir"/verify-*.status | xargs -n1 basename | sed 's/^verify-//; s/\.status$//' > "study/touches/$day/symbols.txt"
  n=$(wc -l < "study/touches/$day/symbols.txt")
  t0=$(date +%s)
  ALPHA_HOME="$H" OUT_BASE=study/approaches JOBS="${JOBS:-3}" bash "$scan" 20 "$day" | tail -1
  files=$(ls "study/approaches/D20/$day"/approaches-*.csv 2>/dev/null | wc -l)
  python3 bin/regime.py --day "$day" --touches study/approaches/D20 | tail -1
  if [ "$files" -ge "$n" ] && [ ! -f "study/approaches/D20/$day/failed.txt" ]; then
    touch "study/approaches/D20/$day/.done"
  fi
  say "$day: ok-монет $n, файлов подходов $files, $(( $(date +%s) - t0 )) с"
done
