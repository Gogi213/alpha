#!/usr/bin/env python3
"""TK-453: обновить docs/findings/binance-klines-1m-2026-10-30.{md,csv,-gaps.csv} по coverage.csv/gaps.csv с calc.
Использование: tk453_report.py <coverage.csv> <gaps.csv> <каталог findings>"""
import csv
import re
import shutil
import sys

cov, gaps, out = sys.argv[1:4]
base = out + "/binance-klines-1m-2026-10-30"
rows = [x for x in csv.DictReader(open(cov, encoding="utf-8")) if x["символ"]]
g = list(csv.DictReader(open(gaps, encoding="utf-8")))
minutes = sum(int(x["минут"]) for x in rows)
miss = sum(int(x["пропусков_мин"]) for x in rows)
zips = sum(int(x["файлов_zip"]) for x in rows)
last = {x["последняя_мс"] for x in rows}
assert len(rows) == 84 and last == {"1793318340000"}, (len(rows), last)
d28 = [x for x in g if x["начало_utc"].startswith("2026-10-28")]
assert len(d28) == 84 and len(g) == 86
shutil.copy(cov, base + ".csv")
shutil.copy(gaps, base + "-gaps.csv")

md = open(base + ".md", encoding="utf-8").read()
fmt = lambda n: f"{n:,}".replace(",", " ")
new = re.sub(r"последняя минута \*\*2026-10-27 23:59 UTC\*\* \(.*?\)\. Всего \*\*[\d ]+ минут\*\*, пропусков в файлах архива \*\*[\d ]+\*\* мин\. Сырьё \([\d ]+ zip",
             f"последняя минута **2026-10-29 23:59 UTC** (дневной архив **28.10 Binance не опубликовал** — HTTP 404 на 30.10 17:08 UTC; 30.10 ещё не закончился). Всего **{fmt(minutes)} минут**, пропусков в файлах архива **{fmt(miss)}** мин (из них 28.10: 84 × 1 440 = 120 960). Сырьё ({fmt(zips)} zip", md, count=1)
assert new != md or "2026-10-29 23:59 UTC** (дневной" in md, "шапка не заменена"
md = new
md = md.replace("5 990 zip, у каждого `.CHECKSUM`; sha256 файла == checksum у всех 5 990", f"{fmt(zips)} zip, у каждого `.CHECKSUM`; sha256 файла == checksum у всех {fmt(zips)}")
md = md.replace("— 84 из 84. Независимый", "— 84 из 84 (с учётом дыры 28.10: `пропусков_мин` = старые + 1 440). Независимый")
md = md.replace("Остались две, потому что их нет и в дневных архивах (404):",
                "Остались две, потому что их нет и в дневных архивах (404) (+ дыра 28.10 у всех 84 — ниже):")
md = re.sub(r"- \*\*Докачка 29\.10 \(TK-453\):\*\*.*?\n(?=- \*\*Потоки)", "", md, flags=re.S)
md = md.replace("- **Потоки/запуск:**",
                "- **Докачка 29.10 (TK-453):** 84 дневных `…-2026-10-29.zip` (по одному на монету) скачаны, sha256 == `.CHECKSUM` (verify.csv: 6 074 из 6 074 ok), пересобрано 84 монет; минуты, уже лежавшие в `.csv.gz`, совпали байт в байт (иначе сборка останавливалась, rc 0); последняя минута у всех 84 — 1793318340000 (2026-10-29 23:59 UTC). **28.10 — дыра у всех 84 монет** (`…-gaps.csv`, 84 строки по 1 440 мин): `data.binance.vision` отдаёт 404 на `…-2026-10-28.zip` (проверено на BTCUSDT 30.10 17:08 UTC); не выдумывалась, REST не использовался. Закроется месячным архивом 2026-10 (выходит в начале ноября) либо поздней публикацией дневного. 30.10 UTC ещё идёт — дневной архив появится не раньше 31.10.\n- **Потоки/запуск:**", 1)
md = md.replace("(`fill`, `tk450-fill.sh`, `gaps-before-fill.csv`)", "(`fill`, `tk450-fill.sh`, `gaps-before-fill.csv`)")
md = md.replace("`…-gaps.csv` (две дыры)", "`…-gaps.csv` (86 строк: дыра 28.10 у 84 монет + ICP 2022-09-22…26 + PUMP 2025-07-10)")

# таблица охвата
head, rest = md.split("| монета | символ | первая дата | минут | пропуск, мин | zip |\n|---|---|---|---:|---:|---:|\n", 1)
_, tail = rest.split("\n\n## Не вошло", 1)
tab = "".join(f"| {x['монета']} | {x['символ']} | {x['первая_мс'] and __import__('datetime').datetime.utcfromtimestamp(int(x['первая_мс'])/1000).strftime('%Y-%m-%d')} | {fmt(int(x['минут']))} | {fmt(int(x['пропусков_мин']))} | {x['файлов_zip']} |\n" for x in rows)
md = head + "| монета | символ | первая дата | минут | пропуск, мин | zip |\n|---|---|---|---:|---:|---:|\n" + tab.rstrip("\n") + "\n\n## Не вошло" + tail
md = re.sub(r"Дозагрузка 28–30\.10 — когда Binance.*$",
            "Дозагрузка: 28.10 (84 монеты) — когда Binance опубликует дневной архив или выйдет месячный 2026-10; 30.10 — после 31.10 (повтор `tools/compute/tk453-pipe.sh`: `map`/`dl`/`verify`/`build`/`coverage`, уже лежащее не качается).", md, flags=re.S)
open(base + ".md", "w", encoding="utf-8").write(md)
print("ok", len(rows), minutes, miss, zips)
