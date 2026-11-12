# TK-488 — проверка пересдачи 274d692e / 2fd575dc (возврат Н6–Н8), 03.11 ~06:10 GMT+4 — ПРИНЯТЬ

Пересдача после двух возвратов (`tk488-tradfi-return-2026-11-03.md`, `tk488-tradfi-return2-2026-11-03.md`). Диспетчер поставил
«потолок возвратов → владельцу»; надзиратель (05:52) верно указал: решение есть — В-93 «нет надо все…». Вопрос владельцу не нужен,
проверяю сдачу по делу.

Читал: `git show 274d692e` (код tk488_tradfi_list.py / tk488_universe.py / tk488_report.py), `docs/findings/tradfi-candles-2026-11-03.md`,
`docs/findings/tradfi-class-table-2026-11-03.csv` (пересчёт по строкам), `data/tk488/tradfi-list.csv`; на calc сам открыл `/data/tk0488/universe3`.

## Пункты

| пункт | итог | где / команда |
|---|---|---|
| Н1–Н5, замечание sha | держатся (приняты во 2-й проверке, не тронуты) | `tk488-tradfi-return2-2026-11-03.md` |
| п.2/4а/5а тесты | держится | `python -m pytest tools/compute/tests/test_tk488_{tradfi_list,universe,report,fill,gate_funding,known3}.py -q` → 22 passed |
| Н6 DEFIUSDT | держится | `tk488_tradfi_list.py:47` `'TradFi' not in sub`; список: DEFI/BTCDOM/ALL — флаг crypto-index; в таблице и `universe3` нет |
| Н7 листинг | держится | `tk488_universe.py` `listing_day`/`after_listing`; сам пересчитал по таблице: `вселенная_с` < листинг −1 сут — 0 из 436, свечи раньше листинга — 0, без даты листинга — 0; MDTUSDT в `union-binance-um.csv` months_pass 0, в universe нет |
| Н8 USDC_USDT | держится | флаг `стейбл-коин` (1 в списке), в таблице и `universe3` нет |
| PAXG/XAUT флаг | держится | таблица: оба `токен на золото (крипто-актив), оставлен во вселенной` |
| итог 436 = 168/184/84 | держится | таблица: Counter по биржам 168/184/84; calc `universe-*.csv`: уникальных символов 168 / 184 / 84 |

## [вживую] п.4, п.5 — открыл сам

- calc `root@89.163.242.211:/data/tk0488/universe3/` по ssh, 03.11 ~06:05, ответ за ~3 с: 13 файлов (universe/union/turnover/sources ×3,
  turnover-delta); `universe-binance-um.csv` начало `AAOIUSDT 2026-06-09..2026-11-02`, `AAPLUSDT 2026-04-07..2026-11-02`; символов 168/184/84;
  grep DEFIUSDT|MDTUSDT|USDC_USDT в universe-* — пусто, в union — только MDTUSDT с 0 месяцев прохода.
- Свечи/фандинг и sha крипто Binance (п.5) открыты во 2-й проверке (`sha-crypto-{before,after}.txt` diff 0); в этом круге не перекачивались.
- п.4 known3 (ADBE/AMDSTOCK/AMAT 3/3) — сверен во 2-й проверке.

## Числа владельцу

| число | файл:строка |
|---|---|
| вселенная TradFi 436 контрактов: Binance 168, Bybit 184, Gate 84 | docs/findings/tradfi-candles-2026-11-03.md:7 |

## Замечания (не возврат)

- Кандидаты на удаление (свечи/фандинг DEFI/USDC/MDT вне вселенной) — только словом владельца, отчёт:38 так и пишет.
- MDTUSDT.csv.gz TK-476 хранит два актива подряд; потребитель свечей TK-488 должен брать минуты от `вселенная_с` — так и сделано вселенной.

Хвостов нет; решения: В-93 (порог 1 млн все три, все классы), В-213 (дельта), сырьё не удалено.
