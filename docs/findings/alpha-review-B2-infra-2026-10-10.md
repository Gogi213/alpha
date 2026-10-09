# Ревью alpha, часть Б2 — инфраструктура счёта (alsched, Летопись/guard, benchrun/calibrate, хуки, vps-check) — 2026-10-10

Статус: **ЧАСТИЧНЫЙ, разделы 1–7 закрыты** (возврат Судьи исправлен: имя файла, except-pass 7 из 19, A1–A9, коммиты числом, скорость). Код не менялся. Calc — только короткие `cat/ls`, `md5sum` отдельных файлов (хук `bench_guard` отказал `du` — убрал). Не повторяю Б-1…Б-7 из `alpha-review-B-2026-10-10.md` (Б-2 про тесты alsched и `except pass` — подтверждаю, число ниже). Рантаймы — `runtimes-design-2026-10-10.md` (Г/Т/Х).

## Замерено

| что | значение | как |
|---|---|---|
| объём части | alsched.py 1 248 стр.; tools/registry 17 файлов 2 779 стр. (guard.py 651, test_guard 463); calibrate.sh 317; vps-check 43; benchrun*.sh 4 файла по 14–30 стр. | `wc -l` |
| когнитивная сложность (complexipy) | 23 функции > 15 из 196 в 16 файлах (db.py не разобран — см. Б2-1) | `/tmp/cx.py` на `complexipy.file_complexity` |
| `except …: pass` | 7 из 19 `except` в `alsched.py` | grep -A1 |
| репо = выложенное | `alsched.py` и `guard.py` на calc совпадают с репо (после снятия CR, 0 строк разницы); `benchrun.sh` и `benchrun2.sh` — **не** совпадают (Б2-4) | diff/md5 по ssh |
| бэкапы на calc | 10 файлов `alsched.py.bak-*` / `.pre-*` в `/data/sched` | `ls` |

## Топ-20 по когнитивной сложности (число / строк)

| # | функция | сложность | строк |
|---|---|---|---|
| 1 | `tools/compute/alsched.py:92 Core.tick` | **141** | 144 |
| 2 | `tools/registry/registry.py:75 main` | **130** | 144 |
| 3 | `tools/registry/bind_verdicts.py:30 run` | 85 | 85 |
| 4 | `tools/registry/guard.py:321 check` | 83 | 61 |
| 5 | `tools/compute/alsched.py:1096 main` | 59 | 95 |
| 6 | `tools/registry/recover_config.py:56 recover` | 51 | 58 |
| 7 | `tools/registry/guard.py:248 context` | 37 | 33 |
| 8 | `tools/registry/snap.py:24 collect` | 34 | 38 |
| 9 | `tools/registry/enrich.py:13 enrich_row` | 30 | 37 |
| 10 | `tools/registry/guard.py:123 dir_fp` | 29 | 33 |
| 11 | `alsched.py:775 SystemdBackend.culprits` | 27 | 40 |
| 12 | `guard.py:384 done` | 24 | 28 |
| 13 | `alsched.py:278 Core.guard_load` | 24 | 23 |
| 14 | `backfill_hyp.py:28 verdicts` | 23 | 22 |
| 15 | `guard.py:602 main` | 21 | 46 |
| 16 | `alsched.py:990 SystemdBackend.legacy_busy` | 20 | 26 |
| 17 | `alsched.py:1071 daemon` | 19 | 23 |
| 18 | `alsched.py:831 SystemdBackend.win_end` | 19 | 34 |
| 19 | `alsched.py:335 Core.preempt_for` | 19 | 31 |
| 20 | `letopis_hyp.py:77 final_verdict` | 18 | 17 |

Порог complexipy по умолчанию — 15: 23 функции выше. Шелл-скрипты не измерены (инструмента для sh нет; `calibrate.sh` 317 стр. одним телом).

## Находки

### Б2-1 · БЛОКЕР (на момент ревью) · `tools/registry/db.py` в рабочем дереве не компилируется
`db.py:146` — `newline="` с реальным переводом строки внутри литерала (`py_compile`: `SyntaxError: unterminated string literal`). HEAD-версия (5f84893a) компилируется; файл изменён, не закоммичен (`git status`: `M db.py`, `M registry.py`, −143 строки). Судя по виду — незаконченная правка кем-то из параллельных сессий, не по моему тикету. Доказательство: `python -m py_compile tools/registry/db.py`. Пока не починено, весь реестр (`registry.py` импортирует `db`) и `guard.py`, если он тянет `db`, не запустятся. Кто правит — вернуть или закоммитить; Судье — проверить, что коммит не пройдёт без `py_compile`.

### Б2-2 · ВАЖНО · две функции — «божественные»: `Core.tick` (141) и `registry.main` (130)
`alsched.py:92 Core.tick` — 144 строки, сложность 141: всё решение планировщика (допуск, упаковка, заморозка, вытеснение) в одном теле; единственная защита — смоук на живом сервере и `sched_sim.py` (макет, не тест). `registry.py:75 main` — 144 строки диспетчера подкоманд if/elif (130). Следом `bind_verdicts.run` 85, `guard.check` 83. Цена: каждая правка TK-071/TK-118 (десятки коммитов TK-071/117/118 в одном `alsched.py`) рискует регрессией без сети. Что сделать: разрезать `tick` на `admit/pack/preempt/thaw` (границы уже видны по методам `guard_load`, `preempt_for`), `registry.main` — таблицей `{команда: функция}`. → **Т**.

### Б2-3 · ВАЖНО · документация и реальность расходятся: `bench_guard.py` «проекта» не существует
`CLAUDE.md:40` и `:138` называют хук `.claude/hooks/bench_guard.py`; каталог `.claude/hooks/` пуст (только `__pycache__`, `.pytest_cache`), хук переехал в плагин (TK-077, `plugin-critic-V-2026-10-08.md:17`). Реально отказ выдаёт плагин (подтверждено: `du` по ssh на calc отклонён). Единственный хук, оставшийся «проектным», — `~/.claude/hooks/alpha_one_build.py` (вне репо, не версионируется; рядом `alpha_one_build.py.pre-tk090` 9 КБ — «временный» бэкап). Что сделать: поправить CLAUDE.md (CEO), добавить `alpha_one_build.py` в репо (`tools/hooks/`) или записать в COMMANDS, что он лежит только у владельца; удалить `.pre-tk090`.

### Б2-4 · ВАЖНО · выложенное `benchrun` ≠ репо (дрейф)
Хеши (без CR): сервер `/data/tk048/benchrun.sh` (на него указывает `/data/benchrun.sh`) и `/data/tk052/benchrun2.sh` **идентичны** друг другу (a14dc14b), а в репо `tools/compute/benchrun.sh` (563950ed) и `benchrun2.sh` (d3837498) — два разных файла, ни один не совпадает с выложенным. Значит, в репо нет того, что реально запускает замок замеров; правка замка идёт мимо git. (alsched.py и guard.py — совпадают, их выкладка чистая.) Что сделать: положить выложенный в репо как единственную копию, второе имя на сервере — симлинк; в гейт выкладки (`tools/compute/deploy`?) добавить сверку хешей. Не проверено, какой из репо-файлов старее (git blame не гонял).

### Б2-5 · ВАЖНО · `settings.json` ссылается на `/data/tk092/*.sh`, а в репо источник — `tools/compute/alsched-job{state,owners}.sh`
`.claude/settings.json:17–18` (`RPV_JOB_OWNERS_CMD`, `RPV_JOB_STATE_CMD`) → `/data/tk092/…`. Файлы на сервере совпадают с репо (md5 696c5dbf, e5879083), но путь привязан к номеру тикета, а не к каталогу «рабочего» — тот же класс, что Б-3 (298 файлов с `/data/tk0…`). Сменится каталог — страж жизни заданий (`job:calc:<id>`) молча перестанет видеть хозяев/состояние. → **Т**: `/data/sched/…`.

### Б2-6 · ВАЖНО · 10 копий `alsched.py.bak-*` на calc + резервные копии как способ версионирования
`/data/sched/alsched.py.bak-0910w, -1008, -1008b, -1009, -1009b/c/d, -c759ef16, -tk118, -tk118b, .pre-tk117` — всего 10 (ls). История уже в git (десятки коммитов TK-071/117/118), бэкапы — «временное», выросшее в норму. Выкладку делают ручным `cp` поверх. → удалить после тега-метки версии, выкладка скриптом с записью хеша в `/data/sched/VERSION` (сейчас хеш выложенного нигде не фиксируется — Б2-4 это и показал).

### Б2-7 · ВАЖНО · непокрытое тестами
Тесты: `tools/compute/tests/test_alsched_fail.py` — 7 тестов на 1 248 строк (Б-2); `tools/registry/test_guard.py` — 28 тестов на `guard.py` (651 стр.) и **ноль** на `registry.py`, `db.py`, `snap.py`, `bind_verdicts.py`, `enrich.py`, `recover_config.py`, `letopis_*`, `backfill*`. Именно эти файлы в топ-20 сложности (#2, #3, #6, #8, #9). `sched_sim.py` — макет-«гейт» TK-071, но не в pytest-сборе (в `tests/` нет). Хуки и `vps-check.sh`/`benchrun*.sh`/`calibrate.sh`/`alsched-job*.sh` — без тестов вообще.

### Б2-8 · ПОТОМ · мёртвый/одноразовый код в `tools/registry`
Ссылок из живых мест (не тикеты/архив/findings): `backfill_hyp.py` — 0, `letopis_check.py` — 0, `letopis_p12ext.py` — 0 (19 строк), `recover_config.py` — 0, `hdr_tk040.sh` — 0; `agg_tk040_usd.py` — 1, `backfill_tickets.py` — 1. Это одноразовые загрузчики TK-068/TK-089 (бэкфилл, восстановление), оставшиеся рядом с живым ядром (`guard.py`, `registry.py`, `db.py`, `snap.py`) — путают, что в рабочем контуре. `alsched.py:451 free_cores` — единственное упоминание (определён, не вызывается) — мёртвый метод. → в `tools/registry/archive/` с указанием в README; `free_cores` удалить.

### Б2-9 · ВАЖНО · глотание ошибок в `alsched.py`: 7 `except …: pass` из 19 `except`
Исправлено после возврата Судьи: всего `except` — 19 (`grep -cE '^\s*except'`), из них с `pass` следом — **7**: строки 178, 734, 743, 900, 909, 972, 1236 (`grep -A1`); `except Exception` — 3. Дополняет Б-2: сбой записи состояния/заявок на этих путях пропускается молча — демон продолжает с неверным состоянием, в `alerts.log` ничего. Что сделать: заменить на запись в `alerts.log` с ограничением частоты минимум для `jobs/*.json`, `preempt.json`, `own/`.

### Б2-10 · ПОТОМ · дублирование шелл-обёрток замка
`benchrun.sh` (25), `benchrun2.sh` (30), `benchrun-inner.sh` (14), `benchrun-sched.sh` (14) + на сервере `benchrun-legacy.sh`, `benchrun2-legacy.sh` — 6 файлов одной ответственности («взять замок замеров → запустить → снять»). Копий замка (`flock -x 8 … 9`) — минимум 3 (`benchrun.sh:7–10`, `benchrun2.sh`, legacy). `settings.json:RPV_GUARD_HEAVY_HINT` отсылает к трём именам сразу. → **Т**: один `benchrun` с аргументом класса (`wave|stand|disk`).

### Б2-11 · ПОТОМ · `settings.json`: список машин/путей захардкожен в env
`RPV_GUARD_HOST_ROOTS` (два хоста, 8 путей), `RPV_GUARD_HEAVY_ALLOW` (regex на команды alsched), `RPV_BUS_URL` с IP в трекаемом файле. Смена сервера = правка пяти ключей (`RPV_CALC_HOST`, `RPV_VPS_HOST`, `RPV_GUARD_HEAVY_HOST`, `RPV_GUARD_FORBIDDEN_HOSTS`, `RPV_BUS_URL`). Единая переменная хоста + шаблон.

### Б2-12 · ПОТОМ · `calibrate.sh` — 317 строк одним телом
Только `set -u -o pipefail` (нет `-e`: ошибка шага не останавливает калибровку), `set -m` в :70. Сложность не измерена (инструмента для sh нет). `vps-check.sh` — `set -euo pipefail`, 43 строки, чистый.

### Б2-13 · ПОТОМ · `Core.tick` читает файл напрямую, мимо `be`
`alsched.py:176` (внутри `tick`, 92–235) — `int(open(pj).read().strip())`: единственный прямой ввод-вывод в `Core` (остальное идёт через `self.be.*`). Подмена `be` в тестах (Б-2) этот путь не закроет. См. раздел «Дизайн».

## (2) Дизайн: A1–A9 и три рантайма

`docs/ARCHITECTURE.md` A1–A9 — решения о Rust-ядре (`src/`: цены целые, `Clock`, поток решений, книга, `Feed`, `Bot<MD>`, разбор/сокет, REST). Моя часть — Python/sh вокруг счёта, `src/` не затрагивает. Применимость по пунктам:
- **A1** (не `f64` для цены/размера): не применимо — `alsched.py` считает доли ядер/памяти (float), цен нет. Нарушений нет.
- **A2 / A3 (время через трейт, ввод-вывод отдельно от решений)**: аналог в части — `Core(be)` + `SystemdBackend` (`alsched.py:70`, `:533`; время — `be.now()` `:544`). Выполнено: в `tick` (92–235) ввод-вывод идёт через `self.be.*`, **кроме** `:176` (Б2-13). Прямые `time.*`/`open`/`subprocess` — в `SystemdBackend` (530–1233) и модульном коде `:13, :41, :51`. Граница соблюдена; смущает размер решения (`tick` 141), не граница.
- **A4–A9**: не применимо (книга, Feed, `Bot<MD>`, разбор сокета, REST).
- **Три рантайма** (`runtimes-design-2026-10-10.md`): `benchrun` одновременно замок замеров (Т) и вызывается из «холодных» цепочек `tk048-*` голым `systemd-run` мимо `alsched` (Б-4); `guard.py` — ядро Т-слоя, но 5 одноразовых загрузчиков (Б2-8) лежат рядом. Лечение — Б2-2, Б2-8, Б2-10.

## (6) Коммиты части — числом

Команда: `git log --oneline -- <файл> | wc -l`.

| файл | коммитов |
|---|---|
| `tools/compute/alsched.py` | **43** (06.10 → 10.10: ≈ 9 в сутки) |
| `tools/registry/guard.py` | 14 |
| `tools/registry/registry.py` | 13 |
| `.claude/settings.json` | 11 |
| `tools/registry/letopis_hyp.py` | 11 |
| `tools/vps-check.sh` | 8 |
| `tools/registry/db.py` | 7 |
| `benchrun.sh` / `benchrun2.sh` / `-inner` / `-sched` | 4 / 3 / 2 / 1 |
| `calibrate.sh`, `snap.py`, `bind_verdicts.py` | 2, 3, 3 |
| `alsched-jobstate.sh`, `alsched-jobowners.sh`, `enrich.py` | 2, 2, 2 |
| `tools/registry` целиком | 42 |

Топ-5 по размеру (`--shortstat`, вставки+удаления) и смешение тем:
1. `4b6014c7` TK-071 — 396 стр., 3 файла (`alsched.py`, `sched-smoke.sh`, `sched_sim.py`): тема одна, но весь планировщик одним коммитом.
2. `66af78a9` TK-081 — 345 стр., 4 файла: `alsched.py` + `guard.py` + `snap.py` + тесты — **смешение**: правка планировщика в коммите про отпечаток реестра.
3. `204a30d5` В-178 (CEO) — 317 стр., 4 файла: `calibrate.sh` + `CLAUDE.md` + `COMMANDS.md` + `roles/README.md` — одна тема (скрипт и правило).
4. `06ec9733` TK-081 — 274 стр., те же 4 файла, что у №2: снова `alsched.py` в коммите про `guard` — **смешение**.
5. `1b4bccc0` TK-118 — 261 стр., **7 файлов**: `roles/README.md`, `roles/engineer.md`, `SKILL.md`, `COMMANDS.md`, `alsched.py`, `guard.py`, тесты — код + три файла правил команды — **смешение**.

Итого: из топ-5 смешаны 3 (№2, №4, №5) — `alsched.py` правится «довеском» к реестру/Летописи, поэтому `git log -- alsched.py` (43) включает коммиты не про планировщик.
- «Временное»: 10 `.bak` на calc (Б2-6), `alpha_one_build.py.pre-tk090` (Б2-3), в корне untracked `tmp-tk115/tmp-tk124/tmp-tk125/tmp-tk126/tmp91/hdr-tk040.tsv/export/examples/research.md` — чужие, но видны.
- Ветки вне ствола (вся репо, не моя часть): `git branch --no-merged master` = 46; `git log master..HEAD` = 2 732 (HEAD на `audit/design-fixes-2026-09-22`).
- Недовлитое: `db.py`/`registry.py` — незакоммиченная правка (Б2-1). Откаты (`Revert`) в части не считал.

## Скорость — числа из логов calc (10.10 ~02:00, `tail/awk` по `util.log`, `alerts.log`)

`/data/sched/util.log`: 1 915 минутных строк с 08.10 05:55 (≈ 32 ч), 161 КБ.
- Средняя загрузка хоста `host` = **0,412**; при активном prod (`prod_run>0`, 1 410 мин) — 0,530. Цель TK-071 (≥ 0,80) в среднем не достигнута.
- Очередь не пуста (`queued>0`) в **919 из 1 915 мин (48 %)**, из них при `host<0,5` — **326 мин (35 % от 919)**: ядра простаивали при ждущих заявках.
- Окно замера (`measure=1`) — 371 мин (19 % времени).
- `alerts.log`: 2 365 строк; «ждёт старта 10 мин» — например три заявки `tk071-synth3-22…24` 09.10 23:12 (prio 9); «ЗАДАНИЕ УПАЛО» — 30 строк: `rc=143: signal` 22, `rc=1: exit-code` 5, `timeout` 1, `oom-kill` 1, `rc` 1 — OOM один, остальное сигнал (вытеснение/заморозка/cancel).
- Длительность самого `tick` в логах не пишется — не измерить; предложение: колонка `tick_ms` в `util.log`.

## Обещано-не-сделано

- `CLAUDE.md:40` и `:138` называют `bench_guard.py` проекта — его нет (Б2-3).
- Загрузка ≥ 80 % (TK-071) — по `util.log` среднее 0,53 при prod.
- `--recompute --why` есть в CLI, в заданиях `recompute: true` 0 (Б-1).

## Не проверено

`alsched.py` построчно вне `tick/main`; `snap.py`/`enrich.py` содержательно; `alert-tg.sh`; хуки плагина; актуальность `RPV_GUARD_HEAVY_ALLOW`; сложность шелл-скриптов; число `Revert`-коммитов в части.
