# G-022 pool-fix: передача дел исполнителя, 16.11.2026

Задача координатора: исправить дыры сбора из `docs/findings/collect-gap-audit-2026-11-16.md` §3 (ветка аудита `bb/g-022-collect-gap-v1-thr_3rgkrr82s2` @ 1c1b0a45) на коде боевых сборок. Ветка работы — `bb/g-022-collect-pool-fix-v1-thr_p3xe532pjz`, основа — `bb/g-022-gate-collector-v1-thr_2r84hvf7gy` @ cfce94f6 (содержит `final6k` = 4b791a2c и адаптер Gate; боевые `final6j` = c62d69aa отличаются от `final6k` только правилом «свежие данные» под флагом `--no-pong-only-degrade`). Выкладывать в бой — только после проверяющего.

## Сделано (коммит b46d3694 и далее)
1. Живой пул — `src/collector/livepool.rs`: опрос REST-оборота (`Exchange::load_turnover`) в потоке `pool-refresh`, гистерезис (`PoolState`), раскладка по шардам (`Layout`), файл пула запуска (`PoolFile`); дельта SUBSCRIBE/UNSUBSCRIBE на открытом соединении (`StreamSet` в `session.rs`, события `pool_delta`); у всех пяти адаптеров `unsubscribe_messages`, `load_turnover`.
2. Топ-10 не исключается нигде: `default_top_exclude` = 0 (причина исключения — правило пула исследовательского бота, не ёмкость; замер ёмкости — `tools/turbo/top10_ladder.sh`).
3. Health: `symbols.not_subscribed`, раздел `pool`; страница: строка «не подписано (REST)».
4. Бюджет: резерв шардам без соединения (`Budget::set_urgent_reserve`), потолки на шард (`Cap`: замены 4, отбраковки 6 за 5 мин); тест бури 18:12 — `tests_pool.rs::binance_storm_1218_…`.
5. `ladder.rs`: убраны 22 срабатывания линта (база превышала бюджет 836: 854).

## Состояние проверок
- `bash tools/vps-check.sh <дерево> all` — результат смотреть в `$BB_THREAD_STORAGE/all-*.txt` (тред исполнителя); известный нестабильный на нагрузке тест — `bingx_session_unpacks_gzip_answers_ping_and_does_not_write_heartbeats` (порог `books > 20`, на параллельном прогоне вышло 20).
- Два теста страницы здоровья (`test_http_api_and_page`, `test_entrance_switch_in_hour_is_warn_and_passes_through`) красные и на основе cfce94f6 (макет страницы против старых тестов) — не от этой правки.

## Что осталось
- Прогнать `all` до зелёного; закоммитить.
- Лестница «топ-10 включён» на srv (Binance, Bybit): собрать боевой бинарь (`binrel`), `systemd-run … top10_ladder.sh`, затем `prodwin.py`; числа — в отчёт `docs/findings/collect-pool-fix-2026-11-16.md`.
- Живая проверка пересмотра на бирже копией: `--pool-file` с урезанным пулом + `--pool-refresh-s 60 --pool-hold-s 120` — события `pool_delta`, файл пула.
- Отчёт и итог координатору (статус, ветка + SHA, числа, отклонения). Выкладка — не делать до проверяющего.
