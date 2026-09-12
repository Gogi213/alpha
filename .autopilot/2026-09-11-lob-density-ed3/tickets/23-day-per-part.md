# 23 — Сутки на уровне части, не каталога

**Требования:** R38, R44, R57
**Blocked by:** 22
**Зона:** `src/commands/lob/session.rs` (`BinlogPart` уже несёт `started_utc`), `src/commands/lob/{profiles,watch}.rs` (`day_utc` из части), `src/commands/lob/mod.rs`
**Волна:** 9
**Status:** done

## Что должно заработать

Ревью таска 22 (R-C): `session_binlog_for` принимает каталог с частями разных
суток, но `day_utc` в `profiles`/`watch` берётся из верхнего `session.json.started_utc`,
который перезаписывает последняя сессия в этот `--root`. Каталог с сутками D и D+1
отдаст все записи как D+1 — сутки перестают быть кластером, окно «сейчас» (таск 21)
фильтрует по тому же завышенному дню. Для однодневного пилота не срабатывает;
до первого многодневного сбора — обязательно.

## Критерии приёмки

- [x] `day_utc` каждой записи — из даты в имени файла части, час старта — из `binlog_files[]` по (symbol, part, сутки); `commands::lob::session_parts_for`
- [x] тест: каталог с частями за двое суток → `G = 2` (`two_day_session_dir_yields_g_two_and_hours_per_part`, `watch_attributes_days_and_hours_per_part_inside_one_directory`), окно «сейчас» с границей между сутками читает только нужные (`now_window_boundary_inside_one_directory_reads_only_the_days_in_window`)
- [x] корректная атрибуция (второй вариант); `shortlist::group_by_day` ставит каталог под каждыми его сутками, во времянку линкует один раз
- [x] **GC**: 607 passed, 0 failed, 5 ignored; clippy `-D warnings` и fmt чистые

## Что сделано

Зона расширена на `src/commands/lob/shortlist.rs` (`group_by_day`, `build_filtered_root`) — тот же дефект атрибуции суток при делении календаря 60/40; без этого каталог с двумя сутками числился бы за одними. Реплей — по суткам внутри каталога: трекер общий на части одних суток (таск 22), чистый на каждые сутки; записи возвращаются по частям, час старта — от части. Карта — `interfaces.md`, раздел «Из таска 23».
