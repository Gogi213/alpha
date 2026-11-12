#!/usr/bin/env python3
"""TK-388: BBO Gate по секундам из почасовых файлов стакана (логика hour() = tk380-ob-bbo.py), один час -> один файл.
Запуск: tk388-bbo-hour.py OUTROOT FILE.csv.gz [FILE...]  -> OUTROOT/<COIN>/<имя файла>.csv.gz: sec,bid,ask"""
import sys, os, gzip, importlib.util
spec = importlib.util.spec_from_file_location('ob', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tk380-ob-bbo.py')); ob = importlib.util.module_from_spec(spec); spec.loader.exec_module(ob)
root = sys.argv[1]
for f in sys.argv[2:]:
    b = os.path.basename(f); coin = b.split('_USDT-')[0]; d = f'{root}/{coin}'; os.makedirs(d, exist_ok=True)
    o = f'{d}/{b}'
    if os.path.exists(o): continue
    out = []; ob.hour(f, out)
    with gzip.open(o + '.tmp', 'wt') as w:
        for t, bb, k in out: w.write(f"{t},{'' if bb is None else bb},{'' if k is None else k}\n")
    os.replace(o + '.tmp', o)
