#!/usr/bin/env bash
# TK-616: запись `guard.py equiv` a112684a ≡ a03c91df (§13.28 п. 5: готовые ряды тейкера TK-599 допустимы только после неё).
# Запускать на srv под systemd-run. Пара — (выходы TK-599 на L_base/S_base, посчитанные a03c91df, без метки DONE.tk599) против выходов a112684a
# с теми же именами вариантов (хэши TK-599), теми же входами, новым пустым кэшем сигналов; три окна wa/wb/wc, шесть пар (окно × вариант).
# Журнал guard — $REG_DIR (на srv /data/registry нет: по умолчанию /data/p42-tk616/registry); строка журнала — в отчёт.
set -uo pipefail
E=/data/p42-tk616/equiv; ROOT=/data/p42-tk599; BIN=/data/p42-tk607/bin/alpha-l1
NEW=a112684a495e61cf5219610e4eecff22; OLD=a03c91df5a70c8eb32cbb7122943b8f9
export REG_DIR="${REG_DIR:-/data/p42-tk616/registry}"; mkdir -p "$REG_DIR"
rm -rf "$E"; mkdir -p "$E/old" "$E/new"
LB='lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20'
SB='lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05'
VIDS="c53a116125b39 c10fbba7be522"
printf 'c53a116125b39 %s\nc10fbba7be522 %s\n' "$LB" "$SB" > "$E/variants.txt"
PAIRS=()
for w in wa wb wc; do
  case $w in wa) t=2026-06-17;; wb) t=2026-07-25;; wc) t=2026-09-01;; esac
  mkdir -p "$E/old/$w"
  for v in $VIDS; do cp -r "$ROOT/out/$w/$v" "$E/old/$w/$v"; rm -f "$E/old/$w/$v/DONE.tk599"; done
  env P42_T0=$t "$BIN" breakout tick --cache "$ROOT/$w/cache" --trades "$ROOT/$w/aggtrades" --variants "$E/variants.txt" --symbols "$(cat /data/p42-t566/symbols.txt)" \
    --out "$E/new/$w" --threads 4 --tick-store "$ROOT/tick-store" --tick-store-cap-gb 3 --tick-store-min-free-gb 5 --sig-cache "$E/sc-$w" > "$E/new-$w.log" 2>&1 || { echo "FAIL engine $w"; exit 1; }
  for v in $VIDS; do PAIRS+=(--pair "$E/old/$w/$v" "$E/new/$w/$v"); done
done
python3 -I /data/p42-tk616/code/tools/registry/guard.py equiv --new "$NEW" --old "$OLD" --scope '*' "${PAIRS[@]}"
echo "rc=$?"
tail -1 "$REG_DIR/ledger.jsonl" | cut -c1-600
