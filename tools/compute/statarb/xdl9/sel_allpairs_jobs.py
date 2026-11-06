#!/usr/bin/env python3
"""TK-493 S1 (П-37 r3.1 §11.8, Steam Deck, stdlib): список заданий «все пары каталога без отбора» для контроля, который считает волна 4 (wave4.sh).
Пары = все неупорядоченные пары символов каталога A (каталог ~/xdl9/cat/a/<биржа>-<полоса>/: основной контракт на базовый актив, число баров выше порога
§6.4); ориентация одна - y = символ с большим именем, x = с меньшим (не как в form: направление по p в контроле не выбирается, чтобы не вносить отбор).
Окно заданий - торговое, 15.10-04.11 (сутки 2479-2500). Скрипт читает только имена файлов каталога, свечи и фандинг не открывает.
Запуск: python3 sel_allpairs_jobs.py <каталог, например binance-crypto> <выходной csv> [from_day to_day]
(F8/S3: необязательные сутки; по умолчанию торговые 2479-2500, для сухого прогона на формировании - 2468 2479)
"""
import os, sys

cid, out = sys.argv[1], sys.argv[2]
d0, d1 = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) >= 5 else (2479, 2500)
d = os.path.join(os.path.expanduser("~/xdl9"), "cat", "a", cid)
syms = sorted(f[:-4] for f in os.listdir(d) if f.endswith(".sap"))
n = 0
with open(out, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("y,x,from_day,to_day\n")
    for i in range(len(syms)):
        for j in range(i):
            fh.write("%s,%s,%d,%d\n" % (syms[i], syms[j], d0, d1))
            n += 1
print("каталог %s: символов %d, пар %d -> %s" % (cid, len(syms), n, out))
