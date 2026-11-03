#!/usr/bin/env bash
# TK-487: панели .sap по вселенной П-32 (calc). Сырьё не трогается: src-<биржа> — только симлинки на свечи.
#   convert_universe.sh <бинарь alpha> <потоки> [биржа…]   по умолчанию: binance-um bybit-linear gate-futures_usdt bybit-spot
# Порядок бирж = порядок волн CEO (D2+A2 → B1 → A1 берут Binance и Bybit раньше Gate); метка <W>/panels-<биржа>.done — готово,
# повтор пропускает готовые. Фьючерсы — символы union-<биржа>.csv ∩ файлы свечей; спот Bybit — все непустые файлы (таблицы
# вселенной спота нет). Имена панелей — родные имена биржи (Gate: AAVE_USDT), как в universe/symmap. Место проверяется до записи.
set -euo pipefail
BIN="${1:?бинарь}"; TH="${2:?потоки}"; shift 2
W="${W:-/data/tk0487}"; U="${U:-/data/tk0476/universe}"; C="${C:-/srv/alpha-sda/candles-1m}"
S="${S:-$(cd "$(dirname "$0")" && pwd)}"
EXS=("$@"); [ ${#EXS[@]} -gt 0 ] || EXS=(binance-um bybit-linear gate-futures_usdt bybit-spot)
mkdir -p "$W/log"
fut=(); for ex in "${EXS[@]}"; do [ "$ex" = bybit-spot ] || fut+=(--pair "$U/union-$ex.csv,$U/sources-$ex.csv"); done
spot=0; for ex in "${EXS[@]}"; do [ "$ex" = bybit-spot ] && spot=$(ls "$C/bybit-spot" | grep -c '\.csv\.gz$'); done
python3 "$S/panels_manifest.py" estimate "${fut[@]}" --full "$spot" --free-path "$W"
for ex in "${EXS[@]}"; do
  [ -e "$W/panels-$ex.done" ] && { echo "== $ex: готово ранее"; continue; }
  if [ "$ex" = bybit-spot ]; then
    python3 "$S/panels_manifest.py" farm --all-files --candles "$C/$ex" --out "$W/src-$ex" --missing-out "$W/missing-$ex.txt"
  else
    python3 "$S/panels_manifest.py" farm --union "$U/union-$ex.csv" --candles "$C/$ex" --out "$W/src-$ex" --missing-out "$W/missing-$ex.txt"
  fi
  mkdir -p "$W/panels-$ex"
  echo "== convert $ex (потоков $TH) $(date +%T)"
  "$BIN" statarb convert --src "$W/src-$ex" --out "$W/panels-$ex" --threads "$TH" 2>&1 | tee "$W/log/convert-$ex.log"
  touch "$W/panels-$ex.done"
done
args=(); for ex in "${EXS[@]}"; do args+=(--panels "$ex=$W/panels-$ex"); done
python3 "$S/panels_manifest.py" manifest "${args[@]}" --out "$W/panels-manifest.csv"
