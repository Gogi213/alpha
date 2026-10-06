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
