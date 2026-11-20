# TK-417 — сделки Bybit сентября 2026 (linear + spot) для TK-414

Файлы на calc: `/data/tk417/bybit-linear/<СИМ>/` (50 инстр., 1404 дн.-файла, 15 ГБ) и `/data/tk417/bybit-spot/<СИМ>/`
(17 мес.-файлов, 133 МБ). Как отдаёт public.bybit.com, без агрегатов: linear — timestamp (с, дроби), side, size, price,
grossValue, …; spot — id, timestamp (мс), price, volume, side. Закачка: job 1027002529853 (net), 17 мин, 429/403 = 0;
калибровка 1/2/4 потока = 30/58/30 МБ/с, боевой прогон 1 поток. Скрипт `tools/compute/tk417-bybit-trades-dl.py`.

Нет в архиве (404, 96 дн.-файлов): 4STOCKUSDT 8, CPUSDT 3, CYPHUSDT 20, HAJIMIUSDT 6, ICXUSDT 12, LONGXIAUSDT 13,
SECZUSDT 17, STONKUSDT 17 — `tk417-missing-2026-10-27.tsv`.

Сверка со свечами 1 м TK-411 (job 1027004347222, `tools/compute/tk417-reconcile.py`, `tk417-reconcile-2026-10-27.csv`):
linear — 140 инстр.-суток (05/15/25 сент.), spot — 510 суток. Σобъём и Σquote: отн. расхождение ≤ 2e-15; доля минут
с совпавшими v и quote_v = 1,0000 везде (плохих строк 0). Число сделок в свечах Bybit API пустое — сверять не с чем.
