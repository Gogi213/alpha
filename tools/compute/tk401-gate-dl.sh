#!/bin/bash
# TK-401: сделки Gate 15–21.10 для новых монет TK-393 без Gate-данных (список — skip.csv «нет данных Gate»). Спот всем; фьюч — монетам «спот+фьюч» в pools-new.csv.
# Свой корень /srv/alpha-sda/tk401-gate (списки tk372 не трогаем); после — симлинки в tk372-gate/raw (event.py читает оттуда). Докачка: готовое пропускается.
set -e
R=/srv/alpha-sda/tk401-gate; S=/data/tk0372/tk372-gate-dl.py; O=/data/tk0393/tk401; mkdir -p $R/log
python3 - <<'PY'
import csv
R = '/srv/alpha-sda/tk401-gate'; O = '/data/tk0393/tk401'
coins = [r['coin'] for r in csv.DictReader(open(O + '/skip.csv')) if r['why'] == 'нет данных Gate']
fut = {r['coin'] for r in csv.DictReader(open('/data/tk0393/pools-new.csv')) if r['kinds'] == 'спот+фьюч'}
H = 'gate,market,turnover24h_usd,native_turnover,native_unit,gate_quanto_multiplier,gate_trade_status'
with open(R + '/list-spot.csv', 'w') as f:
    f.write(H + '\n')
    for c in coins: f.write(f'{c}_USDT,спот,0,0,USDT,,tradable\n')
with open(R + '/list-fut.csv', 'w') as f:
    f.write(H + '\n')
    for c in coins:
        if c in fut: f.write(f'{c}_USDT,фьюч,0,0,USDT,,trading\n')
print(len(coins), 'монет, фьюч', len([c for c in coins if c in fut]))
PY
python3 $S run --root $R --workers 4 --rps 15 >> $R/log/run.log 2>&1
for m in spot fut; do for d in $R/raw/$m/*_USDT; do [ -d "$d" ] || continue; b=$(basename $d); [ -e /srv/alpha-sda/tk372-gate/raw/$m/$b ] || ln -s $d /srv/alpha-sda/tk372-gate/raw/$m/$b; done; done
echo DONE > $R/gate401.done
