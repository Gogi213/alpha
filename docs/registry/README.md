# Реестр прогонов (TK-068)

Канон — `docs/registry/runs.jsonl` (одна строка = один прогон, в git). SQLite `data/registry.sqlite` собирается из него
(`python tools/registry/registry.py build`), в git не лежит. Выбор по В-170: JSONL в git — читается глазами и diff-ом,
ревьюится Судьёй, не бинарный; SQLite — только для запросов, пересобирается за секунды (строк — тысячи, не миллионы).

- `registry.py add --what … --ticket … --data-pool … --period … --binary-md5 … --commit … --flags … --machine … --wall-s … --cpu-s … --result-path … --outcome … --judge … --status боевой|проба|недействителен|неполно [--status-why …]`
- `registry.py find <слово> [слово…] [--status …] [--full]` — «Г-NN — где и на чём считали, итог» одной строкой поиска.
- `registry.py show <id>`, `stats`, `build`; `import-auto <файл>` — влить автозапись benchrun.
- `backfill.py` — пересобирает строки `backfill-*` (JOURNAL, runs.csv, таблица П); вручную добавленные не трогает.
- Автозапись: `tools/compute/benchrun.sh` дописывает на сервере строку в `/data/registry/auto.jsonl` (хост, класс, старт, стена, rc, команда).
- Git Bash на Windows портит аргументы вида `/data/...` (→ `C:/Program Files/Git/data/...`): путь сервера писать с `MSYS_NO_PATHCONV=1`.

## Полный конфиг (владелец 06.10)
Поле `config` строки = всё для повтора байт в байт: `cmdline`, `env` (ALPHA_*/PREWARM_*/EVENTS_*/…), `files` (скрипты и списки клеток — копия по sha256 в `docs/registry/files/<sha>`), `inputs` (sha пула, verdict.csv и др. входов), `binaries` (md5), `git`. `config_status` — «полный…» или «неполон: что не снято» явно.
Снимает `tools/registry/snap.py` (на сервере `/data/registry/snap.py`), вызывается из `benchrun.sh`/`benchrun2.sh` до и после команды; манифест — `/data/registry/runs/<время>-<pid>.json`. Скрипты разворачиваются до глубины 3 по путям в тексте; нестандартные наборы (клетки по суткам) — `REG_GLOBS=glob1:glob2` перед запуском.
Влить: забрать `/data/registry/auto.jsonl` и каталог `runs/` рядом, `registry.py import-auto <auto.jsonl>`; копии файлов — из `/data/registry/files/` в `docs/registry/files/`.

## База (TK-068, схема v1) — решение по В-170
SQLite 3 (миграции `tools/registry/migrations/NNN_*.sql`, версия — таблица `schema_version`; сборка — `tools/registry/db.py`).
Сравнивали: **Postgres** — отдельный сервис, бэкап/доступ с ПК и сервера счёта усложняют, а нагрузка — тысячи строк, пишут редко;
**DuckDB** — колоночная, нужна для сотен миллионов строк, у нас их нет, нет в стандартной поставке Python; **SQLite** — stdlib,
JSON1 для расширения, один файл. Один источник правды — канон в git: `runs.jsonl`, `hypotheses.jsonl`, `cells.jsonl`, `run_cells.jsonl`,
`results.jsonl`, `verdicts.jsonl` (пишут ПК и сервер счёта через `registry.py`/`import-auto`, конфликты решает git, бэкап = git);
`data/registry.sqlite` пересобирается `registry.py build` за секунды. Расширение под новую логику: колонка `cells.logic_version`
+ JSON `cells.params`/`runs.ext`/`results.kpi`, без пересборки схемы; новая типизированная колонка — новая миграция.
Таблицы: `hypotheses`, `runs` (+`config` JSON манифеста), `run_hypotheses`, `run_data` (пул/verdict sha, эпохи, период, монеты),
`run_binaries` (md5/коммит/флаги), `cells` (типизированные вход/стоп/тейк/дедлайн/выход по стене/форма/задержка/очередь/h3/σ/hold-step
+ `params`), `run_cells`, `results` (клетка×месяц), `verdicts`. `registry.py load-cells <файл --cells> --run ID --hyp Г-NN` — клетки прогона.
Заполнены `runs` (538), `run_hypotheses`, `run_data`/`run_binaries`, `hypotheses` (148), `verdicts` (75), `cells` (243) и `results` (2 430 = 243 клетки × 10 месяцев) по полному счёту TK-040 (`R-20261006-s001`).
Клетка TK-040 = форма + «группа/набор» (группа несёт параметры варианта). Результаты — `tools/registry/agg_tk040.py` на сервере (юнит, forms.csv → `<мес>.csv`)
и `registry.py load-results <каталог> --run ID`; сделки = n_fills, Σnet bps в `results.ext`; $ и просадка — `registry.py load-usd <каталог usd-*.csv> --run ID` (`pnl_usd`, `max_dd`; **max_dd — реализованная по моментам выхода, без потолка позиций и mark-to-market, не портфельная** — подпись в `results.ext.dd_note`); шапки bounce-grid группы → latency/queue/h3 и `params.hdr_*` клеток: `registry.py load-hdr <hdr.tsv> --run ID`; привязка групп к гипотезам — `bind-hyp` (B1 = p07b-base = Г-85б). Сумма $ по клеткам гипотезы — не итог гипотезы: клетки — варианты, не портфель.
Не сделано: cells из `--cells`-файлов (оркестраторы строят список на лету); hold_step в шапках TK-040 не пишется. Автостроки (`import-auto`/`enrich`): тикет — из пути `/data/tkNNN`, статус по классу: **замер** (wave), **проба** (stand/gate/служебное), **производство**; период и пул — из дат команды и входа verdict.csv (v171b). Проверка Судьи — `runs.judge` + `verdicts.run_id` (есть для s001; остальные 75 вердиктов без привязки к прогону).
6. Г-86 по месяцам: `select r.month,r.trades,json_extract(r.ext,'$.sum_net_bps') from run_cells rc join results r on r.cell_id=rc.cell_id and r.run_id=rc.run_id where rc.hyp_id='Г-86' order by r.month`

Пять запросов (`registry.py sql "…"`):
1. Где считали Г-NN и итог: `select r.id,r.ts,r.machine,r.status,r.outcome from runs r join run_hypotheses h on h.run_id=r.id where h.hyp_id='Г-85' order by r.ts`
2. Прогоны на бинарнике X: `select r.id,r.ts,r.wall_s from runs r join run_binaries b on b.run_id=r.id where b.md5 like 'abcd%'`
3. Конфиг прогона/клетки целиком: `select json_extract(config,'$.cmdline'),config from runs where id='R-…'`; клетки — `select c.* from run_cells rc join cells c on c.id=rc.cell_id where rc.run_id='R-…'`
4. Что считано на пуле v171b за март: `select r.id,d.period_from,d.period_to from runs r join run_data d on d.run_id=r.id where d.pool='v171b' and d.period_from<='2026-03-31' and d.period_to>='2026-03-01'`
5. Повторить прогон Z: `select cmdline, json_extract(config,'$.env'), json_extract(config,'$.files') from runs where id='Z'` (файлы по sha — `docs/registry/files/`)

## Автозапись
- Замеры: `/data/benchrun.sh|benchrun2.sh wave|stand …` сами снимают манифест (snap.py) → `/data/registry/auto.jsonl`.
- Производство без замка: `systemd-run --unit tk0NN-… --collect /data/registry/regrun.sh prod <команда>` (источник — `tools/compute/regrun.sh`).
- Забор на ПК: `tail -n +1 /data/registry/runs/*.json` и `cat auto.jsonl` по ssh → `registry.py import-auto <auto.jsonl>` (манифесты рядом, в `runs/`); идемпотентно.
- Роли: `python tools/registry/registry.py add --what "…" --ticket TK-NN …`.

Проверки Судьи: `registry.py bind-verdicts` — отчётные ревью по протоколу `P-NN` / тикету `tkNNN-` и записи judge в логах тикетов (раунд = прогоны тикета между прошлой и этой записью) → `verdicts.run_id` (строка на пару ревью×прогон, id `V-…@R-…`) + `runs.judge`; методические ревью (protocol/amend/plan/rule…) остаются без прогона с пометкой в note. Перезапуск идемпотентен.

Мд5 s001: `alpha-b14flag` — обёртка-скрипт (env + exec), md5 9e9e2a2b…; сам бинарник `alpha-b14-pgo` md5 0deb3dc0… (оба в `run_binaries`, пометка в config.binaries). Имя выхода/юнита `tkNNN-…` старше пути оркестратора при выводе тикета.
