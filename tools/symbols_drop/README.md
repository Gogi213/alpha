# TK-584: символы выпали после перезапуска (17.11.2026)

Скрипты анализа к `docs/findings/symbols-drop-after-restart-2026-11-17.md`. Рабочий каталог — `TK584_DIR` (по умолчанию `/tmp/tk584`; на srv — `/data/tk584`); сырое — вне репозитория.

Порядок: на srv `runsym.py` (из пофайловых pkl `/data/arb-hold/b3/gaps` TK-534 → `runsym.json`: символ × запуск → записей, первая и последняя E), `restnow.py` (REST-состав сейчас → `rest-now.json`);
локально (`PYTHONUTF8=1` — в именах контрактов есть не-ASCII): `a1_restarts.py` (сколько выпало на каждом перезапуске) → `a2_pooldiff.py` (разница пулов по файлам `pool-*.json`) → `a5_dropsets.py` (списки выпавших) →
`candles.py` на srv (часовые свечи бирж → `candles.json`) → `a7_classify.py` (оборот за 24 ч на момент перезапуска, вернулся ли, торгуется ли сейчас) → `a8_refresh_return.py`, `a9_pool_vs_data.py`, `a10_reconcile_tk534.py`,
`a11_extra_load.py`, `a12_return_lost_hours.py`, `a3_universe_now.py` → `mkcsv.py` (список символов в `docs/findings/`). BingX 17.11: `ncscan.sh`, `ncscan2.sh` (записи NC-контрактов в файлах на srv), `wstest_bingx.sh` (проба подписки на `status=25`).
