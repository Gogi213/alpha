# TK-493 F3a: два базовых бинарника statarb (эталон «байт в байт» для G6) — 06.11.2026

Статус: **готово**, оба бинарника собраны на VPS София, лежат на Steam Deck, сверены по sha256 в трёх местах (VPS = ПК = Deck), запускаются на Deck (`--version`, `statarb --help`, `--help` каждой подкоманды). Счёт не запускали, слияния не делали. План: `docs/findings/statarb-xdl9-plan-2026-11-06.md` §6 (F3a), §7 (G6).

## 1. Бинарники

| | A: tk479 | B: tk486 |
|---|---|---|
| Файл на Deck | `~/xdl9/bin/statarb-tk479-f27c879e` | `~/xdl9/bin/statarb-tk486-60e61cea` |
| Копия на VPS | `/opt/alpha-compute/xdl9-bin/statarb-tk479-f27c879e` | `/opt/alpha-compute/xdl9-bin/statarb-tk486-60e61cea` |
| Ветка, коммит | `tk479-statarb` @ `f27c879e453b92a8d1a5b9496bf790a049e927c2` (TK-489: E-011, 03.11 06:20 GMT+4) | `tk486-speed` @ `60e61cea7d599c2e8ca9b5687f851dfd3e243b8e` (HEAD, TK-486, 03.11 11:25 GMT+4) |
| `alpha --version` | `alpha f27c879e453b92a8d1a5b9496bf790a049e927c2` | `alpha 60e61cea7d599c2e8ca9b5687f851dfd3e243b8e` |
| sha256 | `09d685f8d4fccc6026d1bf9bb5caf652909050d485ec20bc428b6af0b6450840` | `f9104f0c5a245e6fd6b2b1a6d2637c4403bc55773f9a1ec191b6c996915abe76` |
| md5 | `2ffd828484fbc43c6cd246dff5f29749` | `5be714e6f12adaf39cf7dfc8b71e971a` |
| Размер | 13 264 808 Б | 13 528 520 Б |
| Сборка (cargo, тёплые зависимости) | 76 с стены (cargo: 1m 15s) | 77 с стены (cargo: 1m 17s) |
| Момент сборки | замок взят 20:58:32Z, бинарь 20:59Z (06.11 ~00:59 GMT+4) | замок 21:00:19Z, бинарь 21:01Z (06.11 ~01:01 GMT+4) |
| Связи | `libgcc_s`, `libm`, `libc` (динамически, на Deck разрешаются) | то же |

sha256 на трёх точках (VPS, ПК, Deck) совпал для обоих файлов (сверка при доставке и повторно после: `sha256sum` на VPS и на Deck).

## 2. Как собрано

- Дерево — отдельный detached `git worktree` на нужном коммите (не главное дерево, без новых веток): чистое, `git ls-files --cached --others --exclude-standard` → tar → scp на VPS. Файлов: A 5723, B 5711. `Cargo.toml`, `Cargo.lock`, `vendor/` у двух коммитов совпадают (`git diff --stat f27c879e 60e61cea -- vendor Cargo.toml Cargo.lock` пуст); различаются только исходники `src/`, tools и доки.
- Хост: VPS София (`CHECK_HOST=vps`-путь `tools/vps-check.sh`): rustc/cargo 1.93.1, `nice -n 5`, `flock /opt/alpha-compute/.build.lock`, `sweep.sh run` (проверка места: допустил, отказа по месту не было, свободно 23–24 ГБ по `df`), общий `shared-target`, `cargo build --release -j 3`, **без фич** (без `r2`, без `mimalloc`), **без PGO**. Это эталонные сборки для сверки «байт в байт» (результаты команд не зависят от PGO, но сравнивать с ними надо бинарь с теми же условиями сборки), не боевые бинарники для счёта (В-32 требует PGO для боевого счёта; здесь счёт не гоняли).
- `ALPHA_COMMIT=<полный хеш>` вшит (как в `tools/compute/gate/calc-build.sh`), `alpha --version` сверен с хешем коммита.
- Режим `build` из `vps-check.sh` оставляет бинарь в общем `shared-target/release/alpha`, откуда его перезапишет любая следующая сборка. Поэтому бинарь забирался внутри того же замка: `install -D $TGT/release/alpha /opt/alpha-compute/xdl9-bin/<имя>`, потом scp на ПК и на Deck. Сам скрипт лежал во временной папке сессии (`.../scratchpad/f3a/{build_one.sh,remote_build.sh,deliver.sh}`), в репозиторий не добавлялся.

## 3. Находка по ходу: первый проход B был ложным (тот же класс, что E-011)

Первый проход собрал оба дерева без очистки общего `shared-target`:

| проход | дерево | cargo | sha256 |
|---|---|---|---|
| 1 | A | 1m 17s, собралось | `2859b70534364fbd2e80a03cf78831d464689f8e50b994794212e5ad74f34515` |
| 1 | B | **0,31 с**, «Finished» без компиляции | **тот же** `2859b705…4f34515` |

То есть «бинарь tk486» оказался бинарём tk479: файлы дерева B (mtime = момент `git worktree add`) старше результата сборки A в общем target, cargo счёл крейт свежим. Это ровно E-011 (`.claude/errors/E-011.md`: mtime старше прежней сборки → cargo не пересобирает). На коммите `f27c879e` E-011 числится `status: open`; лечение в `check_e010.py` (mtime вперёд) относится только к его проверке, не к `vps-check.sh`. Бинарь прохода 1 отброшен, оба файла собраны заново проходом 2: перед `cargo build` выполнено `cargo clean --release -p alpha -p hftbacktest` (удалено 537 файлов / 484,2 МиБ у A и 24 файла / 100,3 МиБ у B), после чего sha256 у A и B разные (таблица §1), у B есть `run`/`form`/`corr-bench`, у A нет.

Для F3 и вообще для любых сборок разных деревьев в общем `shared-target` на VPS: проверять sha256 (или `--help`) собранного, не верить «Finished»; или сбрасывать свои крейты (`cargo clean -p alpha -p hftbacktest`), или ставить исходникам mtime вперёд.

Побочный эффект на VPS: из общего `shared-target` удалены артефакты крейтов `alpha` и `hftbacktest` (release). Это кэш; чужая следующая сборка там один раз перекомпилирует `alpha` (по замеру выше ≈ 77 с на 3 потоках).

Сборка не проверялась на побитовую воспроизводимость: A прохода 1 (без `ALPHA_COMMIT`, без очистки) и A прохода 2 различаются по размеру (13 264 952 против 13 264 808 Б). Что это даёт (строка версии, расположение), не выяснялось, и не нужно: эталоном служат файлы из §1 с их sha256.

## 4. Что умеет каждая подкоманда (по `--help` на Deck, полный текст — `docs/findings/xdl9-base-binaries-2026-11-06-help.txt`)

| Подкоманда | A: tk479@f27c879e | B: tk486@60e61cea |
|---|---|---|
| `statarb convert` | есть: `--src --out --threads` | есть, те же флаги |
| `statarb bench` | есть, **с** `--digest`, `--delay-x`, `--funding-k`, `--hold-payments` (kind: pair, cross, basket, funding-pp, funding-ps; engine: kernel, reference, drift) | есть, но **без** `--digest`, `--delay-x`, `--funding-k`, `--hold-payments` |
| `statarb run` | нет (rc=2, неизвестная подкоманда) | есть: `--kind --cell\|--cells --jobs --panels --panels-b --funding-y --funding-x --daily-out --trades-out --trade-jobs --min-span --threads --window --min-obs --beta-fixed --entry-z --exit-z --stop-z --max-hold --refresh` |
| `statarb form` | нет | есть: `--panels --from-day --to-day --window-days --block-chunks --min-obs --alpha --threads --jobs-out --detail-out` |
| `statarb corr-bench` | нет | есть: `--symbols --minutes --min-obs --threads --check-pairs --kernel` |
| Исправление E-010 (минута выплаты фандинга в эпохе панелей) | **есть**: `funding_min = (time_ms - GRID0_MS) / MINUTE_MS` | **нет**: `read_funding` даёт `time_ms / 60000` (unix-минуты), комментарий в `cli.rs` строка 360 |

Следствия для G6 (план §7):

- A годится как эталон `bench --digest` (pair, cross, basket, funding-pp) на панелях Bybit и как эталон `convert`; фандинг в нём считается правильно (E-010).
- B годится как эталон `run` одной клетки и `--cells` против одиночных только **при выключенном фандинге** (так и записано в G6): на реальных данных у B фандинг в ряд не попадает (E-010 не исправлен). Сверять фандинговые виды (funding-pp/ps) с B нельзя.
- В `bench` у A нет флага `--rule`; правило «вход по уровню ставки» задаётся парой `--funding-k` (>=1) + `--hold-payments`. В плане §6.5 перечислено `--rule/--funding-k/--hold-payments`: при допиле `run` флаг `--rule` придётся вводить заново или привести к схеме `--funding-k`/`--hold-payments` из A.
- В `run` у B нет `--delay-x`, колонки `fill_x`, `--from-day/--to-day` (сутки живут в строках файла заданий `y,x,from_day,to_day`); в `form` флаги `--from-day/--to-day` есть.

## 5. Состояние машин после работы

- Steam Deck: `~/xdl9/bin/` содержит ровно эти два файла (права 755, `.part` нет). Служба `xdl-bitgetB.service` (докачка Bitget) не трогалась.
- VPS София: мои временные каталоги `wave2-srcxdl9a-*`, `wave2-srcxdl9b-*` и архивы `/opt/alpha-compute/xdl9-a.tgz`, `xdl9-b.tgz` удалены скриптом. Остались копии бинарников в `/opt/alpha-compute/xdl9-bin/` (`sweep.sh` чистит только `wave2-src*`, `src-*`, `*.tgz` и `bin/`, эту папку не трогает). `wave2-src-tk492b-186104` не удалялся: отказа по месту не было. Чужие `wave2-srcxdl9m-*` и `wave2xdl9m.tgz` на VPS не мои, не тронуты.
- ПК: временные worktree `TK-493-base-a` и `TK-493-base-b` убраны (`git worktree remove`, `prune`). Главное дерево `tk479-statarb` не трогалось.
