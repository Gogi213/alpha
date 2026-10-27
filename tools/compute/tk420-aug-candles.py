#!/usr/bin/env python3
"""TK-420 вар. 5: свечи 1 м Bybit linear за август из УЖЕ скачанных дневных архивов сделок (/data/tk420/tr/bybit/bybit-linear/СИМВОЛ/СИМВОЛ2026-08-DD.csv.gz).
Логика сборки — tk411-bybit-trades.py без правок (добавление дня, формат выхода); меняется только источник: файл на диске вместо public.bybit.com.
Использование: tk420-aug-candles.py <root выхода> <список-символов.txt> [потоков=4] [первый_день=2026-08-01] [последний_день=2026-08-31] [выход=candles/bybit-linear] [каталог архивов]"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "tk411-bybit-trades.py"), encoding="utf-8").read()
ns = {"__name__": "tk411"}
exec(compile(SRC.rsplit("\nmain()", 1)[0], "tk411-bybit-trades.py", "exec"), ns)
TR = sys.argv[7] if len(sys.argv) > 7 else "/data/tk420/tr/bybit/bybit-linear"
del sys.argv[7:]


def local_fetch(url):
    rel = url[len(ns["BASE"]):]  # СИМВОЛ/СИМВОЛ2026-08-DD.csv.gz
    p = os.path.join(TR, rel)
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return f.read()


ns["fetch"] = local_fetch
ns["main"]()
