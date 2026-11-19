#!/usr/bin/env bash
# G-022 pool-fix: проверка пересмотра пула на живой бирже копией (пул фиксированный, пересмотр включён явным `--pool-refresh-s`).
#   poolsmoke.sh <binance|bybit|bingx|gate> <метка> <бинарь alpha> [секунд работы, по умолчанию 330] [E — размер «пула биржи», по умолчанию 140]
# Копия мала (E символов выше порога): порог `--min-quote-volume` = оборот E-го по рангу символа, поэтому по REST выше порога ровно E символов.
# Начальный пул копии: E символов без 40 (ранги 20–59: их должен докинуть пересмотр — SUBSCRIBE на живых соединениях / новый шард), по алфавиту, как у боя,
# плюс 8 символов чуть ниже порога (выходят по гистерезису `--pool-hold-s 150`: UNSUBSCRIBE). Соединений <= 20, бюджет подключений 60 (п. 13 — не более 80 % лимита биржи на IP).
# Итог — `poolsmoke.py` в <каталог метки>/smoke.json: время от изменения пула до первой записи нового символа, записи снятых после отписки, ответы биржи на подписку и отписку.
# Фоновый юнит (systemd-run), не в ssh-сессии (В-147). OKX — только с ALPHA_ALLOW_OKX_COPY=1 (решение координатора), здесь не поддержан.
set -u -o pipefail
EX="$1"; TAG="$2"; BIN="$3"; DUR="${4:-330}"; E="${5:-140}"
S=${TOOLS:-/data/turbo-probe/src/tools/turbo}; PY=/root/bnws-venv/bin/python
D=/data/turbo-probe/$TAG; mkdir -p "$D"
case "$EX" in
  binance) FLAGS="--never-slow --lazy --reactor --worker-inline --reactor-window-us 5000 --rx-stamp kernel --no-pong-only-degrade" ;;
  bybit)   FLAGS="--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel" ;;
  bingx)   FLAGS="--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel --sub-in-reactor" ;;
  gate)    FLAGS="--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel --sub-in-reactor" ;;
  *) echo "биржа?"; exit 2 ;;
esac
if [ "$EX" = binance ] || [ "$EX" = bybit ]; then
  $PY "$S/top10_pool.py" "$EX" "$D/universe.json" --min-qv 1 > "$D/pool.log" 2>&1 || exit 1
else
  # BingX / Gate: пул и оборот — из собственного `--pool-only` бинаря (в файле пула есть `qv`)
  "$BIN" collect run --exchange "$EX" --root "$D/poolprep" --pool-only --min-quote-volume 1 > "$D/poolprep.log" 2>&1 || exit 1
  $PY - "$D" <<'PYEOF'
import glob, json, sys
d = sys.argv[1]
f = json.load(open(sorted(glob.glob(d + "/poolprep/pool-*-*.json"))[-1]))
json.dump({"pool": sorted(f["pool"]), "qv": f["qv"], "meta": f.get("meta", {})}, open(d + "/universe.json", "w"))
PYEOF
fi
MINQV=$($PY - "$D" "$E" <<'PYEOF'
import json, sys
d, e = sys.argv[1], int(sys.argv[2])
u = json.load(open(d + "/universe.json"))
ranked = sorted(u["pool"], key=lambda s: (-u["qv"][s], s))
minqv = max(300_000.0, u["qv"][ranked[e - 1]])
eligible = [s for s in ranked if u["qv"][s] >= minqv]
missing = eligible[20:60]                     # их докинет пересмотр
low = [s for s in ranked if 0 < u["qv"][s] < minqv][:8]   # ниже порога: выйдут по гистерезису
start = sorted([s for s in eligible if s not in missing] + low)   # как у боя: пул по алфавиту (тяжёлые символы разложены по шардам)
json.dump({"pool": start, "qv": {s: u["qv"].get(s, 0) for s in start}, "meta": {s: u["meta"][s] for s in start if s in u.get("meta", {})},
           "expect_add": missing, "expect_remove": low, "eligible": len(eligible), "min_qv": minqv}, open(d + "/pool-start.json", "w"))
print(int(minqv))
sys.stderr.write(f"start {len(start)} eligible {len(eligible)} expect_add {len(missing)} expect_remove {len(low)} min_qv {int(minqv)}\n")
PYEOF
)
echo "min_qv $MINQV" > "$D/status.txt"
systemd-run --quiet --unit="smoke-$TAG" --collect -p KillMode=mixed -p TimeoutStopSec=60 -p LimitNOFILE=65536 \
  "$BIN" collect run --exchange "$EX" --root "$D/data" --health "$D/health.json" --lanes 2 --pool-file "$D/pool-start.json" --pool-refresh-s 60 --pool-hold-s 150 \
  --min-quote-volume "$MINQV" --connect-budget 60 --duration-s "$DUR" $FLAGS
sleep 5
while systemctl is-active --quiet "smoke-$TAG.service"; do sleep 3; done
$PY "$S/poolsmoke.py" "$D" "$EX" > "$D/smoke.json" 2> "$D/smoke.log"
echo "DONE" > "$D/DONE"
