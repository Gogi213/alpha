#!/usr/bin/env bash
# G-022 pool-fix: проверка пересмотра пула на живой бирже копией (пул фиксированный, пересмотр включён явным `--pool-refresh-s`).
#   poolsmoke.sh <binance|bybit|bingx|gate> <метка> <бинарь alpha> [секунд работы, по умолчанию 330]
# Пул копии: самые оборотистые N символов без 20 символов середины (их должен докинуть пересмотр: SUBSCRIBE на живых соединениях / новый шард)
# плюс 8 символов чуть ниже порога (они выходят по гистерезису `--pool-hold-s 150`: UNSUBSCRIBE). Копия мала (<= 8 соединений), бюджет подключений 60 (п. 13 — не более 80 % лимита биржи на IP).
# Итог — `poolsmoke.py` в <каталог метки>/smoke.json: время от изменения пула до первой записи нового символа, записи снятых после отписки, ответы биржи на подписку и отписку.
# Фоновый юнит (systemd-run), не в ssh-сессии (В-147). OKX — только с ALPHA_ALLOW_OKX_COPY=1 (решение координатора), здесь не поддержан.
set -u -o pipefail
EX="$1"; TAG="$2"; BIN="$3"; DUR="${4:-330}"
S=${TOOLS:-/data/turbo-probe/src/tools/turbo}; PY=/root/bnws-venv/bin/python
D=/data/turbo-probe/$TAG; mkdir -p "$D"
case "$EX" in
  binance) FLAGS="--never-slow --lazy --reactor --worker-inline --reactor-window-us 5000 --rx-stamp kernel --no-pong-only-degrade"; N=60 ;;
  bybit)   FLAGS="--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel"; N=60 ;;
  bingx)   FLAGS="--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel --sub-in-reactor"; N=60 ;;
  gate)    FLAGS="--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel --sub-in-reactor"; N=60 ;;
  *) echo "биржа?"; exit 2 ;;
esac
if [ "$EX" = binance ] || [ "$EX" = bybit ]; then
  $PY "$S/top10_pool.py" "$EX" "$D/universe.json" > "$D/pool.log" 2>&1 || exit 1
else
  # BingX / Gate: пул и оборот — из собственного `--pool-only` бинаря (в файле пула есть `qv`); «ниже порога» — по `--min-quote-volume 20000`
  "$BIN" collect run --exchange "$EX" --root "$D/poolprep" --pool-only --min-quote-volume 20000 > "$D/poolprep.log" 2>&1 || exit 1
  $PY - "$D" <<'PYEOF'
import glob, json, sys
d = sys.argv[1]
f = json.load(open(sorted(glob.glob(d + "/poolprep/pool-*-*.json"))[-1]))
qv = f["qv"]
ranked = sorted(qv, key=lambda s: (-qv[s], s))
pool = [s for s in ranked if qv[s] >= 300_000]
low = [s for s in ranked if 20_000 <= qv[s] < 300_000][:8]
json.dump({"pool": sorted(pool), "qv": {s: qv[s] for s in pool}, "top10": ranked[:10], "low": low, "meta": {s: f["meta"][s] for s in ranked if s in f.get("meta", {})}}, open(d + "/universe.json", "w"))
PYEOF
fi
$PY - "$D" "$N" <<'PYEOF'
import json, sys
d, n = sys.argv[1], int(sys.argv[2])
u = json.load(open(d + "/universe.json"))
ranked = sorted(u["pool"], key=lambda s: -u["qv"][s])
missing = ranked[20:40]                       # их докинет пересмотр
keep = [s for s in ranked[:n] if s not in missing]
start = keep + u["low"]                       # low выйдут по гистерезису
json.dump({"pool": start, "qv": {s: u["qv"].get(s, 0) for s in start}, "meta": {s: u.get("meta", {}).get(s) for s in start if u.get("meta", {}).get(s)},
           "expect_add": missing, "expect_remove": u["low"]}, open(d + "/pool-start.json", "w"))
print("start", len(start), "expect_add", len(missing), "expect_remove", len(u["low"]))
PYEOF
systemd-run --quiet --unit="smoke-$TAG" --collect -p KillMode=mixed -p TimeoutStopSec=60 -p LimitNOFILE=65536 \
  "$BIN" collect run --exchange "$EX" --root "$D/data" --health "$D/health.json" --lanes 2 --pool-file "$D/pool-start.json" --pool-refresh-s 60 --pool-hold-s 150 \
  --min-quote-volume 300000 --connect-budget 60 --duration-s "$DUR" $FLAGS
sleep 5
while systemctl is-active --quiet "smoke-$TAG.service"; do sleep 3; done
$PY "$S/poolsmoke.py" "$D" "$EX" > "$D/smoke.json" 2> "$D/smoke.log"
echo "DONE" > "$D/DONE"
