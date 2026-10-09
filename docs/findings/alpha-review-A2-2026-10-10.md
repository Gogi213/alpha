# Ревью alpha, часть А2 — формы исследования в Rust (TK-127) — 2026-10-10

Инженер. Кода не менял, сервер не трогал. Охват: `src/commands/lob/bounce_grid{.rs,/*}`, `touches{.rs,/*}`, дифы веток
`tk115-p12` / `tk065-r2m` / `tk084-r2dl` / `tk049-b15b` против `HEAD` (`git diff --stat HEAD...<ветка>`), их коммиты.
`r1.rs` — это `src/lob/r1.rs` (на стволе; в `commands/lob` его нет); ветка p12 правит 5 строк.
**Не охвачено (лимит запуска 20 мин):** построчное чтение `drive_day`/`plan_grid`; rust-code-analysis не запускался; п.7 — замер clippy на VPS. Тяжесть: блокер / важно / потом.
Рантайм: Г горячий, Т тёплый, Х холодный (`runtimes-design-2026-10-10.md`); весь А2 — Т/Х (прогонщик и CSV), кроме
условий R2, которые попадают в Г через `strategy.rs` (чужая часть, TK-124).

## 1. Мёртвый код
| # | находка | тяжесть | доказательство |
|---|---|---|---|
| М1 | `touches/row.rs: approach_row_pair_names` — вне тестов вызовов нет (1 вызов в тестах). Кандидат на удаление или `#[cfg(test)]`. | потом | грепом по `src/` без `tests.rs` — 0 |
| М2 | `bounce_grid/e2e.rs` (175 стр.) — подключается `mod e2e` (`bounce_grid.rs:107`), активируется `ALPHA_E2E` (`e2e.rs:12`); нигде не описан в `COMMANDS.md`. Либо тестовый стенд в боевом модуле (→ `#[cfg(test)]`), либо недокументированный режим. | потом | `grep ALPHA_E2E docs` = 0 |
| М3 | Остальные `pub fn` в `bounce_grid/*`, `touches/*` имеют внешние вызовы вне тестов (проверено циклом по всем `pub fn`; 1 исключение — М1). `#[allow(dead_code)]` в охвате — 0. | ок | цикл grep |
| М4 | На `tk049-b15b` новые `prep_events.rs` (597 стр.) и `window_store.rs` (491 стр.) — недовлиты в ствол; на стволе их нет, т.е. «мёртвы» относительно ствола, пока ветка не влита. | важно (недовлитое, см. п.6) | `git diff --stat HEAD...tk049-b15b` |

## 2. Дизайн (границы, A1–A9, три рантайма)
| # | находка | тяжесть | доказательство |
|---|---|---|---|
| Д1 | **Поведение прогонщика определяется переменными окружения:** в охвате 16 разных `ALPHA_*` (`ATTEMPT_STATS, BAND_COUNT_OFF, HOLDS_MEMO, ADMIT_SOA, ADMIT_CACHE, SKIP_NOSIGNAL, TRIM_ROWS, TICK_STATS, SIG_CACHE, SHARED_CELLS, SHARED_ENGINE, APPROACH_BIN(_DIR/_LEVEL), TOUCH_BIN, E2E`), разбросаны по `bounce_grid.rs:275–1308`, `drive.rs:177–682`, `touches/{abin,tbin}.rs`. В `COMMANDS.md`/`ARCHITECTURE.md`/`CLAUDE.md` упомянуты 2 из 14 (`APPROACH_BIN`, `TOUCH_BIN`); путь счёта (`ADMIT_SOA`, `TRIM_ROWS`, `HOLDS_MEMO`, `SHARED_ENGINE`) меняется флагом без записи в выходе прогона. Все чтения — в Т-коде, на событие не влияют. Нужно: один `RunFlags::from_env()` на входе команды + печать/запись в выход (воспроизводимость). | важно | `git grep -oh ALPHA_ HEAD -- <охват>` = 16; грепы по докам |
| Д2 | **Нарушение однонаправленности A-слоёв «команда → чистая логика»:** `bounce_grid.rs` (1 358 стр.) содержит `run()` с вложенным циклом по суткам, чтением флагов, ограничением строк (`ALPHA_TRIM_ROWS`, :1000), статистикой (:1199, :1308) — оркестрация и политика в одном теле. Плана в `plan.rs` и драйвера в `drive.rs` недостаточно: часть решений осталась в `bounce_grid.rs`. | важно | `wc -l`; позиции env-чтений выше |
| Д3 | **R2-тесты не в стандартном гейте:** 4 блока `cfg(feature = "r2")` в `bounce_grid/tests.rs` на p12 бегут только с `--features r2`, а `vps-check.sh all` собирает без неё (общий с TK-124 Д1). **Исправление по критике Судьи:** прежняя гипотеза «формы R2 без feature молча не исполняются» **опровергнута** — `forms.rs:209` (p12): `ensure!(strategy::R2 \|\| !form.is_r2(), "…только в исследовательском бинарнике (cargo feature r2)")` отказ; `plan.rs:541` то же для `tape_log`. Тяжесть «блокер» по А2 снята: остаётся «R2-тесты не в гейте» (важно; блокер вливания — по TK-124 Д1). | важно | `git grep 'feature = "r2"' tk115-p12 -- src/commands` = 4; `forms.rs:209` |
| Д4 | `touches`: два параллельных бинарных кэша `abin.rs` (289) и `tbin.rs` (265) с одинаковым каркасом (`enabled/stamp/put/get/encode_file/decode_file`) — см. п.3. Оба за env-флагами, умолчание = CSV. Кэш — Т/Х, на A1–A9 не влияет, границы чистые. | потом | `fn` списки обоих файлов |

## 3. Дублирование (числом копий)
| # | находка | копий | тяжесть | доказательство |
|---|---|---|---|---|
| Дб1 | Каркас бинарного кэша (stamp длины+mtime, varint `put/get`, `encode_file/decode_file`, чтение `ALPHA_APPROACH_BIN_LEVEL`) в `abin.rs` и `tbin.rs`. Расхождение при diff окон 100 строк — 134 строки diff (тип строк разный: `ApproachRow` vs `TouchRow`), но каркас и уровень zstd (`ALPHA_APPROACH_BIN_LEVEL` читается **в обоих**, `abin.rs:167`, `tbin.rs:123`; в `tbin` имя флага чужое — запах). Свести в общий `cache_codec<T: Row>`. | 2 | важно | `abin.rs:17–182`, `tbin.rs:17–138` |
| Дб2 | Парсер формы выхода/входа: `forms.rs::parse` / `parse_any` / `parse_wall_eat` / `parse_half` + на каждую ветку R2 свои `parse_*`; тест-таблицы имён форм дублируются в `sets.rs` (whitelist). На p12 +246, r2m +159, r2dl +136 строк в одном `forms.rs`, и добавлена каждая форма в **три места** (форма, whitelist в `sets.rs` +19 стр. у каждой из трёх веток, тест порогов времени) — «одна форма = три правки». | 3 места на форму | важно | `git diff --stat HEAD...<ветка> -- forms.rs sets.rs` |
| Дб3 | `write_numbers`, `write_minute_flow`, `run_moves` в `touches/*`: схема «открыть CSV → заголовок → цикл → flush» повторена 4×; `touch_row_pairs` (132 стр.) — таблица пар имя/значение с 1 ветвлением, по сути данные в виде кода. | 4 | потом | длины функций п.7 |
| Дб4 | Те же `plan.rs`-правки на ветках: `backtest/plan.rs` +91/+57/+62, `bounce_grid/sets.rs` +19/+19/+19 — три ветки правят одно и то же место, три общих коммита (`cac9a7c6, aacee828, 2a03cdc5` лежат в `r2m∩p12` = 26 коммитов, `r2dl∩p12` = 17). | 3 ветки | потом | `comm` списков коммитов |

## 4. Избыточность
| # | находка | тяжесть | доказательство |
|---|---|---|---|
| И1 | `drive.rs` содержит `drive_day`, `drive_day_shared` (:546), `exit_groups` (:208), `signals_for` (:47) — три драйвера суток (одиночный, общий-пачками, windowed). Совпадает с Р1 TK-124 (8 путей «клетка×сутки»); из А2: выбор пути — по env (`ALPHA_SHARED_ENGINE`, `drive.rs:682`) и аргументам, эталон не назван. | важно | `drive.rs:546, 682` |
| И2 | `cache.rs` (159 стр.) + `carry.rs` (277) + `entry_sigma.rs` (77) + `verdict.rs` (35): `verdict.rs` из 35 строк — кандидат на слияние в `outputs.rs`. | потом | `wc -l` |
| И3 | `#[allow(clippy::too_many_arguments)]` на 5 функциях (`carry.rs:153, drive.rs:207, forms.rs:403, outputs.rs:181, :333`) — вместо структуры параметров; зеркалит смесь ответственностей. | потом | `grep allow` |

## 5. Запахи
| # | находка | тяжесть | доказательство |
|---|---|---|---|
| З1 | Чужое имя флага: `tbin.rs:123` читает `ALPHA_APPROACH_BIN_LEVEL` (флаг подходов) для кэша касаний. | потом | `tbin.rs:123` |
| З2 | Тест-стенд в боевом файле: `e2e.rs` (`ALPHA_E2E`) в списке модулей `bounce_grid.rs:107` без `cfg(test)`. | потом | `e2e.rs:12` |
| З3 | `bounce_grid/tests.rs` — 3 989 стр. одним файлом (+117 p12, +10, +13, +5 на ветках); `touches/tests.rs` — 920. Поиск и ревью теста — по смещению. Разбивать по темам (формы / планы / выход / R2). | важно | `wc -l` |
| З4 | Магические env-значения «`== "1"` / `== "0"` / `is_some()`» — три разные семантики «включён» (`bounce_grid.rs:280` `=="0"`, `:837` `=="1"`, `:275` `is_some`). Путаница: `ALPHA_ATTEMPT_STATS=0` включает. | важно | `bounce_grid.rs:275, 280, 837, 1308` |
| З5 | `unwrap_or`/молчаливый откат на кэш при несовпадении штампа (`abin.rs:47`) — по замыслу, но без счётчика промахов в выходе → скорость волны зависит от тёплого кэша без отметки в отчёте. Запись об этом — в Д1. | потом | `abin.rs:47–78` |

## 6. Коммиты своей части (git)
| # | находка | тяжесть | доказательство |
|---|---|---|---|
| К1 | **Четыре ветки вне ствола.** p12 (+59 коммитов, 35 файлов, +2 992/−51), r2m (+26, 14 ф., +1 562), r2dl (+18, 18 ф., +1 414), b15b (+11, 18 ф., +1 727; не пересекается с p12: 0 общих). r2m целиком ⊂ p12 (26 из 26 общих), r2dl — 17 из 18 общих с p12. Т.е. «r2m» и «r2dl» не самостоятельные ветки — подмножества p12; вливать p12 и закрывать остальные. | важно | `comm -12` по спискам коммитов |
| К2 | **Коммит `005cff86 «wip-walls»`** (George Stern, 09.10 06:46): +66 строк `src/lob/levels.rs`, лежит только в `tk115-p12`, сообщение без номера тикета/темы, «wip» в истории вливаемой ветки. Содержимое — не в охвате А2 (`src/lob/levels.rs`, чужая часть), но тема смешана с TK-115. | важно | `git branch --contains 005cff86` = `tk115-p12`; `git show --stat` |
| К3 | В p12 4 коммита тестов-«поправок порядка строк/порогов» (`e85440f4, 87898a13, 831bf693, bd30137a`) и «fmt» (`814f8807, 2a03cdc5, 19fa23b8`) отдельными коммитами: мелочь, засоряет историю (3 «fmt» из 59). Не критично. | потом | `git log` |
| К4 | В одной ветке p12 смешаны темы: формы R2 (TK-065), формы выхода tape/cxl (TK-115), замер W (Г-133), `wip-walls`, правки `replay.rs` (+29 стр.) и `fill_capacity/tests.rs`. Ревью по темам невозможно. | важно | `git diff --stat HEAD...tk115-p12` |
| К5 | `tk049-b15b`: `prep_events.rs` + `window_store.rs` (+1 088 стр. кода +271 тестов) — новый формат ALPREP и хранилище окон; ключевое ускорение (ALWIN закрыт по CLAUDE.md «Закрыты: ALWIN»). Если ALWIN закрыт, 1 088 стр. недовлитого кода — «мёртвый груз» ветки: решить — влить за флагом или архивировать ветку тегом. | важно | CLAUDE.md «СОСТОЯНИЕ»; `git diff --stat` |
| К6 | За последние 9 дней `bounce_grid*`/`touches*` на стволе — 37 коммитов из 122 за всё время (30 %): очень горячий каталог; `bounce_grid/tests.rs` — главная зона конфликтов между ветками. | потом | `git log --since=2026-10-01` |

## 7. Когнитивная сложность и длины (замер инструментом)
**Метод:** `cargo clippy -W clippy::cognitive_complexity` с `clippy.toml: cognitive-complexity-threshold = 1` на VPS (В-147, дерево `git archive HEAD` + `clippy.toml`, профиль dev, 1 м 11 с; вывод `docs/findings/alpha-review-A2-cognitive-2026-10-10.tsv` — 1 142 функции всего крейта: «балл<TAB>файл:строка»). Это метрика clippy (Sonar-подобная: ветвления с вложенностью), rust-code-analysis не гонялся. Охват А2 — нетестовые файлы `bounce_grid*`, `touches*`. Для сравнения: максимум по крейту — `lob/backtest.rs:2069` = 55 и `:1527` = 34 (часть А1), `bybit/conn.rs:726` = 33.

| # | функция | файл:строка | балл |
|---|---|---|---|
| 1 | `run_symbol` | src/commands/lob/bounce_grid.rs:468 | 59 |
| 2 | `decode_file (tbin)` | src/commands/lob/touches/tbin.rs:138 | 31 |
| 3 | `decode_file (abin)` | src/commands/lob/touches/abin.rs:182 | 24 |
| 4 | `run_touches` | src/commands/lob/touches.rs:452 | 22 |
| 5 | `write_minute_flow` | src/commands/lob/touches/minute_flow.rs:42 | 17 |
| 6 | `plan_grid` | src/commands/lob/bounce_grid/plan.rs:101 | 16 |
| 7 | `run_bounce_grid_inner` | src/commands/lob/bounce_grid.rs:274 | 16 |
| 8 | `Form::parse` | src/commands/lob/bounce_grid/forms.rs:165 | 15 |
| 9 | `write_form` | src/commands/lob/bounce_grid/outputs.rs:334 | 13 |
| 10 | `write_numbers` | src/commands/lob/touches/numbers.rs:127 | 11 |
| 11 | `admits` | src/commands/lob/bounce_grid/sets.rs:605 | 11 |
| 12 | `open_outputs` | src/commands/lob/bounce_grid/plan.rs:416 | 11 |
| 13 | `run_moves` | src/commands/lob/touches/moves.rs:13 | 10 |
| 14 | `grid_forms_with_axes` | src/commands/lob/bounce_grid/forms.rs:338 | 10 |
| 15 | `admits_row` | src/commands/lob/bounce_grid/sets.rs:681 | 9 |
| 16 | `exit_groups` | src/commands/lob/bounce_grid/drive.rs:208 | 9 |
| 17 | `append_carry_events` | src/commands/lob/bounce_grid/carry.rs:105 | 8 |
| 18 | `encode_file (tbin)` | src/commands/lob/touches/tbin.rs:55 | 7 |
| 19 | `замыкание header_for` | src/commands/lob/bounce_grid/plan.rs:460 | 7 |
| 20 | `замыкание scope.spawn` | src/commands/lob/bounce_grid/drive.rs:697 | 7 |

Порог «плохо» (в clippy по умолчанию 25): превышают **две** функции А2 — `run_symbol` (59, худшая в охвате и вторая во всём крейте) и `tbin::decode_file` (31); у `abin::decode_file` 24 — на границе, `run_touches` 22. Двойное чтение дубля (Дб1): два декодера кэша 31+24 при одном каркасе.
Длины (грубо, по скобкам; тот же скрипт, как первый вариант отчёта): `plan_grid` 243 строки, `run_touches` 208, `write_form` 201, `open_outputs` 165, `Sets::parse` 151, `drive_day` 149. **Замечание:** длина и балл не совпадают — `drive_day` (149 строк) и `touch_row_pairs` (132 строки, данные в виде кода) по баллу вне топ-20 (< 7): они длинные, но плоские; режутся по длине, не по сложности. Опасные по сложности — `run_symbol` и два `decode_file`.
Файлы (нетестовые): `bounce_grid.rs` 1 358 (!), `drive.rs` 854, `sets.rs` 736, `plan.rs` 681, `touches.rs` 662, `outputs.rs` 535, `args.rs` 513 (все > 500). Тесты: `bounce_grid/tests.rs` 3 989.
Рекомендация: `run_symbol` — разрезать по фазам (подготовка суток → цикл допуска → запись), он же носитель 6 из 9 env-чтений `bounce_grid.rs` (Д1); `decode_file` ×2 — общий разборщик (Дб1).

## 8. Скорость, обещано-не-сделано, покрытие
| # | находка | тяжесть | доказательство |
|---|---|---|---|
| С1 | **Цена флагов в выкл. виде:** +7,6 % ЦП на d15 (194,08 → 208,92 с user) и «d01 40,51 → …» при R2 выключенном в бинаре b15pyr4-pgo; CEO 16:25 принял её **только для бинаря R2-сеток**, боевые волны и ускорения — на бинаре без R2 (тот же разбор: гейт6 diff 0 на d15 и d01 729/729). Значит у R2 два бинарника (с `--features r2` и без), и «боевой PGO-бинарник» не проверяется общим гейтом. | важно | `.claude/tickets/archive/TK-065-log.md:384, 393` |
| С2 | Обещано в `touches`: бинкэши `ALPHA_APPROACH_BIN`/`ALPHA_TOUCH_BIN` — «колоночный формат (297 с)» в CLAUDE.md закрыт; режим остался в коде, умолчание — выкл. Нужно решить: принят (включить) или закрыт (удалить `abin.rs`+`tbin.rs` = 554 стр.). | важно | CLAUDE.md «Закрыты: … колоночный формат» |
| С3 | Не покрыт тестами `touches/moves.rs` (`run_moves`, 109 строк, балл 10): в `touches/tests.rs` только заполнение поля `moves: None` в конфиге (:31–33), прогона с `moves: Some` нет (`grep 'moves: Some'` = 0). **Исправление по критике:** `minute_flow` покрыт — `touches/tests.rs:775 minute_flow_dir_writes_series_and_keeps_touch_bytes`. | важно | `touches/tests.rs:31–33, 775` |
| С4 | Тесты p12 — 4 `cfg(feature = "r2")` блока; без `--features r2` не бегут (то же, что Д3; блокер вливания — по TK-124 Д1). | важно | п.2 Д3 |

## Сводка (топ для решения Судьи)
1. **R2-тесты не в гейте** (Д3/С4, общий с TK-124 Д1 — там блокер вливания); формы R2 в бое без feature **отвергаются** (`forms.rs:209`), молчаливого игнора нет; два бинарника (С1).
2. **Ветки:** r2m и r2dl ⊂ p12 → влить p12 одной серией и закрыть; `wip-walls` (К2) вынести/переименовать; b15b решить по ALWIN (К5).
3. **Сложность (замер clippy):** `run_symbol` = 59 (худшая в охвате, 2-я в крейте), `tbin::decode_file` = 31 (п.7). **Env-флаги:** 16 `ALPHA_*` без записи в выходе, 3 разные семантики «включён» (Д1, З4).
4. **Дубли/длины:** `abin`/`tbin` (Дб1), `plan_grid`/`run_touches`/`drive_day` (п.7), `bounce_grid.rs` 1 358 стр. (Д2), `tests.rs` 3 989 стр. (З3).

## Осталось
- rust-code-analysis не гонялся (метрика — clippy, п.7).
- Построчное чтение `plan_grid`, `drive_day`, `write_form` (дублирование внутри) не делалось.
