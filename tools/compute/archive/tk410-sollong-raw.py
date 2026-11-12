#!/usr/bin/env python3
"""TK-410 хвост: swaps-usd.csv (TK-389, 7 монет Solana 01.09–14.10, price_usd) -> long-raw/COIN.csv формата tk410-map2 (kind=sol, цена исполнения USD, без середины пула).
Дедуп (slot,txidx,sig) не нужен: map2 сам режет (block,logidx); txidx — logidx, повторы одного tx одной монеты схлопываются (как в W11). Запуск (calc, cwd /data/tk0393/tk410)."""
import csv, os
os.makedirs('long-raw', exist_ok=True); fo = {}
for r in csv.DictReader(open('/srv/alpha-sda/tk387-sqdsol/swaps-usd.csv')):
    c = r['coin']
    if c not in fo:
        fo[c] = open(f'long-raw/{c}.csv', 'w'); fo[c].write('ts,block,logidx,kind,mid_raw,liq0,liq1,tick\n')
    if r['price_usd']: fo[c].write(f"{r['ts']},{r['slot']},{r['txidx']},sol,{r['price_usd']},,,\n")
for c, f in fo.items(): f.close()
print(sorted(fo))
