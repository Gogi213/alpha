#!/bin/bash
# Проверка боевого коллектора после наката: rollout-check.sh <биржа> — версия, ядра, символы со свежими данными, соединения, провалы, queue_lag, реактор; страница :8088.
EX=$1
case $EX in
  binance) H=/data/turbo-probe/soak-binance/health.json ;;
  bybit) H=/data/turbo-probe/soak-bybit/health.json ;;
  okx) H=/data/collect/okx/health.json ;;
  bingx) H=/data/collect/bingx/health.json ;;
esac
python3 - $H $EX <<'PY'
import json, sys, time
h = json.load(open(sys.argv[1]))
p, w, b = h["process"], h["ws"], h["backpressure"]
age = int(time.time() * 1000) - h["ts_ms"]
print(sys.argv[2], "версия", p["version"], "| ядер", p["cpu_cores"], "| потоков", p["threads"], "| RSS МБ", p["rss_mb"], "| символы", h["symbols"], "| соединений", w["connections"],
      "| gaps_1h", w["gaps_1h"], "reconnects_1h", w["reconnects_1h"], "timeouts_1h", w["timeouts_1h"], "| queue_lag", b["queue_lag_ms"], "| lag_in", h["lag_in_ms"],
      "| ring", b["ring"]["depth_max"], b["ring"]["full_events_total"], "| реактор", (h.get("reactor") or {}).get("heartbeat_age_ms"), "| возраст health, мс", age)
PY
curl -s -m 8 http://127.0.0.1:8088/api/v3 | python3 -c "
import json, sys
d = json.load(sys.stdin)
for r in d['rows']:
    if r['id'] == '$EX':
        print('страница :8088 /api/v3:', r['id'], 'ok', r['ok'], 'live_bad', r['live_bad'], 'lost_1h_s', r['lost_1h_s'])
"
