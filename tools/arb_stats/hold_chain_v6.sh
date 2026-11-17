#!/bin/bash
# arb-holding v6 (G-023, TK-534): пересчёт после правки «хвост после последней записи символа — молчание» (hold_rawgaps.py, v6).
# Нужны уже готовые разбор книг (b3), таблицы scan/table и marks/trades/dead v4; пересчитываются rawgaps → filter → resim → report → stay и проверки.
# Старые выходы v4 уходят в v4/pre-tail/ (не удаляются). Запуск юнитом systemd-run на srv из tools/arb_stats; конец — файл v4/CHAIN6.DONE.
set -euo pipefail
V=/data/arb-hold/v4
B=/data/arb-hold/b3
cd "$(dirname "$0")"
mkdir -p $V/pre-tail
for f in rawgaps.pkl flag-strict.pkl flag-tol.pkl resim-strict.pkl resim-tol.pkl tables-strict.md tables-tol.md pairs-strict.csv pairs-tol.csv cases-strict.csv cases-tol.csv stay.pkl stay.md check-raw.md check-raw.log window.md cmp.md incident.md; do
  [ -e $V/$f ] && mv $V/$f $V/pre-tail/
done
rm -f $V/CHAIN6.DONE
python3 hold_rawgaps.py $B/gaps $V/rawgaps.pkl > $V/rawgaps.log 2>&1
bash hold_run_v4.sh filter > $V/filter.log 2>&1
bash hold_run_v4.sh resim > $V/resim.log 2>&1
bash hold_run_v4.sh report stay > $V/report.log 2>&1
python3 hold_window.py $V/flag-strict.pkl $V/rawgaps.pkl $V/window.json $V/dead.json $V/window.md > $V/window.log 2>&1
python3 hold_cmp_review.py $V/flag-strict.pkl $V/cmp.md > $V/cmp.log 2>&1
python3 hold_check.py raw $V/flag-strict.pkl $B 30 7 $V/check-raw.md > $V/check-raw.log 2>&1
python3 hold_incident.py $V/flag-strict.pkl $V/inc/incidents.txt $V/inc/ser > $V/incident.md 2> $V/incident.err   # inc/ — копия docs/findings/arb-holding-incidents-2026-11-17.txt и снимки series-minutes.jsonl пяти бирж (<биржа>.jsonl)
touch $V/CHAIN6.DONE
