#!/usr/bin/env python3
"""TK-445: список сделок Bybit за ВЕСЬ месяц для прошлых лет (выбор Исследователя TK-444): spot — месячный файл, linear — все дни месяца.
Имя linear разрешается кандидатами (C, 1000C...); лишние кандидаты отсеивает HEAD (tk445-dl.py --head -> 404).
Использование: tk445-past-tasks.py <out.tsv> <ГГГГ-ММ,ГГГГ-ММ,..> [--coins A,B,..]
Строка выхода: mk<TAB>sym<TAB>url-путь<TAB>относительный путь файла (как tk445-tasks.py)."""
import calendar
import sys

COINS = "TRB PEPE NOT ZRO PHB X AKE SNM BTC ETH SOL DOGE XRP ARB APT OP SUI".split()
BOTH = {"PEPE", "NOT", "X", "AKE", "SNM", "PHB"}  # возможен 1000-кратный linear
out, months = sys.argv[1], sys.argv[2].split(",")
if "--coins" in sys.argv:
    COINS = sys.argv[sys.argv.index("--coins") + 1].split(",")
n = 0
with open(out, "w") as f:
    for mo in months:
        y, m = map(int, mo.split("-"))
        for c in COINS:
            s = c + "USDT"
            f.write("spot\t%s\t/spot/%s/%s-%s.csv.gz\tbybit-spot/%s/%s-%s.csv.gz\n" % (s, s, s, mo, s, s, mo))
            for ls in [s] + (["1000" + s] if c in BOTH else []):
                for d in range(1, calendar.monthrange(y, m)[1] + 1):
                    t = "%s-%02d" % (mo, d)
                    f.write("linear\t%s\t/trading/%s/%s%s.csv.gz\tbybit-linear/%s/%s%s.csv.gz\n" % (ls, ls, ls, t, ls, ls, t))
                    n += 1
print("linear-файлов %d, месяцев %d, монет %d" % (n, len(months), len(COINS)))
