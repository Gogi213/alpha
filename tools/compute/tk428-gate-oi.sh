#!/bin/bash
# TK-428 п.1 (G-017): Gate contract_stats (OI, long/short, ликвидации liq_*, 5 мин) + funding_rate май–июль 2026, все фьючерсы.
# API держит ~180 дней (сегодня 27.10 → с ~30.04), январь–апрель недоступны: проба глубины пишется в $ROOT/depth-probe.tsv.
# Список — фьючерсы gate-list.tsv (TK-423: 1018, торговались в августе/сентябре); закрытые до августа в списке нет (записать в отчёте).
# Использование: tk428-gate-oi.sh   (ROOT=/srv/alpha-sda/tk428, SRC=/data/tk0428/src, LIST=/data/tk0393/tk423/gate-list.tsv)
set -u
root=${ROOT:-/srv/alpha-sda/tk428}; src=${SRC:-/data/tk0428/src}; list=${LIST:-/data/tk0393/tk423/gate-list.tsv}
mkdir -p "$root/gate-oi" "$root/log"
printf 'биржа,рынок,инструмент,минут_с_данными\n' > "$root/gate-oi/list.csv"
awk -F'\t' '$1=="fut" {print "gate,futures_usdt," $2 ",1"}' "$list" >> "$root/gate-oi/list.csv"
# глубина истории: первая запись contract_stats BTC_USDT с from = 1 янв / 1 апр / 25 апр / 1 мая
: > "$root/depth-probe.tsv"
for f in 1767225600 1775001600 1777075200 1777593600; do
  n=$(curl -s "https://api.gateio.ws/api/v4/futures/usdt/contract_stats?contract=BTC_USDT&from=$f&interval=5m&limit=3" | python3 -c 'import sys,json
d=json.load(sys.stdin)
print(("%d записей, первая t=%s" % (len(d), d[0]["time"])) if isinstance(d,list) and d else (str(d)[:120]))')
  printf '%s\t%s\n' "$f" "$n" >> "$root/depth-probe.tsv"
done
# 2026-05-01 00:00 UTC .. 2026-08-01 00:00 UTC (исключая)
K_START=1777593600000 K_END=1785542400000 python3 -I "$src/tk415-oi-funding.py" gate "$root/gate-oi" "$root/gate-oi/list.csv" 15 >> "$root/log/gate-oi.log" 2>&1
echo "gate-oi rc=$? $(date +%T)" >> "$root/log/run.log"
