#!/usr/bin/env python3
"""TK-425 (G-017, Д13): отчёт об охвате доп. данных -> markdown в stdout. Запуск на calc после tk425-final.sh.
Использование: tk425-report.py <root tk425> (по умолчанию /srv/alpha-sda/tk425)"""
import collections
import csv
import glob
import gzip
import os
import sys

R = sys.argv[1] if len(sys.argv) > 1 else "/srv/alpha-sda/tk425"
A = os.path.dirname(R.rstrip("/"))
out = []


def p(s=""):
    out.append(s)


def minutes(path):
    if not os.path.exists(path):
        return None
    with gzip.open(path, "rt") as f:
        return sum(1 for _ in f) - 1


def read_csv(path):
    return list(csv.DictReader(open(path, encoding="utf-8")))


p("## (1) Пары одной монеты")
pairs = read_csv(R + "/meta/pairs.csv")
cnt = collections.Counter(r["связь"] for r in pairs)
has = lambda r, k: bool(r[k])
combo = collections.Counter(
    "".join(c if has(r, k) else "-" for c, k in zip("LSFG", ["bybit_linear", "bybit_spot", "gate_futures", "gate_spot"])) for r in pairs)
p("Таблица `%s/meta/pairs.csv`: %d баз; связь: %s." % (R, len(pairs), ", ".join("%s %d" % kv for kv in sorted(cnt.items()))))
p("Набор площадок (L=Bybit linear, S=Bybit spot, F=Gate futures, G=Gate spot): " + ", ".join("%s %d" % kv for kv in combo.most_common()) + ".")
p("Bybit↔Gate (есть L или S и F или G): %d баз." % sum(1 for r in pairs if (r["bybit_linear"] or r["bybit_spot"]) and (r["gate_futures"] or r["gate_spot"])))
disp = read_csv(R + "/meta/disputed.csv")
p("Спорные (разные имена базы — множитель 1000/10000…, список `meta/disputed.csv`): %d." % len(disp))
p()
p("## (4) Дата листинга")
lst = read_csv(R + "/meta/listing.csv")
c = collections.Counter((r["биржа"], r["рынок"], r["источник"], bool(r["листинг_мс"])) for r in lst)
p("`meta/listing.csv`, %d инструментов. биржа/рынок/источник/дата есть: количество:" % len(lst))
for k, v in sorted(c.items()):
    p("- %s %s · %s · %s: %d" % (k[0], k[1], k[2] or "—", "есть" if k[3] else "НЕТ", v))
p()
p("## (2) BTC/ETH свечи 1 м (из TK-411/TK-423/этого тикета, повторно не качались)")
p("| месяц | инструмент | Bybit linear | Bybit spot | Gate futures | Gate spot |")
p("|---|---|---|---|---|---|")
paths = {
    "июль (24–31.07 Bybit; весь июль Gate)": (R + "/jul/candles/bybit-linear/%sUSDT.csv.gz", R + "/jul/candles/bybit-spot/%sUSDT.csv.gz",
                                              R + "/jul-gate/candles/fut/%s_USDT.csv.gz", R + "/jul-gate/candles/spot/%s_USDT.csv.gz"),
    "август": (A + "/candles-1m-aug/bybit-linear/%sUSDT.csv.gz", A + "/candles-1m-aug/bybit-spot/%sUSDT.csv.gz",
               A + "/candles-1m-aug/gate-futures_usdt/%s_USDT.csv.gz", A + "/candles-1m-aug/gate-spot/%s_USDT.csv.gz"),
    "сентябрь": (A + "/candles-1m/bybit-linear/%sUSDT.csv.gz", A + "/candles-1m/bybit-spot/%sUSDT.csv.gz",
                 A + "/candles-1m/gate-futures_usdt/%s_USDT.csv.gz", A + "/candles-1m/gate-spot/%s_USDT.csv.gz"),
}
for mon, ps in paths.items():
    for coin in ("BTC", "ETH"):
        p("| %s | %s | %s |" % (mon, coin, " | ".join(str(minutes(x % coin)) if os.path.exists(x % coin) else "нет" for x in ps)))
p()
p("## (3) OI и фандинг Bybit linear (REST, как отдаёт биржа)")
for lab, d in (("август (этот тикет)", R + "/aug-oi/cov"), ("сентябрь (TK-415)", A + "/tk415/out/cov")):
    for kind in ("oi", "funding"):
        rows = []
        for f in glob.glob("%s/bybit-%s-*.tsv" % (d, kind)):
            t = open(f, encoding="utf-8").read().rstrip("\n").split("\t")
            rows.append(t)
        n = len(rows)
        ok = sum(1 for t in rows if int(t[2]) > 0)
        tot = sum(int(t[2]) for t in rows)
        notes = collections.Counter((t[6] if len(t) > 6 else "") for t in rows if int(t[2]) == 0)
        p("- %s · %s: инструментов %d, с данными %d, строк %d; пусто: %s." % (
            lab, kind, n, ok, tot, ", ".join("%s: %d" % (k or "без примечания", v) for k, v in notes.most_common()) or "нет"))
p("Файлы: `%s/aug-oi/bybit/{oi,funding}/СИМВОЛ.csv.gz`, сентябрь `%s/tk415/out/bybit/{oi,funding}` (там же mark/index/premium). OI — шаг 5 мин (самый мелкий в API), фандинг — события." % (R, A))
p()
p("## (5) Свечи 1 м июля")
for lab, f in (("Bybit 24–31.07 (TK-427)", R + "/jul/coverage.csv"), ("Gate весь июль", R + "/jul-gate/coverage.csv")):
    if not os.path.exists(f):
        p("- %s: coverage.csv ещё нет" % lab)
        continue
    rows = read_csv(f)
    g = collections.Counter((r["рынок"], int(r["минут_с_данными"]) > 0) for r in rows)
    p("- %s (`%s`): %s." % (lab, f, "; ".join("%s со свечами %d / без %d" % (m, g[(m, True)], g[(m, False)]) for m in sorted({k[0] for k in g}))))
    why = collections.Counter(r["причина_пропусков"].split(":")[0][:60] for r in rows if int(r["минут_с_данными"]) == 0)
    if why:
        p("  Без свечей, причины: " + "; ".join("%s — %d" % kv for kv in why.most_common(6)))
p()
p("## (6) Ликвидации Bybit — «нет»")
p("- `https://public.bybit.com/` содержит каталоги kline_for_metatrader4, premium_index, spot_index, trading, spot; каталога ликвидаций нет (`/liquidation/` → HTTP 404).")
p("- `GET /v5/market/liquidation` → HTTP 404 (публичного REST истории ликвидаций у Bybit нет); в архиве сделок `trading/<SYM>/<SYM>YYYY-MM-DD.csv.gz` колонки timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional,RPI — признака ликвидации нет. Остаётся только вебсокет Bybit вживую (топик ликвидаций по документации, не проверялся) — истории нет; коллектор остановлен.")
print("\n".join(out))
