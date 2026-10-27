# TK-425 доп. данные G-017 (Д13) — приёмка Судьи, 27.10.2026 ~23:05

Сдача: docs/findings/g017-extra-data-2026-10-27.md (5acc7149), файлы calc /srv/alpha-sda/tk425. Сверено по файлам на calc.

| пункт заказа | да/нет | на чём проверено |
|---|---|---|
| (1) пары Bybit L↔S, Gate F↔G, Bybit↔Gate, спорные отдельно | да | meta/pairs.csv 2794 строк; связь exact 982 / mult 26 / single 1786; Bybit↔Gate (L/S и F/G) 971; disputed.csv 34 — = отчёту |
| (2) BTC/ETH 1 м обеих бирж, без повторной закачки | да | таблица минут по месяцам; ссылки на TK-411/TK-423 |
| (3) OI 5 мин и фандинг Bybit linear | да | aug-oi/bybit/oi 888 файлов, funding 888, с данными 816 (пересчёт) = отчёту; BTCUSDT OI авг 8928 строк = 31×288 (полный месяц); формат = TK-415 (931 сент.) |
| (4) листинг | да | listing.csv: bybit linear 1966, spot «нет» 527, gate fut 1066, spot 2104 + «нет» 79 — = отчёту |
| (5) свечи июля | да | по TK-427 хватает 24–31.07; jul-gate/coverage: fut 823/195, spot 2084/85 — = отчёту; причины пропусков в строках |
| (6) ликвидации Bybit — «нет» с источником | да | public.bybit.com каталоги, /v5/market/liquidation 404, колонки архива сделок |
| отчёт docs/findings + охват/пропуски/причины | да | раздел «Пропуски — сводка» |

Замечания (вывод не меняют): в jul/coverage.csv причина «полный месяц» для окна 24–31.07 — точнее «полное окно»;
колонка single_open_interest — та же, что в принятом TK-415.

Вердикт: **принято**, хвостов нет.

## Ключевые числа
| число | файл:строка |
|---|---|
| 971 | docs/findings/g017-extra-data-2026-10-27.md:20 |
| 34 | docs/findings/g017-extra-data-2026-10-27.md:21 |
| 888 | docs/findings/g017-extra-data-2026-10-27.md:36 |
| 816 | docs/findings/g017-extra-data-2026-10-27.md:36 |
| 1966 | docs/findings/g017-extra-data-2026-10-27.md:44 |
| 527 | docs/findings/g017-extra-data-2026-10-27.md:45 |
| 823 | docs/findings/g017-extra-data-2026-10-27.md:53 |
| 2084 | docs/findings/g017-extra-data-2026-10-27.md:53 |
