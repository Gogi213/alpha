#!/usr/bin/env bash
# G-022 pool-fix (16.11): лестница «топ-10 включён» на тех же окнах 3 мин, что лестницы G-022 (`steps.sh`): ступень `10` — ровно прежний исключённый топ-10 по обороту, ступень `full` — весь пул
# вместе с топ-10; по 3 окна на ступень, каждое окно — свежий процесс новой сборки с флагами боя; REST-сверка сделок — после лестницы (`restpass.sh`, п. 23: на хосте во время окон ничего постороннего).
#   top10_ladder.sh <binance|bybit> <метка> <бинарь alpha новой сборки>
# Копия идёт рядом с боевым процессом биржи (п. 13: боевой + копии <= 80 % лимита биржи на IP): бюджет подключений копии <= 140 за 5 мин (`BUDGET_MAX`), окна не пачкой (пауза 20 с между окнами).
# Нагрузка боевого процесса рядом с каждым окном — `prodwatch.py` (в ту же метку), итог по окнам — `prodwin.py`. Запускать фоновым юнитом (systemd-run), не в ssh-сессии (В-147).
set -u -o pipefail
EX="$1"; TAG="$2"; BIN="$3"
S=${TOOLS:-/data/turbo-probe/src/tools/turbo}; PY=/root/bnws-venv/bin/python
D=/data/turbo-probe/$TAG; mkdir -p "$D"
case "$EX" in
  binance) FLAGS="--never-slow --lazy --reactor --worker-inline --reactor-window-us 5000 --rx-stamp kernel --no-pong-only-degrade"
           PROD_UNIT=collect-binance.service; PROD_HEALTH=/data/turbo-probe/soak-binance/health.json ;;
  bybit)   FLAGS="--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel"
           PROD_UNIT=soak-bybit.service; PROD_HEALTH=/data/turbo-probe/soak-bybit/health.json ;;
  *) echo "биржа?"; exit 2 ;;
esac
$PY "$S/top10_pool.py" "$EX" "$D/pool-full.json" > "$D/pool.log" 2>&1 || { echo "pool fail" >> "$D/status.txt"; exit 1; }
cp "$D/pool-full.json" "$D/pool-full.orig.json"
$PY "$S/prodwatch.py" --unit "$PROD_UNIT" --health "$PROD_HEALTH" --out "$D/prodwatch.jsonl" --seconds 3600 &
WATCH=$!
[ "$EX" = bybit ] && export ENVARGS="--setenv=ALPHA_COLLECT_CAPTURE=1"   # Bybit: id сделок — UUID без порядка, сверка по сырому захвату (restcheck.py bybit); ЦП окон Bybit — с захватом (верхняя граница)
EXCH="$EX" BUDGET_MAX=140 RESTPASS=defer TOOLS="$S" ROOT=/data/turbo-probe bash "$S/steps.sh" "$TAG" "$BIN" "$FLAGS" "10 full" 3
RC=$?
kill "$WATCH" 2>/dev/null
EXCH="$EX" TOOLS="$S" ROOT=/data/turbo-probe bash "$S/restpass.sh" "$TAG" "10 full" 3
$PY "$S/prodwin.py" "$D" > "$D/prodwin.txt" 2>&1
echo "DONE rc=$RC" >> "$D/status.txt"
